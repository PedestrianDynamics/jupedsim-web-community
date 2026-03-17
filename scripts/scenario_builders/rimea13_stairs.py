"""Helpers for RiMEA 13 stair fundamental-diagram examples and tests."""

from __future__ import annotations

import numpy as np

STAIR_WALKABLE_AREA_WKT = (
    "POLYGON ((0 0, 10 0, 10 4, 19 4, 19 6, 10 6, 10 10, 0 10, 0 0))"
)
STAIR_ZONE_COORDINATES = [[12.0, 4.0], [17.0, 4.0], [17.0, 6.0], [12.0, 6.0], [12.0, 4.0]]
EXIT_COORDINATES = [[18.8, 4.0], [19.0, 4.0], [19.0, 6.0], [18.8, 6.0], [18.8, 4.0]]

FRUIN_STAIR_SPEEDS = {
    "down": [
        {"label": "under_30_internal", "speed": 0.76},
        {"label": "under_30_external", "speed": 0.81},
        {"label": "age_30_50_internal", "speed": 0.65},
        {"label": "age_30_50_external", "speed": 0.78},
        {"label": "over_50_internal", "speed": 0.55},
        {"label": "over_50_external", "speed": 0.59},
        {"label": "impaired_mobility", "speed": 0.42},
    ],
    "up": [
        {"label": "under_30_internal", "speed": 0.55},
        {"label": "under_30_external", "speed": 0.58},
        {"label": "age_30_50_internal", "speed": 0.50},
        {"label": "age_30_50_external", "speed": 0.58},
        {"label": "over_50_internal", "speed": 0.42},
        {"label": "over_50_external", "speed": 0.42},
        {"label": "impaired_mobility", "speed": 0.32},
    ],
}

RIEMA_STANDARD_DEMOGRAPHIC = [
    {"label": "under_30_internal", "count": 15, "v0": 1.34},
    {"label": "under_30_external", "count": 15, "v0": 1.30},
    {"label": "age_30_50_internal", "count": 15, "v0": 1.24},
    {"label": "age_30_50_external", "count": 15, "v0": 1.20},
    {"label": "over_50_internal", "count": 15, "v0": 1.14},
    {"label": "over_50_external", "count": 15, "v0": 1.10},
    {"label": "impaired_mobility", "count": 10, "v0": 0.96},
]

STAIR_ZONE_SPEED_FACTORS = {"up": 0.5, "down": 0.7}


def build_distribution_specs(direction: str) -> list[dict]:
    """Build 100 one-agent source cells inside the start room."""
    if direction not in STAIR_ZONE_SPEED_FACTORS:
        raise ValueError(f"Unknown direction: {direction}")

    x0s = np.linspace(0.3, 8.8, 10)
    y0s = np.linspace(0.3, 8.8, 10)
    cells = [(x, y) for y in y0s for x in x0s]

    specs = []
    index = 0
    for group in RIEMA_STANDARD_DEMOGRAPHIC:
        for _ in range(group["count"]):
            x0, y0 = cells[index]
            specs.append(
                {
                    "distribution_id": f"jps-distributions_{index}",
                    "group": group["label"],
                    "direction": direction,
                    "assigned_speed": float(group["v0"]),
                    "coordinates": [
                        [float(x0), float(y0)],
                        [float(x0 + 0.5), float(y0)],
                        [float(x0 + 0.5), float(y0 + 0.5)],
                        [float(x0), float(y0 + 0.5)],
                        [float(x0), float(y0)],
                    ],
                }
            )
            index += 1

    return specs


def build_raw_scenario(direction: str, seed: int = 42, max_simulation_time: float = 200.0) -> dict:
    """Build the raw scenario for either the upstairs or downstairs case."""
    distributions = {}
    for spec in build_distribution_specs(direction):
        distributions[spec["distribution_id"]] = {
            "type": "polygon",
            "group": spec["group"],
            "direction": direction,
            "coordinates": spec["coordinates"],
            "parameters": {
                "number": 1,
                "radius": 0.12,
                "v0": spec["assigned_speed"],
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }

    return {
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
        "exits": {
            "jps-exits_0": {
                "type": "polygon",
                "coordinates": EXIT_COORDINATES,
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "zones": {
            "jps-zones_0": {
                "coordinates": STAIR_ZONE_COORDINATES,
                "speed_factor": STAIR_ZONE_SPEED_FACTORS[direction],
            }
        },
        "journeys": [{"id": "jps-journeys_0", "stages": ["jps-exits_0"]}],
    }


def corbetta_envelope_bounds(direction: str, rho):
    """Return lower and upper Corbetta envelope bounds for a density array."""
    rho = np.asarray(rho, dtype=float)

    if direction == "up":
        mean_fast = 0.18 * rho**2 - 0.55 * rho + 1.02
        mean_slow = 0.01 * rho + 0.48
        std_fast = np.maximum(-0.14 * rho + 0.35, 0.0)
        std_slow = np.maximum(-0.02 * rho + 0.10, 0.0)
    elif direction == "down":
        mean_fast = 0.33 * rho**2 - 1.02 * rho + 1.39
        mean_slow = -0.08 * rho + 0.67
        std_fast = np.maximum(-0.11 * rho + 0.29, 0.0)
        std_slow = np.maximum(-0.06 * rho + 0.14, 0.0)
    else:
        raise ValueError(f"Unknown direction: {direction}")

    low = np.minimum(mean_fast - std_fast, mean_slow - std_slow)
    high = np.maximum(mean_fast + std_fast, mean_slow + std_slow)
    return low, high
