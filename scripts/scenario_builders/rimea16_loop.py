"""Helpers for RiMEA 16 looped-ring examples and tests."""

from __future__ import annotations

import csv
import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
import numpy as np
from shapely.geometry import LineString, Point, Polygon

from core.scenario import Scenario

LENGTH_M = 4.0
RADIUS_M = 3.0
HALF_WIDTH_M = 0.4
CHECKPOINT_HALF_SIZE_M = 0.22
DEFAULT_AGENT_RADIUS_M = 0.15
DEFAULT_AGENT_SPACING_M = 2.4
DEFAULT_AGENT_COUNT = 10
DEFAULT_FRAME_STEP = 10
MIN_LAPS_FOR_ANALYSIS = 3
REFERENCE_CSV = (
    Path(__file__).resolve().parents[1]
    / "scenarios"
    / "rimea16_percentile_reference.csv"
)


@dataclass(frozen=True)
class LoopGeometry:
    walkable_polygon: Polygon
    centerline: LineString
    positions: list[tuple[float, float]]

    @property
    def walkable_area_wkt(self) -> str:
        return self.walkable_polygon.wkt

    @property
    def track_length(self) -> float:
        return float(self.centerline.length)

    def estimate_rho_max(self, spacing: float = DEFAULT_AGENT_SPACING_M) -> float:
        return 1.0 / spacing


def generate_oval_shape_points(
    num_points: int,
    length: float = LENGTH_M,
    radius: float = RADIUS_M,
    start: tuple[float, float] = (0.0, 0.0),
    dx: float = 0.1,
    threshold: float = DEFAULT_AGENT_SPACING_M,
) -> tuple[list[tuple[float, float]], list[tuple[float, float]]]:
    """Generate a closed oval with two straight segments and two half circles."""
    points = [start]
    selected_points = [start]
    last_selected = start

    def dist(p1: tuple[float, float], p2: tuple[float, float]) -> float:
        return math.hypot(p1[0] - p2[0], p1[1] - p2[1])

    dphi = dx / radius
    center2 = (start[0] + length, start[1] + radius)
    center1 = (start[0], start[1] + radius)
    npoint_on_segment = int(length / dx)

    for i in range(1, npoint_on_segment + 1):
        tmp_point = (start[0] + i * dx, start[1])
        points.append(tmp_point)
        if dist(tmp_point, last_selected) >= threshold:
            selected_points.append(tmp_point)
            last_selected = tmp_point

    phi = -math.pi / 2.0
    while phi < math.pi / 2.0:
        tmp_point = (
            center2[0] + radius * math.cos(phi),
            center2[1] + radius * math.sin(phi),
        )
        points.append(tmp_point)
        if dist(tmp_point, last_selected) >= threshold:
            selected_points.append(tmp_point)
            last_selected = tmp_point
        phi += dphi

    for i in range(1, npoint_on_segment + 1):
        tmp_point = (
            start[0] + (npoint_on_segment + 1) * dx - i * dx,
            start[1] + 2 * radius,
        )
        points.append(tmp_point)
        if dist(tmp_point, last_selected) >= threshold:
            selected_points.append(tmp_point)
            last_selected = tmp_point

    phi = math.pi / 2.0
    while phi < (3 * math.pi / 2.0) - dphi:
        tmp_point = (
            center1[0] + radius * math.cos(phi),
            center1[1] + radius * math.sin(phi),
        )
        points.append(tmp_point)
        if dist(tmp_point, last_selected) >= threshold:
            selected_points.append(tmp_point)
            last_selected = tmp_point
        phi += dphi

    if math.hypot(selected_points[-1][0] - start[0], selected_points[-1][1] - start[1]) < threshold:
        selected_points.pop()

    return points, selected_points[:num_points]


def build_loop_geometry(
    num_agents: int = DEFAULT_AGENT_COUNT,
    length: float = LENGTH_M,
    radius: float = RADIUS_M,
    half_width: float = HALF_WIDTH_M,
    spacing: float = DEFAULT_AGENT_SPACING_M,
) -> LoopGeometry:
    """Build the walkable ring, centerline, and evenly spaced agent positions."""
    centerline_points, _ = generate_oval_shape_points(
        num_points=max(num_agents, 999),
        length=length,
        radius=radius,
        start=(0.0, 0.0),
        dx=0.05,
        threshold=spacing,
    )
    _, exterior = generate_oval_shape_points(
        num_points=999,
        length=length,
        radius=radius + half_width,
        start=(0.0, -half_width),
        dx=0.05,
        threshold=0.2,
    )
    _, interior = generate_oval_shape_points(
        num_points=999,
        length=length,
        radius=radius - half_width,
        start=(0.0, half_width),
        dx=0.05,
        threshold=0.2,
    )
    walkable_polygon = Polygon(exterior).difference(Polygon(interior))
    centerline = LineString(centerline_points + [centerline_points[0]])
    track_length = float(centerline.length)
    positions = [
        (point.x, point.y)
        for point in (
            centerline.interpolate((track_length * index) / num_agents)
            for index in range(num_agents)
        )
    ]
    return LoopGeometry(
        walkable_polygon=walkable_polygon,
        centerline=centerline,
        positions=positions,
    )


def approximate_agent_count(track_length: float, target_density: float) -> int:
    """Convert an approximate 1D target density to an integer agent count."""
    return max(2, int(round(track_length * target_density)))


def default_density_sweep(track_length: float, rho_max: float) -> list[dict]:
    """Return three approximate RiMEA 16 density cases."""
    targets = [
        ("low density", rho_max / 6.0, "#4C72B0"),
        ("medium density", rho_max / 2.0, "#55A868"),
        ("near-max density", max(rho_max - 1.0, 0.7 * rho_max), "#C44E52"),
    ]
    sweep = []
    for label, density, color in targets:
        sweep.append(
            {
                "label": label,
                "target_density_1pm": float(density),
                "num_agents": approximate_agent_count(track_length, density),
                "color": color,
            }
        )
    return sweep


def _square(center_x: float, center_y: float, half_size: float) -> list[list[float]]:
    return [
        [center_x - half_size, center_y - half_size],
        [center_x + half_size, center_y - half_size],
        [center_x + half_size, center_y + half_size],
        [center_x - half_size, center_y + half_size],
        [center_x - half_size, center_y - half_size],
    ]


def build_loop_scenario(
    *,
    label: str,
    desired_speed: float,
    agent_radius: float = DEFAULT_AGENT_RADIUS_M,
    num_agents: int = DEFAULT_AGENT_COUNT,
    max_simulation_time: float = 120.0,
    spacing: float = DEFAULT_AGENT_SPACING_M,
    seed: int = 42,
) -> tuple[Scenario, LoopGeometry]:
    """Build a shared-runner scenario for RiMEA 16."""
    geometry = build_loop_geometry(num_agents=num_agents, spacing=spacing)
    checkpoint_distances = [
        0.20 * geometry.track_length,
        0.50 * geometry.track_length,
        0.80 * geometry.track_length,
    ]
    checkpoint_points = [geometry.centerline.interpolate(d) for d in checkpoint_distances]
    checkpoint_ids = [f"jps-checkpoints_{index}" for index in range(len(checkpoint_points))]
    checkpoints = {
        f"jps-checkpoints_{index}": {
            "coordinates": _square(point.x, point.y, CHECKPOINT_HALF_SIZE_M)
        }
        for index, point in enumerate(checkpoint_points)
    }

    distributions = {}
    journeys = []
    transitions = []
    for index, (x_pos, y_pos) in enumerate(geometry.positions):
        distribution_id = f"jps-distributions_{index}"
        journey_id = f"journey_{index}"
        start_distance = geometry.centerline.project(Point(x_pos, y_pos))
        first_checkpoint_index = next(
            (
                checkpoint_index
                for checkpoint_index, checkpoint_distance in enumerate(checkpoint_distances)
                if checkpoint_distance > start_distance
            ),
            0,
        )
        ordered_checkpoints = (
            checkpoint_ids[first_checkpoint_index:] + checkpoint_ids[:first_checkpoint_index]
        )
        distributions[distribution_id] = {
            "coordinates": _square(x_pos, y_pos, agent_radius + 0.04),
            "parameters": {
                "number": 1,
                "radius": agent_radius,
                "v0": desired_speed,
                "distribution_mode": "by_number",
            },
        }
        journeys.append(
            {
                "id": journey_id,
                "stages": [distribution_id, *ordered_checkpoints],
            }
        )
        rotated_pairs = list(zip(ordered_checkpoints, ordered_checkpoints[1:] + ordered_checkpoints[:1]))
        transitions.extend(
            [{"from": distribution_id, "to": ordered_checkpoints[0], "journey_id": journey_id}]
            + [
                {"from": source, "to": target, "journey_id": journey_id}
                for source, target in rotated_pairs
            ]
        )

    first_position = geometry.positions[0]
    raw = {
        "config": {
            "simulation_settings": {
                "baseSeed": seed,
                "simulationParams": {
                    "model_type": "CollisionFreeSpeedModel",
                    "max_simulation_time": max_simulation_time,
                },
            }
        },
        "distributions": distributions,
        "checkpoints": checkpoints,
        "exits": {
            "jps-exits_0": {
                "coordinates": _square(first_position[0], first_position[1], 0.08)
            }
        },
        "journeys": journeys,
        "transitions": transitions,
    }
    scenario = Scenario(
        raw=raw,
        walkable_area_wkt=geometry.walkable_area_wkt,
        model_type="CollisionFreeSpeedModel",
        seed=seed,
        sim_params={
            "model_type": "CollisionFreeSpeedModel",
            "max_simulation_time": max_simulation_time,
        },
        source_path=label,
    )
    return scenario, geometry


def _unwrap_positions(
    projected_distances: pd.Series,
    track_length: float,
) -> pd.Series:
    values = projected_distances.to_numpy(dtype=float)
    unwrapped = np.empty_like(values)
    if len(values) == 0:
        return pd.Series(dtype=float, index=projected_distances.index)
    offset = 0.0
    unwrapped[0] = values[0]
    for index in range(1, len(values)):
        delta = values[index] - values[index - 1]
        if delta < (-0.5 * track_length):
            offset += track_length
        elif delta > (0.5 * track_length):
            offset -= track_length
        unwrapped[index] = values[index] + offset
    return pd.Series(unwrapped, index=projected_distances.index)


def compute_density_speed_samples(
    trajectory_df: pd.DataFrame,
    frame_rate: float,
    centerline: LineString,
    track_length: float,
    frame_step: int = DEFAULT_FRAME_STEP,
    min_laps: int = MIN_LAPS_FOR_ANALYSIS,
) -> pd.DataFrame:
    """Compute per-agent speed-density samples on the loop."""
    if trajectory_df.empty:
        return pd.DataFrame(columns=["frame", "id", "density_1pm", "speed_mps", "lap"])

    df = trajectory_df.sort_values(["id", "frame"]).copy()
    df["projected_distance"] = df.apply(
        lambda row: centerline.project(Point(float(row["x"]), float(row["y"]))),
        axis=1,
    )
    df["unwrapped_distance"] = (
        df.groupby("id", group_keys=False)["projected_distance"]
        .apply(lambda series: _unwrap_positions(series, track_length))
    )
    df["lap"] = np.floor(df["unwrapped_distance"] / track_length).astype(int)

    df["future_unwrapped"] = df.groupby("id")["unwrapped_distance"].shift(-frame_step)
    df["future_frame"] = df.groupby("id")["frame"].shift(-frame_step)
    delta_frames = df["future_frame"] - df["frame"]
    dt_seconds = delta_frames / frame_rate
    df["speed_mps"] = (df["future_unwrapped"] - df["unwrapped_distance"]) / dt_seconds

    density_rows = []
    for frame, frame_df in df.groupby("frame"):
        frame_sorted = frame_df.sort_values("projected_distance").reset_index()
        if len(frame_sorted) < 2:
            continue
        positions = frame_sorted["projected_distance"].to_numpy(dtype=float)
        forward = np.roll(positions, -1) - positions
        forward[-1] += track_length
        backward = positions - np.roll(positions, 1)
        backward[0] += track_length
        voronoi_distance = 0.5 * (forward + backward)
        density = 1.0 / voronoi_distance
        for row_index, density_value in zip(frame_sorted["index"], density):
            density_rows.append((row_index, frame, density_value))

    density_df = pd.DataFrame(density_rows, columns=["row_index", "frame", "density_1pm"])
    merged = df.merge(density_df, left_index=True, right_on="row_index", how="left")
    merged = merged.dropna(subset=["density_1pm", "speed_mps"]).copy()
    merged = merged[merged["lap"] >= min_laps].copy()
    return merged[["frame_x", "id", "density_1pm", "speed_mps", "lap"]].rename(
        columns={"frame_x": "frame"}
    )


def compute_lap_counts(
    trajectory_df: pd.DataFrame,
    centerline: LineString,
    track_length: float,
) -> pd.DataFrame:
    """Compute completed laps per agent from projected centerline positions."""
    if trajectory_df.empty:
        return pd.DataFrame(columns=["id", "completed_laps"])
    df = trajectory_df.sort_values(["id", "frame"]).copy()
    df["projected_distance"] = df.apply(
        lambda row: centerline.project(Point(float(row["x"]), float(row["y"]))),
        axis=1,
    )
    df["unwrapped_distance"] = (
        df.groupby("id", group_keys=False)["projected_distance"]
        .apply(lambda series: _unwrap_positions(series, track_length))
    )
    lap_counts = (
        df.groupby("id")["unwrapped_distance"]
        .max()
        .div(track_length)
        .apply(math.floor)
        .reset_index(name="completed_laps")
    )
    return lap_counts


def compute_density_speed_curve(
    samples: pd.DataFrame,
    density_bin_size: float = 0.2,
    min_samples: int = 1,
) -> pd.DataFrame:
    """Aggregate speed-density samples into density bins."""
    if samples.empty:
        return pd.DataFrame(columns=["density_1pm", "speed_mps", "sample_count"])
    curve = samples.copy()
    curve["density_bin"] = (
        np.floor(curve["density_1pm"] / density_bin_size) * density_bin_size
    )
    curve = (
        curve.groupby("density_bin")["speed_mps"]
        .agg(["median", "count"])
        .reset_index()
        .rename(
            columns={
                "density_bin": "density_1pm",
                "median": "speed_mps",
                "count": "sample_count",
            }
        )
    )
    curve = curve[curve["sample_count"] >= min_samples].reset_index(drop=True)
    return curve


def load_reference_band(csv_path: Path = REFERENCE_CSV) -> pd.DataFrame:
    """Load the RiMEA 16 percentile reference band from CSV."""
    rows = []
    with csv_path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle, delimiter=";")
        for raw_row in reader:
            row = [cell.replace("\xa0", "").strip() for cell in raw_row]
            if len(row) < 4:
                continue
            if not row[0] or row[0].lower().startswith("1d fundamental"):
                continue
            if row[0].lower().startswith("rimea test case"):
                continue
            if row[0].lower().startswith("density") or row[0].startswith("/1/m"):
                continue
            rows.append(
                {
                    "density_1pm": float(row[0].replace(",", ".")),
                    "speed_p10_mps": float(row[1].replace(",", ".")),
                    "speed_p50_mps": float(row[2].replace(",", ".")),
                    "speed_p90_mps": float(row[3].replace(",", ".")),
                }
            )
    return pd.DataFrame(rows)


def summarize_reference_fit(curve: pd.DataFrame, reference: pd.DataFrame) -> pd.DataFrame:
    """Join a speed curve with the reference band and classify each bin."""
    if curve.empty:
        return pd.DataFrame(
            columns=[
                "density_1pm",
                "speed_mps",
                "speed_p10_mps",
                "speed_p50_mps",
                "speed_p90_mps",
                "below_p10",
                "inside_band",
                "above_p90",
            ]
        )
    reference_sorted = reference.sort_values("density_1pm")
    curve_sorted = curve.sort_values("density_1pm").copy()
    densities = curve_sorted["density_1pm"].to_numpy(dtype=float)
    curve_sorted["speed_p10_mps"] = np.interp(
        densities,
        reference_sorted["density_1pm"],
        reference_sorted["speed_p10_mps"],
    )
    curve_sorted["speed_p50_mps"] = np.interp(
        densities,
        reference_sorted["density_1pm"],
        reference_sorted["speed_p50_mps"],
    )
    curve_sorted["speed_p90_mps"] = np.interp(
        densities,
        reference_sorted["density_1pm"],
        reference_sorted["speed_p90_mps"],
    )
    curve_sorted["below_p10"] = curve_sorted["speed_mps"] < curve_sorted["speed_p10_mps"]
    curve_sorted["inside_band"] = (
        (curve_sorted["speed_mps"] >= curve_sorted["speed_p10_mps"])
        & (curve_sorted["speed_mps"] <= curve_sorted["speed_p90_mps"])
    )
    curve_sorted["above_p90"] = curve_sorted["speed_mps"] > curve_sorted["speed_p90_mps"]
    return curve_sorted
