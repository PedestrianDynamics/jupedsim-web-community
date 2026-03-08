"""Shared RiMEA 07 demographic-speed scenario helpers."""

from __future__ import annotations

from typing import Any

WALKABLE_AREA_WKT = "POLYGON ((0 0, 70 0, 70 20, 0 20, 0 0))"

AGE_GROUPS = [
    {"age_years": 20, "count": 10, "vmin": 1.60, "vmax": 1.64},
    {"age_years": 30, "count": 10, "vmin": 1.52, "vmax": 1.56},
    {"age_years": 40, "count": 10, "vmin": 1.46, "vmax": 1.50},
    {"age_years": 50, "count": 10, "vmin": 1.39, "vmax": 1.43},
    {"age_years": 60, "count": 5, "vmin": 1.27, "vmax": 1.27},
    {"age_years": 70, "count": 5, "vmin": 1.07, "vmax": 1.07},
]

_CELL_X = [0.4, 1.2]
_CELL_WIDTH = 0.6
_CELL_HEIGHT = 0.72
_CELL_AGENT_HEIGHT = 0.45
_ROWS_PER_COLUMN = 25
_Y_MARGIN = 0.5
_SPAWN_SPACING_SECONDS = 0.5
_FLOW_WINDOW_SECONDS = 1.0


def build_distribution_specs() -> list[dict[str, Any]]:
    """Build one deterministic source per agent, ordered by spawn time."""
    specs: list[dict[str, Any]] = []
    source_index = 0

    for group in AGE_GROUPS:
        count = int(group["count"])
        if count <= 1:
            speeds = [float(group["vmin"])]
        else:
            step = (float(group["vmax"]) - float(group["vmin"])) / float(count - 1)
            speeds = [float(group["vmin"]) + step * idx for idx in range(count)]

        for speed in speeds:
            column = source_index // _ROWS_PER_COLUMN
            row = source_index % _ROWS_PER_COLUMN
            x0 = _CELL_X[column]
            y0 = _Y_MARGIN + row * _CELL_HEIGHT
            flow_start = source_index * _SPAWN_SPACING_SECONDS
            flow_end = flow_start + _FLOW_WINDOW_SECONDS

            specs.append(
                {
                    "distribution_id": f"jps-distributions_{source_index}",
                    "age_years": int(group["age_years"]),
                    "assigned_speed": round(speed, 4),
                    "source_index": source_index,
                    "flow_start_time": round(flow_start, 2),
                    "flow_end_time": round(flow_end, 2),
                    "coordinates": [
                        [x0, y0],
                        [x0 + _CELL_WIDTH, y0],
                        [x0 + _CELL_WIDTH, y0 + _CELL_AGENT_HEIGHT],
                        [x0, y0 + _CELL_AGENT_HEIGHT],
                        [x0, y0],
                    ],
                }
            )
            source_index += 1

    return specs


def build_raw_scenario(seed: int = 42, max_simulation_time: float = 140.0) -> dict[str, Any]:
    """Return the RiMEA 07 scenario config as a raw JSON-like dict."""
    distributions = {}
    for spec in build_distribution_specs():
        distributions[spec["distribution_id"]] = {
            "type": "polygon",
            "age_years": spec["age_years"],
            "coordinates": spec["coordinates"],
            "parameters": {
                "number": 1,
                "radius": 0.15,
                "v0": spec["assigned_speed"],
                "use_flow_spawning": True,
                "flow_start_time": spec["flow_start_time"],
                "flow_end_time": spec["flow_end_time"],
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
                "coordinates": [
                    [69.5, 0.0],
                    [70.0, 0.0],
                    [70.0, 20.0],
                    [69.5, 20.0],
                    [69.5, 0.0],
                ],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "journeys": [{"id": "jps-journeys_0", "stages": ["jps-exits_0"]}],
    }
