"""Shared helpers for V&V tests.

Build web-style scenario JSON and execute it through the standalone core runner.
"""

import json
import pathlib
import tempfile

import pytest

try:
    import pedpy
    from core.scenario import load_scenario, run_scenario

    HAS_JUPEDSIM = True
except ImportError:
    HAS_JUPEDSIM = False


def run_vv_scenario(
    walkable_area_wkt: str,
    exits: dict,
    distributions: dict,
    model_type: str = "CollisionFreeSpeedModel",
    max_simulation_time: float = 300.0,
    seed: int = 42,
    checkpoints: dict | None = None,
    journeys: list | None = None,
    transitions: list | None = None,
    model_params: dict | None = None,
) -> tuple[dict, "pedpy.TrajectoryData"]:
    """Run a V&V scenario and return (metrics, trajectory)."""
    if not HAS_JUPEDSIM:
        pytest.skip("JuPedSim not installed")

    if journeys is None and transitions is None:
        dist_keys = list(distributions.keys())
        exit_keys = list(exits.keys())
        journeys = []
        transitions = []
        for i, dk in enumerate(dist_keys):
            ek = exit_keys[i % len(exit_keys)]
            journey_id = f"journey_{i}"
            journeys.append(
                {
                    "id": journey_id,
                    "stages": [dk, ek],
                    "transitions": [{"from": dk, "to": ek, "journey_id": journey_id}],
                }
            )
            transitions.append({"from": dk, "to": ek, "journey_id": journey_id})

    config = {
        "config": {
            "simulation_settings": {
                "simulationParams": {
                    "max_simulation_time": max_simulation_time,
                    "model_type": model_type,
                    **(model_params or {}),
                },
                "numberOfSimulations": 1,
                "baseSeed": seed,
            },
            "ui_state": {"useShortestPaths": False},
        },
        "exits": exits,
        "distributions": distributions,
        "checkpoints": checkpoints or {},
        "zones": {},
        "journeys": journeys,
        "transitions": transitions,
    }

    with tempfile.TemporaryDirectory() as scenario_dir:
        scenario_path = pathlib.Path(scenario_dir)
        (scenario_path / "scenario.json").write_text(
            json.dumps(config, indent=2),
            encoding="utf-8",
        )
        (scenario_path / "walkable_area.wkt").write_text(
            walkable_area_wkt.strip(),
            encoding="utf-8",
        )

        scenario = load_scenario(str(scenario_path))
        result = run_scenario(scenario, seed=seed)

        try:
            trajectory = pedpy.load_trajectory_from_jupedsim_sqlite(
                pathlib.Path(result.sqlite_file)
            )
        except Exception:
            trajectory = None

        metrics = dict(result.metrics)
        result.cleanup()
        return metrics, trajectory


def measure_flow_rate(metrics: dict) -> float:
    """Calculate flow rate as agents_evacuated / evacuation_time."""
    evac_time = metrics.get("evacuation_time", 0)
    if evac_time <= 0:
        return 0.0
    return metrics["agents_evacuated"] / evac_time


def agents_within_bounds(
    trajectory: "pedpy.TrajectoryData",
    min_x: float,
    min_y: float,
    max_x: float,
    max_y: float,
    margin: float = 0.5,
) -> list[str]:
    """Check all agents stay within bounding box. Returns violation descriptions.

    Works with pedpy TrajectoryData (DataFrame with id, frame, x, y columns).
    """
    violations = []
    if trajectory is None:
        return violations
    df = trajectory.data
    oob = df[
        (df["x"] < min_x - margin)
        | (df["x"] > max_x + margin)
        | (df["y"] < min_y - margin)
        | (df["y"] > max_y + margin)
    ]
    for _, row in oob.iterrows():
        violations.append(
            f"Frame {int(row['frame'])}, agent {int(row['id'])}: "
            f"({row['x']:.4f}, {row['y']:.4f}) outside "
            f"[{min_x}, {max_x}] x [{min_y}, {max_y}]"
        )
    return violations
