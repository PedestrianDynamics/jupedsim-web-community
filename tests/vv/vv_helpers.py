"""Shared helpers for V&V tests.

Build web-style scenario JSON and execute it through the standalone core runner.
"""

import json
import pathlib
import tempfile

import pytest

try:
    import pedpy
    from jupedsim_scenarios import load_scenario, run_scenario

    HAS_VV_DEPS = True
except ImportError:
    HAS_VV_DEPS = False

# Backward-compatible alias for older imports.
HAS_JUPEDSIM = HAS_VV_DEPS

DEFAULT_SEED = 42


def run_vv_scenario(
    walkable_area_wkt: str,
    exits: dict,
    distributions: dict,
    model_type: str = "CollisionFreeSpeedModel",
    max_simulation_time: float = 300.0,
    seed: int = DEFAULT_SEED,
    checkpoints: dict | None = None,
    journeys_v2: list | None = None,
    model_params: dict | None = None,
) -> tuple[dict, "pedpy.TrajectoryData"]:
    """Run a V&V scenario and return (metrics, trajectory)."""
    if not HAS_VV_DEPS:
        pytest.skip("V&V runtime dependencies not installed")

    # Default route assignment: pair each distribution with one exit
    # (round-robin if there are more distributions than exits) via a
    # dedicated journey. Callers can override by passing `journeys_v2`
    # together with per-distribution `journey_weights` directly.
    if journeys_v2 is None:
        if not exits:
            raise ValueError("At least one exit is required to build default journeys")
        # If a caller pre-populated journey_weights on any distribution but
        # didn't pass journeys_v2, the intent is ambiguous: appending the
        # defaults would silently route agents through multiple journeys at
        # once. Reject the input so the test fails loudly instead of
        # producing nondeterministic results.
        conflicting = [
            dk for dk, dv in distributions.items() if dv.get("journey_weights")
        ]
        if conflicting:
            raise ValueError(
                "run_vv_scenario was asked to build default journeys, but these "
                f"distributions already carry journey_weights: {conflicting}. "
                "Pass journeys_v2 explicitly when overriding the default routing."
            )

        dist_keys = list(distributions.keys())
        exit_keys = list(exits.keys())
        journeys_v2 = []
        # Build a fresh distributions dict so journey_weights only ever lives
        # on the copies we own — the caller's input dicts stay untouched.
        import copy as _copy

        distributions = {k: _copy.deepcopy(v) for k, v in distributions.items()}
        for i, dk in enumerate(dist_keys):
            ek = exit_keys[i % len(exit_keys)]
            journey_id = f"journey_{i}"
            journeys_v2.append(
                {
                    "id": journey_id,
                    "name": journey_id,
                    "color": "#888888",
                    "sequence": [ek],
                }
            )
            distributions[dk]["journey_weights"] = [
                {"journey_id": journey_id, "weight": 100}
            ]

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
        "journeys_v2": journeys_v2,
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


def agents_outside_polygon(
    trajectory: "pedpy.TrajectoryData",
    walkable_area_wkt: str,
    tolerance: float = 0.001,
) -> list[str]:
    """Check every agent centre lies in the walkable area. Returns violations.

    Unlike agents_within_bounds, this detects cutting through an inner wall
    of a non-convex geometry such as a corner.
    """
    from shapely import wkt
    from shapely.geometry import Point

    if trajectory is None:
        return []
    walkable = wkt.loads(walkable_area_wkt).buffer(tolerance)
    df = trajectory.data
    return [
        f"Frame {int(row.frame)}, agent {int(row.id)}: "
        f"({row.x:.4f}, {row.y:.4f}) outside the walkable area"
        for row in df.itertuples()
        if not walkable.covers(Point(row.x, row.y))
    ]
