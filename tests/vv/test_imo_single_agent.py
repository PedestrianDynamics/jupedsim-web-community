"""Tier 1 — Single Agent Mechanics (IMO-inspired).

SA-01: Speed in corridor (IMO 1)
SA-02: Speed distribution (IMO 7)
SA-04: Cornering (IMO 6)

Note: SA-03 (pre-movement delay) deferred to later phase.
"""

import pytest
from vv_helpers import run_vv_scenario, agents_within_bounds, HAS_VV_DEPS

pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(
        not HAS_VV_DEPS, reason="V&V runtime dependencies not installed"
    ),
]


class TestSA01SpeedInCorridor:
    """IMO 1: Single agent traverses a straight corridor.

    Geometry: 2m x 40m corridor with 2m exit zone on the right.
    Agent: 1 agent, v0=1.2 m/s (JuPedSim default free speed).
    Expected: Evacuation time ≈ distance / speed, within 20% tolerance.
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
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }

    def test_evacuation_time(self):
        """Single agent should traverse ~38m at ~1.2 m/s → ~32s ± 20%."""
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        assert metrics["agents_remaining"] == 0, "Agent did not evacuate"
        expected_time = 38.0 / 1.2  # ~31.7s
        tolerance = 0.20
        evac = metrics["evacuation_time"]
        assert (
            expected_time * (1 - tolerance) <= evac <= expected_time * (1 + tolerance)
        ), (
            f"Evacuation time {evac:.2f}s outside {expected_time:.1f}s ± {tolerance * 100}%"
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


class TestSA02SpeedDistribution:
    """IMO 7: Multiple agents with varying speeds.

    Geometry: 40m x 10m room with 2m exit on right.
    Agents: 50 agents, v0 uniformly distributed via different start positions.
    Expected: Evacuation time spread reflects speed variation.
    """

    WALKABLE = "POLYGON ((0 0, 40 0, 40 10, 0 10, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[38, 0], [40, 0], [40, 10], [38, 10], [38, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST = {
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

    def test_all_evacuate(self):
        """All 50 agents must evacuate."""
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        assert metrics["total_agents"] == 50
        assert metrics["agents_remaining"] == 0, (
            f"{metrics['agents_remaining']} agents did not evacuate"
        )

    def test_evacuation_time_reasonable(self):
        """Evacuation time should be between 25s and 120s for 50 agents."""
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=300.0,
        )
        evac = metrics["evacuation_time"]
        assert 25 <= evac <= 120, f"Evacuation time {evac:.2f}s outside [25, 120]"


class TestSA04Cornering:
    """IMO 6: Agents navigate a 90-degree corner.

    Geometry: L-shaped corridor (2m wide) with a right-angle turn.
    Agents: 20 agents.
    Expected: All agents stay within geometry and evacuate.
    """

    # L-shaped: horizontal 10m then vertical 10m, both 2m wide
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

    def test_all_evacuate(self):
        """All 20 agents must navigate the corner and evacuate."""
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
        """No agent should leave the L-shaped corridor bounds."""
        _, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=120.0,
        )
        # Use bounding box of the L-shape: x∈[0,12], y∈[-2,10]
        violations = agents_within_bounds(trajectory, 0, -2, 12, 10)
        assert not violations, "Agents left L-corridor:\n" + "\n".join(violations[:10])
