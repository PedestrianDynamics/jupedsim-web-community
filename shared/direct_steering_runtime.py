import math
import random
from importlib import import_module
from typing import Any, Dict

from shapely.ops import nearest_points


def simulation_init_module():
    try:
        return import_module("utils.simulation_init")
    except ModuleNotFoundError:
        return import_module("shared.simulation_init")


def normalize_speed_factor(value: Any) -> float:
    try:
        speed_factor = float(value)
    except (TypeError, ValueError):
        return 1.0
    if not math.isfinite(speed_factor) or speed_factor < 0.0:
        return 1.0
    return min(speed_factor, 3.0)


def random_point_in_polygon(polygon, rng, min_clearance: float = 0.2):
    return simulation_init_module()._random_point_in_polygon(
        polygon,
        rng,
        min_clearance=min_clearance,
    )


def largest_polygon(geometry):
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


def stage_center(stage_cfg):
    polygon = (stage_cfg or {}).get("polygon")
    if polygon is None:
        return None
    try:
        center = polygon.representative_point()
    except Exception:
        return None
    return (float(center.x), float(center.y))


def pick_stage_target(wait_state, next_stage_cfg, current_xy):
    from shapely.geometry import Point, Polygon as ShapelyPolygon

    polygon = (next_stage_cfg or {}).get("polygon")
    if polygon is None:
        return None

    target_rng = random.Random(
        int(wait_state.get("base_seed", 0)) + int(wait_state.get("step_index", 0))
    )
    reach_penetration = max(0.0, float(wait_state.get("reach_penetration", 0.25)))
    target_clearance = max(
        0.05,
        float(wait_state.get("agent_radius", 0.2)) * 0.8,
        reach_penetration,
    )
    fallback_target = random_point_in_polygon(
        polygon,
        target_rng,
        min_clearance=target_clearance,
    )
    if not current_xy:
        return fallback_target

    cx, cy = float(current_xy[0]), float(current_xy[1])
    next_center = stage_center(next_stage_cfg)
    origin_center = stage_center(
        wait_state.get("stage_configs", {}).get(wait_state.get("current_origin"))
    )

    if next_center and origin_center:
        hx = float(next_center[0]) - float(origin_center[0])
        hy = float(next_center[1]) - float(origin_center[1])
    elif next_center:
        hx = float(next_center[0]) - cx
        hy = float(next_center[1]) - cy
    else:
        return fallback_target

    heading_norm = math.hypot(hx, hy)
    if heading_norm <= 1e-9:
        return fallback_target
    heading_x = hx / heading_norm
    heading_y = hy / heading_norm
    perp_x = -heading_y
    perp_y = heading_x

    half_width = max(0.6, float(wait_state.get("agent_radius", 0.2)) * 2.5)
    look_length = max(1.8, half_width * 3.0)
    look_polygon = ShapelyPolygon(
        [
            (cx + perp_x * half_width, cy + perp_y * half_width),
            (cx - perp_x * half_width, cy - perp_y * half_width),
            (
                cx - perp_x * half_width + heading_x * look_length,
                cy - perp_y * half_width + heading_y * look_length,
            ),
            (
                cx + perp_x * half_width + heading_x * look_length,
                cy + perp_y * half_width + heading_y * look_length,
            ),
        ]
    )
    overlap = largest_polygon(polygon.intersection(look_polygon))
    if overlap is None:
        near_radius = max(1.2, look_length * 0.75)
        overlap = largest_polygon(polygon.intersection(Point(cx, cy).buffer(near_radius)))

    if overlap is not None:
        candidate_polygon = overlap
        min_alignment_cos = 0.5
        best_candidate = fallback_target
        best_alignment = -1.0
        for _ in range(24):
            candidate = random_point_in_polygon(
                candidate_polygon,
                target_rng,
                min_clearance=target_clearance,
            )
            vx = float(candidate[0]) - cx
            vy = float(candidate[1]) - cy
            vnorm = math.hypot(vx, vy)
            if vnorm <= 1e-9:
                return candidate
            alignment = (vx * heading_x + vy * heading_y) / vnorm
            if alignment > best_alignment:
                best_alignment = alignment
                best_candidate = candidate
            if alignment >= min_alignment_cos:
                return candidate
        return best_candidate

    nearest_on_polygon = nearest_points(polygon, Point(cx, cy))[0]
    local_region = largest_polygon(
        polygon.intersection(
            nearest_on_polygon.buffer(max(0.35, target_clearance * 2.0))
        )
    )
    if local_region is not None:
        return random_point_in_polygon(
            local_region, target_rng, min_clearance=target_clearance
        )
    return (float(nearest_on_polygon.x), float(nearest_on_polygon.y))


def extract_agent_xy(agent):
    pos = getattr(agent, "position", None)
    if pos is not None:
        if isinstance(pos, (tuple, list)) and len(pos) >= 2:
            return float(pos[0]), float(pos[1])
        if hasattr(pos, "x") and hasattr(pos, "y"):
            return float(pos.x), float(pos.y)
    if hasattr(agent, "x") and hasattr(agent, "y"):
        return float(agent.x), float(agent.y)
    return None, None


def assign_agent_target(agent, target):
    if not target:
        return
    tx, ty = float(target[0]), float(target[1])
    try:
        agent.target = (tx, ty)
        return
    except Exception:
        pass
    try:
        agent.target = [tx, ty]
    except Exception:
        pass


def is_inside_polygon(x, y, polygon):
    if polygon is None:
        return False
    try:
        from shapely.geometry import Point

        point = Point(float(x), float(y))
        return bool(polygon.contains(point) or polygon.touches(point))
    except Exception:
        return False


def is_inside_with_penetration(polygon, x: float, y: float, penetration: float) -> bool:
    from shapely.geometry import Point

    if polygon is None:
        return False
    point = Point(float(x), float(y))
    covers_fn = getattr(polygon, "covers", None)
    contains_fn = getattr(polygon, "contains", None)
    touches_fn = getattr(polygon, "touches", None)
    if callable(covers_fn):
        inside = bool(covers_fn(point))
    elif callable(contains_fn):
        inside = bool(contains_fn(point))
        if not inside and callable(touches_fn):
            inside = bool(touches_fn(point))
    else:
        return False

    if not inside:
        return False
    if penetration <= 0.0:
        return True
    boundary = getattr(polygon, "boundary", None)
    if boundary is None or not hasattr(boundary, "distance"):
        return True
    return float(boundary.distance(point)) >= float(penetration)


def checkpoint_stage_reached(wait_info, stage_cfg, current_time: float, x: float, y: float):
    polygon = stage_cfg.get("polygon")
    if polygon is None:
        return False
    penetration = float(wait_info.get("reach_penetration", 0.25))
    dwell_seconds = float(wait_info.get("reach_dwell_seconds", 0.2))
    if not is_inside_with_penetration(polygon, x, y, penetration):
        wait_info["inside_since"] = None
        return False
    inside_since = wait_info.get("inside_since")
    if inside_since is None:
        wait_info["inside_since"] = float(current_time)
        return False
    return float(current_time) - float(inside_since) >= dwell_seconds


def sample_wait_time(stage_cfg, base_seed, step_index):
    mean_wait = float(stage_cfg.get("waiting_time", 0.0))
    if stage_cfg.get("waiting_time_distribution") == "gaussian":
        std_wait = float(stage_cfg.get("waiting_time_std", 1.0))
        rng = random.Random(int(base_seed) + int(step_index) * 131 + 17)
        return max(0.1, float(rng.gauss(mean_wait, std_wait)))
    return max(0.0, mean_wait)


def get_agent_desired_speed(agent) -> float | None:
    model_obj = getattr(agent, "model", None)
    if model_obj is None:
        return None
    if hasattr(model_obj, "desired_speed"):
        try:
            return float(model_obj.desired_speed)
        except Exception:
            return None
    return None


def set_agent_desired_speed(agent, speed: float) -> bool:
    model_obj = getattr(agent, "model", None)
    if model_obj is None:
        return False
    if hasattr(model_obj, "desired_speed"):
        try:
            model_obj.desired_speed = float(speed)
            return True
        except Exception:
            return False
    return False


def ensure_agent_speed_state(agent_speed_state: Dict[int, Dict[str, Any]], agent_id: int, agent):
    state = agent_speed_state.setdefault(
        int(agent_id),
        {"original_speed": None, "active_checkpoint": None},
    )
    current_speed = get_agent_desired_speed(agent)
    if current_speed is not None and state.get("active_checkpoint") is None:
        state["original_speed"] = current_speed
    elif current_speed is not None and state.get("original_speed") is None:
        state["original_speed"] = current_speed
    return state


def restore_agent_speed(agent_speed_state: Dict[int, Dict[str, Any]], agent_id: int, agent) -> None:
    state = ensure_agent_speed_state(agent_speed_state, agent_id, agent)
    if state.get("active_checkpoint") is None:
        return
    original_speed = state.get("original_speed")
    if original_speed is None:
        return
    if set_agent_desired_speed(agent, float(original_speed)):
        state["active_checkpoint"] = None


def update_checkpoint_speed(
    agent_speed_state: Dict[int, Dict[str, Any]],
    direct_steering_info: Dict[str, Dict[str, Any]] | None,
    agent_id: int,
    agent,
    checkpoint_key: str | None,
    stage_cfg: Dict[str, Any] | None,
    x: float,
    y: float,
) -> None:
    state = ensure_agent_speed_state(agent_speed_state, agent_id, agent)
    active_zone_key = None
    active_speed_factor = 1.0

    if checkpoint_key and stage_cfg:
        stage_polygon = stage_cfg.get("polygon")
        stage_speed_factor = normalize_speed_factor(stage_cfg.get("speed_factor", 1.0))
        if math.fabs(stage_speed_factor - 1.0) > 1e-9 and is_inside_polygon(
            x, y, stage_polygon
        ):
            active_zone_key = checkpoint_key
            active_speed_factor = stage_speed_factor

    if active_zone_key is None:
        for zone_key, zone_cfg in (direct_steering_info or {}).items():
            zone_speed_factor = normalize_speed_factor(zone_cfg.get("speed_factor", 1.0))
            if math.fabs(zone_speed_factor - 1.0) <= 1e-9:
                continue
            if not is_inside_polygon(x, y, zone_cfg.get("polygon")):
                continue
            if active_zone_key is None or math.fabs(zone_speed_factor - 1.0) > math.fabs(
                active_speed_factor - 1.0
            ):
                active_zone_key = zone_key
                active_speed_factor = zone_speed_factor

    if active_zone_key is not None and math.fabs(active_speed_factor - 1.0) > 1e-9:
        original_speed = state.get("original_speed")
        if original_speed is None:
            return
        slowed_speed = max(0.0, float(original_speed) * active_speed_factor)
        if set_agent_desired_speed(agent, slowed_speed):
            state["active_checkpoint"] = active_zone_key
        return

    restore_agent_speed(agent_speed_state, agent_id, agent)


def advance_path_target(wait_info):
    path_choices = wait_info.get("path_choices", {})
    stage_configs = wait_info.get("stage_configs", {})
    current_stage = wait_info.get("current_target_stage")
    next_candidates = path_choices.get(current_stage, [])
    if not next_candidates:
        wait_info["state"] = "done"
        return

    total = sum(max(0.0, float(weight)) for _, weight in next_candidates)
    if total <= 0:
        next_stage = next_candidates[0][0]
    else:
        choose_rng = random.Random(
            int(wait_info.get("base_seed", 0))
            + int(wait_info.get("step_index", 0)) * 131
            + 53
        )
        pick = choose_rng.random() * total
        running = 0.0
        next_stage = next_candidates[-1][0]
        for stage_key, weight in next_candidates:
            running += max(0.0, float(weight))
            if pick <= running:
                next_stage = stage_key
                break

    if next_stage not in stage_configs:
        wait_info["state"] = "done"
        return

    wait_info["current_origin"] = current_stage
    wait_info["current_target_stage"] = next_stage
    wait_info["step_index"] = int(wait_info.get("step_index", 0)) + 1
    wait_info["target_assigned"] = False
    wait_info["wait_until"] = None
    wait_info["state"] = "to_target"
    wait_info["inside_since"] = None
    wait_info["target"] = pick_stage_target(
        wait_info,
        stage_configs[next_stage],
        wait_info.get("current_position"),
    )
