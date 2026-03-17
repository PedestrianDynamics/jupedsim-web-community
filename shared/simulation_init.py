import json
import math
import random
import zlib
import pedpy
from shapely.geometry import Point, Polygon
from typing import Any, Dict, List, Tuple
import jupedsim as jps
import shapely
from collections import defaultdict
import numpy as np

import importlib.util
import subprocess
import sys

required_packages = [
    ("jupedsim", "jupedsim"),
    ("shapely", "shapely"),
    ("numpy", "numpy"),
    ("matplotlib", "matplotlib"),
    ("pedpy", "pedpy"),
    ("ezdxf", "ezdxf"),
    ("plotly", "plotly"),
    ("geopandas", "geopandas"),
    ("typer", "typer"),
    ("nbformat", "nbformat"),
]


def is_package_installed(import_name: str) -> bool:
    """Check if packages is installed."""
    return importlib.util.find_spec(import_name) is not None


def install_if_missing(pip_name: str, import_name: str = None):
    """Pip install missing packages."""
    import_name = import_name or pip_name
    if not is_package_installed(import_name):
        print(f"Installing {pip_name}...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", pip_name])
    else:
        print(f"{pip_name} already installed.")


def create_agent_parameters(
    model_type: str,
    position: tuple,
    params: dict,
    global_params=None,
    journey_id=None,
    stage_id=None,
):
    """Create appropriate agent parameters based on the model type"""

    base_params = {
        "position": position,
        "radius": params.get("radius", 0.2),
    }

    # Add journey and stage if provided
    if journey_id is not None:
        base_params["journey_id"] = journey_id
    if stage_id is not None:
        base_params["stage_id"] = stage_id

    if model_type == "CollisionFreeSpeedModel":
        base_params["desired_speed"] = params.get("v0", 1.2)
        return jps.CollisionFreeSpeedModelAgentParameters(**base_params)

    elif model_type == "CollisionFreeSpeedModelV2":
        v2_params = base_params.copy()
        v2_params["desired_speed"] = params.get("v0", 1.2)
        v2_params["time_gap"] = 1.0
        if global_params:
            v2_params["strength_neighbor_repulsion"] = (
                global_params.strength_neighbor_repulsion
            )
            v2_params["range_neighbor_repulsion"] = (
                global_params.range_neighbor_repulsion
            )
        return jps.CollisionFreeSpeedModelV2AgentParameters(**v2_params)

    elif model_type == "GeneralizedCentrifugalForceModel":
        gcfm_params = {
            "position": position,
            "desired_speed": params.get("v0", 1.2),
            "mass": getattr(global_params, "mass", 80.0) if global_params else 80.0,
            "tau": getattr(global_params, "tau", 0.5) if global_params else 0.5,
            "a_v": getattr(global_params, "a_v", 1.0) if global_params else 1.0,
            "a_min": getattr(global_params, "a_min", 0.2) if global_params else 0.2,
            "b_min": getattr(global_params, "b_min", 0.2) if global_params else 0.2,
            "b_max": getattr(global_params, "b_max", 0.4) if global_params else 0.4,
        }
        if journey_id is not None:
            gcfm_params["journey_id"] = journey_id
        if stage_id is not None:
            gcfm_params["stage_id"] = stage_id
        try:
            return jps.GeneralizedCentrifugalForceModelAgentParameters(**gcfm_params)
        except TypeError as error:
            if "unexpected keyword argument" not in str(error):
                raise
            for param_name in ("a_v", "a_min", "b_min", "b_max"):
                gcfm_params.pop(param_name, None)
            return jps.GeneralizedCentrifugalForceModelAgentParameters(**gcfm_params)

    elif model_type == "SocialForceModel":
        sfm_params = base_params.copy()
        sfm_params["desired_speed"] = params.get("v0", 0.8)
        sfm_params["reaction_time"] = (
            global_params.relaxation_time if global_params else 0.5
        )
        sfm_params["agent_scale"] = (
            global_params.agent_strength if global_params else 2000
        )
        sfm_params["force_distance"] = (
            global_params.agent_range if global_params else 0.08
        )
        return jps.SocialForceModelAgentParameters(**sfm_params)

    elif model_type == "AnticipationVelocityModel":
        avm_params = base_params.copy()
        avm_params["desired_speed"] = params.get("v0", 1.2)
        avm_params["time_gap"] = 1.06  # Default value
        if global_params:
            avm_params["anticipation_time"] = (
                global_params.T if hasattr(global_params, "T") else 1.0
            )
            avm_params["reaction_time"] = (
                global_params.s0 if hasattr(global_params, "s0") else 0.3
            )
        else:
            avm_params["anticipation_time"] = 1.0
            avm_params["reaction_time"] = 0.3
        return jps.AnticipationVelocityModelAgentParameters(**avm_params)

    else:
        # Fallback to CollisionFreeSpeedModel
        base_params["v0"] = params.get("v0", 1.2)
        return jps.CollisionFreeSpeedModelAgentParameters(**base_params)


def _estimate_max_capacity(polygon, max_radius):
    """Estimate how many agents fit in a polygon using packing approximation."""
    effective_radius = max(max_radius, 0.1)
    theoretical = polygon.area / (math.pi * effective_radius * effective_radius)
    return max(1, math.floor(theoretical * 0.5))


def _get_max_agent_radius(params):
    """Get max effective radius for spacing calculations.

    For Gaussian distribution, use mean + 3*std (99.7% coverage) clipped to max 1.0.
    For constant distribution, use mean radius.
    """
    mean_radius = params.get("radius", 0.2)
    if params.get("radius_distribution") == "gaussian" and params.get("radius_std"):
        return min(mean_radius + 3 * params["radius_std"], 1.0)
    return mean_radius


def _get_distribution_mode_and_count(params):
    """Get distribution mode and agent count based on distribution_mode parameter.

    Returns:
        tuple: (distribution_mode, number_of_agents)
            - distribution_mode: 'by_number' or 'by_percentage'
            - number_of_agents: 0 for percentage mode, actual count for by_number
    """
    mode = params.get("distribution_mode", "by_number")
    if mode == "by_number":
        number = int(params.get("number", 0))
        return mode, max(0, number)
    elif mode in {"by_percentage", "fill_area", "until_full"}:
        return "by_percentage", 0
    else:
        number = int(params.get("number", 0))
        return "by_number", max(0, number)


def _get_distribution_percentage(params):
    """Return clamped distribution density percentage for by_percentage mode."""
    mode = params.get("distribution_mode", "by_number")
    default_percentage = 100 if mode in {"fill_area", "until_full"} else 50
    raw_percentage = params.get("percentage", default_percentage)
    try:
        percentage = int(float(raw_percentage))
    except (TypeError, ValueError):
        percentage = default_percentage
    return max(1, min(100, percentage))


def _normalize_flow_schedule_entries(params):
    """Return validated scheduled flow windows for a distribution."""
    raw_schedule = params.get("flow_schedule", [])
    if not raw_schedule:
        return []

    normalized = []
    for entry in raw_schedule:
        start_time = entry.get("flow_start_time", entry.get("start_time_s"))
        end_time = entry.get("flow_end_time", entry.get("end_time_s"))
        number = entry.get("number", entry.get("sim_count"))
        if start_time is None or end_time is None or number is None:
            raise ValueError(
                "flow_schedule entries must define start/end time and number"
            )

        start_time = max(0.0, float(start_time))
        end_time = float(end_time)
        number = int(number)
        if end_time <= start_time:
            raise ValueError(
                f"Invalid flow_schedule window [{start_time}, {end_time}]"
            )
        if number <= 0:
            continue
        normalized.append(
            {
                "flow_start_time": start_time,
                "flow_end_time": end_time,
                "number": number,
            }
        )

    normalized.sort(key=lambda entry: (entry["flow_start_time"], entry["flow_end_time"]))
    return normalized


def _sample_agent_values(params, n_agents, rng):
    """Sample per-agent radius and v0 values based on distribution settings."""
    mean_radius = params.get("radius", 0.2)
    mean_v0 = params.get("v0", 1.2)

    if params.get("radius_distribution") == "gaussian" and params.get("radius_std"):
        radii = rng.normal(mean_radius, params["radius_std"], n_agents).clip(0.1, 1.0)
    else:
        radii = np.full(n_agents, mean_radius)

    if params.get("v0_distribution") == "gaussian" and params.get("v0_std"):
        v0s = rng.normal(mean_v0, params["v0_std"], n_agents).clip(0.1, 5.0)
    else:
        v0s = np.full(n_agents, mean_v0)

    return radii, v0s


def _normalize_speed_factor(value: Any) -> float:
    """Normalize checkpoint speed factor to the supported interval [0, 3]."""
    try:
        speed_factor = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not np.isfinite(speed_factor) or speed_factor < 0.0:
        return 1.0
    return min(speed_factor, 3.0)


def _normalize_bool(value: Any) -> bool:
    """Normalize booleans from JSON-like payloads."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "on"}:
            return True
        if normalized in {"false", "0", "no", "off", ""}:
            return False
    return bool(value)


def _normalize_checkpoint_mode(
    waiting_time: Any,
    enable_throughput_throttling: Any,
    speed_factor: Any,
) -> Tuple[float, bool, float]:
    """Enforce mutually exclusive checkpoint behavior modes."""
    try:
        normalized_waiting_time = float(waiting_time)
    except (TypeError, ValueError):
        normalized_waiting_time = 0.0
    if not np.isfinite(normalized_waiting_time) or normalized_waiting_time < 0.0:
        normalized_waiting_time = 0.0

    normalized_throughput = _normalize_bool(enable_throughput_throttling)
    normalized_speed_factor = _normalize_speed_factor(speed_factor)

    if normalized_waiting_time > 0.0:
        normalized_throughput = False
        normalized_speed_factor = 1.0
    elif normalized_throughput:
        normalized_waiting_time = 0.0
        normalized_speed_factor = 1.0
    elif abs(normalized_speed_factor - 1.0) > 1e-9:
        normalized_waiting_time = 0.0
        normalized_throughput = False

    return normalized_waiting_time, normalized_throughput, normalized_speed_factor


def _normalize_variant_weights(
    distribution_journeys: List[Dict[str, Any]],
) -> Tuple[List[float], float]:
    """Return non-negative variant weights and a strictly positive total."""
    weights: List[float] = []
    for variant_info in distribution_journeys:
        raw_percentage = variant_info.get("variant_data", {}).get("percentage", 0.0)
        try:
            weight = float(raw_percentage)
        except (TypeError, ValueError):
            weight = 0.0
        if not np.isfinite(weight) or weight < 0.0:
            weight = 0.0
        weights.append(weight)

    total_weight = float(sum(weights))
    if total_weight <= 0.0 and weights:
        # If all configured weights are zero/invalid, spread agents uniformly.
        weights = [1.0] * len(weights)
        total_weight = float(len(weights))
    return weights, total_weight


def _largest_polygon(geometry):
    """Return the largest polygon from a geometry container."""
    if geometry is None or geometry.is_empty:
        return None
    if geometry.geom_type == "Polygon":
        return geometry
    if geometry.geom_type == "MultiPolygon":
        geoms = list(getattr(geometry, "geoms", []))
        return max(geoms, key=lambda g: g.area) if geoms else None
    if geometry.geom_type == "GeometryCollection":
        polygons = [
            g
            for g in getattr(geometry, "geoms", [])
            if getattr(g, "geom_type", "") in {"Polygon", "MultiPolygon"}
        ]
        if not polygons:
            return None
        flattened = []
        for poly in polygons:
            if poly.geom_type == "Polygon":
                flattened.append(poly)
            else:
                flattened.extend(list(getattr(poly, "geoms", [])))
        return max(flattened, key=lambda g: g.area) if flattened else None
    return None


def _pick_initial_stage_target(
    stage_cfg: Dict[str, Any],
    current_position: Tuple[float, float] | None,
    rng,
    agent_radius: float,
    reach_penetration: float = 0.25,
):
    """Pick a random point inside the stage polygon with interior clearance."""
    polygon = (stage_cfg or {}).get("polygon")
    if polygon is None:
        return None

    target_clearance = max(0.05, float(agent_radius) * 0.8, float(reach_penetration))
    return _random_point_in_polygon(polygon, rng, min_clearance=target_clearance)


def build_agent_path_state(
    variant_data: Dict[str, Any],
    journey_key: str | None,
    transitions: List[Dict[str, Any]],
    direct_steering_info: Dict[str, Dict[str, Any]],
    waypoint_routing: Dict[str, Any] | None,
    seed: int,
    agent_id: int,
    initial_position: Tuple[float, float] | None = None,
    agent_radius: float = 0.2,
) -> Dict[str, Any] | None:
    """Build DS routing state as origin->weighted-next mapping."""
    if not direct_steering_info:
        return None

    outgoing: Dict[str, List[str]] = {}

    def _append_edge(origin: str, target: str) -> None:
        targets = outgoing.setdefault(origin, [])
        if target not in targets:
            targets.append(target)

    # Build outgoing edges from the variant's resolved stages first.
    full_stages = variant_data.get("stages", []) or variant_data.get(
        "actual_stages", []
    )
    variant_edges: set = set()
    for idx in range(len(full_stages) - 1):
        from_stage = full_stages[idx]
        to_stage = full_stages[idx + 1]
        if isinstance(from_stage, str) and isinstance(to_stage, str):
            _append_edge(from_stage, to_stage)
            variant_edges.add(from_stage)

    # Add journey transitions only for stages NOT already covered by the variant.
    # This preserves cyclic edges and continuity while preventing re-randomization
    # at routing split points where the variant has already resolved the choice.
    if journey_key:
        for transition in transitions:
            if transition.get("journey_id") != journey_key:
                continue
            from_stage = transition.get("from")
            to_stage = transition.get("to")
            if isinstance(from_stage, str) and isinstance(to_stage, str):
                if from_stage not in variant_edges:
                    _append_edge(from_stage, to_stage)

    if not outgoing:
        return None

    path_choices: Dict[str, List[Tuple[str, float]]] = {}
    routing_for_journey = waypoint_routing if isinstance(waypoint_routing, dict) else {}
    for origin, targets in outgoing.items():
        configured = None
        if journey_key:
            configured = (
                routing_for_journey.get(origin, {})
                .get(journey_key, {})
                .get("destinations", [])
            )

        choices: List[Tuple[str, float]] = []
        if configured:
            for dest in configured:
                target = dest.get("target")
                pct = float(dest.get("percentage", 0.0))
                if (
                    isinstance(target, str)
                    and target in targets
                    and target in direct_steering_info
                    and pct > 0
                ):
                    choices.append((target, pct))
        if not choices:
            ds_targets = [
                target for target in targets if target in direct_steering_info
            ]
            if len(ds_targets) == 1:
                choices = [(ds_targets[0], 100.0)]
            elif len(ds_targets) > 1:
                uniform_pct = 100.0 / len(ds_targets)
                choices = [(target, uniform_pct) for target in ds_targets]

        if choices:
            path_choices[origin] = choices

    if not path_choices:
        return None

    distribution_stages = [
        stage
        for stage in full_stages
        if isinstance(stage, str) and stage.startswith("jps-distributions_")
    ]
    start_origin = next(
        (stage for stage in distribution_stages if stage in path_choices), None
    )
    if start_origin is None:
        start_origin = next(
            (
                stage
                for stage in variant_data.get("actual_stages", [])
                if isinstance(stage, str) and stage in path_choices
            ),
            None,
        )
    if start_origin is None:
        return None

    start_choices = path_choices.get(start_origin, [])
    if not start_choices:
        return None
    chooser_rng = random.Random(int(seed) + int(agent_id) * 9973)
    total = sum(max(0.0, float(weight)) for _, weight in start_choices)
    if total <= 0:
        current_target_stage = start_choices[0][0]
    else:
        pick = chooser_rng.random() * total
        running = 0.0
        current_target_stage = start_choices[-1][0]
        for stage_key, weight in start_choices:
            running += max(0.0, float(weight))
            if pick <= running:
                current_target_stage = stage_key
                break

    stage_configs: Dict[str, Dict[str, Any]] = {}
    for stage_key, info in direct_steering_info.items():
        stage_configs[stage_key] = {
            "polygon": info.get("polygon"),
            "stage_type": info.get("stage_type", "checkpoint"),
            "waiting_time": float(info.get("waiting_time", 0.0)),
            "waiting_time_distribution": info.get(
                "waiting_time_distribution", "constant"
            ),
            "waiting_time_std": float(info.get("waiting_time_std", 1.0)),
            "enable_throughput_throttling": bool(
                info.get("enable_throughput_throttling", False)
            ),
            "max_throughput": float(info.get("max_throughput", 1.0)),
            "speed_factor": _normalize_speed_factor(info.get("speed_factor", 1.0)),
        }

    base_seed = int(seed) + int(agent_id) * 9973
    target_rng = np.random.RandomState(base_seed)
    target = _pick_initial_stage_target(
        stage_configs.get(current_target_stage, {}),
        initial_position,
        target_rng,
        float(agent_radius),
        0.25,
    )

    return {
        "mode": "path",
        "path_choices": path_choices,
        "stage_configs": stage_configs,
        "current_origin": start_origin,
        "current_target_stage": current_target_stage,
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


def initialize_simulation_from_json(
    json_path: str,
    simulation: jps.Simulation,
    walkable_area: pedpy.WalkableArea,
    seed: int = 42,
    model_type: str = "CollisionFreeSpeedModel",
    global_parameters=None,
) -> Tuple[Dict[str, Any], List[Tuple[float, float]], Dict[int, float]]:
    """
    Initialize a JuPedSim simulation from a JSON configuration with fallback logic.
    """
    try:
        with open(json_path, "r") as f:
            data = json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        raise ValueError(f"Error loading JSON configuration: {e}")

    # Only require exits - everything else can be fallback
    if "exits" not in data or not data["exits"]:
        raise ValueError("At least one exit is required in JSON configuration")

    # Check what's missing and use fallback logic
    needs_fallback = False
    fallback_reasons = []

    if "distributions" not in data or not data["distributions"]:
        needs_fallback = True
        fallback_reasons.append("No distributions defined")

    if ("journeys" not in data or not data["journeys"]) and (
        "transitions" not in data or not data["transitions"]
    ):
        needs_fallback = True
        fallback_reasons.append("No journeys or transitions defined")

    if "checkpoints" not in data and "waiting_polygons" not in data:
        data["checkpoints"] = {}

    if "transitions" not in data:
        data["transitions"] = []

    if needs_fallback:
        print(f"Using fallback logic: {', '.join(fallback_reasons)}")

        result_data, positions, agent_radii, spawning_info = _initialize_with_fallback(
            simulation, data, walkable_area, seed, model_type, global_parameters
        )
        # Return empty spawning_info for fallback
        return result_data, positions, agent_radii, spawning_info
    else:
        # Use original logic for complete configurations
        result_data, positions, agent_radii, spawning_info = (
            _initialize_complete_config(
                simulation, data, walkable_area, seed, model_type, global_parameters
            )
        )
        return result_data, positions, agent_radii, spawning_info


def _initialize_complete_config(
    simulation: jps.Simulation,
    data: Dict[str, Any],
    walkable_area: pedpy.WalkableArea,
    seed: int,
    model_type: str,
    global_parameters=None,
) -> Tuple[Dict[str, Any], List[Tuple[float, float]], Dict[int, float]]:
    """Original initialization logic for complete configurations"""
    stage_map, direct_steering_info = _add_stages(simulation, data)
    dist_geom, dist_params = _process_distributions(data)
    direct_steering_keys = set(direct_steering_info.keys())
    journey_data = _create_journeys(simulation, data, stage_map, direct_steering_keys)
    global_ds_stage_id = None
    global_ds_journey_id = None
    if direct_steering_info:
        global_ds_stage_id = simulation.add_direct_steering_stage()
        global_ds_journey = jps.JourneyDescription([global_ds_stage_id])
        global_ds_journey_id = simulation.add_journey(global_ds_journey)

    positions, agent_radii, spawning_info = _add_agents(
        simulation=simulation,
        data=data,
        stage_map=stage_map,
        dist_geom=dist_geom,
        dist_params=dist_params,
        journey_data=journey_data,
        walkable_area=walkable_area,
        seed=seed,
        model_type=model_type,
        global_parameters=global_parameters,
        direct_steering_info=direct_steering_info,
        global_ds_journey_id=global_ds_journey_id,
        global_ds_stage_id=global_ds_stage_id,
    )

    # Inject direct steering info into spawning_info
    spawning_info["direct_steering_info"] = direct_steering_info
    spawning_info["global_ds_journey_id"] = global_ds_journey_id
    spawning_info["global_ds_stage_id"] = global_ds_stage_id

    return (
        {
            "stage_map": stage_map,
            "journey_ids": journey_data["journey_ids"],
        },
        positions,
        agent_radii,
        spawning_info,
    )


def _initialize_with_fallback(
    simulation: jps.Simulation,
    data: Dict[str, Any],
    walkable_area: pedpy.WalkableArea,
    seed: int,
    model_type: str,
    global_parameters=None,
) -> Tuple[Dict[str, Any], List[Tuple[float, float]], Dict[int, float], Dict[str, Any]]:
    """Fallback initialization logic"""
    from shapely.geometry import Polygon
    from shapely.ops import unary_union
    import numpy as np

    # print("Data:", data)

    # Extract default parameters from distributions if available
    default_agent_radius = 0.2
    default_v0 = 1.2
    default_n_agents = 100

    # Try to get parameters from the first distribution with valid parameters
    if "distributions" in data and data["distributions"]:
        for dist_id, dist_data in data["distributions"].items():
            if "parameters" in dist_data:
                params = dist_data["parameters"]
                print(f"Processing with parameters: {params}")
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        continue

                if isinstance(params, dict):
                    default_agent_radius = params.get("radius", default_agent_radius)
                    default_v0 = params.get("v0", default_v0)
                    default_n_agents = params.get("number", default_n_agents)
                    break

    # # Default parameters
    # default_agent_radius = 0.2
    # default_v0 = 1.2
    # default_n_agents = 100

    # Override defaults with global parameters if provided
    if global_parameters:
        default_v0 = getattr(global_parameters, "v0", default_v0)
        default_agent_radius = getattr(
            global_parameters, "radius", default_agent_radius
        )
        default_n_agents = getattr(global_parameters, "number", default_n_agents)

    print(
        f"Using default parameters: v0={default_v0}, radius={default_agent_radius}, n_agents={default_n_agents}"
    )

    print()
    # Step 1: Add exits to simulation
    stage_map = {}
    exits = []
    exit_geometries = {}
    direct_steering_info = {}

    for exit_id, exit_data in data.get("exits", {}).items():
        if "coordinates" in exit_data:
            coords = exit_data["coordinates"]
            if isinstance(coords, list) and len(coords) >= 3:
                exit_polygon = Polygon(coords)
                exits.append(exit_polygon)

                enable_throttling = _normalize_bool(
                    exit_data.get("enable_throughput_throttling", False)
                )
                ds_stage = simulation.add_direct_steering_stage()
                stage_map[exit_id] = ds_stage
                exit_geometries[exit_id] = exit_polygon

                direct_steering_info[exit_id] = {
                    "polygon": exit_polygon,
                    "waiting_time": 0.0,
                    "waiting_time_distribution": "constant",
                    "waiting_time_std": 0.0,
                    "speed_factor": 1.0,
                    "ds_stage_id": ds_stage,
                    "enable_throughput_throttling": enable_throttling,
                    "max_throughput": float(exit_data.get("max_throughput", 0.0)),
                    "stage_type": "exit",
                }

    if not exits:
        raise ValueError("No valid exits found in configuration")

    # Preserve checkpoint direct-steering metadata even in fallback mode so
    # runtime zone speed factors can still be applied without explicit journeys.
    checkpoint_data = data.get("checkpoints", {}) or data.get("waiting_polygons", {})
    for cp_id, cp_data in checkpoint_data.items():
        coordinates = cp_data.get("coordinates", [])
        if not coordinates:
            continue
        try:
            checkpoint_polygon = Polygon(coordinates)
        except Exception:
            continue
        waiting_time, enable_throttling, speed_factor = _normalize_checkpoint_mode(
            cp_data.get("waiting_time", 0),
            cp_data.get("enable_throughput_throttling", False),
            cp_data.get("speed_factor", 1.0),
        )
        direct_steering_info[cp_id] = {
            "polygon": checkpoint_polygon,
            "waiting_time": waiting_time,
            "waiting_time_distribution": cp_data.get(
                "waiting_time_distribution", "constant"
            ),
            "waiting_time_std": cp_data.get("waiting_time_std", 1.0),
            "speed_factor": speed_factor,
            "enable_throughput_throttling": enable_throttling,
            "max_throughput": cp_data.get("max_throughput", 1.0),
            "stage_type": "checkpoint",
        }

    # Geometry-only speed zones are applied at runtime and are not journey stages.
    for zone_id, zone_data in data.get("zones", {}).items():
        coordinates = zone_data.get("coordinates", [])
        if not coordinates:
            continue
        try:
            zone_polygon = Polygon(coordinates)
        except Exception:
            continue
        direct_steering_info[zone_id] = {
            "polygon": zone_polygon,
            "waiting_time": 0.0,
            "waiting_time_distribution": "constant",
            "waiting_time_std": 0.0,
            "speed_factor": _normalize_speed_factor(zone_data.get("speed_factor", 1.0)),
            "enable_throughput_throttling": False,
            "max_throughput": 0.0,
            "stage_type": "zone",
        }

    # Step 2: Handle distributions (use walkable area if none provided)
    distributions = []
    distribution_params = []  # Store parameters for each distribution
    total_agents = 0

    if "distributions" in data and data["distributions"]:
        # Use provided distributions
        for dist_id, dist_data in data["distributions"].items():
            if "coordinates" in dist_data:
                coords = dist_data["coordinates"]
                if isinstance(coords, list) and len(coords) >= 3:
                    dist_polygon = Polygon(coords)
                    distributions.append(dist_polygon)

                    # Get parameters for this specific distribution
                    params = dist_data.get("parameters", {})
                    if isinstance(params, str):
                        try:
                            params = json.loads(params)
                        except Exception:
                            params = {}

                    # Use distribution-specific parameters or fall back to defaults
                    dist_params = {
                        "number": params.get("number", default_n_agents),
                        "radius": params.get("radius", default_agent_radius),
                        "v0": params.get("v0", default_v0),
                        "distribution_mode": params.get(
                            "distribution_mode", "by_number"
                        ),
                        "percentage": params.get("percentage", None),
                        "use_flow_spawning": params.get("use_flow_spawning", False),
                        "flow_start_time": params.get("flow_start_time", 0),
                        "flow_end_time": params.get("flow_end_time", 10),
                        "use_premovement": params.get("use_premovement", False),
                        "premovement_distribution": params.get(
                            "premovement_distribution", "gamma"
                        ),
                        "premovement_param_a": params.get("premovement_param_a", None),
                        "premovement_param_b": params.get("premovement_param_b", None),
                        "premovement_seed": params.get("premovement_seed", None),
                        "radius_distribution": params.get(
                            "radius_distribution", "constant"
                        ),
                        "radius_std": params.get("radius_std", None),
                        "v0_distribution": params.get("v0_distribution", "constant"),
                        "v0_std": params.get("v0_std", None),
                    }

                    distribution_params.append(dist_params)
                    total_agents += int(dist_params["number"])

                    print(f"Distribution {dist_id}: {dist_params}")

    # Fallback: use walkable area if no valid distributions
    if not distributions:
        print("No valid distributions found; using walkable area as fallback")
        distributions = [walkable_area.polygon]
        distribution_params = [
            {
                "number": default_n_agents,
                "radius": default_agent_radius,
                "v0": default_v0,
                "distribution_mode": "by_number",
                "percentage": None,
                "use_flow_spawning": False,
                "flow_start_time": 0,
                "flow_end_time": 10,
                "use_premovement": False,
                "premovement_distribution": "gamma",
                "premovement_param_a": None,
                "premovement_param_b": None,
                "premovement_seed": None,
            }
        ]
        total_agents = default_n_agents

    # Step 3: Create a single global DS journey for all fallback agents
    global_ds_stage_id = simulation.add_direct_steering_stage()
    global_ds_journey = jps.JourneyDescription([global_ds_stage_id])
    global_ds_journey_id = simulation.add_journey(global_ds_journey)

    # Step 4: Handle obstacles (holes in walkable area)
    holes = [Polygon(interior) for interior in walkable_area.polygon.interiors]
    obstacles_union = unary_union(holes) if holes else None

    # Step 5: Handle flow spawning vs immediate spawning
    spawning_freqs_and_numbers = []
    starting_pos_per_source = []
    num_agents_per_source = []
    flow_distributions = []
    has_flow_spawning = False

    all_positions = []
    agent_radii = {}
    agent_counter = 0
    fallback_agent_wait_info = {}

    immediate_spawn_distributions = []

    np.random.seed(seed)

    # Separate flow spawning from immediate spawning
    for i, (dist_area, dist_params) in enumerate(
        zip(distributions, distribution_params)
    ):
        use_flow_spawning = dist_params.get("use_flow_spawning", False)
        dist_mode, requested_n_agents = _get_distribution_mode_and_count(dist_params)
        flow_schedule = _normalize_flow_schedule_entries(dist_params)
        initial_n_agents = int(
            dist_params.get(
                "initial_number",
                0 if flow_schedule else requested_n_agents,
            )
            or 0
        )

        if dist_mode == "by_number" and requested_n_agents <= 0 and initial_n_agents <= 0 and not flow_schedule:
            continue

        # Remove obstacles from distribution area
        if obstacles_union and not obstacles_union.is_empty:
            clean_dist_area = dist_area.difference(obstacles_union)
        else:
            clean_dist_area = dist_area

        # Ensure distribution area is within walkable area
        clean_dist_area = shapely.intersection(clean_dist_area, walkable_area.polygon)

        if clean_dist_area.is_empty:
            print(f"Warning: Distribution area {i} is outside walkable area")
            continue

        if flow_schedule:
            has_flow_spawning = True

            for schedule_entry in flow_schedule:
                max_radius = _get_max_agent_radius(dist_params)
                max_capacity = _estimate_max_capacity(clean_dist_area, max_radius)
                n_agents = schedule_entry["number"]
                flow_start_time = schedule_entry["flow_start_time"]
                flow_end_time = max(
                    flow_start_time + 0.1, schedule_entry["flow_end_time"]
                )
                flow_duration = flow_end_time - flow_start_time
                flow_rate = n_agents / flow_duration
                if flow_rate > max_capacity:
                    raise ValueError(
                        f"Distribution {i}: flow rate of {flow_rate:.1f} agents/s "
                        f"exceeds area capacity of {max_capacity} agents. "
                        f"Reduce the number of agents ({n_agents}) or increase "
                        f"the flow duration ({flow_duration:.1f}s)."
                    )

                flow_params = dict(dist_params)
                flow_params["number"] = n_agents
                flow_params["use_flow_spawning"] = True
                flow_params["flow_start_time"] = flow_start_time
                flow_params["flow_end_time"] = flow_end_time

                frequency = flow_duration / n_agents
                agents_per_spawn = 1

                spawning_freqs_and_numbers.append([frequency, agents_per_spawn])
                num_agents_per_source.append(n_agents)

                positions = jps.distribute_until_filled(
                    polygon=clean_dist_area,
                    distance_to_agents=2 * max_radius,
                    distance_to_polygon=max_radius,
                    seed=seed + i,
                )
                shuffle_rng = random.Random(seed + i)
                shuffle_rng.shuffle(positions)
                starting_pos_per_source.append(positions)

                flow_distributions.append(
                    {
                        "dist_index": i,
                        "params": flow_params,
                        "start_time": flow_start_time,
                        "end_time": flow_end_time,
                        "area": clean_dist_area,
                    }
                )

            if initial_n_agents > 0:
                immediate_params = dict(dist_params)
                immediate_params["number"] = initial_n_agents
                immediate_params["use_flow_spawning"] = False
                immediate_spawn_distributions.append(
                    {"area": clean_dist_area, "params": immediate_params, "index": i}
                )

            print(
                f"Flow spawning: Distribution {i} - {sum(entry['number'] for entry in flow_schedule)} scheduled agents"
            )

        elif use_flow_spawning:
            has_flow_spawning = True

            max_radius = _get_max_agent_radius(dist_params)
            max_capacity = _estimate_max_capacity(clean_dist_area, max_radius)

            # Flow spawning: agents spawn over time so the full requested
            # count is valid even if it exceeds simultaneous capacity.
            if dist_mode == "by_number":
                n_agents = requested_n_agents
            else:  # by_percentage
                percentage = _get_distribution_percentage(dist_params)
                n_agents = max(1, int(max_capacity * percentage / 100))

            if n_agents <= 0:
                print(f"Warning: No agents fit in distribution {i}")
                continue

            # Get flow parameters
            flow_start_time = max(0, dist_params.get("flow_start_time", 0))
            flow_end_time = max(
                flow_start_time + 0.1, dist_params.get("flow_end_time", 10)
            )
            flow_duration = flow_end_time - flow_start_time

            # Validate flow rate does not exceed area capacity
            flow_rate = n_agents / flow_duration
            if flow_rate > max_capacity:
                raise ValueError(
                    f"Distribution {i}: flow rate of {flow_rate:.1f} agents/s "
                    f"exceeds area capacity of {max_capacity} agents. "
                    f"Reduce the number of agents ({n_agents}) or increase "
                    f"the flow duration ({flow_duration:.1f}s)."
                )

            dist_params["number"] = n_agents

            # Calculate frequency (seconds between spawns)
            frequency = flow_duration / n_agents
            agents_per_spawn = 1  # spawn 1 agent at a time for smooth flow

            spawning_freqs_and_numbers.append([frequency, agents_per_spawn])
            num_agents_per_source.append(n_agents)

            positions = jps.distribute_until_filled(
                polygon=clean_dist_area,
                distance_to_agents=2 * max_radius,
                distance_to_polygon=max_radius,
                seed=seed + i,
            )
            shuffle_rng = random.Random(seed + i)
            shuffle_rng.shuffle(positions)
            starting_pos_per_source.append(positions)

            # Store flow distribution info
            flow_distributions.append(
                {
                    "dist_index": i,
                    "params": dist_params,
                    "start_time": flow_start_time,
                    "end_time": flow_end_time,
                    "area": clean_dist_area,
                }
            )

            print(
                f"Flow spawning: Distribution {i} - {n_agents} agents over {flow_duration}s"
            )

        else:
            # Store for immediate spawning
            immediate_spawn_distributions.append(
                {"area": clean_dist_area, "params": dist_params, "index": i}
            )

    # Handle immediate spawning (with optional premovement)
    premovement_times = {}  # Dictionary mapping agent_id -> (premovement_time, position)
    has_premovement = False

    for spawn_data in immediate_spawn_distributions:
        try:
            max_radius = _get_max_agent_radius(spawn_data["params"])
            requested_count = int(spawn_data["params"]["number"])
            max_capacity = _estimate_max_capacity(spawn_data["area"], max_radius)
            if requested_count > max_capacity:
                raise ValueError(
                    f"Distribution {spawn_data['index']}: requested {requested_count} agents "
                    f"but area can hold at most ~{max_capacity}. "
                    f"Reduce the number of agents or enlarge the distribution area."
                )
            positions = jps.distribute_by_number(
                polygon=spawn_data["area"],
                number_of_agents=requested_count,
                distance_to_agents=2 * max_radius,
                distance_to_polygon=max_radius,
                seed=seed + spawn_data["index"],
            )
        except Exception as e:
            error_msg = (
                f"CRITICAL: Failed to place agents in distribution area {spawn_data['index']}. "
                f"Error: {str(e)}. This usually means the spawn area is too small or crowded. "
                f"Consider: 1) Making the distribution area larger, 2) Reducing the number of agents, "
                f"3) Increasing distance between agents, or 4) Checking for obstacles in the area."
            )
            print(f"ERROR: {error_msg}")
            raise Exception(error_msg)

        # Check if this distribution uses premovement
        use_premovement = spawn_data["params"].get("use_premovement", False)

        # Generate premovement times if enabled
        agent_premovement_times = None
        if use_premovement:
            has_premovement = True
            from utils.premovement_distributions import (
                create_premovement_distribution,
                PREMOVEMENT_PRESETS,
            )

            dist_type = spawn_data["params"].get("premovement_distribution", "gamma")
            param_a = spawn_data["params"].get("premovement_param_a")
            param_b = spawn_data["params"].get("premovement_param_b")
            premovement_seed = spawn_data["params"].get("premovement_seed")

            # Use custom parameters if provided, otherwise use presets
            if param_a is not None and param_b is not None:
                dist_params = {"a": param_a, "b": param_b}
            else:
                dist_params = PREMOVEMENT_PRESETS.get(
                    dist_type, PREMOVEMENT_PRESETS["gamma"]
                )

            # Use distribution-specific seed or global seed
            if premovement_seed is None:
                premovement_seed = seed + spawn_data["index"] + 1000

            print(
                f"Generating premovement times: {dist_type} with params {dist_params}, seed={premovement_seed}"
            )

            distribution = create_premovement_distribution(
                dist_type, dist_params, premovement_seed
            )
            agent_premovement_times = distribution.sample(len(positions))

            print(
                f"Premovement times stats - Min: {agent_premovement_times.min():.2f}s, "
                f"Max: {agent_premovement_times.max():.2f}s, "
                f"Mean: {agent_premovement_times.mean():.2f}s"
            )

        # Sample per-agent radius and v0
        rng = np.random.RandomState(seed + spawn_data["index"])
        sampled_radii, sampled_v0s = _sample_agent_values(
            spawn_data["params"], len(positions), rng
        )

        # Build stage configs for DS navigation (only needed if throttled exits exist)
        stage_configs = {}
        if direct_steering_info:
            for sk, info in direct_steering_info.items():
                stage_configs[sk] = {
                    "polygon": info.get("polygon"),
                    "stage_type": info.get("stage_type", "exit"),
                    "waiting_time": float(info.get("waiting_time", 0.0)),
                    "waiting_time_distribution": info.get(
                        "waiting_time_distribution", "constant"
                    ),
                    "waiting_time_std": float(info.get("waiting_time_std", 1.0)),
                    "enable_throughput_throttling": bool(
                        info.get("enable_throughput_throttling", False)
                    ),
                    "max_throughput": float(info.get("max_throughput", 1.0)),
                    "speed_factor": _normalize_speed_factor(
                        info.get("speed_factor", 1.0)
                    ),
                }

        # Add agents with nearest exit assignment — all on global DS journey
        for idx, pos in enumerate(positions):
            nearest_exit_id = _find_nearest_exit(pos, exit_geometries=exit_geometries)

            agent_radius = float(sampled_radii[idx])
            agent_v0 = float(sampled_v0s[idx])

            # Modify agent parameters based on premovement
            agent_params_dict = {
                "radius": agent_radius,
                "v0": 0.0 if use_premovement else agent_v0,
            }

            agent_params = create_agent_parameters(
                model_type=model_type,
                position=pos,
                params=agent_params_dict,
                global_params=global_parameters,
                journey_id=global_ds_journey_id,
                stage_id=global_ds_stage_id,
            )

            agent_id = simulation.add_agent(agent_params)
            all_positions.append(pos)
            agent_radii[agent_id] = agent_radius

            # Build DS wait info for ALL agents to navigate to nearest exit
            base_seed = seed + idx * 9973
            target_rng = np.random.RandomState(base_seed)
            exit_polygon = direct_steering_info[nearest_exit_id]["polygon"]
            target = _random_point_in_polygon(exit_polygon, target_rng)
            fallback_agent_wait_info[agent_id] = {
                "mode": "path",
                "path_choices": {},
                "stage_configs": stage_configs,
                "current_origin": nearest_exit_id,
                "current_target_stage": nearest_exit_id,
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

            # Store premovement time and desired speed for this agent
            if use_premovement and agent_premovement_times is not None:
                premovement_times[agent_id] = {
                    "premovement_time": float(agent_premovement_times[idx]),
                    "position": pos,
                    "desired_speed": agent_v0,
                    "activated": False,
                }

            agent_counter += 1

    # Prepare spawning info for flow spawning and premovement
    agent_counter_per_source = [0] * len(flow_distributions)

    spawning_info = {
        "has_flow_spawning": has_flow_spawning,
        "spawning_freqs_and_numbers": spawning_freqs_and_numbers,
        "starting_pos_per_source": starting_pos_per_source,
        "num_agents_per_source": num_agents_per_source,
        "agent_counter_per_source": agent_counter_per_source,
        "flow_distributions": flow_distributions,
        "model_type": model_type,
        "global_parameters": global_parameters,
        "stage_map": stage_map,
        "exit_geometries": exit_geometries,
        "exits": exits,
        "has_premovement": has_premovement,
        "premovement_times": premovement_times,
        "agent_wait_info": fallback_agent_wait_info,
        "direct_steering_info": direct_steering_info,
        "global_ds_journey_id": global_ds_journey_id,
        "global_ds_stage_id": global_ds_stage_id,
    }

    print(
        f"Added {len(all_positions)} agents using fallback logic (immediate), prepared {len(flow_distributions)} flow sources"
    )

    return (
        {
            "stage_map": stage_map,
            "journey_ids": {},
        },
        all_positions,
        agent_radii,
        spawning_info,
    )


def _find_nearest_exit(
    position: tuple,
    stage_map: dict | None = None,
    exits: list | None = None,
    exit_geometries: dict | None = None,
):
    """Find the key of the nearest exit to the given position."""

    point = Point(position)
    min_distance = float("inf")
    nearest_stage_id = None

    if exit_geometries:
        for stage_id, exit_geom in exit_geometries.items():
            distance = point.distance(exit_geom)
            if distance < min_distance:
                min_distance = distance
                nearest_stage_id = stage_id
        if nearest_stage_id is not None:
            return nearest_stage_id

    if stage_map and exits:
        preferred_stage_ids = [
            stage_id
            for stage_key, stage_id in stage_map.items()
            if isinstance(stage_key, str) and stage_key.startswith("jps-exits_")
        ]
        if not preferred_stage_ids:
            preferred_stage_ids = [
                stage_id
                for stage_key, stage_id in stage_map.items()
                if isinstance(stage_key, str) and "exit" in stage_key.lower()
            ]
        if not preferred_stage_ids and len(stage_map) == len(exits):
            preferred_stage_ids = list(stage_map.values())
        if not preferred_stage_ids:
            preferred_stage_ids = list(stage_map.values())[: len(exits)]

        for stage_id, exit_geom in zip(preferred_stage_ids, exits):
            if stage_id == -1:
                continue
            distance = point.distance(exit_geom)

            if distance < min_distance:
                min_distance = distance
                nearest_stage_id = stage_id
        if nearest_stage_id is not None:
            return nearest_stage_id

    raise ValueError("No exits available for nearest-exit assignment")


def _random_point_in_polygon(polygon, rng, min_clearance: float = 0.2):
    """Generate a random point inside a polygon, preferring interior clearance."""

    candidate_polygon = polygon
    if min_clearance > 0:
        try:
            inner_polygon = polygon.buffer(-float(min_clearance))
            if not inner_polygon.is_empty:
                if hasattr(inner_polygon, "geoms"):
                    candidate_polygon = max(
                        inner_polygon.geoms, key=lambda geom: geom.area
                    )
                else:
                    candidate_polygon = inner_polygon
        except Exception:
            candidate_polygon = polygon

    minx, miny, maxx, maxy = candidate_polygon.bounds
    for _ in range(1000):
        x = rng.uniform(minx, maxx)
        y = rng.uniform(miny, maxy)
        if candidate_polygon.contains(Point(x, y)):
            return (x, y)

    # Fallback to the original polygon when inner buffering was too restrictive.
    if candidate_polygon is not polygon:
        minx, miny, maxx, maxy = polygon.bounds
        for _ in range(1000):
            x = rng.uniform(minx, maxx)
            y = rng.uniform(miny, maxy)
            if polygon.contains(Point(x, y)):
                return (x, y)

    c = candidate_polygon.representative_point()
    return (c.x, c.y)


def _add_stages(
    simulation: jps.Simulation, data: Dict[str, Any]
) -> Tuple[Dict[str, int], Dict[str, Dict]]:
    """Add checkpoints and exits. Returns (stage_map, direct_steering_info)."""
    stage_map = {}
    direct_steering_info = {}

    # Parse checkpoints (with backward compat for waiting_polygons key)
    checkpoint_data = data.get("checkpoints", {}) or data.get("waiting_polygons", {})
    for cp_id, cp_data in checkpoint_data.items():
        coordinates = cp_data.get("coordinates", [])
        if not coordinates:
            continue
        waiting_time, enable_throttling, speed_factor = _normalize_checkpoint_mode(
            cp_data.get("waiting_time", 0),
            cp_data.get("enable_throughput_throttling", False),
            cp_data.get("speed_factor", 1.0),
        )
        from shapely.geometry import Polygon as ShapelyPolygon

        polygon = ShapelyPolygon(coordinates)
        ds_stage = simulation.add_direct_steering_stage()
        direct_steering_info[cp_id] = {
            "polygon": polygon,
            "waiting_time": waiting_time,
            "waiting_time_distribution": cp_data.get(
                "waiting_time_distribution", "constant"
            ),
            "waiting_time_std": cp_data.get("waiting_time_std", 1.0),
            "speed_factor": speed_factor,
            "ds_stage_id": ds_stage,
            "enable_throughput_throttling": enable_throttling,
            "max_throughput": cp_data.get("max_throughput", 1.0),
        }
        stage_map[cp_id] = ds_stage
        print(
            f"Added DirectSteeringStage for checkpoint {cp_id}: time={cp_data.get('waiting_time', 0)}s"
        )

    # Zones are geometry modifiers and intentionally omitted from stage_map.
    for zone_id, zone_data in data.get("zones", {}).items():
        coordinates = zone_data.get("coordinates", [])
        if not coordinates:
            continue
        from shapely.geometry import Polygon as ShapelyPolygon

        polygon = ShapelyPolygon(coordinates)
        direct_steering_info[zone_id] = {
            "polygon": polygon,
            "waiting_time": 0.0,
            "waiting_time_distribution": "constant",
            "waiting_time_std": 0.0,
            "speed_factor": _normalize_speed_factor(zone_data.get("speed_factor", 1.0)),
            "enable_throughput_throttling": False,
            "max_throughput": 0.0,
            "stage_type": "zone",
        }

    for exit_id, exit_data in data.get("exits", {}).items():
        coordinates = exit_data.get("coordinates", [])
        if not coordinates:
            continue
        from shapely.geometry import Polygon as ShapelyPolygon

        exit_polygon = ShapelyPolygon(coordinates)
        enable_throttling = _normalize_bool(
            exit_data.get("enable_throughput_throttling", False)
        )

        if enable_throttling:
            ds_stage = simulation.add_direct_steering_stage()
            stage_map[exit_id] = ds_stage
        else:
            ds_stage = simulation.add_direct_steering_stage()
            stage_map[exit_id] = simulation.add_exit_stage(exit_polygon)

        # Always include exits in direct_steering_info so DS-routed agents
        # (e.g. coming from a checkpoint) can navigate to and be removed at exits.
        direct_steering_info[exit_id] = {
            "polygon": exit_polygon,
            "waiting_time": 0.0,
            "waiting_time_distribution": "constant",
            "waiting_time_std": 0.0,
            "speed_factor": 1.0,
            "ds_stage_id": ds_stage,
            "enable_throughput_throttling": enable_throttling,
            "max_throughput": float(exit_data.get("max_throughput", 0.0)),
            "stage_type": "exit",
        }

    for dist_id, dist_data in data.get("distributions", {}).items():
        # Distributions don't need to be added as stages in JuPedSim,
        # but we need them in stage_map for journey creation
        stage_map[dist_id] = -1

    return stage_map, direct_steering_info


def _process_distributions(
    data: Dict[str, Any],
) -> Tuple[Dict[str, List[List[float]]], Dict[str, Dict[str, Any]]]:
    """Process distribution geometries from JSON."""
    dist_geom = {}
    dist_params = {}

    for dist_id, dist_data in data.get("distributions", {}).items():
        dist_geom[dist_id] = dist_data["coordinates"]

        params = dist_data.get("parameters", {})
        if isinstance(params, str):
            try:
                params = json.loads(params)
            except json.JSONDecodeError:
                params = {"number": 10, "radius": 0.2, "v0": 1.2}
        elif not isinstance(params, dict):
            params = {"number": 10, "radius": 0.2, "v0": 1.2}

        dist_params[dist_id] = {
            "number": params.get("number", 10),
            "radius": params.get("radius", 0.2),
            "v0": params.get("v0", 1.2),
            "use_flow_spawning": params.get("use_flow_spawning", False),
            "flow_start_time": params.get("flow_start_time", 0),
            "flow_end_time": params.get("flow_end_time", 10),
            "use_premovement": params.get("use_premovement", False),
            "premovement_distribution": params.get("premovement_distribution", "gamma"),
            "premovement_param_a": params.get("premovement_param_a", None),
            "premovement_param_b": params.get("premovement_param_b", None),
            "premovement_seed": params.get("premovement_seed", None),
            "radius_distribution": params.get("radius_distribution", "constant"),
            "radius_std": params.get("radius_std", None),
            "v0_distribution": params.get("v0_distribution", "constant"),
            "v0_std": params.get("v0_std", None),
            "distribution_mode": params.get("distribution_mode", "by_number"),
            "percentage": params.get("percentage", None),
        }

        print(f"DEBUG: Distribution {dist_id} RAW params from JSON: {params}")
        print(f"DEBUG: Distribution {dist_id} processed params: {dist_params[dist_id]}")

    return dist_geom, dist_params


def _is_routing_split_node(stage_key: Any) -> bool:
    """Routing split nodes are checkpoints (with backward compat for waypoints/waiting_polygons)."""
    return isinstance(stage_key, str) and (
        stage_key.startswith("jps-checkpoints_")
        or stage_key.startswith("jps-waypoints_")
        or stage_key.startswith("jps-waiting_polygons_")
    )


def _distribution_stage_keys(stages: List[Any]) -> List[str]:
    """Return unique distribution stage keys preserving first-seen order."""
    keys = [
        stage
        for stage in stages
        if isinstance(stage, str) and stage.startswith("jps-distributions_")
    ]
    return list(dict.fromkeys(keys))


def _create_journeys_with_percentages(
    simulation: jps.Simulation,
    data: Dict[str, Any],
    stage_map: Dict[str, int],
    direct_steering_keys: set | None = None,
) -> Dict[str, Any]:
    """Enhanced journey creation with percentage-based routing"""

    journey_ids = {}
    journey_variants = {}
    waypoint_routing = data.get("waypoint_routing", {})
    journey_endpoints = {}

    print(f"Creating journeys with routing: {waypoint_routing}")
    direct_steering_keys = direct_steering_keys or set()

    # Index transitions by journey id for robust stage ordering decisions.
    transitions_by_journey = defaultdict(list)
    for tr in data.get("transitions", []):
        jid = tr.get("journey_id")
        if jid:
            transitions_by_journey[jid].append(tr)

    # Enforce explicit branching: a routing split node with multiple outgoing edges in one
    # journey must have explicit waypoint_routing for that node/journey.
    for jid, transitions in transitions_by_journey.items():
        outgoing = defaultdict(set)
        for tr in transitions:
            from_key = tr.get("from")
            to_key = tr.get("to")
            if from_key and to_key:
                outgoing[from_key].add(to_key)

        for from_key, targets in outgoing.items():
            if not _is_routing_split_node(from_key):
                continue
            if len(targets) <= 1:
                continue

            routing_cfg = waypoint_routing.get(from_key, {}).get(jid)
            if not routing_cfg or not routing_cfg.get("destinations"):
                raise ValueError(
                    f"Explicit routing required for {from_key} in {jid}: "
                    f"{len(targets)} outgoing connections found."
                )

            destinations = routing_cfg.get("destinations", [])
            configured_targets = {
                d.get("target") for d in destinations if d.get("target")
            }
            missing_targets = [t for t in targets if t not in configured_targets]
            if missing_targets:
                raise ValueError(
                    f"Explicit routing for {from_key} in {jid} is incomplete. "
                    f"Missing targets: {missing_targets}"
                )

            total_percentage = sum(float(d.get("percentage", 0)) for d in destinations)
            if total_percentage <= 0:
                raise ValueError(
                    f"Explicit routing for {from_key} in {jid} has invalid percentages "
                    f"(sum must be > 0)."
                )

    # First, create journey variants based on percentage routing
    for journey in data.get("journeys", []):
        jid = journey["id"]
        base_stages = journey["stages"]

        print(f"Processing journey {jid} with stages: {base_stages}")

        # Generate all possible journey variants for this journey
        variants = _generate_journey_variants(
            jid, base_stages, waypoint_routing, stage_map
        )
        journey_variants[jid] = []

        print(f"Generated {len(variants)} variants for journey {jid}")

        for variant_idx, (variant_stages, percentage) in enumerate(variants):
            variant_id = f"{jid}_variant_{variant_idx}"

            print(f"Creating variant {variant_id}: {variant_stages} ({percentage}%)")

            # Filter out distributions first.
            actual_stages = [
                stage
                for stage in variant_stages
                if not stage.startswith("jps-distributions_")
            ]

            # JuPedSim DirectSteeringStage may not be mixed with other stages.
            # Keep the initial regular segment; if the journey starts with direct steering,
            # keep only that direct steering stage.
            entry_stages = None

            # Prefer explicit transition topology: if a waiting stage is directly after a
            # distribution, start in that direct-steering stage.
            dist_to_ds = None
            journey_transitions = transitions_by_journey.get(jid, [])
            dist_keys = [
                s
                for s in variant_stages
                if isinstance(s, str) and s.startswith("jps-distributions_")
            ]
            for tr in journey_transitions:
                from_key = tr.get("from")
                to_key = tr.get("to")
                if from_key in dist_keys and to_key in direct_steering_keys:
                    dist_to_ds = to_key
                    break

            if dist_to_ds:
                entry_stages = [dist_to_ds]
            else:
                first_ds_idx = next(
                    (
                        idx
                        for idx, key in enumerate(actual_stages)
                        if key in direct_steering_keys
                    ),
                    None,
                )
                if first_ds_idx is None:
                    entry_stages = actual_stages
                elif first_ds_idx == 0:
                    entry_stages = [actual_stages[0]]
                else:
                    entry_stages = [
                        k
                        for k in actual_stages[:first_ds_idx]
                        if k not in direct_steering_keys
                    ]

            stage_ids = [stage_map[k] for k in entry_stages if k in stage_map]

            if len(stage_ids) >= 1:  # Need at least one actual stage (exit)
                jd = jps.JourneyDescription(stage_ids)

                # Set linear transitions for this variant (only between actual stages)
                for i in range(len(stage_ids) - 1):
                    jd.set_transition_for_stage(
                        stage_ids[i],
                        jps.Transition.create_fixed_transition(stage_ids[i + 1]),
                    )

                variant_journey_id = simulation.add_journey(jd)
                journey_variants[jid].append(
                    {
                        "id": variant_journey_id,
                        "stages": variant_stages,  # Keep original stages for reference
                        "actual_stages": actual_stages,  # Add filtered stages
                        "entry_stages": entry_stages,  # Initial stage segment used for the spawned journey
                        "percentage": percentage,
                        "variant_name": variant_id,
                    }
                )

                # Store journey endpoints for compatibility
                dist_key = variant_stages[0] if variant_stages else None
                exit_key = variant_stages[-1] if variant_stages else None
                if dist_key and exit_key:
                    journey_endpoints[variant_id] = (dist_key, exit_key)

    # Create journeys_per_distribution for compatibility.
    journeys_per_distribution = defaultdict(list)
    for jid, variants in journey_variants.items():
        journey_def = next(
            (j for j in data.get("journeys", []) if j["id"] == jid), None
        )
        journey_distributions = (
            _distribution_stage_keys(journey_def.get("stages", []))
            if journey_def
            else []
        )

        for variant in variants:
            variant_distributions = _distribution_stage_keys(variant.get("stages", []))
            target_distributions = variant_distributions or journey_distributions

            for dist_key in target_distributions:
                journeys_per_distribution[dist_key].append(
                    {"original_journey_id": jid, "variant_data": variant}
                )
                print(
                    f"DEBUG: Added variant {variant.get('variant_name')} to distribution {dist_key}"
                )

    print(f"DEBUG: journey_variants = {journey_variants}")
    print(
        f"DEBUG: journeys_per_distribution keys = {list(journeys_per_distribution.keys())}"
    )
    print(f"DEBUG: journeys_per_distribution = {dict(journeys_per_distribution)}")
    return {
        "journey_ids": journey_ids,  # Keep for compatibility
        "journey_variants": journey_variants,
        "journey_endpoints": journey_endpoints,
        "journeys_per_distribution": journeys_per_distribution,
        "waypoint_routing": waypoint_routing,
    }


def _generate_journey_variants(
    journey_id: str,
    base_stages: List[str],
    waypoint_routing: Dict,
    stage_map: Dict[str, int],
) -> List[Tuple[List[str], float]]:
    """Generate all possible journey variants with their percentages."""
    _ = stage_map  # kept for compatibility with existing call sites

    if not waypoint_routing:
        return [(base_stages, 100.0)]

    variants = []

    # Find ALL distributions for this journey (not just the first one)
    distributions = [
        stage for stage in base_stages if stage.startswith("jps-distributions_")
    ]
    if not distributions:
        return [(base_stages, 100.0)]

    print(f"DEBUG: Found distributions for journey {journey_id}: {distributions}")

    # Routing split nodes may be waypoints or waiting polygons.
    all_routing_nodes = [
        stage for stage in base_stages if _is_routing_split_node(stage)
    ]

    # Find split nodes that are targets of routing (not initial split nodes).
    target_routing_nodes = set()
    for node, journeys in waypoint_routing.items():
        if not _is_routing_split_node(node):
            continue
        if journey_id in journeys:
            for dest in journeys[journey_id].get("destinations", []):
                target = dest.get("target")
                if _is_routing_split_node(target):
                    target_routing_nodes.add(target)

    # Initial split nodes are those with routing rules but not targets of others.
    initial_routing_nodes = []
    for node in all_routing_nodes:
        if (
            node in waypoint_routing
            and journey_id in waypoint_routing[node]
            and node not in target_routing_nodes
        ):
            initial_routing_nodes.append(node)

    print(f"DEBUG: All routing nodes: {all_routing_nodes}")
    print(f"DEBUG: Target routing nodes: {target_routing_nodes}")
    print(f"DEBUG: Initial routing nodes: {initial_routing_nodes}")

    if not initial_routing_nodes:
        # Cyclic routing graphs can make every routing node a target. In that case,
        # start from the first routing node in journey order instead of discarding routing.
        if all_routing_nodes:
            initial_routing_nodes = [all_routing_nodes[0]]
        else:
            return [(base_stages, 100.0)]

    # Generate variants per source distribution so each source can be mapped independently.
    for reference_distribution in distributions:
        for initial_node in initial_routing_nodes:
            paths = _explore_all_paths_from_waypoint(
                initial_node,
                journey_id,
                waypoint_routing,
                [reference_distribution],
                base_stages,
                visited=set(),
            )
            variants.extend(paths)

    return variants if variants else [(base_stages, 100.0)]


def _explore_all_paths_from_waypoint(
    waypoint: str,
    journey_id: str,
    waypoint_routing: Dict,
    path_so_far: List[str],
    base_stages: List[str],
    visited: set | None = None,
) -> List[Tuple[List[str], float]]:
    """Explore all paths from a given routing split node."""
    current_path = path_so_far + [waypoint]

    seen = set(visited or set())
    if waypoint in seen:
        routing_cfg = waypoint_routing.get(waypoint, {}).get(journey_id, {})
        terminal_destinations = [
            dest
            for dest in routing_cfg.get("destinations", [])
            if dest.get("target") and not _is_routing_split_node(dest.get("target"))
        ]
        if terminal_destinations:
            return [
                (current_path + [dest["target"]], float(dest.get("percentage", 0)))
                for dest in terminal_destinations
            ]
        return [(current_path, 100.0)]
    seen.add(waypoint)

    # Check if this split node has routing for this journey.
    if waypoint in waypoint_routing and journey_id in waypoint_routing[waypoint]:
        routing_config = waypoint_routing[waypoint][journey_id]
        destinations = routing_config.get("destinations", [])

        if destinations:
            # Split into multiple paths based on percentages
            paths = []
            for dest_config in destinations:
                target = dest_config.get("target")
                percentage = float(dest_config.get("percentage", 0))
                if not target:
                    continue

                if _is_routing_split_node(target):
                    # Continue exploring from this split node.
                    sub_paths = _explore_all_paths_from_waypoint(
                        target,
                        journey_id,
                        waypoint_routing,
                        current_path,
                        base_stages,
                        visited=seen,
                    )
                    # Scale percentages
                    for sub_path, sub_percentage in sub_paths:
                        paths.append((sub_path, percentage * sub_percentage / 100.0))
                else:
                    # Non-split targets can still have a fixed tail in base journey.
                    final_path = current_path + [target]
                    try:
                        target_idx = base_stages.index(target)
                    except ValueError:
                        target_idx = -1

                    if target_idx >= 0:
                        tail = base_stages[target_idx + 1 :]
                        terminal_paths = [(final_path, percentage)]
                        for tail_stage in tail:
                            if (
                                _is_routing_split_node(tail_stage)
                                and tail_stage in waypoint_routing
                                and journey_id in waypoint_routing[tail_stage]
                            ):
                                expanded_paths = []
                                for pth, pct in terminal_paths:
                                    sub_paths = _explore_all_paths_from_waypoint(
                                        tail_stage,
                                        journey_id,
                                        waypoint_routing,
                                        pth,
                                        base_stages,
                                        visited=seen,
                                    )
                                    for sub_path, sub_pct in sub_paths:
                                        expanded_paths.append(
                                            (sub_path, pct * sub_pct / 100.0)
                                        )
                                terminal_paths = expanded_paths
                                break

                            terminal_paths = [
                                (pth + [tail_stage], pct) for pth, pct in terminal_paths
                            ]

                        paths.extend(terminal_paths)
                    else:
                        paths.append((final_path, percentage))

            return paths if paths else [(current_path, 100.0)]

    # No routing or no destinations - handle gracefully.
    return [(current_path, 100.0)]


# Update the main function name call
def _create_journeys(
    simulation: jps.Simulation,
    data: Dict[str, Any],
    stage_map: Dict[str, int],
    direct_steering_keys: set | None = None,
) -> Dict[str, Any]:
    """Wrapper to maintain compatibility"""
    return _create_journeys_with_percentages(
        simulation, data, stage_map, direct_steering_keys
    )


def _add_agents(
    simulation: jps.Simulation,
    data: Dict[str, Any],
    stage_map: Dict[str, int],
    dist_geom: Dict[str, List[List[float]]],
    dist_params: Dict[str, Dict[str, Any]],
    journey_data: Dict[str, Any],
    walkable_area: pedpy.WalkableArea,
    seed: int,
    model_type: str = "CollisionFreeSpeedModel",
    global_parameters=None,
    direct_steering_info=None,
    global_ds_journey_id=None,
    global_ds_stage_id=None,
) -> Tuple[List[Tuple[float, float]], Dict[int, float], Dict[str, Any]]:
    """Add agents to the simulation based on distributions and journeys."""
    journey_ids = journey_data["journey_ids"]
    journeys_per_distribution = journey_data["journeys_per_distribution"]

    np.random.seed(seed)
    all_positions = []
    agent_radii = {}
    current_agent_id = 0
    agent_wait_info = {}

    # Create individual journeys for each exit for agents without explicit journeys.
    exit_to_journey = {}
    exit_geometries = {}
    for exit_id, exit_data in data.get("exits", {}).items():
        if exit_id in stage_map:
            stage_id = stage_map[exit_id]

            if "coordinates" in exit_data:
                exit_geometries[stage_id] = Polygon(exit_data["coordinates"])

            exit_has_journey = False
            for journey_def in data.get("journeys", []):
                if journey_def["id"] in journey_ids:
                    if exit_id in journey_def.get("stages", []):
                        exit_has_journey = True
                        exit_to_journey[stage_id] = journey_ids[journey_def["id"]]
                        break

            if not exit_has_journey:
                journey_desc = jps.JourneyDescription([stage_id])
                new_journey_id = simulation.add_journey(journey_desc)
                exit_to_journey[stage_id] = new_journey_id
                print(f"Created new journey {new_journey_id} for exit {exit_id}")

    def find_nearest_exit_journey(agent_position):
        """Find the nearest exit and return its journey_id and stage_id."""
        if not exit_geometries:
            if exit_to_journey:
                stage_id = list(exit_to_journey.keys())[0]
                return exit_to_journey[stage_id], stage_id
            raise ValueError("No exits available for agent assignment")

        from shapely.geometry import Point

        agent_point = Point(agent_position)
        min_distance = float("inf")
        nearest_stage_id = None

        for stage_id, exit_geometry in exit_geometries.items():
            distance = agent_point.distance(exit_geometry)
            if distance < min_distance:
                min_distance = distance
                nearest_stage_id = stage_id

        if nearest_stage_id is not None and nearest_stage_id in exit_to_journey:
            return exit_to_journey[nearest_stage_id], nearest_stage_id

        stage_id = list(exit_to_journey.keys())[0]
        return exit_to_journey[stage_id], stage_id

    spawning_freqs_and_numbers = []
    starting_pos_per_source = []
    num_agents_per_source = []
    flow_distributions = []
    has_flow_spawning = False

    # Process distributions to separate flow vs immediate spawning
    immediate_spawn_distributions = {}
    journeys_per_distribution = journey_data["journeys_per_distribution"]

    for dist_key, polygon in dist_geom.items():
        print(f"DEBUG: Processing distribution with dist_key = '{dist_key}'")
        print(
            f"DEBUG: Available journeys_per_distribution keys = {list(journeys_per_distribution.keys())}"
        )

        params = dist_params[dist_key]
        dist_mode, requested_n_agents = _get_distribution_mode_and_count(params)
        use_flow_spawning = params.get("use_flow_spawning", False)
        flow_schedule = _normalize_flow_schedule_entries(params)
        initial_n_agents = int(
            params.get(
                "initial_number",
                0 if flow_schedule else requested_n_agents,
            )
            or 0
        )

        if dist_mode == "by_number" and requested_n_agents <= 0 and initial_n_agents <= 0 and not flow_schedule:
            continue

        try:
            polygon_obj = Polygon(polygon)
            dist_area = shapely.intersection(polygon_obj, walkable_area.polygon)

            if dist_area.is_empty:
                print(f"Warning: Distribution {dist_key} is outside walkable area")
                continue

            # dist_key already matches journey mapping keys (e.g. jps-distributions_0).
            distribution_journeys = journeys_per_distribution.get(dist_key, [])
            print(
                f"DEBUG: dist_key = '{dist_key}', found {len(distribution_journeys)} distribution_journeys"
            )

            if flow_schedule:
                has_flow_spawning = True

                max_radius = _get_max_agent_radius(params)
                max_capacity = _estimate_max_capacity(dist_area, max_radius)

                positions = jps.distribute_until_filled(
                    polygon=dist_area,
                    distance_to_agents=2 * max_radius,
                    distance_to_polygon=max_radius,
                    seed=seed + len(starting_pos_per_source),
                )
                shuffle_rng = random.Random(seed + hash(dist_key))
                shuffle_rng.shuffle(positions)

                for schedule_entry in flow_schedule:
                    n_agents = schedule_entry["number"]
                    flow_start_time = schedule_entry["flow_start_time"]
                    flow_end_time = max(
                        flow_start_time + 0.1, schedule_entry["flow_end_time"]
                    )
                    flow_duration = flow_end_time - flow_start_time
                    flow_rate = n_agents / flow_duration
                    if flow_rate > max_capacity:
                        raise ValueError(
                            f"Distribution '{dist_key}': flow rate of {flow_rate:.1f} agents/s "
                            f"exceeds area capacity of {max_capacity} agents. "
                            f"Reduce the number of agents ({n_agents}) or increase "
                            f"the flow duration ({flow_duration:.1f}s)."
                        )

                    flow_params = dict(params)
                    flow_params["number"] = n_agents
                    flow_params["use_flow_spawning"] = True
                    flow_params["flow_start_time"] = flow_start_time
                    flow_params["flow_end_time"] = flow_end_time

                    frequency = flow_duration / n_agents
                    agents_per_spawn = 1

                    spawning_freqs_and_numbers.append([frequency, agents_per_spawn])
                    num_agents_per_source.append(n_agents)
                    starting_pos_per_source.append(list(positions))

                    flow_distributions.append(
                        {
                            "dist_key": dist_key,
                            "source_id": len(flow_distributions),
                            "params": flow_params,
                            "start_time": flow_start_time,
                            "end_time": flow_end_time,
                            "journey_info": distribution_journeys,
                        }
                    )

                if initial_n_agents > 0:
                    immediate_params = dict(params)
                    immediate_params["number"] = initial_n_agents
                    immediate_params["use_flow_spawning"] = False
                    immediate_spawn_distributions[dist_key] = {
                        "area": dist_area,
                        "params": immediate_params,
                    }

                print(
                    f"Flow spawning: {dist_key} - {sum(entry['number'] for entry in flow_schedule)} scheduled agents"
                )

            elif use_flow_spawning:
                has_flow_spawning = True

                max_radius = _get_max_agent_radius(params)
                max_capacity = _estimate_max_capacity(dist_area, max_radius)

                # Flow spawning: agents spawn over time so the full requested
                # count is valid even if it exceeds simultaneous capacity.
                if dist_mode == "by_number":
                    n_agents = requested_n_agents
                else:  # by_percentage
                    percentage = _get_distribution_percentage(params)
                    n_agents = max(1, int(max_capacity * percentage / 100))

                if n_agents <= 0:
                    print(f"Warning: No agents fit in distribution {dist_key}")
                    continue

                # Get flow parameters
                flow_start_time = max(0, params.get("flow_start_time", 0))
                flow_end_time = max(
                    flow_start_time + 0.1, params.get("flow_end_time", 10)
                )
                flow_duration = flow_end_time - flow_start_time

                # Validate flow rate does not exceed area capacity
                flow_rate = n_agents / flow_duration
                if flow_rate > max_capacity:
                    raise ValueError(
                        f"Distribution '{dist_key}': flow rate of {flow_rate:.1f} agents/s "
                        f"exceeds area capacity of {max_capacity} agents. "
                        f"Reduce the number of agents ({n_agents}) or increase "
                        f"the flow duration ({flow_duration:.1f}s)."
                    )

                params["number"] = n_agents

                frequency = flow_duration / n_agents  # seconds between spawns
                agents_per_spawn = 1  # spawn 1 agent at a time for smooth flow

                spawning_freqs_and_numbers.append([frequency, agents_per_spawn])
                num_agents_per_source.append(n_agents)

                positions = jps.distribute_until_filled(
                    polygon=dist_area,
                    distance_to_agents=2 * max_radius,
                    distance_to_polygon=max_radius,
                    seed=seed + len(starting_pos_per_source),
                )
                shuffle_rng = random.Random(seed + zlib.crc32(dist_key.encode()))
                shuffle_rng.shuffle(positions)
                starting_pos_per_source.append(positions)

                # Store distribution info for flow spawning
                flow_distributions.append(
                    {
                        "dist_key": dist_key,
                        "source_id": len(flow_distributions),
                        "params": params,
                        "start_time": flow_start_time,
                        "end_time": flow_end_time,
                        "journey_info": distribution_journeys,
                    }
                )

                print(
                    f"Flow spawning: {dist_key} - {n_agents} agents over {flow_duration}s (freq: {frequency:.2f}s, rate: {1 / frequency:.2f} agents/s)"
                )

            else:
                # Store for immediate spawning
                immediate_spawn_distributions[dist_key] = {
                    "polygon": polygon,
                    "params": params,
                    "area": dist_area,
                    "distribution_journeys": distribution_journeys,
                }

        except Exception as e:
            print(f"Warning: Error processing distribution {dist_key}: {e}")
            continue

    agent_counter_per_source = [0] * len(flow_distributions)

    # Initialize premovement tracking
    premovement_times = {}
    has_premovement = False

    # Handle immediate spawning distributions (existing logic)
    for dist_key, spawn_data in immediate_spawn_distributions.items():
        try:
            max_radius = _get_max_agent_radius(spawn_data["params"])
            requested_count = int(spawn_data["params"].get("number", 0))
            max_capacity = _estimate_max_capacity(spawn_data["area"], max_radius)
            if requested_count > max_capacity:
                raise ValueError(
                    f"Distribution '{dist_key}': requested {requested_count} agents "
                    f"but area can hold at most ~{max_capacity}. "
                    f"Reduce the number of agents or enlarge the distribution area."
                )
            positions = jps.distribute_by_number(
                polygon=spawn_data["area"],
                number_of_agents=requested_count,
                distance_to_agents=2 * max_radius,
                distance_to_polygon=max_radius,
                seed=seed,
            )

            all_positions.extend(positions)

            distribution_journeys = spawn_data["distribution_journeys"]

            # Check if this distribution uses premovement
            use_premovement = params.get("use_premovement", False)

            # Generate premovement times if enabled
            agent_premovement_times = None
            if use_premovement:
                has_premovement = True
                from utils.premovement_distributions import (
                    create_premovement_distribution,
                    PREMOVEMENT_PRESETS,
                )

                dist_type = params.get("premovement_distribution", "gamma")
                param_a = params.get("premovement_param_a")
                param_b = params.get("premovement_param_b")
                premovement_seed = params.get("premovement_seed")

                # Use custom parameters if provided, otherwise use presets
                if param_a is not None and param_b is not None:
                    dist_params = {"a": param_a, "b": param_b}
                else:
                    dist_params = PREMOVEMENT_PRESETS.get(
                        dist_type, PREMOVEMENT_PRESETS["gamma"]
                    )

                # Use distribution-specific seed or global seed
                if premovement_seed is None:
                    premovement_seed = seed + 1000

                print(
                    f"Generating premovement times for {dist_key}: {dist_type} with params {dist_params}, seed={premovement_seed}"
                )

                distribution = create_premovement_distribution(
                    dist_type, dist_params, premovement_seed
                )
                agent_premovement_times = distribution.sample(len(positions))

                print(
                    f"Premovement times stats - Min: {agent_premovement_times.min():.2f}s, "
                    f"Max: {agent_premovement_times.max():.2f}s, "
                    f"Mean: {agent_premovement_times.mean():.2f}s"
                )

            # Sample per-agent radius and v0
            rng = np.random.RandomState(seed + zlib.crc32(dist_key.encode()) % (2**31))
            sampled_radii, sampled_v0s = _sample_agent_values(
                params, len(positions), rng
            )

            if distribution_journeys:
                print(
                    f"Distribution {dist_key} has {len(distribution_journeys)} journey variants"
                )

                variant_weights, total_weight = _normalize_variant_weights(
                    distribution_journeys
                )

                # Calculate agent distribution using proportional allocation
                agent_assignments = []
                remaining_agents = len(positions)

                for i, variant_info in enumerate(distribution_journeys):
                    variant_data = variant_info["variant_data"]
                    variant_weight = (
                        variant_weights[i] if i < len(variant_weights) else 0.0
                    )

                    if i == len(distribution_journeys) - 1:
                        # Last variant gets all remaining agents to ensure exact total
                        variant_agents = remaining_agents
                    else:
                        # Calculate proportional assignment (rounded)
                        variant_agents = round(
                            (len(positions) * variant_weight) / total_weight
                        )
                        # Ensure we don't exceed remaining agents
                        variant_agents = min(variant_agents, remaining_agents)

                    if variant_agents > 0:
                        agent_assignments.append((variant_info, variant_agents))
                        remaining_agents -= variant_agents

                    print(
                        f"Variant {variant_data['variant_name']}: {variant_agents} agents "
                        f"(weight={variant_weight}, total={total_weight})"
                    )

                # Verify we're using all agents
                total_assigned = sum(assignment[1] for assignment in agent_assignments)
                print(f"Total agents assigned: {total_assigned}/{len(positions)}")

                agent_index = 0
                for variant_info, variant_agents in agent_assignments:
                    variant_data = variant_info["variant_data"]
                    journey_key = variant_info.get("original_journey_id")
                    uses_direct_steering = bool(
                        direct_steering_info
                        and any(
                            stage in direct_steering_info
                            for stage in variant_data.get("actual_stages", [])
                        )
                    )

                    # Entry stage comes from the pre-segmented journey (never mixed DS + regular stages).
                    entry_stages = variant_data.get("entry_stages", [])
                    start_stage_key = next(
                        (
                            stage
                            for stage in entry_stages
                            if stage in stage_map and stage_map[stage] != -1
                        ),
                        None,
                    )

                    if start_stage_key:
                        for j in range(variant_agents):
                            if agent_index < len(positions):
                                pos = positions[agent_index]
                                agent_radius = float(sampled_radii[agent_index])
                                agent_v0 = float(sampled_v0s[agent_index])

                                # Use v0=0 if premovement is enabled, otherwise use sampled v0
                                actual_v0 = 0.0 if use_premovement else agent_v0
                                agent_journey_id = variant_data["id"]
                                agent_stage_id = stage_map[start_stage_key]
                                if (
                                    uses_direct_steering
                                    and global_ds_journey_id is not None
                                    and global_ds_stage_id is not None
                                ):
                                    agent_journey_id = global_ds_journey_id
                                    agent_stage_id = global_ds_stage_id

                                agent_params = create_agent_parameters(
                                    model_type=model_type,
                                    position=pos,
                                    params={"v0": actual_v0, "radius": agent_radius},
                                    global_params=global_parameters,
                                    journey_id=agent_journey_id,
                                    stage_id=agent_stage_id,
                                )

                                agent_id = simulation.add_agent(agent_params)
                                agent_radii[agent_id] = agent_radius

                                # Store premovement time if enabled
                                if (
                                    use_premovement
                                    and agent_premovement_times is not None
                                ):
                                    premovement_times[agent_id] = {
                                        "premovement_time": float(
                                            agent_premovement_times[agent_index]
                                        ),
                                        "position": pos,
                                        "desired_speed": agent_v0,
                                        "activated": False,
                                    }

                                # Record path-based direct steering state.
                                # Use agent_index (not JuPedSim agent_id) for seeding
                                # to ensure determinism across runs in the same process.
                                if direct_steering_info:
                                    path_state = build_agent_path_state(
                                        variant_data=variant_data,
                                        journey_key=journey_key,
                                        transitions=data.get("transitions", []),
                                        direct_steering_info=direct_steering_info,
                                        waypoint_routing=journey_data.get(
                                            "waypoint_routing", {}
                                        ),
                                        seed=seed,
                                        agent_id=agent_index,
                                        initial_position=(float(pos[0]), float(pos[1])),
                                        agent_radius=agent_radius,
                                    )
                                    if path_state:
                                        agent_wait_info[agent_id] = path_state

                                agent_index += 1
                                current_agent_id += 1
            else:
                print(
                    f"Distribution {dist_key} has no journey variants - using nearest exit assignment"
                )

                for idx, pos in enumerate(positions):
                    nearest_journey_id, nearest_stage_id = find_nearest_exit_journey(
                        pos
                    )

                    agent_radius = float(sampled_radii[idx])
                    agent_v0 = float(sampled_v0s[idx])

                    agent_params_dict = {
                        "radius": agent_radius,
                        "v0": 0.0 if use_premovement else agent_v0,
                    }

                    agent_params = create_agent_parameters(
                        model_type=model_type,
                        position=pos,
                        params=agent_params_dict,
                        global_params=global_parameters,
                        journey_id=nearest_journey_id,
                        stage_id=nearest_stage_id,
                    )

                    agent_id = simulation.add_agent(agent_params)
                    agent_radii[agent_id] = agent_radius

                    if use_premovement and agent_premovement_times is not None:
                        premovement_times[agent_id] = {
                            "premovement_time": float(agent_premovement_times[idx]),
                            "position": pos,
                            "desired_speed": agent_v0,
                            "activated": False,
                        }
                    current_agent_id += 1

        except Exception as e:
            error_msg = (
                f"CRITICAL: Failed to place agents in distribution '{dist_key}'. "
                f"Error: {str(e)}. This usually means the spawn area is too small or crowded. "
                f"Consider: 1) Making the distribution area larger, 2) Reducing the number of agents, "
                f"3) Increasing distance between agents, or 4) Checking for obstacles in the area."
            )
            print(f"ERROR: {error_msg}")
            raise Exception(error_msg)

    spawning_info = {
        "has_flow_spawning": has_flow_spawning,
        "spawning_freqs_and_numbers": spawning_freqs_and_numbers,
        "starting_pos_per_source": starting_pos_per_source,
        "num_agents_per_source": num_agents_per_source,
        "agent_counter_per_source": agent_counter_per_source,
        "flow_distributions": flow_distributions,
        "model_type": model_type,
        "global_parameters": global_parameters,
        "stage_map": stage_map,
        "exit_to_journey": exit_to_journey,
        "exit_geometries": exit_geometries,
        "has_premovement": has_premovement,
        "premovement_times": premovement_times,
        "agent_wait_info": agent_wait_info,
        "transitions": data.get("transitions", []),
        "waypoint_routing": journey_data.get("waypoint_routing", {}),
        "global_ds_journey_id": global_ds_journey_id,
        "global_ds_stage_id": global_ds_stage_id,
    }

    return all_positions, agent_radii, spawning_info
