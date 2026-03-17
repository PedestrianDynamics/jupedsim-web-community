"""Standalone helpers for loading and running JuPedSim web-UI scenario JSON files.

No dependency on the web backend — only JuPedSim, Shapely, and NumPy.

Usage::

    from core.scenario import load_scenario, run_scenario

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
REPO_ROOT = SCRIPT_DIR.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from shared.direct_steering_runtime import (
    advance_path_target,
    assign_agent_target,
    ensure_agent_speed_state,
    extract_agent_xy,
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


def _normalize_flow_schedule_entry(entry: dict) -> dict:
    start_time = entry.get("flow_start_time", entry.get("start_time_s"))
    end_time = entry.get("flow_end_time", entry.get("end_time_s"))
    number = entry.get("number", entry.get("sim_count"))

    if start_time is None or end_time is None or number is None:
        raise ValueError(
            "Each flow schedule entry must define start/end time and number. "
            "Accepted keys: flow_start_time|start_time_s, flow_end_time|end_time_s, number|sim_count."
        )

    start_time = float(start_time)
    end_time = float(end_time)
    number = int(number)

    if start_time < 0 or end_time <= start_time:
        raise ValueError(
            f"Invalid flow window [{start_time}, {end_time}] - end_time must be greater than start_time."
        )
    if number <= 0:
        raise ValueError(f"Flow schedule numbers must be positive integers, got {number!r}")

    return {
        "flow_start_time": start_time,
        "flow_end_time": end_time,
        "number": number,
    }


def _normalized_flow_schedule(params: dict) -> list[dict]:
    raw_schedule = params.get("flow_schedule", [])
    if not raw_schedule:
        return []
    normalized = [_normalize_flow_schedule_entry(entry) for entry in raw_schedule]
    normalized.sort(key=lambda entry: (entry["flow_start_time"], entry["flow_end_time"]))
    return normalized


def _distribution_agent_budget(dist: dict) -> int:
    params = dist.get("parameters", {})
    schedule = _normalized_flow_schedule(params)
    if schedule:
        initial_number = int(params.get("initial_number", 0) or 0)
        return initial_number + sum(entry["number"] for entry in schedule)
    return int(params.get("number", 0) or 0)


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
        self._sync_runtime_to_raw()

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

    def _simulation_settings(self) -> Dict[str, Any]:
        config = self.raw.setdefault("config", {})
        return config.setdefault("simulation_settings", {})

    def _simulation_params(self) -> Dict[str, Any]:
        settings = self._simulation_settings()
        return settings.setdefault("simulationParams", {})

    def _sync_runtime_to_raw(self) -> None:
        settings = self._simulation_settings()
        settings["baseSeed"] = self.seed
        params = self._simulation_params()
        params.update(self.sim_params)
        params["model_type"] = self.model_type

    def summary(self) -> str:
        total_agents = sum(
            _distribution_agent_budget(d)
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

    def plot(self, ax=None):
        """Plot the scenario geometry with labeled distributions, exits, zones, and checkpoints.

        Returns the matplotlib Axes so callers can further customise the figure.
        """
        import matplotlib.pyplot as plt
        from matplotlib.patches import Polygon as MplPolygon

        if ax is None:
            _, ax = plt.subplots(figsize=(12, 7), constrained_layout=True)

        # Walkable area (exterior + interior holes as walls)
        from matplotlib.path import Path as MplPath
        from matplotlib.patches import PathPatch

        exterior_coords = list(self.walkable_polygon.exterior.coords)
        codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(exterior_coords) - 2) + [MplPath.CLOSEPOLY]
        verts = list(exterior_coords)

        for interior in self.walkable_polygon.interiors:
            hole_coords = list(interior.coords)
            codes += [MplPath.MOVETO] + [MplPath.LINETO] * (len(hole_coords) - 2) + [MplPath.CLOSEPOLY]
            verts += list(hole_coords)

        path = MplPath(verts, codes)
        patch = PathPatch(path, facecolor="#f0f0ec", edgecolor="#3a3a3a",
                          linewidth=1.5, alpha=0.5, zorder=0)
        ax.add_patch(patch)

        # Draw wall outlines explicitly
        wx, wy = self.walkable_polygon.exterior.xy
        ax.plot(wx, wy, color="#3a3a3a", linewidth=1.5, zorder=1)
        for interior in self.walkable_polygon.interiors:
            ix, iy = interior.xy
            ax.plot(ix, iy, color="#3a3a3a", linewidth=1.5, zorder=1)

        palette = {
            "distribution": "#2563EB",
            "exit": "#DC2626",
            "zone": "#059669",
            "checkpoint": "#D97706",
        }

        def _plot_element(coords, color, label, alpha=0.35):
            poly = MplPolygon(coords[:-1], closed=True, facecolor=color,
                              edgecolor=color, alpha=alpha, linewidth=1.5, zorder=2)
            ax.add_patch(poly)
            cx = sum(c[0] for c in coords[:-1]) / max(len(coords) - 1, 1)
            cy = sum(c[1] for c in coords[:-1]) / max(len(coords) - 1, 1)
            ax.text(cx, cy, label, ha="center", va="center",
                    fontsize=8, fontweight="bold", color=color, zorder=3)

        for i, (did, d) in enumerate(self.distributions.items()):
            n = _distribution_agent_budget(d)
            _plot_element(d["coordinates"], palette["distribution"],
                          f"D{i}\n({n} ag)")

        for i, (eid, e) in enumerate(self.exits.items()):
            _plot_element(e["coordinates"], palette["exit"], f"E{i}", alpha=0.5)

        for i, (zid, z) in enumerate(self.zones.items()):
            sf = z.get("speed_factor", 1.0)
            _plot_element(z["coordinates"], palette["zone"],
                          f"Z{i}\n(sf={sf})", alpha=0.25)

        for i, (sid, s) in enumerate(self.stages.items()):
            wt = s.get("waiting_time", 0.0)
            _plot_element(s["coordinates"], palette["checkpoint"],
                          f"C{i}\n(w={wt}s)", alpha=0.3)

        # Legend
        from matplotlib.patches import Patch
        handles = []
        if self.distributions:
            handles.append(Patch(facecolor=palette["distribution"], alpha=0.35, label="Distribution"))
        if self.exits:
            handles.append(Patch(facecolor=palette["exit"], alpha=0.5, label="Exit"))
        if self.zones:
            handles.append(Patch(facecolor=palette["zone"], alpha=0.25, label="Zone"))
        if self.stages:
            handles.append(Patch(facecolor=palette["checkpoint"], alpha=0.3, label="Checkpoint"))
        if handles:
            ax.legend(handles=handles, loc="best", frameon=False, fontsize=9)

        ax.set_aspect("equal")
        ax.set_xlabel("x [m]")
        ax.set_ylabel("y [m]")
        ax.set_title(f"Scenario: {self.source_path or '(in-memory)'}", pad=10)
        ax.grid(True, linestyle="--", linewidth=0.5, alpha=0.3)
        ax.spines["top"].set_visible(False)
        ax.spines["right"].set_visible(False)
        return ax

    # -- resolver helpers (private) -----------------------------------------

    def _resolve_distribution_id(self, id: int | str) -> str:
        """Accept an int index or string key for a distribution."""
        if isinstance(id, int):
            keys = list(self.distributions.keys())
            if id < 0 or id >= len(keys):
                raise IndexError(
                    f"Distribution index {id} out of range. "
                    f"Available indices: 0..{len(keys) - 1}"
                )
            return keys[id]
        if id not in self.distributions:
            raise KeyError(
                f"Distribution '{id}' not found. "
                f"Available: {list(self.distributions.keys())}"
            )
        return id

    def _resolve_zone_id(self, id: int | str) -> str:
        """Accept an int index or string key for a zone."""
        if isinstance(id, int):
            keys = list(self.zones.keys())
            if id < 0 or id >= len(keys):
                raise IndexError(
                    f"Zone index {id} out of range. "
                    f"Available indices: 0..{len(keys) - 1}"
                )
            return keys[id]
        if id not in self.zones:
            raise KeyError(
                f"Zone '{id}' not found. "
                f"Available: {list(self.zones.keys())}"
            )
        return id

    def _resolve_stage_id(self, id: int | str) -> str:
        """Accept an int index or string key for a stage/checkpoint."""
        if isinstance(id, int):
            keys = list(self.stages.keys())
            if id < 0 or id >= len(keys):
                raise IndexError(
                    f"Stage index {id} out of range. "
                    f"Available indices: 0..{len(keys) - 1}"
                )
            return keys[id]
        if id not in self.stages:
            raise KeyError(
                f"Stage '{id}' not found. "
                f"Available: {list(self.stages.keys())}"
            )
        return id

    # -- discovery methods ---------------------------------------------------

    def list_distributions(self) -> list[dict]:
        """Return a list of ``{"index", "id", "agents", "flow"}`` dicts."""
        result = []
        for i, (did, d) in enumerate(self.distributions.items()):
            params = d.get("parameters", {})
            result.append({
                "index": i,
                "id": did,
                "agents": _distribution_agent_budget(d),
                "flow": params.get("use_flow_spawning", False) or bool(params.get("flow_schedule")),
            })
        return result

    def list_zones(self) -> list[dict]:
        """Return a list of ``{"index", "id", "speed_factor"}`` dicts."""
        result = []
        for i, (zid, z) in enumerate(self.zones.items()):
            result.append({
                "index": i,
                "id": zid,
                "speed_factor": z.get("speed_factor", 1.0),
            })
        return result

    def list_stages(self) -> list[dict]:
        """Return a list of ``{"index", "id", "waiting_time"}`` dicts."""
        result = []
        for i, (sid, s) in enumerate(self.stages.items()):
            result.append({
                "index": i,
                "id": sid,
                "waiting_time": s.get("waiting_time", 0.0),
            })
        return result

    # -- copy ----------------------------------------------------------------

    def copy(self, **overrides) -> "Scenario":
        """Return an independent deep copy of this scenario, with optional field overrides."""
        import copy

        clone = copy.deepcopy(self)
        for key, value in overrides.items():
            if not hasattr(clone, key):
                raise AttributeError(f"Scenario has no attribute '{key}'")
            setattr(clone, key, value)
        if "walkable_area_wkt" in overrides:
            clone._walkable_polygon = wkt.loads(clone.walkable_area_wkt)
        clone._sync_runtime_to_raw()
        return clone

    # -- setters -------------------------------------------------------------

    def set_agent_count(self, distribution_id: int | str, count: int):
        distribution_id = self._resolve_distribution_id(distribution_id)
        if not isinstance(count, int) or count <= 0:
            raise ValueError(f"count must be a positive integer, got {count!r}")
        dist = self.distributions[distribution_id]
        dist.setdefault("parameters", {})["number"] = count
        dist["parameters"]["distribution_mode"] = "by_number"

    def set_seed(self, seed: int):
        if not isinstance(seed, int) or seed < 0:
            raise ValueError(f"seed must be a non-negative integer, got {seed!r}")
        self.seed = seed
        self._simulation_settings()["baseSeed"] = seed

    def set_max_time(self, seconds: float):
        if not isinstance(seconds, (int, float)) or seconds <= 0:
            raise ValueError(f"seconds must be a positive number, got {seconds!r}")
        self.sim_params["max_simulation_time"] = seconds
        self._simulation_params()["max_simulation_time"] = seconds

    def set_model_type(self, model_type: str):
        if model_type not in _MODEL_BUILDERS:
            raise ValueError(f"Unknown model: {model_type}. Available: {list(_MODEL_BUILDERS)}")
        self.model_type = model_type
        self.sim_params["model_type"] = model_type
        self._simulation_params()["model_type"] = model_type

    def set_model_params(self, **kwargs):
        """Set model-specific parameters (e.g. strength_neighbor_repulsion, range_neighbor_repulsion)."""
        for key, value in kwargs.items():
            if isinstance(value, (int, float)) and value < 0:
                raise ValueError(f"Numeric parameter '{key}' must be non-negative, got {value}")
        self.sim_params.update(kwargs)
        self._simulation_params().update(kwargs)

    def set_agent_params(self, distribution_id: int | str, **kwargs):
        """Set agent parameters for a distribution.

        Supported keys: radius, desired_speed (or v0), radius_distribution,
        radius_std, desired_speed_distribution (or v0_distribution),
        desired_speed_std (or v0_std), use_flow_spawning, flow_start_time,
        flow_end_time, distribution_mode, number.
        """
        distribution_id = self._resolve_distribution_id(distribution_id)
        speed_value = kwargs.get("desired_speed", kwargs.get("v0"))
        speed_std_value = kwargs.get("desired_speed_std", kwargs.get("v0_std"))
        speed_dist_value = kwargs.get(
            "desired_speed_distribution",
            kwargs.get("v0_distribution"),
        )
        if "radius" in kwargs:
            r = kwargs["radius"]
            if not isinstance(r, (int, float)) or r <= 0 or r > 1.0:
                raise ValueError(f"radius must be in (0, 1.0], got {r!r}")
        if speed_value is not None:
            if not isinstance(speed_value, (int, float)) or speed_value <= 0 or speed_value > 5.0:
                raise ValueError(f"desired_speed/v0 must be in (0, 5.0], got {speed_value!r}")
        if speed_std_value is not None:
            if not isinstance(speed_std_value, (int, float)) or speed_std_value < 0:
                raise ValueError(f"desired_speed_std/v0_std must be non-negative, got {speed_std_value!r}")
        if speed_dist_value is not None:
            if speed_dist_value not in {"constant", "gaussian"}:
                raise ValueError(
                    f"desired_speed_distribution/v0_distribution must be 'constant' or 'gaussian', got {speed_dist_value!r}"
                )
        if "number" in kwargs:
            n = kwargs["number"]
            if not isinstance(n, int) or n <= 0:
                raise ValueError(f"number must be a positive integer, got {n!r}")
        dist = self.distributions[distribution_id]
        params = dist.setdefault("parameters", {})
        params.update(kwargs)
        if speed_value is not None:
            params["desired_speed"] = speed_value
            params["v0"] = speed_value
        if speed_std_value is not None:
            params["desired_speed_std"] = speed_std_value
            params["v0_std"] = speed_std_value
        if speed_dist_value is not None:
            params["desired_speed_distribution"] = speed_dist_value
            params["v0_distribution"] = speed_dist_value

    def set_flow_schedule(
        self,
        distribution_id: int | str,
        schedule: list[dict],
        *,
        keep_initial_agents: bool = False,
    ):
        """Attach a time-windowed inflow schedule to one source distribution."""
        distribution_id = self._resolve_distribution_id(distribution_id)
        if not isinstance(schedule, list) or not schedule:
            raise ValueError("schedule must be a non-empty list of flow schedule entries")

        normalized_schedule = [_normalize_flow_schedule_entry(entry) for entry in schedule]
        normalized_schedule.sort(key=lambda entry: (entry["flow_start_time"], entry["flow_end_time"]))

        dist = self.distributions[distribution_id]
        params = dist.setdefault("parameters", {})

        if keep_initial_agents:
            params["initial_number"] = int(params.get("number", 0) or 0)
        else:
            params.pop("initial_number", None)

        params["flow_schedule"] = normalized_schedule
        params["use_flow_spawning"] = True
        params["distribution_mode"] = "by_number"
        params["number"] = sum(entry["number"] for entry in normalized_schedule)
        params["flow_start_time"] = normalized_schedule[0]["flow_start_time"]
        params["flow_end_time"] = normalized_schedule[-1]["flow_end_time"]

    def set_zone_speed_factor(self, zone_id: int | str, factor: float):
        """Set the speed factor for a zone."""
        zone_id = self._resolve_zone_id(zone_id)
        if not isinstance(factor, (int, float)) or factor < 0:
            raise ValueError(f"factor must be non-negative, got {factor!r}")
        self.zones[zone_id]["speed_factor"] = factor

    def set_checkpoint_waiting_time(self, checkpoint_id: int | str, waiting_time: float):
        """Set the waiting time for a checkpoint/stage."""
        checkpoint_id = self._resolve_stage_id(checkpoint_id)
        if not isinstance(waiting_time, (int, float)) or waiting_time < 0:
            raise ValueError(f"waiting_time must be non-negative, got {waiting_time!r}")
        self.stages[checkpoint_id]["waiting_time"] = waiting_time


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
                        reached_target = False
                        reach_dist = float(
                            wait_info.get("agent_radius", 0.2)
                        ) + 0.5
                        if target is not None:
                            reached_target = (
                                math.hypot(x - float(target[0]), y - float(target[1]))
                                <= reach_dist
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
