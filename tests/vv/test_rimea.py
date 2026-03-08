"""RiMEA 4.1.1 — Guideline for Microscopic Evacuation Analysis.

Annex 1: Verification tests (Tests 1–16).
Reference: https://rimeaweb.wordpress.com/wp-content/uploads/2025/09/rimea-4.1.1-d-e-1.pdf

Tests are grouped by RiMEA annex sections:
  A2 — Testing of components (Tests 1–7)
  A3 — Functional verification (Test 8)
  A4 — Qualitative verification (Tests 9–16)
  A5 — Quantitative verification (not automated)
"""

import pytest
from vv_helpers import run_vv_scenario, agents_within_bounds, HAS_JUPEDSIM

pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(not HAS_JUPEDSIM, reason="JuPedSim not installed"),
]


# ---------------------------------------------------------------------------
# A2 — Testing of Components
# ---------------------------------------------------------------------------


class TestRiMEA01SpeedCorridor:
    """RiMEA Test 1: Maintaining the specified walking speed in a corridor.

    Geometry: 2m x 40m corridor.
    Agent: 1 agent, v0=1.33 m/s.
    Expected: Travel time in [26, 34] seconds.
    """

    WALKABLE = "POLYGON ((0 0, 40 0, 40 2, 0 2, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[38, 0], [40, 0], [40, 2], [38, 2], [38, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [2, 0], [2, 2], [0, 2], [0, 0]],
            "parameters": {
                "number": 1,
                "radius": 0.15,
                "v0": 1.33,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    def test_travel_time(self):
        """Travel time at 1.33 m/s should be in [26, 34] seconds."""
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        assert metrics["agents_remaining"] == 0, "Agent did not evacuate"
        evac = metrics["evacuation_time"]
        assert 26 <= evac <= 34, (
            f"Travel time {evac:.2f}s outside RiMEA range [26, 34]"
        )

    def test_agent_stays_in_corridor(self):
        """Agent must remain within corridor bounds."""
        _, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        violations = agents_within_bounds(trajectory, 0, 0, 40, 2)
        assert not violations, "Agent left corridor:\n" + "\n".join(violations[:5])


@pytest.mark.skip(reason="Requires stair/ramp zone modeling — placeholder")
class TestRiMEA02SpeedUpStairs:
    """RiMEA Test 2: Maintaining the specified walking speed up stairs.

    Geometry: 2m x 10m staircase (measured along slope).
    Expected: Travel time consistent with defined stair speed.
    """

    def test_travel_time(self):
        pass


@pytest.mark.skip(reason="Requires stair/ramp zone modeling — placeholder")
class TestRiMEA03SpeedDownStairs:
    """RiMEA Test 3: Maintaining the specified walking speed down stairs.

    Geometry: 2m x 10m staircase (measured along slope).
    Expected: Travel time consistent with defined stair speed.
    """

    def test_travel_time(self):
        pass


@pytest.mark.skip(reason="Requires measurement areas and density sweeps — placeholder")
class TestRiMEA04FundamentalDiagram:
    """RiMEA Test 4: Measurement of the fundamental diagram.

    Geometry: 1000m x 10m corridor with 3 measuring points.
    Sweep densities: 0.5, 1, 2, 3, 4, 5, 6 P/m².
    Expected: Speed decreases with density; flow = speed * density.
    """

    def test_speed_density_relation(self):
        pass


@pytest.mark.skip(reason="Requires premovement delay support — placeholder")
class TestRiMEA05PremovementTime:
    """RiMEA Test 5: Premovement time.

    Geometry: 8m x 5m room, 1m exit.
    Agents: 10 persons, premovement U[10, 100] seconds.
    Expected: Each person starts moving at their assigned premovement time.
    """

    def test_premovement_respected(self):
        pass


class TestRiMEA06Corner:
    """RiMEA Test 6: Movement around a corner.

    Geometry: L-shaped corridor (10m x 6m area + 2m wide turn + 10m x 2m exit arm).
    Agents: 20 persons.
    Expected: All navigate the corner without passing through walls.
    """

    # L-shape from RiMEA Fig. 6: 10m horizontal, 2m wide, then 10m vertical, 2m wide
    WALKABLE = "POLYGON ((0 0, 10 0, 10 -2, 12 -2, 12 10, 10 10, 10 2, 0 2, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[10, 8], [12, 8], [12, 10], [10, 10], [10, 8]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [6, 0], [6, 2], [0, 2], [0, 0]],
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

    def test_all_evacuate(self):
        """All 20 agents navigate corner and evacuate."""
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        assert metrics["agents_remaining"] == 0, (
            f"{metrics['agents_remaining']} agents stuck at corner"
        )

    def test_agents_stay_in_geometry(self):
        """No agent passes through walls."""
        _, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        violations = agents_within_bounds(trajectory, 0, -2, 12, 10)
        assert not violations, "Agents left geometry:\n" + "\n".join(violations[:10])


@pytest.mark.skip(reason="Requires age-speed distribution mapping — placeholder")
class TestRiMEA07DemographicParams:
    """RiMEA Test 7: Allocation of demographic parameters.

    Agents: 50 adults with age-based speed distribution (Weidmann).
    Expected: Simulated speed distribution matches tabulated values.
    """

    def test_speed_distribution(self):
        pass


# ---------------------------------------------------------------------------
# A3 — Functional Verification
# ---------------------------------------------------------------------------


@pytest.mark.skip(reason="Requires multi-story building — placeholder")
class TestRiMEA08ParameterStudy:
    """RiMEA Test 8: Parameter study.

    Geometry: 3-storey test floor plan.
    Expected: Evacuation time varies monotonically with parameter changes.
    """

    def test_parameter_sensitivity(self):
        pass


# ---------------------------------------------------------------------------
# A4 — Qualitative Verification
# ---------------------------------------------------------------------------


class TestRiMEA09LargePublicSpace:
    """RiMEA Test 9: Crowd leaving a large public space.

    Geometry: 20m x 20m room.
    Step 1: 200 agents, 4 exits (1m each) with routed journeys → record time.
    Step 2: 2 exits only → evacuation time should increase.
    Uses per-exit distribution groups to ensure agents use all exits.
    """

    WALKABLE = "POLYGON ((0 0, 20 0, 20 20, 0 20, 0 0))"

    EXITS_4 = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[0, 9], [0, 11], [1, 11], [1, 9], [0, 9]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_1": {
            "type": "polygon",
            "coordinates": [[19, 9], [19, 11], [20, 11], [20, 9], [19, 9]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_2": {
            "type": "polygon",
            "coordinates": [[9, 0], [11, 0], [11, 1], [9, 1], [9, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_3": {
            "type": "polygon",
            "coordinates": [[9, 19], [11, 19], [11, 20], [9, 20], [9, 19]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
    }

    EXITS_2 = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[9, 0], [11, 0], [11, 1], [9, 1], [9, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
        "jps-exits_1": {
            "type": "polygon",
            "coordinates": [[9, 19], [11, 19], [11, 20], [9, 20], [9, 19]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        },
    }

    @staticmethod
    def _make_dist_and_journeys(exits):
        """Create per-exit distributions so agents split across exits."""
        exit_keys = list(exits.keys())
        n_per = 200 // len(exit_keys)
        distributions = {}
        journeys = []
        transitions = []
        # Quadrants for spawn areas
        quads = [(2, 2, 9, 9), (11, 2, 18, 9), (2, 11, 9, 18), (11, 11, 18, 18)]
        for i, ek in enumerate(exit_keys):
            dk = f"jps-distributions_{i}"
            q = quads[i % len(quads)]
            distributions[dk] = {
                "type": "polygon",
                "coordinates": [
                    [q[0], q[1]], [q[2], q[1]], [q[2], q[3]],
                    [q[0], q[3]], [q[0], q[1]],
                ],
                "parameters": {
                    "number": n_per,
                    "radius": 0.15,
                    "v0": 1.2,
                    "use_flow_spawning": False,
                    "distribution_mode": "by_number",
                    "radius_distribution": "constant",
                    "v0_distribution": "constant",
                },
            }
            jid = f"journey_{i}"
            journeys.append({
                "id": jid,
                "stages": [dk, ek],
                "transitions": [{"from": dk, "to": ek, "journey_id": jid}],
            })
            transitions.append({"from": dk, "to": ek, "journey_id": jid})
        return distributions, journeys, transitions

    def test_closing_exits_increases_time(self):
        """Closing 2 of 4 exits should increase evacuation time."""
        dist_4, j_4, t_4 = self._make_dist_and_journeys(self.EXITS_4)
        metrics_4, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXITS_4,
            distributions=dist_4,
            journeys=j_4,
            transitions=t_4,
            max_simulation_time=600.0,
        )
        dist_2, j_2, t_2 = self._make_dist_and_journeys(self.EXITS_2)
        metrics_2, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXITS_2,
            distributions=dist_2,
            journeys=j_2,
            transitions=t_2,
            max_simulation_time=600.0,
        )
        assert metrics_4["agents_remaining"] == 0, "4-exit: not all evacuated"
        assert metrics_2["agents_remaining"] == 0, "2-exit: not all evacuated"
        assert metrics_2["evacuation_time"] > metrics_4["evacuation_time"], (
            f"2-exit ({metrics_2['evacuation_time']:.1f}s) should be slower "
            f"than 4-exit ({metrics_4['evacuation_time']:.1f}s)"
        )


@pytest.mark.skip(reason="Requires journey-based route assignment — placeholder")
class TestRiMEA10RouteAllocation:
    """RiMEA Test 10: Allocation of escape routes.

    Geometry: Corridor with 12 adjacent rooms, 23 agents.
    Expected: Agents go to their assigned exits.
    """

    def test_agents_use_assigned_exits(self):
        pass


@pytest.mark.skip(reason="Requires route choice modeling — placeholder")
class TestRiMEA11EscapeRouteChoice:
    """RiMEA Test 11: Choice of escape route.

    Geometry: 30m x 20m room, 2 exits (1m each) on same wall.
    Agents: 1000, occupied from left side.
    Expected: Agents prefer closer exit; some use farther exit due to congestion.
    """

    def test_prefer_closer_exit(self):
        pass


class TestRiMEA12aGoalPosition:
    """RiMEA Test 12a: Position of goal cells.

    Geometry: Two rooms (10m x 10m + 10m x 10m) connected by 1m bottleneck.
    Agents: 150 in room 1.
    Vary distance a (0–10m) between bottleneck and goal.
    Expected: Evacuation time decreases as goal approaches bottleneck.
    """

    WALKABLE_TEMPLATE = "POLYGON ((0 0, 10 0, 10 4.5, 10.2 4.5, 10.2 0, 20.2 0, 20.2 10, 10.2 10, 10.2 5.5, 10 5.5, 10 10, 0 10, 0 0))"

    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]],
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

    def _make_exit(self, distance_from_bottleneck):
        """Goal positioned at distance a from the bottleneck in room 2."""
        x = 10.2 + distance_from_bottleneck
        x = min(x, 18.2)  # Keep within room 2
        return {
            "jps-exits_0": {
                "type": "polygon",
                "coordinates": [
                    [x, 0], [x + 2, 0], [x + 2, 10], [x, 10], [x, 0]
                ],
                "enable_throughput_throttling": False,
                "max_throughput": 0,
            }
        }

    def test_closer_goal_faster(self):
        """Goal near bottleneck should yield shorter evacuation than far goal."""
        metrics_near, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE_TEMPLATE,
            exits=self._make_exit(0),
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        metrics_far, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE_TEMPLATE,
            exits=self._make_exit(8),
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        assert metrics_near["agents_remaining"] == 0
        assert metrics_far["agents_remaining"] == 0
        assert metrics_near["evacuation_time"] <= metrics_far["evacuation_time"], (
            f"Near goal ({metrics_near['evacuation_time']:.1f}s) should be <= "
            f"far goal ({metrics_far['evacuation_time']:.1f}s)"
        )


class TestRiMEA12bBottleneckLength:
    """RiMEA Test 12b: Length of a bottleneck.

    Two rooms connected by 1m-wide bottleneck. Short vs long bottleneck.
    Agents: 150 in room 1.
    Expected: Longer bottleneck increases evacuation time.
    """

    # Short bottleneck: 0.2m long
    WALKABLE_SHORT = "POLYGON ((0 0, 10 0, 10 4.5, 10.2 4.5, 10.2 0, 20.2 0, 20.2 10, 10.2 10, 10.2 5.5, 10 5.5, 10 10, 0 10, 0 0))"
    # Long bottleneck: 5m long
    WALKABLE_LONG = "POLYGON ((0 0, 10 0, 10 4.5, 15 4.5, 15 0, 25 0, 25 10, 15 10, 15 5.5, 10 5.5, 10 10, 0 10, 0 0))"

    EXIT_SHORT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[18, 0], [20.2, 0], [20.2, 10], [18, 10], [18, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    EXIT_LONG = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[23, 0], [25, 0], [25, 10], [23, 10], [23, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }

    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]],
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

    def test_longer_bottleneck_slower(self):
        """Longer bottleneck should increase evacuation time."""
        metrics_short, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE_SHORT,
            exits=self.EXIT_SHORT,
            distributions=self.DIST,
            max_simulation_time=600.0,
        )
        metrics_long, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE_LONG,
            exits=self.EXIT_LONG,
            distributions=self.DIST,
            max_simulation_time=600.0,
        )
        assert metrics_short["agents_remaining"] == 0
        assert metrics_long["agents_remaining"] == 0
        assert metrics_long["evacuation_time"] > metrics_short["evacuation_time"], (
            f"Long bottleneck ({metrics_long['evacuation_time']:.1f}s) should be "
            f"slower than short ({metrics_short['evacuation_time']:.1f}s)"
        )


@pytest.mark.skip(reason="Requires flow measurement at bottlenecks — placeholder")
class TestRiMEA12cCongestionInfluence:
    """RiMEA Test 12c: Influence of congestion.

    Three rooms connected by two identical bottlenecks.
    Expected: Measure agent counts and flows over time at bottlenecks 1 and 2.
    """

    def test_congestion_at_bottlenecks(self):
        pass


class TestRiMEA12dBottleneckWidthFlow:
    """RiMEA Test 12d: Influence of bottleneck width on flow.

    Two rooms connected by long bottleneck, width varied: 0.8m, 1.0m, 1.2m.
    Expected: Flow increases with bottleneck width.
    """

    def _make_geometry(self, width):
        """Two rooms connected by a 5m-long bottleneck of given width."""
        hw = width / 2
        cy = 5.0  # center y
        return (
            f"POLYGON ((0 0, 10 0, 10 {cy - hw}, 15 {cy - hw}, "
            f"15 0, 25 0, 25 10, 15 10, 15 {cy + hw}, "
            f"10 {cy + hw}, 10 10, 0 10, 0 0))"
        )

    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[23, 0], [25, 0], [25, 10], [23, 10], [23, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]],
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

    def test_wider_bottleneck_faster(self):
        """Wider bottleneck should yield shorter evacuation time."""
        times = {}
        for w in [0.8, 1.0, 1.2]:
            metrics, _ = run_vv_scenario(
                walkable_area_wkt=self._make_geometry(w),
                exits=self.EXIT,
                distributions=self.DIST,
                max_simulation_time=600.0,
            )
            assert metrics["agents_remaining"] == 0, f"w={w}: not all evacuated"
            times[w] = metrics["evacuation_time"]

        assert times[1.2] < times[0.8], (
            f"1.2m ({times[1.2]:.1f}s) should be faster than 0.8m ({times[0.8]:.1f}s)"
        )


@pytest.mark.skip(reason="Requires stair/ramp zone modeling — placeholder")
class TestRiMEA13FundamentalDiagramStairs:
    """RiMEA Test 13: Fundamental diagram on stairs.

    Expected: Speed downstairs > speed upstairs at same density.
    """

    def test_down_faster_than_up(self):
        pass


@pytest.mark.skip(reason="Requires multi-story with route choice — placeholder")
class TestRiMEA14RouteChoice:
    """RiMEA Test 14: Choice of route.

    Multi-storey: start and target connected by stairs and corridor.
    Expected: Document whether agents take short or long route.
    """

    def test_route_choice(self):
        pass


class TestRiMEA15LargeCrowdCorner:
    """RiMEA Test 15: Movement of a large crowd around a corner.

    Three geometries: straight, corner, and long detour (same path length).
    500 agents. Corner time should be between straight and detour times.
    """

    # Straight corridor: 6m wide, 55m long
    WALKABLE_STRAIGHT = "POLYGON ((0 0, 6 0, 6 -55, 0 -55, 0 0))"
    EXIT_STRAIGHT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[0, -55], [6, -55], [6, -53], [0, -53], [0, -55]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }

    # L-shaped: horizontal 21x6 + vertical 6x35, overlapping 1m
    # Total path ~55m (20m horizontal + 35m vertical)
    WALKABLE_CORNER = "POLYGON ((26 1, 26 -34, 20 -34, 20 0, 0 0, 0 6, 21 6, 21 1, 26 1))"
    EXIT_CORNER = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[20, -34], [26, -34], [26, -32], [20, -32], [20, -34]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }

    DIST_STRAIGHT = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [6, 0], [6, -6], [0, -6], [0, 0]],
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

    DIST_CORNER = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[1, 1], [6, 1], [6, 5], [1, 5], [1, 1]],
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

    def test_corner_slower_than_straight(self):
        """Corner should slow down evacuation compared to straight path."""
        metrics_straight, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE_STRAIGHT,
            exits=self.EXIT_STRAIGHT,
            distributions=self.DIST_STRAIGHT,
            max_simulation_time=300.0,
        )
        metrics_corner, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE_CORNER,
            exits=self.EXIT_CORNER,
            distributions=self.DIST_CORNER,
            max_simulation_time=300.0,
        )
        assert metrics_straight["agents_remaining"] == 0
        assert metrics_corner["agents_remaining"] == 0
        assert metrics_corner["evacuation_time"] > metrics_straight["evacuation_time"], (
            f"Corner ({metrics_corner['evacuation_time']:.1f}s) should be slower "
            f"than straight ({metrics_straight['evacuation_time']:.1f}s)"
        )


@pytest.mark.skip(reason="Requires 1D ring/narrow corridor measurement — placeholder")
class TestRiMEA161DFundamentalDiagram:
    """RiMEA Test 16: 1D fundamental diagram.

    Ring or long narrow corridor, measure speed vs 1D density.
    Expected: Curves lie within 10/90% percentile envelope of empirical data.
    """

    def test_1d_fundamental_diagram(self):
        pass
