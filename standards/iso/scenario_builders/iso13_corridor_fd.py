"""
ISO 20414:2020 Test 13 - Relationship between flow rate, density and walking
speeds in a corridor.

A corridor of area 120 m^2 (2 m wide, 60 m long) is divided into three equal
zones. Agents are uniformly distributed over the whole corridor and move to the
exit on the right. The occupant count sets the initial density:

    60 -> 0.5,  120 -> 1.0,  240 -> 2.0,  360 -> 3.0,  480 -> 4.0  [p/m^2]

Walking speed in zone 2 is measured between line A (x = 20) and line B (x = 40);
density is measured in zone 2; the line-B flow is recorded. The expected result
is the classic speed-down / flow-hump fundamental diagram.

Source: ISO 20414:2020(E) Table 14 (Test 13).
"""
from __future__ import annotations

CORRIDOR_WIDTH = 2.0
CORRIDOR_LENGTH = 60.0
CORRIDOR_AREA = CORRIDOR_WIDTH * CORRIDOR_LENGTH  # 120 m^2

LINE_A_X = 20.0   # zone 1 / zone 2 boundary
LINE_B_X = 40.0   # zone 2 / zone 3 boundary
ZONE2_LENGTH = LINE_B_X - LINE_A_X            # 20 m
ZONE2_AREA = ZONE2_LENGTH * CORRIDOR_WIDTH    # 40 m^2

# occupant count -> initial corridor density [p/m^2]
DENSITY_CASES = {60: 0.5, 120: 1.0, 240: 2.0, 360: 3.0, 480: 4.0}

WALKING_SPEED = 1.0
AGENT_RADIUS = 0.15
JOURNEY_ID = "jps-journeys_0"

CORRIDOR_WKT = (
    f"POLYGON ((0 0, {CORRIDOR_LENGTH:g} 0, "
    f"{CORRIDOR_LENGTH:g} {CORRIDOR_WIDTH:g}, 0 {CORRIDOR_WIDTH:g}, 0 0))"
)


def build_raw_scenario(n_agents: int, seed: int = 42, max_simulation_time: float = 200.0) -> dict:
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
        "distributions": {
            "jps-distributions_0": {
                "type": "polygon",
                "coordinates": [
                    [0.3, 0.3], [CORRIDOR_LENGTH - 0.3, 0.3],
                    [CORRIDOR_LENGTH - 0.3, CORRIDOR_WIDTH - 0.3],
                    [0.3, CORRIDOR_WIDTH - 0.3], [0.3, 0.3],
                ],
                "parameters": {
                    "number": int(n_agents),
                    "radius": AGENT_RADIUS,
                    "v0": WALKING_SPEED,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                    "use_flow_spawning": False,
                },
                "journey_weights": [{"journey_id": JOURNEY_ID, "weight": 100}],
            }
        },
        "exits": {
            "jps-exits_0": {
                "type": "polygon",
                "coordinates": [
                    [CORRIDOR_LENGTH - 0.3, 0.0], [CORRIDOR_LENGTH, 0.0],
                    [CORRIDOR_LENGTH, CORRIDOR_WIDTH],
                    [CORRIDOR_LENGTH - 0.3, CORRIDOR_WIDTH],
                    [CORRIDOR_LENGTH - 0.3, 0.0],
                ],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "journeys_v2": [
            {
                "id": JOURNEY_ID,
                "name": JOURNEY_ID,
                "color": "#a855f7",
                "sequence": ["jps-exits_0"],
            }
        ],
    }


def measure_zone2(traj_df, frame_rate: float) -> dict:
    """Mean zone-2 speed (line A -> line B), peak zone-2 density, line-B flow."""
    import numpy as np

    speeds = []
    for _, sub in traj_df.sort_values(["id", "frame"]).groupby("id"):
        a_rows = sub[sub.x >= LINE_A_X]
        b_rows = sub[sub.x >= LINE_B_X]
        # Agents spawned at or past line A never cross it; timing them from
        # their first frame would overstate their zone-2 speed.
        if sub.iloc[0].x >= LINE_A_X or a_rows.empty or b_rows.empty:
            continue
        t_a = a_rows.iloc[0].frame / frame_rate
        t_b = b_rows.iloc[0].frame / frame_rate
        if t_b > t_a:
            speeds.append(ZONE2_LENGTH / (t_b - t_a))

    # peak zone-2 density over the run
    in_zone2 = traj_df[(traj_df.x >= LINE_A_X) & (traj_df.x < LINE_B_X)]
    if len(in_zone2):
        per_frame = in_zone2.groupby("frame")["id"].nunique()
        peak_density = per_frame.max() / ZONE2_AREA
        mean_density = per_frame.mean() / ZONE2_AREA
    else:
        peak_density = mean_density = float("nan")

    # line-B flow: first crossings of x = LINE_B_X
    crossings = []
    for _, sub in traj_df.sort_values(["id", "frame"]).groupby("id"):
        before = sub[sub.x < LINE_B_X]
        after = sub[sub.x >= LINE_B_X]
        if len(before) and len(after):
            crossings.append(after.iloc[0].frame / frame_rate)
    crossings.sort()
    if len(crossings) >= 2 and (crossings[-1] - crossings[0]) > 0:
        flow_b = (len(crossings) - 1) / (crossings[-1] - crossings[0])
    else:
        flow_b = float("nan")

    return {
        "mean_speed_zone2_m_s": float(np.mean(speeds)) if speeds else float("nan"),
        "n_speed_samples": len(speeds),
        "peak_density_zone2_p_m2": float(peak_density),
        "mean_density_zone2_p_m2": float(mean_density),
        "flow_line_b_p_s": float(flow_b),
        "specific_flow_line_b_p_m_s": float(flow_b) / CORRIDOR_WIDTH if flow_b == flow_b else float("nan"),
    }
