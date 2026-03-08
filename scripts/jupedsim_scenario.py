"""Standalone helpers for loading and running JuPedSim web-UI scenario JSON files.

No dependency on the web backend — only JuPedSim, Shapely, and NumPy.

Usage::

    from jupedsim_scenario import load_scenario, run_scenario

    scenario = load_scenario("scenario.zip")
    print(scenario.summary())

    result = run_scenario(scenario)
    print(result.metrics)

    df = result.trajectory_dataframe()
"""

from __future__ import annotations

import json
import math
import os
import pathlib
import random
import sqlite3
import sys
import tempfile
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

import jupedsim as jps
import numpy as np
from shapely import wkt
from shapely.geometry import Polygon

SCRIPT_DIR = pathlib.Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.direct_steering_runtime import (
    advance_path_target,
    assign_agent_target,
    checkpoint_stage_reached,
    ensure_agent_speed_state,
    extract_agent_xy,
    is_inside_polygon,
    sample_wait_time,
    set_agent_desired_speed,
    update_checkpoint_speed,
)
from shared.simulation_init import (
    _find_nearest_exit,
    _random_point_in_polygon,
    build_agent_path_state,
    create_agent_parameters,
    initialize_simulation_from_json,
)

# ---------------------------------------------------------------------------
# Model factory
# ---------------------------------------------------------------------------

_MODEL_BUILDERS = {
    "CollisionFreeSpeedModel": lambda p: jps.CollisionFreeSpeedModel(
        strength_neighbor_repulsion=p.get("strength_neighbor_repulsion", 2.6),
        range_neighbor_repulsion=p.get("range_neighbor_repulsion", 0.1),
    ),
    "CollisionFreeSpeedModelV2": lambda p: jps.CollisionFreeSpeedModelV2(
        strength_neighbor_repulsion=p.get("strength_neighbor_repulsion", 2.6),
        range_neighbor_repulsion=p.get("range_neighbor_repulsion", 0.1),
    ),
      "AnticipationVelocityModel": lambda p: jps.AnticipationVelocityModel(
        #strength_neighbor_repulsion=p.get("strength_neighbor_repulsion", 2.6),
        #range_neighbor_repulsion=p.get("range_neighbor_repulsion", 0.1),
        #anticipation_time=p.get("anticipation_time", 1.0)
    ),
    "GeneralizedCentrifugalForceModel": lambda p: jps.GeneralizedCentrifugalForceModel(
        strength_neighbor_repulsion=p.get("gcfm_strength_neighbor_repulsion", 0.3),
        strength_geometry_repulsion=p.get("gcfm_strength_geometry_repulsion", 0.2),
        max_neighbor_interaction_distance=p.get("gcfm_max_neighbor_interaction_distance", 2.0),
        max_geometry_interaction_distance=p.get("gcfm_max_geometry_interaction_distance", 2.0),
        max_neighbor_repulsion_force=p.get("gcfm_max_neighbor_repulsion_force", 9.0),
        max_geometry_repulsion_force=p.get("gcfm_max_geometry_repulsion_force", 3.0),
    ),
    "SocialForceModel": lambda p: jps.SocialForceModel(
        bodyForce=p.get("agent_strength", 2000),
        friction=p.get("agent_range", 0.08),
    ),

}

_AGENT_PARAM_BUILDERS = {
    "CollisionFreeSpeedModel": lambda **kw: jps.CollisionFreeSpeedModelAgentParameters(**kw),
    "CollisionFreeSpeedModelV2": lambda **kw: jps.CollisionFreeSpeedModelV2AgentParameters(**kw),
    "GeneralizedCentrifugalForceModel": lambda **kw: jps.GeneralizedCentrifugalForceModelAgentParameters(
        desired_speed=kw["desired_speed"],
        a_v=1.0, a_min=kw["radius"], b_min=kw["radius"], b_max=kw["radius"] * 2,
        position=kw["position"], journey_id=kw["journey_id"], stage_id=kw["stage_id"],
    ),
    "SocialForceModel": lambda **kw: jps.SocialForceModelAgentParameters(**kw),
    "AnticipationVelocityModel": lambda **kw: jps.AnticipationVelocityModelAgentParameters(**kw),
}


def _build_model(model_type: str, sim_params: dict):
    builder = _MODEL_BUILDERS.get(model_type)
    if builder is None:
        raise ValueError(f"Unknown model type: {model_type}. Available: {list(_MODEL_BUILDERS)}")
    return builder(sim_params)


def _build_agent_params(
    model_type: str,
    v0: float,
    radius: float,
    position: Tuple[float, float],
    journey_id: int,
    stage_id: int,
):
    builder = _AGENT_PARAM_BUILDERS.get(model_type)
    if builder is None:
        raise ValueError(f"No agent params builder for model type: {model_type}")
    return builder(
        desired_speed=v0, radius=radius,
        position=position, journey_id=journey_id, stage_id=stage_id,
    )


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _estimate_max_capacity(polygon: Polygon, max_radius: float) -> int:
    effective_radius = max(max_radius, 0.1)
    theoretical = polygon.area / (math.pi * effective_radius * effective_radius)
    return max(1, math.floor(theoretical * 0.5))


def _sample_agent_values(
    params: dict, n_agents: int, rng: np.random.Generator
) -> Tuple[np.ndarray, np.ndarray]:
    """Sample radii and speeds for *n_agents*."""
    mean_radius = max(0.1, min(1.0, params.get("radius", 0.2)))
    mean_v0 = max(0.1, min(5.0, params.get("desired_speed", params.get("v0", 1.2))))

    if params.get("radius_distribution") == "gaussian" and params.get("radius_std"):
        radii = rng.normal(mean_radius, params["radius_std"], n_agents).clip(0.1, 1.0)
    else:
        radii = np.full(n_agents, mean_radius)

    v0_dist = params.get("desired_speed_distribution", params.get("v0_distribution"))
    v0_std = params.get("desired_speed_std", params.get("v0_std"))
    if v0_dist == "gaussian" and v0_std:
        v0s = rng.normal(mean_v0, v0_std, n_agents).clip(0.1, 5.0)
    else:
        v0s = np.full(n_agents, mean_v0)

    return radii, v0s


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Scenario:
    """A loaded scenario ready for inspection and execution."""

    raw: Dict[str, Any]
    walkable_area_wkt: str
    model_type: str
    seed: int
    sim_params: Dict[str, Any]
    source_path: Optional[str] = None

    _walkable_polygon: Any = field(default=None, repr=False)

    def __post_init__(self):
        self._walkable_polygon = wkt.loads(self.walkable_area_wkt)

    @property
    def walkable_polygon(self):
        return self._walkable_polygon

    @property
    def max_simulation_time(self) -> float:
        return self.sim_params.get("max_simulation_time", 300)

    @property
    def exits(self) -> Dict[str, Any]:
        return self.raw.get("exits", {})

    @property
    def distributions(self) -> Dict[str, Any]:
        return self.raw.get("distributions", {})

    @property
    def stages(self) -> Dict[str, Any]:
        return self.raw.get("checkpoints", {})

    @property
    def zones(self) -> Dict[str, Any]:
        return self.raw.get("zones", {})

    @property
    def journeys(self) -> List[Dict[str, Any]]:
        return self.raw.get("journeys", [])

    def summary(self) -> str:
        total_agents = sum(
            d.get("parameters", {}).get("number", 0)
            for d in self.distributions.values()
        )
        journey_sequence = []
        journeys = self.raw.get("journeys", [])
        if journeys:
            journey_sequence = list(journeys[0].get("stages", []))
        lines = [
            f"Scenario: {self.source_path or '(in-memory)'}",
            f"  Model:         {self.model_type}",
            f"  Seed:          {self.seed}",
            f"  Max time:      {self.max_simulation_time}s",
            f"  Exits:         {len(self.exits)}",
            f"  Distributions: {len(self.distributions)}",
            f"  Stages:        {len(self.stages)}",
            f"  Zones:         {len(self.zones)}",
            f"  Journeys:      {len(self.journeys)}",
            f"  Agents:        ~{total_agents}",
        ]
        if journey_sequence:
            checkpoint_count = sum(
                stage.startswith("jps-checkpoints_") for stage in journey_sequence
            )
            exit_count = sum(stage.startswith("jps-exits_") for stage in journey_sequence)
            distribution_count = sum(
                stage.startswith("jps-distributions_") for stage in journey_sequence
            )
            lines.append(f"  Journey elems: {len(journey_sequence)}")
            lines.append(
                "  Route:         "
                f"{distribution_count} distribution, "
                f"{checkpoint_count} checkpoint, "
                f"{exit_count} exit"
            )
            lines.append(f"  Sequence:      {' -> '.join(journey_sequence)}")
        for dist_id, dist in self.distributions.items():
            params = dist.get("parameters", {})
            flow = params.get("use_flow_spawning", False)
            n = params.get("number", "?")
            tag = f" (flow: {params.get('flow_start_time', 0)}-{params.get('flow_end_time', 10)}s)" if flow else ""
            lines.append(f"    {dist_id}: {n} agents{tag}")
        return "\n".join(lines)

    def set_agent_count(self, distribution_id: str, count: int):
        dist = self.distributions.get(distribution_id)
        if dist is None:
            raise KeyError(
                f"Distribution '{distribution_id}' not found. "
                f"Available: {list(self.distributions.keys())}"
            )
        dist.setdefault("parameters", {})["number"] = count
        dist["parameters"]["distribution_mode"] = "by_number"

    def set_seed(self, seed: int):
        self.seed = seed

    def set_max_time(self, seconds: float):
        self.sim_params["max_simulation_time"] = seconds

    def set_model_type(self, model_type: str):
        if model_type not in _MODEL_BUILDERS:
            raise ValueError(f"Unknown model: {model_type}. Available: {list(_MODEL_BUILDERS)}")
        self.model_type = model_type
        self.sim_params["model_type"] = model_type

    def set_model_params(self, **kwargs):
        """Set model-specific parameters (e.g. strength_neighbor_repulsion, range_neighbor_repulsion)."""
        self.sim_params.update(kwargs)

    def set_agent_params(self, distribution_id: str, **kwargs):
        """Set agent parameters for a distribution.

        Supported keys: radius, desired_speed (or v0), radius_distribution,
        radius_std, desired_speed_distribution (or v0_distribution),
        desired_speed_std (or v0_std), use_flow_spawning, flow_start_time,
        flow_end_time, distribution_mode, number.
        """
        dist = self.distributions.get(distribution_id)
        if dist is None:
            raise KeyError(
                f"Distribution '{distribution_id}' not found. "
                f"Available: {list(self.distributions.keys())}"
            )
        dist.setdefault("parameters", {}).update(kwargs)


@dataclass
class ScenarioResult:
    """Results from running a scenario."""

    metrics: Dict[str, Any]
    sqlite_file: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.metrics.get("success", False)

    @property
    def evacuation_time(self) -> float:
        return self.metrics.get("evacuation_time", 0.0)

    @property
    def total_agents(self) -> int:
        return self.metrics.get("total_agents", 0)

    @property
    def agents_evacuated(self) -> int:
        return self.metrics.get("agents_evacuated", 0)

    @property
    def agents_remaining(self) -> int:
        return self.metrics.get("agents_remaining", 0)

    @property
    def frame_rate(self) -> float:
        """Trajectory frame rate in frames per second (dt=0.01, every_nth_frame=10 → 10 fps)."""
        return self.metrics.get("frame_rate", 10.0)

    @property
    def dt(self) -> float:
        """Simulation timestep in seconds."""
        return self.metrics.get("dt", 0.01)

    @property
    def seed(self) -> int:
        """Random seed used for this run."""
        return self.metrics.get("seed", 0)

    @property
    def walkable_polygon(self):
        """Walkable area as a Shapely Polygon (for pedpy analysis)."""
        return self.metrics.get("walkable_polygon")

    def trajectory_dataframe(self):
        """Load trajectory data into a pandas DataFrame.

        Columns: frame, id, x, y, ori_x, ori_y
        """
        import pandas as pd

        if not self.sqlite_file or not os.path.exists(self.sqlite_file):
            raise FileNotFoundError("No trajectory SQLite file available")

        con = sqlite3.connect(self.sqlite_file)
        try:
            df = pd.read_sql_query(
                "SELECT frame, id, pos_x AS x, pos_y AS y, ori_x, ori_y FROM trajectory_data",
                con,
            )
        finally:
            con.close()
        return df

    def cleanup(self):
        """Delete the temporary SQLite trajectory file."""
        if self.sqlite_file and os.path.exists(self.sqlite_file):
            os.unlink(self.sqlite_file)
            self.sqlite_file = None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_scenario(path: str) -> Scenario:
    """Load a scenario ZIP or directory exported from the JuPedSim web UI."""
    import zipfile

    resolved = pathlib.Path(path).resolve()

    if resolved.is_dir():
        json_files = sorted(resolved.glob("*.json"))
        wkt_files = sorted(resolved.glob("*.wkt"))
        if not json_files or not wkt_files:
            raise ValueError(
                f"Scenario directory must contain one JSON and one WKT file: {resolved}"
            )
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        walkable_wkt = wkt_files[0].read_text(encoding="utf-8").strip()
        source_path = str(resolved)
    else:
        source_path = str(resolved)
        with zipfile.ZipFile(source_path) as zf:
            names = zf.namelist()

            json_name = next((n for n in names if n.endswith(".json")), None)
            if json_name is None:
                raise ValueError(f"ZIP contains no JSON file. Found: {names}")
            data = json.loads(zf.read(json_name))

            wkt_name = next((n for n in names if n.endswith(".wkt")), None)
            if wkt_name is None:
                raise ValueError(f"ZIP contains no WKT file. Found: {names}")
            walkable_wkt = zf.read(wkt_name).decode("utf-8").strip()

    sim_settings = data.get("config", {}).get("simulation_settings", {})
    sim_params = sim_settings.get("simulationParams", {})
    model_type = sim_params.get("model_type", "CollisionFreeSpeedModel")
    seed = sim_settings.get("baseSeed", 42)

    sim_params.setdefault("max_simulation_time", 300)

    return Scenario(
        raw=data,
        walkable_area_wkt=walkable_wkt,
        model_type=model_type,
        seed=seed,
        sim_params=sim_params,
        source_path=source_path,
    )


def run_scenario(scenario: Scenario, *, seed: Optional[int] = None) -> ScenarioResult:
    """Run a scenario with the same shared setup/runtime semantics as the web app."""
    seed = seed if seed is not None else scenario.seed

    model = _build_model(scenario.model_type, scenario.sim_params)

    sqlite_tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    output_file = sqlite_tmp.name
    sqlite_tmp.close()

    writer = jps.SqliteTrajectoryWriter(
        output_file=pathlib.Path(output_file),
        every_nth_frame=10,
    )
    simulation = jps.Simulation(
        model=model,
        geometry=scenario.walkable_polygon,
        trajectory_writer=writer,
    )

    config_tmp = tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False)
    try:
        json.dump(scenario.raw, config_tmp, indent=2)
        config_tmp.close()

        walkable_area = SimpleNamespace(polygon=scenario.walkable_polygon)
        global_parameters = SimpleNamespace(**scenario.sim_params)
        _, _positions, agent_radii, spawning_info = initialize_simulation_from_json(
            config_tmp.name,
            simulation,
            walkable_area,
            seed=seed,
            model_type=scenario.model_type,
            global_parameters=global_parameters,
        )

        initial_agent_count = simulation.agent_count()
        has_flow_spawning = spawning_info.get("has_flow_spawning", False)
        spawning_freqs_and_numbers = spawning_info.get("spawning_freqs_and_numbers", [])
        starting_pos_per_source = spawning_info.get("starting_pos_per_source", [])
        num_agents_per_source = spawning_info.get("num_agents_per_source", [])
        agent_counter_per_source = spawning_info.get("agent_counter_per_source", [])
        flow_distributions = spawning_info.get("flow_distributions", [])
        has_premovement = spawning_info.get("has_premovement", False)
        premovement_times = spawning_info.get("premovement_times", {})
        direct_steering_info = spawning_info.get("direct_steering_info", {})
        agent_wait_info = spawning_info.get("agent_wait_info", {})
        checkpoint_throughput_tracker = {}
        agent_speed_state: Dict[int, Dict[str, Any]] = {}
        flow_variant_rng = random.Random(seed)

        while simulation.elapsed_time() < scenario.max_simulation_time and (
            simulation.agent_count() > 0
            or (
                has_flow_spawning
                and sum(agent_counter_per_source) < sum(num_agents_per_source)
            )
        ):
            if has_flow_spawning:
                current_time = simulation.elapsed_time()

                for source_id in range(len(spawning_freqs_and_numbers)):
                    if source_id >= len(flow_distributions):
                        continue

                    flow_dist = flow_distributions[source_id]
                    spawn_frequency = spawning_freqs_and_numbers[source_id][0]
                    next_spawn_time = flow_dist["start_time"] + (
                        agent_counter_per_source[source_id] * spawn_frequency
                    )

                    if agent_counter_per_source[source_id] >= num_agents_per_source[source_id]:
                        continue
                    if current_time < flow_dist["start_time"] or current_time > flow_dist["end_time"]:
                        continue
                    if current_time < next_spawn_time:
                        continue

                    for _ in range(spawning_freqs_and_numbers[source_id][1]):
                        spawned_this_attempt = False
                        selected_variant = None
                        selected_variant_info = None

                        for j in range(len(starting_pos_per_source[source_id])):
                            pos_index = (
                                agent_counter_per_source[source_id] + j
                            ) % len(starting_pos_per_source[source_id])
                            position = starting_pos_per_source[source_id][pos_index]
                            flow_params = flow_dist["params"]

                            try:
                                agent_parameters = create_agent_parameters(
                                    model_type=spawning_info["model_type"],
                                    position=position,
                                    params=flow_params,
                                    global_params=spawning_info["global_parameters"],
                                    journey_id=None,
                                    stage_id=None,
                                )

                                if flow_dist.get("journey_info"):
                                    distribution_journeys = flow_dist["journey_info"]
                                    total_weight = sum(
                                        variant_info["variant_data"]["percentage"]
                                        for variant_info in distribution_journeys
                                    )
                                    rand_val = flow_variant_rng.random() * total_weight
                                    cumulative_weight = 0.0
                                    for variant_info in distribution_journeys:
                                        cumulative_weight += variant_info["variant_data"]["percentage"]
                                        if rand_val <= cumulative_weight:
                                            selected_variant_info = variant_info
                                            break
                                    if selected_variant_info is None:
                                        selected_variant_info = distribution_journeys[0]

                                    selected_variant = selected_variant_info["variant_data"]
                                    agent_parameters.journey_id = selected_variant["id"]

                                    selected_stage_id = None
                                    for stage in selected_variant.get("entry_stages", []):
                                        if (
                                            stage in spawning_info["stage_map"]
                                            and spawning_info["stage_map"][stage] != -1
                                        ):
                                            selected_stage_id = spawning_info["stage_map"][stage]
                                            break
                                    if selected_stage_id is None:
                                        raise ValueError(
                                            f"No valid entry stage for variant {selected_variant.get('variant_name', selected_variant.get('id'))}"
                                        )
                                    agent_parameters.stage_id = selected_stage_id
                                    uses_direct_steering = any(
                                        stage in direct_steering_info
                                        for stage in selected_variant.get("actual_stages", [])
                                    )
                                    global_ds_journey_id = spawning_info.get("global_ds_journey_id")
                                    global_ds_stage_id = spawning_info.get("global_ds_stage_id")
                                    if (
                                        uses_direct_steering
                                        and global_ds_journey_id is not None
                                        and global_ds_stage_id is not None
                                    ):
                                        agent_parameters.journey_id = global_ds_journey_id
                                        agent_parameters.stage_id = global_ds_stage_id
                                else:
                                    nearest_exit_stage_id = _find_nearest_exit(
                                        position,
                                        stage_map=spawning_info.get("stage_map"),
                                        exits=spawning_info.get("exits"),
                                        exit_geometries=spawning_info.get("exit_geometries"),
                                    )
                                    nearest_journey_id = spawning_info.get("exit_to_journey", {}).get(
                                        nearest_exit_stage_id
                                    )
                                    if nearest_journey_id is None:
                                        raise ValueError(
                                            f"Missing exit journey mapping for stage {nearest_exit_stage_id}"
                                        )
                                    agent_parameters.journey_id = nearest_journey_id
                                    agent_parameters.stage_id = nearest_exit_stage_id

                                agent_id = simulation.add_agent(agent_parameters)
                                agent_radii[agent_id] = flow_params.get("radius", 0.2)

                                if selected_variant and agent_wait_info is not None and direct_steering_info:
                                    path_state = build_agent_path_state(
                                        variant_data=selected_variant,
                                        journey_key=(
                                            selected_variant_info.get("original_journey_id")
                                            if selected_variant_info
                                            else None
                                        ),
                                        transitions=spawning_info.get("transitions", []),
                                        direct_steering_info=direct_steering_info,
                                        waypoint_routing=spawning_info.get("waypoint_routing", {}),
                                        seed=seed,
                                        agent_id=agent_id,
                                        initial_position=(float(position[0]), float(position[1])),
                                        agent_radius=float(flow_params.get("radius", 0.2)),
                                    )
                                    if path_state:
                                        agent_wait_info[agent_id] = path_state
                                elif (
                                    not selected_variant
                                    and agent_wait_info is not None
                                    and direct_steering_info
                                ):
                                    stage_id_to_exit = {
                                        v: k for k, v in spawning_info.get("stage_map", {}).items()
                                    }
                                    exit_id = stage_id_to_exit.get(agent_parameters.stage_id)
                                    if exit_id and exit_id in direct_steering_info:
                                        exit_info = direct_steering_info[exit_id]
                                        base_seed = seed + agent_id * 9973
                                        target_rng = random.Random(base_seed)
                                        target = _random_point_in_polygon(
                                            exit_info["polygon"],
                                            target_rng,
                                        )
                                        stage_configs = {}
                                        for sk, info in direct_steering_info.items():
                                            stage_configs[sk] = {
                                                "polygon": info.get("polygon"),
                                                "stage_type": info.get("stage_type", "exit"),
                                                "waiting_time": float(info.get("waiting_time", 0.0)),
                                                "waiting_time_distribution": info.get(
                                                    "waiting_time_distribution",
                                                    "constant",
                                                ),
                                                "waiting_time_std": float(info.get("waiting_time_std", 1.0)),
                                                "enable_throughput_throttling": bool(
                                                    info.get("enable_throughput_throttling", False)
                                                ),
                                                "max_throughput": float(info.get("max_throughput", 1.0)),
                                                "speed_factor": float(info.get("speed_factor", 1.0)),
                                            }
                                        agent_wait_info[agent_id] = {
                                            "mode": "path",
                                            "path_choices": {},
                                            "stage_configs": stage_configs,
                                            "current_origin": exit_id,
                                            "current_target_stage": exit_id,
                                            "target": target,
                                            "target_assigned": False,
                                            "state": "to_target",
                                            "wait_until": None,
                                            "inside_since": None,
                                            "reach_penetration": 0.25,
                                            "reach_dwell_seconds": 0.2,
                                            "step_index": 0,
                                            "base_seed": base_seed,
                                        }

                                spawned_this_attempt = True
                                break
                            except Exception:
                                continue

                        if not spawned_this_attempt:
                            break
                        agent_counter_per_source[source_id] += 1

            if has_premovement:
                current_time = simulation.elapsed_time()
                for agent in simulation.agents():
                    agent_id = agent.id
                    if agent_id in premovement_times and not premovement_times[agent_id]["activated"]:
                        if current_time >= premovement_times[agent_id]["premovement_time"]:
                            desired_speed = premovement_times[agent_id]["desired_speed"]
                            set_agent_desired_speed(agent, desired_speed)
                            speed_state = ensure_agent_speed_state(
                                agent_speed_state, agent_id, agent
                            )
                            speed_state["original_speed"] = float(desired_speed)
                            speed_state["active_checkpoint"] = None
                            premovement_times[agent_id]["activated"] = True

            if direct_steering_info:
                live_agent_ids = set()
                for agent in simulation.agents():
                    agent_id = int(agent.id)
                    live_agent_ids.add(agent_id)
                    x, y = extract_agent_xy(agent)
                    if x is None or y is None:
                        continue
                    update_checkpoint_speed(
                        agent_speed_state,
                        direct_steering_info,
                        agent_id,
                        agent,
                        None,
                        None,
                        x,
                        y,
                    )

                for tracked_agent_id in list(agent_speed_state.keys()):
                    if tracked_agent_id not in live_agent_ids:
                        agent_speed_state.pop(tracked_agent_id, None)

            if direct_steering_info and agent_wait_info:
                current_time = simulation.elapsed_time()
                agents_by_id = {agent.id: agent for agent in simulation.agents()}

                for agent_id, wait_info in list(agent_wait_info.items()):
                    if wait_info.get("mode") != "path":
                        continue
                    agent = agents_by_id.get(agent_id)
                    if agent is None:
                        continue

                    state = wait_info.get("state", "to_target")
                    x, y = extract_agent_xy(agent)
                    if x is None or y is None:
                        continue
                    wait_info["current_position"] = (x, y)

                    if state == "done":
                        update_checkpoint_speed(
                            agent_speed_state,
                            direct_steering_info,
                            agent_id,
                            agent,
                            None,
                            None,
                            x,
                            y,
                        )
                        continue

                    current_target_stage = wait_info.get("current_target_stage")
                    stage_cfg = wait_info.get("stage_configs", {}).get(current_target_stage, {})
                    target = wait_info.get("target")

                    if state == "to_target":
                        update_checkpoint_speed(
                            agent_speed_state,
                            direct_steering_info,
                            agent_id,
                            agent,
                            current_target_stage,
                            stage_cfg,
                            x,
                            y,
                        )
                        if not wait_info.get("target_assigned", False):
                            assign_agent_target(agent, target)
                            wait_info["target_assigned"] = True

                        stage_type = stage_cfg.get("stage_type")
                        if stage_type == "exit":
                            reached_target = is_inside_polygon(x, y, stage_cfg.get("polygon"))
                            if not reached_target and target is not None:
                                reached_target = (
                                    math.hypot(x - float(target[0]), y - float(target[1])) <= 0.2
                                )
                        else:
                            reached_target = checkpoint_stage_reached(
                                wait_info,
                                stage_cfg,
                                current_time,
                                x,
                                y,
                            )
                            if not reached_target and target is not None:
                                reached_target = (
                                    math.hypot(x - float(target[0]), y - float(target[1])) <= 0.2
                                )

                        if reached_target:
                            enable_throttling = stage_cfg.get("enable_throughput_throttling", False)
                            max_throughput = float(stage_cfg.get("max_throughput", 1.0))
                            wp_key = current_target_stage
                            if enable_throttling and wp_key and max_throughput > 0:
                                min_interval = 1.0 / max_throughput
                                tracker = checkpoint_throughput_tracker.get(
                                    wp_key,
                                    {"last_exit_time": -9999},
                                )
                                if current_time - tracker.get("last_exit_time", -9999) < min_interval:
                                    continue
                                checkpoint_throughput_tracker[wp_key] = {
                                    "last_exit_time": current_time
                                }

                            if stage_type == "exit":
                                try:
                                    simulation.mark_agent_for_removal(agent_id)
                                except Exception:
                                    pass
                                wait_info["state"] = "done"
                                continue

                            wait_time = sample_wait_time(
                                stage_cfg,
                                wait_info.get("base_seed", 0),
                                wait_info.get("step_index", 0),
                            )
                            if wait_time > 0:
                                wait_info["state"] = "waiting"
                                wait_info["wait_until"] = current_time + wait_time
                            else:
                                advance_path_target(wait_info)
                        continue

                    if state == "waiting":
                        update_checkpoint_speed(
                            agent_speed_state,
                            direct_steering_info,
                            agent_id,
                            agent,
                            current_target_stage,
                            stage_cfg,
                            x,
                            y,
                        )
                        if current_time >= float(wait_info.get("wait_until", current_time)):
                            advance_path_target(wait_info)
                        continue

            simulation.iterate()

        evacuation_time = simulation.elapsed_time()
        remaining = simulation.agent_count()
        total_agents = initial_agent_count
        if has_flow_spawning:
            total_agents += sum(agent_counter_per_source)

        metrics = {
            "success": remaining == 0 or evacuation_time >= scenario.max_simulation_time,
            "evacuation_time": round(evacuation_time, 2),
            "total_agents": total_agents,
            "agents_evacuated": total_agents - remaining,
            "agents_remaining": remaining,
            "all_evacuated": remaining == 0,
            "frame_rate": 10.0,
            "dt": 0.01,
            "seed": seed,
            "walkable_polygon": scenario.walkable_polygon,
        }

        return ScenarioResult(metrics=metrics, sqlite_file=output_file)
    finally:
        try:
            writer.close()
        except Exception:
            pass
        try:
            os.unlink(config_tmp.name)
        except Exception:
            pass
