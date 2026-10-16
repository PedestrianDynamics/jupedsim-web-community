"""IMO MSC.1/Circ.1533 verification and validation tests.

This module provides one ordered view of IMO Tests 1 through 11. The existing
implementations are copied here under IMO-numbered class names so this file is
self-contained.

The original ``test_imo_*.py`` files are intentionally retained. The generic
behavior/property checks follow the numbered IMO cases at the end of this
module because they are useful across all IMO-inspired scenarios but do not
correspond to an individual IMO test number.
"""

import io
import json
import pathlib
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout
from typing import ClassVar

import numpy as np
import pytest
from jupedsim_scenarios import load_scenario, run_scenario
from shapely.geometry import Point, Polygon
from vv_helpers import (
    DEFAULT_SEED,
    FLOW_CAP_TOLERANCE,
    HAS_VV_DEPS,
    MAX_SPECIFIC_FLOW,
    agents_outside_polygon,
    agents_within_bounds,
    run_vv_scenario,
    specific_exit_flow,
    with_flow_limiter,
)

STANDARDS_DIR = pathlib.Path(__file__).resolve().parents[2] / "standards"
if str(STANDARDS_DIR / "rimea") not in sys.path:
    sys.path.insert(0, str(STANDARDS_DIR / "rimea"))
if str(STANDARDS_DIR) not in sys.path:
    sys.path.insert(0, str(STANDARDS_DIR))

from scenario_builders.rimea16_loop import (
    DEFAULT_AGENT_COUNT,
    build_loop_scenario,
    compute_density_speed_curve,
    compute_density_speed_samples,
    compute_lap_counts,
    load_reference_band,
    summarize_reference_fit,
)

pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(
        not HAS_VV_DEPS, reason="V&V runtime dependencies not installed"
    ),
]


# ---------------------------------------------------------------------------
# IMO 1 - Walking speed in a corridor
# ---------------------------------------------------------------------------


class TestIMO01WalkingSpeedCorridor:
    """Single agent traverses a straight corridor at its assigned speed."""

    WALKABLE = "POLYGON ((0 0, 40 0, 40 2, 0 2, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[38, 0], [40, 0], [40, 2], [38, 2], [38, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
            "parameters": {
                "number": 1,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    def test_evacuation_time(self, vv_row):
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        assert metrics["agents_remaining"] == 0, "Agent did not evacuate"
        expected_time = 38.0 / 1.2
        tolerance = 0.20
        evacuation_time = metrics["evacuation_time"]
        vv_row.set(
            quantity="evacuation_time", unit="s",
            lower=expected_time * (1 - tolerance),
            upper=expected_time * (1 + tolerance),
            measured=evacuation_time, n=metrics["total_agents"], seed=42,
            model="CollisionFreeSpeedModel",
            rule=(
                "bound: agents_remaining = 0; 38/1.2 * (1 - 0.20) <= measured "
                "<= 38/1.2 * (1 + 0.20)"
            ),
        )
        assert (
            expected_time * (1 - tolerance)
            <= evacuation_time
            <= expected_time * (1 + tolerance)
        ), (
            f"Evacuation time {evacuation_time:.2f}s outside "
            f"{expected_time:.1f}s +/- {tolerance * 100}%"
        )

    def test_agent_stays_in_corridor(self, vv_row):
        metrics, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        violations = agents_outside_polygon(
            trajectory,
            self.WALKABLE,
            radius=self.DIST["jps-distributions_0"]["parameters"]["radius"],
        )
        vv_row.set(
            quantity="out_of_bounds_samples", unit="count", lower=0, upper=0,
            measured=len(violations), n=metrics["total_agents"], seed=42,
            model="CollisionFreeSpeedModel",
            rule="bound: 0 <= measured <= 0 (samples outside the walkable area)",
        )
        assert not violations, "Agent left corridor:\n" + "\n".join(violations[:5])


# ---------------------------------------------------------------------------
# IMO 2 - Walking speed up stairs
# ---------------------------------------------------------------------------


class TestIMO02WalkingSpeedUpstairs:
    """IMO Test 2: Maintaining the specified walking speed up stairs.

    IMO MSC.1/Circ.1533 Annex 3: one person on a stair 2 m wide and 10 m
    long along the incline, walking at 1 m/s, covers the distance in 10 s.
    The stair is a flat speed-factor zone (0.5), so the desired speed is
    set to 1 / 0.5 to give 1 m/s on the stair. Direction is not modelled.
    """

    STAIR_SPEED_FACTOR = 0.5

    WALKABLE = "POLYGON ((0 0, 10.4 0, 10.4 2, 0 2, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            # Exit deliberately widened to 20 cm (was 5 cm) so the
            # direct-steering arrival waypoint lands inside the polygon.
            # See jupedsim-scenarios#15.
            "coordinates": [
                [10.20, 0.8],
                [10.4, 0.8],
                [10.4, 1.2],
                [10.20, 1.2],
                [10.20, 0.8],
            ],
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0.0, 0.75], [0.5, 0.75], [0.5, 1.25], [0.0, 1.25], [0.0, 0.75]],
            "parameters": {
                "number": 1,
                "radius": 0.15,
                "v0": 2.0,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
            "journey_weights": [{"journey_id": "jps-journeys_0", "weight": 100}],
        }
    }
    ZONES: ClassVar[dict] = {
        "jps-zones_0": {
            "coordinates": [[0, 0], [10.4, 0], [10.4, 2], [0, 2], [0, 0]],
            "speed_factor": STAIR_SPEED_FACTOR,
        }
    }
    JOURNEYS_V2: ClassVar[list] = [
        {
            "id": "jps-journeys_0",
            "name": "jps-journeys_0",
            "color": "#888888",
            "sequence": ["jps-exits_0"],
        }
    ]

    def test_travel_time(self, vv_row):
        """1 m/s on the 10 m stair gives about 10 s."""
        raw = {
            "config": {
                "simulation_settings": {
                    "baseSeed": 42,
                    "simulationParams": {
                        "model_type": "CollisionFreeSpeedModel",
                        "max_simulation_time": 60,
                    },
                }
            },
            "distributions": self.DIST,
            "exits": self.EXIT,
            "zones": self.ZONES,
            "journeys_v2": self.JOURNEYS_V2,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(self.WALKABLE, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)
            evac = result.evacuation_time
            total_agents = result.total_agents
            result.cleanup()

        vv_row.set(
            quantity="stair_travel_time", unit="s", lower=9.5, upper=10.5,
            measured=evac, n=total_agents, seed=42,
            model="CollisionFreeSpeedModel",
            rule="bound: 9.5 <= measured <= 10.5",
        )
        # ~10 m at v0 * speed_factor = 1 m/s -> ~10 s.
        assert 9.5 <= evac <= 10.5, (
            f"Stair travel time {evac:.2f}s outside expected range [9.5, 10.5]"
        )


# ---------------------------------------------------------------------------
# IMO 3 - Walking speed down stairs
# ---------------------------------------------------------------------------


class TestIMO03WalkingSpeedDownstairs:
    """IMO Test 3: Maintaining the specified walking speed down stairs.

    IMO MSC.1/Circ.1533 Annex 3: one person on a stair 2 m wide and 10 m
    long along the incline, walking at 1 m/s, covers the distance in 10 s.
    The stair is a flat speed-factor zone (0.75), so the desired speed is
    set to 1 / 0.75 to give 1 m/s on the stair. Direction is not modelled.
    """

    STAIR_SPEED_FACTOR = 0.75

    WALKABLE = "POLYGON ((0 0, 10.4 0, 10.4 2, 0 2, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            # Exit deliberately widened to 20 cm (was 5 cm) so the
            # direct-steering arrival waypoint lands inside the polygon.
            # See jupedsim-scenarios#15.
            "coordinates": [
                [10.20, 0.8],
                [10.4, 0.8],
                [10.4, 1.2],
                [10.20, 1.2],
                [10.20, 0.8],
            ],
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0.0, 0.75], [0.5, 0.75], [0.5, 1.25], [0.0, 1.25], [0.0, 0.75]],
            "parameters": {
                "number": 1,
                "radius": 0.15,
                "v0": 1.0 / 0.75,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
            "journey_weights": [{"journey_id": "jps-journeys_0", "weight": 100}],
        }
    }
    ZONES: ClassVar[dict] = {
        "jps-zones_0": {
            "coordinates": [[0, 0], [10.4, 0], [10.4, 2], [0, 2], [0, 0]],
            "speed_factor": STAIR_SPEED_FACTOR,
        }
    }
    JOURNEYS_V2: ClassVar[list] = [
        {
            "id": "jps-journeys_0",
            "name": "jps-journeys_0",
            "color": "#888888",
            "sequence": ["jps-exits_0"],
        }
    ]

    def test_travel_time(self, vv_row):
        """1 m/s on the 10 m stair gives about 10 s."""
        raw = {
            "config": {
                "simulation_settings": {
                    "baseSeed": 42,
                    "simulationParams": {
                        "model_type": "CollisionFreeSpeedModel",
                        "max_simulation_time": 60,
                    },
                }
            },
            "distributions": self.DIST,
            "exits": self.EXIT,
            "zones": self.ZONES,
            "journeys_v2": self.JOURNEYS_V2,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(self.WALKABLE, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)
            evac = result.evacuation_time
            total_agents = result.total_agents
            result.cleanup()

        vv_row.set(
            quantity="stair_travel_time", unit="s", lower=9.5, upper=10.5,
            measured=evac, n=total_agents, seed=42,
            model="CollisionFreeSpeedModel",
            rule="bound: 9.5 <= measured <= 10.5",
        )
        # ~10 m at v0 * speed_factor = 1 m/s -> ~10 s.
        assert 9.5 <= evac <= 10.5, (
            f"Stair travel time {evac:.2f}s outside expected range [9.5, 10.5]"
        )


# ---------------------------------------------------------------------------
# IMO 4 - Exit flow rate
# ---------------------------------------------------------------------------


class TestIMO04ExitFlowRate:
    """One hundred agents evacuate through a 1 m exit.

    IMO: the exit flow must not exceed 1.33 p/s. The run enables the exit
    throughput limiter at that value; the unthrottled flow is reported only.
    The exit is a 1 x 1 m absorbing square inside the room, not a wall opening.
    """

    WALKABLE = "POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[7, 2], [8, 2], [8, 3], [7, 3], [7, 2]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [6, 0], [6, 5], [0, 5], [0, 0]],
            "parameters": {
                "number": 100,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    def test_all_evacuate(self, vv_row):
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        vv_row.set(
            quantity="agents_remaining", unit="count", lower=0, upper=0,
            measured=metrics["agents_remaining"], n=metrics["total_agents"],
            seed=42, model="CollisionFreeSpeedModel",
            rule="bound: total_agents = 100; 0 <= measured <= 0",
        )
        assert metrics["total_agents"] == 100
        assert metrics["agents_remaining"] == 0

    def _flow(self, limiter_on):
        metrics, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=with_flow_limiter(self.EXIT, limiter_on, MAX_SPECIFIC_FLOW),
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        assert metrics["agents_remaining"] == 0, "not all agents evacuated"
        return specific_exit_flow(trajectory.data, trajectory.frame_rate)

    def test_flow_within_cap_with_limiter(self, vv_row):
        """Flow must not exceed 1.33 p/s with the exit limiter enabled."""
        flow = self._flow(limiter_on=True)
        cap = MAX_SPECIFIC_FLOW * (1 + FLOW_CAP_TOLERANCE)
        vv_row.set(
            quantity="specific_exit_flow_limiter_on", unit="p/m/s", upper=cap,
            measured=flow, n=100, seed=42, model="CollisionFreeSpeedModel",
            rule=(
                f"bound: measured <= {MAX_SPECIFIC_FLOW} * (1 + "
                f"{FLOW_CAP_TOLERANCE}); exit limiter on, 1 m exit"
            ),
        )
        assert flow <= cap, (
            f"Flow {flow:.3f} p/s exceeds the {MAX_SPECIFIC_FLOW} p/s cap "
            f"(+{FLOW_CAP_TOLERANCE:.0%} tolerance)"
        )

    def test_emergent_flow_without_limiter_is_reported(self, vv_row):
        """Information only: the emergent flow is printed, not asserted."""
        flow = self._flow(limiter_on=False)
        vv_row.set(
            quantity="specific_exit_flow_limiter_off", unit="p/m/s",
            measured=flow, n=100, seed=42, model="CollisionFreeSpeedModel",
            rule="information only: emergent flow, not asserted",
        )
        print(f"IMO 4 emergent flow, limiter off: {flow:.2f} p/s")


# ---------------------------------------------------------------------------
# IMO 5 - Response time
# ---------------------------------------------------------------------------


class TestIMO05ResponseTime:
    """IMO Test 5: Agents respect assigned pre-movement response times.

    Geometry: 8m x 5m room, 1m exit.
    Agents: 10 persons, pre-movement U[10, 100] seconds.
    """

    WALKABLE = "POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[0, 2], [1, 2], [1, 3], [0, 3], [0, 2]],
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[5.5, 1.0], [7.5, 1.0], [7.5, 4.0], [5.5, 4.0], [5.5, 1.0]],
            "parameters": {
                "number": 10,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
                "use_premovement": True,
                "premovement_distribution": "uniform",
                "premovement_param_a": 10.0,
                "premovement_param_b": 100.0,
                "premovement_seed": 12345,
            },
            "journey_weights": [{"journey_id": "jps-journeys_0", "weight": 100}],
        }
    }
    JOURNEYS_V2: ClassVar[list] = [
        {
            "id": "jps-journeys_0",
            "name": "jps-journeys_0",
            "color": "#888888",
            "sequence": ["jps-exits_0"],
        }
    ]

    def test_premovement_respected(self, vv_row):
        raw = {
            "config": {
                "simulation_settings": {
                    "baseSeed": 42,
                    "simulationParams": {
                        "model_type": "CollisionFreeSpeedModel",
                        "max_simulation_time": 180,
                    },
                }
            },
            "distributions": self.DIST,
            "exits": self.EXIT,
            "journeys_v2": self.JOURNEYS_V2,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(self.WALKABLE, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)
            frame_rate = result.frame_rate
            df = result.trajectory_dataframe().sort_values(["id", "frame"]).copy()
            result.cleanup()

        expected_times = np.random.default_rng(12345).uniform(10.0, 100.0, 10)
        agent_ids = sorted(df["id"].unique())
        assert len(agent_ids) == 10, f"Expected 10 agents, got {len(agent_ids)}"

        movement_start = {}
        for agent_id in agent_ids:
            agent_df = df[df["id"] == agent_id].copy()
            start_x = float(agent_df.iloc[0]["x"])
            start_y = float(agent_df.iloc[0]["y"])
            displacement = np.hypot(agent_df["x"] - start_x, agent_df["y"] - start_y)
            moved = agent_df[displacement > 0.05]
            assert not moved.empty, f"Agent {agent_id} never started moving"
            movement_start[agent_id] = float(moved.iloc[0]["frame"]) / frame_rate

        observed = np.array([movement_start[agent_id] for agent_id in agent_ids])
        vv_row.set(
            quantity="movement_start_time", unit="s", lower=9.9, upper=101.0,
            measured=observed.tolist(), n=len(agent_ids), seed=42,
            premovement_seed=12345, model="CollisionFreeSpeedModel",
            rule=(
                "bound: 9.9 <= measured <= 101.0; comparison: per-agent measured "
                "vs that agent's premovement sample (premovement_seed), "
                "max abs delta <= 0.5 s"
            ),
        )
        assert np.all(observed >= 9.9), f"Observed movement before 10s: {observed}"
        assert np.all(observed <= 101.0), (
            f"Observed movement after expected window: {observed}"
        )

        # ScenarioResult does not expose the per-agent assignment. The runner
        # adds agents in spawn order (ids ascend with the spawn index) and
        # gives spawn index i the i-th draw of the uniform premovement
        # sample, so the i-th smallest agent id owns expected_times[i].
        deltas = np.abs(observed - expected_times)
        assert np.all(deltas <= 0.5), (
            "Observed movement start times do not match each agent's own "
            f"premovement delay. Expected={expected_times}, observed={observed}, "
            f"deltas={deltas}"
        )


# ---------------------------------------------------------------------------
# IMO 6 - Rounding corners
# ---------------------------------------------------------------------------


class TestIMO06RoundingCorners:
    """Twenty agents navigate a 90-degree corner."""

    WALKABLE = "POLYGON ((0 0, 10 0, 10 -2, 12 -2, 12 10, 10 10, 10 2, 0 2, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[10, 8], [12, 8], [12, 10], [10, 10], [10, 8]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [4, 0], [4, 2], [0, 2], [0, 0]],
            "parameters": {
                "number": 20,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    def test_all_evacuate(self, vv_row):
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        vv_row.set(
            quantity="agents_remaining", unit="count", lower=0, upper=0,
            measured=metrics["agents_remaining"], n=metrics["total_agents"],
            seed=42, model="CollisionFreeSpeedModel",
            rule="bound: 0 <= measured <= 0",
        )
        assert metrics["agents_remaining"] == 0, (
            f"{metrics['agents_remaining']} agents stuck at corner"
        )

    def test_agents_stay_in_geometry(self, vv_row):
        metrics, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        violations = agents_outside_polygon(
            trajectory,
            self.WALKABLE,
            radius=self.DIST["jps-distributions_0"]["parameters"]["radius"],
        )
        vv_row.set(
            quantity="out_of_bounds_samples", unit="count", lower=0, upper=0,
            measured=len(violations), n=metrics["total_agents"], seed=42,
            model="CollisionFreeSpeedModel",
            rule="bound: 0 <= measured <= 0 (samples outside the walkable area)",
        )
        assert not violations, "Agents left L-corridor:\n" + "\n".join(violations[:10])


# ---------------------------------------------------------------------------
# IMO 7 - Assignment of population demographics
# ---------------------------------------------------------------------------


class TestIMO07PopulationDemographics:
    """Fifty agents with assigned movement parameters evacuate successfully."""

    WALKABLE = "POLYGON ((0 0, 40 0, 40 10, 0 10, 0 0))"
    EXIT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[38, 0], [40, 0], [40, 10], [38, 10], [38, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [4, 0], [4, 10], [0, 10], [0, 0]],
            "parameters": {
                "number": 50,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    def test_all_evacuate(self, vv_row):
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        vv_row.set(
            quantity="agents_remaining", unit="count", lower=0, upper=0,
            measured=metrics["agents_remaining"], n=metrics["total_agents"],
            seed=42, model="CollisionFreeSpeedModel",
            rule="bound: total_agents = 50; 0 <= measured <= 0",
        )
        assert metrics["total_agents"] == 50
        assert metrics["agents_remaining"] == 0, (
            f"{metrics['agents_remaining']} agents did not evacuate"
        )

    def test_evacuation_time_reasonable(self, vv_row):
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        evacuation_time = metrics["evacuation_time"]
        vv_row.set(
            quantity="evacuation_time", unit="s", lower=25, upper=120,
            measured=evacuation_time, n=metrics["total_agents"], seed=42,
            model="CollisionFreeSpeedModel",
            rule="bound: 25 <= measured <= 120",
        )
        assert 25 <= evacuation_time <= 120, (
            f"Evacuation time {evacuation_time:.2f}s outside [25, 120]"
        )


# ---------------------------------------------------------------------------
# IMO 8 - Counterflow
# ---------------------------------------------------------------------------


class TestIMO08Counterflow:
    """Counterflow increases evacuation time relative to one-way movement."""

    WALKABLE = (
        "POLYGON ((0 0, 10 0, 10 4, 20 4, 20 0, 30 0, 30 10, "
        "20 10, 20 6, 10 6, 10 10, 0 10, 0 0))"
    )
    EXIT_RIGHT: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[28, 0], [30, 0], [30, 10], [28, 10], [28, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    EXIT_BOTH: ClassVar[dict] = {
        **EXIT_RIGHT,
        "jps-exits_1": {
            "type": "polygon",
            "coordinates": [[0, 0], [2, 0], [2, 10], [0, 10], [0, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
    }
    DIST_LEFT_ONLY: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [8, 0], [8, 10], [0, 10], [0, 0]],
            "parameters": {
                "number": 20,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }
    DIST_COUNTERFLOW: ClassVar[dict] = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [8, 0], [8, 10], [0, 10], [0, 0]],
            "parameters": {
                "number": 20,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
            "journey_weights": [{"journey_id": "journey_0", "weight": 100}],
        },
        "jps-distributions_1": {
            "type": "polygon",
            "coordinates": [[22, 0], [30, 0], [30, 10], [22, 10], [22, 0]],
            "parameters": {
                "number": 20,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
            "journey_weights": [{"journey_id": "journey_1", "weight": 100}],
        },
    }

    def test_counterflow_increases_time(self, vv_row):
        metrics_without, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT_RIGHT,
            distributions=self.DIST_LEFT_ONLY,
            max_simulation_time=300.0,
        )
        journeys = [
            {
                "id": "journey_0",
                "name": "journey_0",
                "color": "#888888",
                "sequence": ["jps-exits_0"],
            },
            {
                "id": "journey_1",
                "name": "journey_1",
                "color": "#888888",
                "sequence": ["jps-exits_1"],
            },
        ]
        metrics_counterflow, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT_BOTH,
            distributions=self.DIST_COUNTERFLOW,
            journeys_v2=journeys,
            max_simulation_time=300.0,
        )
        vv_row.set(
            quantity="evacuation_time_counterflow_vs_one_way", unit="s",
            measured=[
                metrics_counterflow["evacuation_time"],
                metrics_without["evacuation_time"],
            ],
            n=metrics_counterflow["total_agents"], seed=42,
            model="CollisionFreeSpeedModel",
            rule=(
                "bound: agents_remaining = 0 in both runs; comparison: "
                "measured[0] (counterflow, 40 agents) > measured[1] "
                "(one-way, 20 agents)"
            ),
        )
        assert metrics_without["agents_remaining"] == 0
        assert metrics_counterflow["agents_remaining"] == 0
        assert (
            metrics_counterflow["evacuation_time"] > metrics_without["evacuation_time"]
        ), (
            f"Counterflow ({metrics_counterflow['evacuation_time']:.1f}s) "
            f"should be slower than no counterflow "
            f"({metrics_without['evacuation_time']:.1f}s)"
        )


# ---------------------------------------------------------------------------
# IMO 9 - Exit-count sensitivity
# ---------------------------------------------------------------------------


class TestIMO09ExitCountSensitivity:
    """Reducing the number of available exits increases evacuation time."""

    WALKABLE = "POLYGON ((0 0, 30 0, 30 20, 0 20, 0 0))"
    EXITS_4: ClassVar[dict] = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[29, 0], [30, 0], [30, 1], [29, 1], [29, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_1": {
            "type": "polygon",
            "coordinates": [[29, 5], [30, 5], [30, 6], [29, 6], [29, 5]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_2": {
            "type": "polygon",
            "coordinates": [[29, 14], [30, 14], [30, 15], [29, 15], [29, 14]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_3": {
            "type": "polygon",
            "coordinates": [[29, 19], [30, 19], [30, 20], [29, 20], [29, 19]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
    }
    EXITS_2: ClassVar[dict] = {
        "jps-exits_0": EXITS_4["jps-exits_0"],
        "jps-exits_1": EXITS_4["jps-exits_3"],
    }

    @staticmethod
    def _make_distributions_and_journeys(exits):
        exit_keys = list(exits)
        number_per_exit = 200 // len(exit_keys)
        distributions = {}
        journeys = []
        for index, exit_key in enumerate(exit_keys):
            distribution_key = f"jps-distributions_{index}"
            journey_id = f"journey_{index}"
            y_low = index * (20 // len(exit_keys))
            y_high = (index + 1) * (20 // len(exit_keys))
            distributions[distribution_key] = {
                "type": "polygon",
                "coordinates": [
                    [0, y_low],
                    [25, y_low],
                    [25, y_high],
                    [0, y_high],
                    [0, y_low],
                ],
                "parameters": {
                    "number": number_per_exit,
                    "radius": 0.15,
                    "v0": 1.2,
                    "use_flow_spawning": False,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                },
                "journey_weights": [{"journey_id": journey_id, "weight": 100}],
            }
            journeys.append(
                {
                    "id": journey_id,
                    "name": journey_id,
                    "color": "#888888",
                    "sequence": [exit_key],
                }
            )
        return distributions, journeys

    def test_time_ratio(self, vv_row):
        distributions_4, journeys_4 = self._make_distributions_and_journeys(
            self.EXITS_4
        )
        metrics_4, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXITS_4,
            distributions=distributions_4,
            journeys_v2=journeys_4,
            max_simulation_time=600.0,
        )
        distributions_2, journeys_2 = self._make_distributions_and_journeys(
            self.EXITS_2
        )
        metrics_2, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXITS_2,
            distributions=distributions_2,
            journeys_v2=journeys_2,
            max_simulation_time=600.0,
        )
        vv_row.set(
            quantity="evacuation_time_4_exits_vs_2_exits", unit="s",
            measured=[metrics_4["evacuation_time"], metrics_2["evacuation_time"]],
            n=metrics_4["total_agents"], seed=42,
            model="CollisionFreeSpeedModel",
            rule=(
                "bound: agents_remaining = 0 in both runs; comparison: "
                "measured[1] (2 exits) > measured[0] (4 exits)"
            ),
        )
        assert metrics_4["agents_remaining"] == 0, "4-exit: not all evacuated"
        assert metrics_2["agents_remaining"] == 0, "2-exit: not all evacuated"
        assert metrics_2["evacuation_time"] > metrics_4["evacuation_time"], (
            f"2-exit ({metrics_2['evacuation_time']:.1f}s) should be slower "
            f"than 4-exit ({metrics_4['evacuation_time']:.1f}s)"
        )


# ---------------------------------------------------------------------------
# IMO 10 - Exit route allocation
# ---------------------------------------------------------------------------


class TestIMO10ExitRouteAllocation:
    """IMO Test 10: Agents follow their assigned escape routes.

    Geometry: Corridor with 12 adjacent rooms, 23 agents.
    Expected: Agents go to their assigned exits.
    """

    SCENARIO_ZIP = STANDARDS_DIR / "rimea" / "scenario_files" / "Rimea-10.zip"

    def _load_raw(self):
        with zipfile.ZipFile(self.SCENARIO_ZIP) as zf:
            return json.loads(zf.read("config.json"))

    def _agent_to_distribution(self, trajectory, distributions):
        first_positions = (
            trajectory.data.sort_values(["id", "frame"])
            .groupby("id")
            .first()
            .reset_index()
        )
        distribution_polygons = {
            key: Polygon(value["coordinates"]) for key, value in distributions.items()
        }
        agent_to_distribution = {}
        for row in first_positions.itertuples():
            point = Point(row.x, row.y)
            for distribution_id, polygon in distribution_polygons.items():
                if polygon.covers(point):
                    agent_to_distribution[row.id] = distribution_id
                    break
        return agent_to_distribution

    def _agent_to_actual_exit(self, trajectory, exits):
        last_positions = (
            trajectory.data.sort_values(["id", "frame"])
            .groupby("id")
            .last()
            .reset_index()
        )
        exit_polygons = {
            key: Polygon(value["coordinates"]) for key, value in exits.items()
        }
        agent_to_exit = {}
        for row in last_positions.itertuples():
            point = Point(row.x, row.y)
            agent_to_exit[row.id] = min(
                exit_polygons,
                key=lambda exit_id: exit_polygons[exit_id].distance(point),
            )
        return agent_to_exit

    def test_agents_use_assigned_exits(self, vv_row):
        import pedpy

        raw = self._load_raw()
        scenario = load_scenario(str(self.SCENARIO_ZIP))
        result = run_scenario(scenario, seed=42)

        assert result.agents_remaining == 0

        trajectory = pedpy.TrajectoryData(
            result.trajectory_dataframe()[["id", "frame", "x", "y"]].copy(),
            frame_rate=result.frame_rate,
        )

        agent_to_distribution = self._agent_to_distribution(
            trajectory, raw["distributions"]
        )
        journey_by_id = {journey["id"]: journey for journey in raw["journeys_v2"]}
        distribution_to_exit = {
            distribution_id: journey_by_id[
                distribution["journey_weights"][0]["journey_id"]
            ]["sequence"][-1]
            for distribution_id, distribution in raw["distributions"].items()
            if distribution.get("journey_weights")
        }
        expected_agent_exit = {
            agent_id: distribution_to_exit[distribution_id]
            for agent_id, distribution_id in agent_to_distribution.items()
        }
        actual_agent_exit = self._agent_to_actual_exit(trajectory, raw["exits"])

        assert len(expected_agent_exit) == result.total_agents
        assert set(expected_agent_exit) == set(actual_agent_exit)

        mismatches = {
            agent_id: (expected_agent_exit[agent_id], actual_agent_exit[agent_id])
            for agent_id in expected_agent_exit
            if expected_agent_exit[agent_id] != actual_agent_exit[agent_id]
        }
        vv_row.set(
            quantity="wrong_exit_agents", unit="count", lower=0, upper=0,
            measured=len(mismatches), n=result.total_agents, seed=42,
            model="CollisionFreeSpeedModel",
            scenario_file="standards/rimea/scenario_files/Rimea-10.zip",
            rule="bound: 0 <= measured <= 0 (agents at a non-assigned exit)",
        )
        assert not mismatches, f"Agents reached wrong exits: {mismatches}"

        result.cleanup()


# ---------------------------------------------------------------------------
# IMO 11 - 1D speed-density curve on a ring
# ---------------------------------------------------------------------------


class TestIMO11RingFundamentalDiagram:
    """IMO Test 11 checked against the RiMEA Test 16 ring fundamental diagram.

    Runs a loop at three desired speeds and measures speed versus 1D density.
    Checks: at least 75% of baseline samples lie inside the reference 10/90
    percentile band; the slower run has a lower mean speed than the baseline;
    at least 75% of faster-run samples lie above the 90th percentile.
    This is a consistency check against a reference band, not a validation.
    """

    def test_1d_fundamental_diagram(self, vv_row):
        reference = load_reference_band()
        runs = {}
        for label, desired_speed in {
            "slower": 0.9,
            "baseline": 1.2,
            "faster": 1.5,
        }.items():
            scenario, geometry = build_loop_scenario(
                label=f"imo11-{label}",
                desired_speed=desired_speed,
            )
            with redirect_stdout(io.StringIO()):
                result = run_scenario(scenario, seed=42)
            trajectory_df = result.trajectory_dataframe()[["id", "frame", "x", "y"]]
            lap_counts = compute_lap_counts(
                trajectory_df=trajectory_df,
                centerline=geometry.centerline,
                track_length=geometry.track_length,
            )
            assert (lap_counts["completed_laps"] >= 3).all(), (
                f"All agents should complete at least 3 laps, got "
                f"{lap_counts['completed_laps'].tolist()}"
            )
            samples = compute_density_speed_samples(
                trajectory_df=trajectory_df,
                frame_rate=result.frame_rate,
                centerline=geometry.centerline,
                track_length=geometry.track_length,
            )
            curve = compute_density_speed_curve(samples)
            runs[label] = summarize_reference_fit(curve, reference)
            result.cleanup()

        baseline = runs["baseline"]
        slower = runs["slower"]
        faster = runs["faster"]

        vv_row.set(
            quantity="baseline_fraction_inside_reference_band", unit="fraction",
            lower=0.75,
            measured={
                "baseline_inside_band": float(baseline["inside_band"].mean()),
                "slower_mean_speed_mps": float(slower["speed_mps"].mean()),
                "baseline_mean_speed_mps": float(baseline["speed_mps"].mean()),
                "faster_above_p90": float(faster["above_p90"].mean()),
            },
            n=DEFAULT_AGENT_COUNT, seed=42, model="CollisionFreeSpeedModel",
            rule=(
                "bound: baseline_inside_band >= 0.75; comparison: "
                "slower_mean_speed_mps < baseline_mean_speed_mps; bound: "
                "faster_above_p90 >= 0.75"
            ),
        )
        assert baseline["inside_band"].mean() >= 0.75, (
            f"Baseline curve should mostly lie within the reference band, got "
            f"{baseline['inside_band'].mean():.2%} inside"
        )
        assert float(slower["speed_mps"].mean()) < float(
            baseline["speed_mps"].mean()
        ), (
            f"Lower desired speed should shift the curve down, got "
            f"slower={slower['speed_mps'].mean():.3f}, "
            f"baseline={baseline['speed_mps'].mean():.3f}"
        )
        assert faster["above_p90"].mean() >= 0.75, (
            f"Higher desired speed should push the curve above the 90th "
            f"percentile band, got {faster['above_p90'].mean():.2%} above"
        )


# ---------------------------------------------------------------------------
# Additional behavior/property checks (not individual IMO test numbers)
# ---------------------------------------------------------------------------


BEHAVIOR_SCENARIOS = {
    "small_room": {
        "walkable": "POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))",
        "bounds": (0, 0, 8, 5),
        "exits": {
            "jps-exits_0": {
                "type": "polygon",
                "coordinates": [[7, 2], [8, 2], [8, 3], [7, 3], [7, 2]],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "distributions": {
            "jps-distributions_0": {
                "type": "polygon",
                "coordinates": [[0, 0], [5, 0], [5, 5], [0, 5], [0, 0]],
                "parameters": {
                    "number": 30,
                    "radius": 0.15,
                    "v0": 1.2,
                    "use_flow_spawning": False,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                },
            }
        },
    },
    "narrow_corridor": {
        "walkable": "POLYGON ((0 0, 30 0, 30 2, 0 2, 0 0))",
        "bounds": (0, 0, 30, 2),
        "exits": {
            "jps-exits_0": {
                "type": "polygon",
                "coordinates": [[28, 0], [30, 0], [30, 2], [28, 2], [28, 0]],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "distributions": {
            "jps-distributions_0": {
                "type": "polygon",
                "coordinates": [[0, 0], [4, 0], [4, 2], [0, 2], [0, 0]],
                "parameters": {
                    "number": 15,
                    "radius": 0.15,
                    "v0": 1.2,
                    "use_flow_spawning": False,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                },
            }
        },
    },
    "large_room": {
        "walkable": "POLYGON ((0 0, 30 0, 30 20, 0 20, 0 0))",
        "bounds": (0, 0, 30, 20),
        "exits": {
            "jps-exits_0": {
                "type": "polygon",
                "coordinates": [[28, 9], [30, 9], [30, 11], [28, 11], [28, 9]],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        },
        "distributions": {
            "jps-distributions_0": {
                "type": "polygon",
                "coordinates": [[0, 0], [25, 0], [25, 20], [0, 20], [0, 0]],
                "parameters": {
                    "number": 100,
                    "radius": 0.15,
                    "v0": 1.2,
                    "use_flow_spawning": False,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                },
            }
        },
    },
}


class TestIMOBehaviorAgentsInBounds:
    """All agents remain inside each scenario's walkable bounds."""

    @pytest.mark.parametrize("scenario_name", list(BEHAVIOR_SCENARIOS))
    def test_agents_within_bounds(self, scenario_name, vv_row):
        vv_row.set(seed=DEFAULT_SEED)
        scenario = BEHAVIOR_SCENARIOS[scenario_name]
        _, trajectory = run_vv_scenario(
            walkable_area_wkt=scenario["walkable"],
            exits=scenario["exits"],
            distributions=scenario["distributions"],
            max_simulation_time=300.0,
        )
        violations = agents_within_bounds(trajectory, *scenario["bounds"])
        assert not violations, (
            f"Agents left bounds in '{scenario_name}':\n" + "\n".join(violations[:10])
        )


class TestIMOBehaviorAllEvacuate:
    """All agents evacuate within each scenario's simulation budget."""

    @pytest.mark.parametrize("scenario_name", list(BEHAVIOR_SCENARIOS))
    def test_all_agents_evacuate(self, scenario_name, vv_row):
        vv_row.set(seed=DEFAULT_SEED)
        scenario = BEHAVIOR_SCENARIOS[scenario_name]
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=scenario["walkable"],
            exits=scenario["exits"],
            distributions=scenario["distributions"],
            max_simulation_time=300.0,
        )
        assert metrics["agents_remaining"] == 0, (
            f"'{scenario_name}': {metrics['agents_remaining']} agents "
            f"did not evacuate within {metrics.get('evacuation_time', 'N/A')}s"
        )
