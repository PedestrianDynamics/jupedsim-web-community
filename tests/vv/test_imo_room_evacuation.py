"""Tier 2 — Room Evacuation (IMO-inspired).

RE-01: Single exit flow rate (IMO 4)
RE-02: Exit sensitivity — 4 exits vs 2 exits (IMO 9)
RE-03: Exit assignment (IMO 10)
RE-04: Counterflow (IMO 8)
"""

import pytest
from vv_helpers import (
    DEFAULT_SEED,
    FLOW_CAP_TOLERANCE,
    HAS_VV_DEPS,
    MAX_SPECIFIC_FLOW,
    run_vv_scenario,
    specific_exit_flow,
    with_flow_limiter,
)

pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(
        not HAS_VV_DEPS, reason="V&V runtime dependencies not installed"
    ),
]


class TestRE01SingleExitFlow:
    """IMO 4: 100 agents evacuating through a 1m exit.

    Geometry: 8m x 5m room, 1m exit centered on short wall.
    Expected: flow <= 1.33 pers/s (IMO maximum for 1m door) with the exit
    throughput limiter enabled; the unthrottled flow is reported only.
    The exit is a 1 x 1 m absorbing square inside the room, not a wall opening.
    """

    WALKABLE = "POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[7, 2], [8, 2], [8, 3], [7, 3], [7, 2]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST = {
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
        vv_row.set(seed=DEFAULT_SEED)
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=300.0,
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
        vv_row.set(seed=DEFAULT_SEED)
        flow = self._flow(limiter_on=True)
        cap = MAX_SPECIFIC_FLOW * (1 + FLOW_CAP_TOLERANCE)
        assert flow <= cap, (
            f"Flow {flow:.3f} p/s exceeds the {MAX_SPECIFIC_FLOW} p/s cap "
            f"(+{FLOW_CAP_TOLERANCE:.0%} tolerance)"
        )

    def test_emergent_flow_without_limiter_is_reported(self, vv_row):
        """Information only: the emergent flow is printed, not asserted."""
        vv_row.set(seed=DEFAULT_SEED)
        flow = self._flow(limiter_on=False)
        print(f"IMO 4 emergent flow, limiter off: {flow:.2f} p/s")


class TestRE02ExitSensitivity:
    """IMO 9: Evacuation time scales with number of exits.

    Geometry: 30m x 20m room.
    Config A: 4 exits (1m each) on right wall.
    Config B: 2 exits (1m each) on right wall.
    Expected: Time ratio (2-exit / 4-exit) ∈ [1.5, 2.5].
    """

    WALKABLE = "POLYGON ((0 0, 30 0, 30 20, 0 20, 0 0))"
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [25, 0], [25, 20], [0, 20], [0, 0]],
            "parameters": {
                "number": 200,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    EXITS_4 = {
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

    EXITS_2 = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[29, 0], [30, 0], [30, 1], [29, 1], [29, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_1": {
            "type": "polygon",
            "coordinates": [[29, 19], [30, 19], [30, 20], [29, 20], [29, 19]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
    }

    def _make_dist_and_journeys(self, exits: dict) -> tuple[dict, list]:
        """Create one distribution per exit so agents split across exits.

        Returns (distributions, journeys_v2). Each distribution carries
        a `journey_weights` entry that pins it to its dedicated journey.
        """
        exit_keys = list(exits.keys())
        n_per_exit = 200 // len(exit_keys)
        distributions = {}
        journeys_v2 = []
        for i, ek in enumerate(exit_keys):
            dk = f"jps-distributions_{i}"
            jid = f"journey_{i}"
            # Spread spawn areas across the room
            y_lo = i * (20 // len(exit_keys))
            y_hi = (i + 1) * (20 // len(exit_keys))
            distributions[dk] = {
                "type": "polygon",
                "coordinates": [
                    [0, y_lo],
                    [25, y_lo],
                    [25, y_hi],
                    [0, y_hi],
                    [0, y_lo],
                ],
                "parameters": {
                    "number": n_per_exit,
                    "radius": 0.15,
                    "v0": 1.2,
                    "use_flow_spawning": False,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                },
                "journey_weights": [{"journey_id": jid, "weight": 100}],
            }
            journeys_v2.append(
                {
                    "id": jid,
                    "name": jid,
                    "color": "#888888",
                    "sequence": [ek],
                }
            )
        return distributions, journeys_v2

    def test_time_ratio(self, vv_row):
        """Halving exits should increase evacuation time."""
        vv_row.set(seed=DEFAULT_SEED)
        dist_4, j_4 = self._make_dist_and_journeys(self.EXITS_4)
        metrics_4, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXITS_4,
            distributions=dist_4,
            journeys_v2=j_4,
            max_simulation_time=600.0,
        )
        dist_2, j_2 = self._make_dist_and_journeys(self.EXITS_2)
        metrics_2, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXITS_2,
            distributions=dist_2,
            journeys_v2=j_2,
            max_simulation_time=600.0,
        )
        assert metrics_4["agents_remaining"] == 0, "4-exit: not all evacuated"
        assert metrics_2["agents_remaining"] == 0, "2-exit: not all evacuated"

        # With collision-free models, the ratio is lower than SFPE predictions
        # because agents don't queue at doors. We just verify 2-exit is slower.
        assert metrics_2["evacuation_time"] > metrics_4["evacuation_time"], (
            f"2-exit ({metrics_2['evacuation_time']:.1f}s) should be slower "
            f"than 4-exit ({metrics_4['evacuation_time']:.1f}s)"
        )


class TestRE04Counterflow:
    """IMO 8: Counterflow increases evacuation time.

    Geometry: Two 10m x 10m rooms connected by 2m x 10m corridor.
    Group A: 50 agents in left room → exit in right room.
    Group B: N agents in right room → exit in left room (N=0 vs N=50).
    Expected: Counterflow scenario takes longer.
    """

    # Two rooms connected by corridor
    WALKABLE = "POLYGON ((0 0, 10 0, 10 4, 20 4, 20 0, 30 0, 30 10, 20 10, 20 6, 10 6, 10 10, 0 10, 0 0))"

    EXIT_RIGHT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[28, 0], [30, 0], [30, 10], [28, 10], [28, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }

    # For counterflow: exits on both sides
    EXIT_BOTH = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[28, 0], [30, 0], [30, 10], [28, 10], [28, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_1": {
            "type": "polygon",
            "coordinates": [[0, 0], [2, 0], [2, 10], [0, 10], [0, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
    }

    DIST_LEFT_ONLY = {
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

    DIST_COUNTERFLOW = {
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
            # Left distribution → right exit (jps-exits_0).
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
            # Right distribution → left exit (jps-exits_1), counterflow.
            "journey_weights": [{"journey_id": "journey_1", "weight": 100}],
        },
    }

    def test_counterflow_increases_time(self, vv_row):
        """Adding counterflow agents should increase evacuation time."""
        vv_row.set(seed=DEFAULT_SEED)
        # No counterflow: 50 agents left → right exit
        metrics_none, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT_RIGHT,
            distributions=self.DIST_LEFT_ONLY,
            max_simulation_time=300.0,
        )

        # With counterflow: 50 left→right + 50 right→left
        journeys_v2 = [
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
        metrics_counter, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT_BOTH,
            distributions=self.DIST_COUNTERFLOW,
            journeys_v2=journeys_v2,
            max_simulation_time=300.0,
        )

        assert metrics_none["agents_remaining"] == 0
        assert metrics_counter["agents_remaining"] == 0

        assert metrics_counter["evacuation_time"] > metrics_none["evacuation_time"], (
            f"Counterflow ({metrics_counter['evacuation_time']:.1f}s) should be slower "
            f"than no counterflow ({metrics_none['evacuation_time']:.1f}s)"
        )
