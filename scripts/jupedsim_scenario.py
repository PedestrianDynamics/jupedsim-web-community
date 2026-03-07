"""Standalone helpers for loading and running JuPedSim web-UI scenario JSON files.

No dependency on the web backend — only JuPedSim, Shapely, and NumPy.

Usage::

    from jupedsim_scenario import load_scenario, run_scenario

    scenario = load_scenario("config.json")
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
import tempfile
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import jupedsim as jps
import numpy as np
from shapely import wkt
from shapely.geometry import Polygon

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
    def checkpoints(self) -> Dict[str, Any]:
        return self.raw.get("checkpoints", {})

    @property
    def journeys(self) -> List[Dict[str, Any]]:
        return self.raw.get("journeys", [])

    def summary(self) -> str:
        total_agents = sum(
            d.get("parameters", {}).get("number", 0)
            for d in self.distributions.values()
        )
        lines = [
            f"Scenario: {self.source_path or '(in-memory)'}",
            f"  Model:         {self.model_type}",
            f"  Seed:          {self.seed}",
            f"  Max time:      {self.max_simulation_time}s",
            f"  Exits:         {len(self.exits)}",
            f"  Distributions: {len(self.distributions)}",
            f"  Checkpoints:   {len(self.checkpoints)}",
            f"  Journeys:      {len(self.journeys)}",
            f"  Agents:        ~{total_agents}",
        ]
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
    """Load a scenario JSON exported from the JuPedSim web UI.

    The JSON must contain ``walkable_area_wkt`` (WKT string for the geometry)
    and the standard config/exits/distributions/journeys structure.
    """
    path = str(pathlib.Path(path).resolve())
    with open(path) as f:
        data = json.load(f)

    walkable_wkt = data.get("walkable_area_wkt", "")
    if not walkable_wkt:
        raise ValueError("Scenario JSON must contain 'walkable_area_wkt'")

    model_type = data.get("model_type", "CollisionFreeSpeedModel")
    seed = data.get("seed", 42)
    sim_params = (
        data.get("config", {})
        .get("simulation_settings", {})
        .get("simulationParams", {})
    )
    sim_params.setdefault("max_simulation_time", 300)
    sim_params.setdefault("model_type", model_type)

    return Scenario(
        raw=data,
        walkable_area_wkt=walkable_wkt,
        model_type=model_type,
        seed=seed,
        sim_params=sim_params,
        source_path=path,
    )


def run_scenario(scenario: Scenario, *, seed: Optional[int] = None) -> ScenarioResult:
    """Run a scenario and return results with trajectory data.

    Args:
        scenario: A Scenario from :func:`load_scenario`.
        seed: Override the scenario's seed for this run.

    Returns:
        A :class:`ScenarioResult` with metrics and trajectory access.
    """
    seed = seed if seed is not None else scenario.seed
    rng = np.random.default_rng(seed)

    # --- model & simulation --------------------------------------------------
    model = _build_model(scenario.model_type, scenario.sim_params)

    tmp = tempfile.NamedTemporaryFile(suffix=".sqlite", delete=False)
    output_file = tmp.name
    tmp.close()

    writer = jps.SqliteTrajectoryWriter(
        output_file=pathlib.Path(output_file), every_nth_frame=10,
    )
    simulation = jps.Simulation(
        model=model,
        geometry=scenario.walkable_polygon,
        trajectory_writer=writer,
    )

    # --- stages --------------------------------------------------------------
    stage_map: Dict[str, int] = {}  # element_id -> jps stage id
    exit_polygons: Dict[str, Polygon] = {}

    # Exits
    for exit_id, exit_data in scenario.exits.items():
        coords = exit_data.get("coordinates", [])
        if not coords:
            continue
        poly = Polygon(coords)
        exit_polygons[exit_id] = poly
        stage_map[exit_id] = simulation.add_exit_stage(poly)

    # Checkpoints → waypoint stages at centroid
    for cp_id, cp_data in scenario.checkpoints.items():
        coords = cp_data.get("coordinates", [])
        if not coords:
            continue
        poly = Polygon(coords)
        centroid = poly.centroid
        # Distance = half the shortest side of the bounding box
        minx, miny, maxx, maxy = poly.bounds
        distance = min(maxx - minx, maxy - miny) / 2.0
        stage_map[cp_id] = simulation.add_waypoint_stage(
            (centroid.x, centroid.y), distance
        )

    # --- journeys ------------------------------------------------------------
    journey_id_map: Dict[str, int] = {}  # journey id string -> jps journey id

    for journey_def in scenario.journeys:
        jid = journey_def["id"]
        stages = journey_def.get("stages", [])
        # Filter to stages that exist in JuPedSim (skip distributions)
        jps_stage_ids = [
            stage_map[s]
            for s in stages
            if s in stage_map and stage_map[s] >= 0
        ]
        if not jps_stage_ids:
            continue

        jd = jps.JourneyDescription(jps_stage_ids)
        for i in range(len(jps_stage_ids) - 1):
            jd.set_transition_for_stage(
                jps_stage_ids[i],
                jps.Transition.create_fixed_transition(jps_stage_ids[i + 1]),
            )
        journey_id_map[jid] = simulation.add_journey(jd)

    # If no explicit journeys, create a default one: first exit
    if not journey_id_map and exit_polygons:
        first_exit_id = next(iter(exit_polygons))
        jd = jps.JourneyDescription([stage_map[first_exit_id]])
        journey_id_map["_default"] = simulation.add_journey(jd)

    # --- agent placement -----------------------------------------------------
    total_agents_placed = 0
    flow_sources: List[Dict[str, Any]] = []

    for dist_id, dist_data in scenario.distributions.items():
        params = dist_data.get("parameters", {})
        coords = dist_data.get("coordinates", [])
        if not coords:
            continue

        dist_polygon = Polygon(coords)
        # Clip to walkable area
        clipped = dist_polygon.intersection(scenario.walkable_polygon)
        if clipped.is_empty:
            continue

        n_agents = params.get("number", 10)
        max_radius = params.get("radius", 0.2)
        if params.get("radius_distribution") == "gaussian" and params.get("radius_std"):
            max_radius = min(1.0, max_radius + 3 * params["radius_std"])

        use_flow = params.get("use_flow_spawning", False)

        # Find which journey this distribution belongs to
        journey_jps_id = None
        for j_def in scenario.journeys:
            if dist_id in j_def.get("stages", []):
                journey_jps_id = journey_id_map.get(j_def["id"])
                break
        if journey_jps_id is None:
            journey_jps_id = next(iter(journey_id_map.values()), None)
        if journey_jps_id is None:
            continue

        # First stage of the journey for this distribution
        first_stage_id = None
        for j_def in scenario.journeys:
            if j_def["id"] in journey_id_map and journey_id_map[j_def["id"]] == journey_jps_id:
                for s in j_def.get("stages", []):
                    if s in stage_map and stage_map[s] >= 0:
                        first_stage_id = stage_map[s]
                        break
                break
        if first_stage_id is None:
            first_stage_id = next(iter(stage_map.values()))

        if use_flow:
            # Flow spawning: generate positions, spawn over time
            positions = jps.distribute_until_filled(
                polygon=clipped,
                distance_to_agents=2 * max_radius,
                distance_to_polygon=max_radius,
                seed=seed + hash(dist_id) % 10000,
            )
            shuffle_rng = random.Random(seed + hash(dist_id))
            shuffle_rng.shuffle(positions)

            flow_start = max(0, params.get("flow_start_time", 0))
            flow_end = max(flow_start + 0.1, params.get("flow_end_time", 10))
            flow_duration = flow_end - flow_start
            frequency = flow_duration / max(1, n_agents)

            radii, v0s = _sample_agent_values(params, n_agents, rng)

            flow_sources.append({
                "positions": positions,
                "n_agents": n_agents,
                "start_time": flow_start,
                "frequency": frequency,
                "radii": radii,
                "v0s": v0s,
                "journey_id": journey_jps_id,
                "stage_id": first_stage_id,
                "spawned": 0,
            })
        else:
            # Instant placement
            max_capacity = _estimate_max_capacity(clipped, max_radius)
            actual_n = min(n_agents, max_capacity)

            positions = jps.distribute_by_number(
                polygon=clipped,
                number_of_agents=actual_n,
                distance_to_agents=2 * max_radius,
                distance_to_polygon=max_radius,
                seed=seed + hash(dist_id) % 10000,
            )

            radii, v0s = _sample_agent_values(params, len(positions), rng)

            for i, pos in enumerate(positions):
                agent_params = _build_agent_params(
                    scenario.model_type, float(v0s[i]), float(radii[i]),
                    position=pos, journey_id=journey_jps_id, stage_id=first_stage_id,
                )
                simulation.add_agent(agent_params)
                total_agents_placed += 1

    # --- simulation loop -----------------------------------------------------
    max_time = scenario.max_simulation_time
    initial_count = simulation.agent_count()

    has_pending_flow = bool(flow_sources)
    while (simulation.agent_count() > 0 or has_pending_flow) and simulation.elapsed_time() < max_time:
        # Flow spawning
        for src in flow_sources:
            if src["spawned"] >= src["n_agents"]:
                continue
            t = simulation.elapsed_time()
            if t < src["start_time"]:
                continue
            expected = min(
                src["n_agents"],
                int((t - src["start_time"]) / src["frequency"]) + 1,
            )
            while src["spawned"] < expected:
                idx = src["spawned"]
                pos = src["positions"][idx % len(src["positions"])]
                agent_params = _build_agent_params(
                    scenario.model_type,
                    float(src["v0s"][idx]),
                    float(src["radii"][idx]),
                    position=pos,
                    journey_id=src["journey_id"],
                    stage_id=src["stage_id"],
                )
                try:
                    simulation.add_agent(agent_params)
                    total_agents_placed += 1
                except Exception:
                    pass  # position occupied, skip
                src["spawned"] += 1

        has_pending_flow = any(s["spawned"] < s["n_agents"] for s in flow_sources)
        simulation.iterate()

    # --- collect results -----------------------------------------------------
    evac_time = simulation.elapsed_time()
    remaining = simulation.agent_count()

    # Close writer so SQLite is flushed
    try:
        writer.close()
    except Exception:
        pass

    total_with_flow = initial_count + sum(s["spawned"] for s in flow_sources)

    dt = 0.01  # JuPedSim default timestep
    every_nth = 10  # matches SqliteTrajectoryWriter(every_nth_frame=10)

    metrics = {
        "success": remaining == 0 or evac_time >= max_time,
        "evacuation_time": round(evac_time, 2),
        "total_agents": total_with_flow,
        "agents_evacuated": total_with_flow - remaining,
        "agents_remaining": remaining,
        "all_evacuated": remaining == 0,
        "frame_rate": 1.0 / (dt * every_nth),
        "dt": dt,
        "seed": seed,
        "walkable_polygon": scenario.walkable_polygon,
    }

    return ScenarioResult(metrics=metrics, sqlite_file=output_file)
