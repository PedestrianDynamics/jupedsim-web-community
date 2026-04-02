"""Tier 5 — Behavior & Property Tests.

BP-02: Agents stay within walkable area bounds (all scenarios)
BP-04: All agents evacuate within max_time (all scenarios)

These are universal property checks that should hold for any valid scenario.
"""

import pytest
from vv_helpers import run_vv_scenario, agents_within_bounds, HAS_VV_DEPS

pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(
        not HAS_VV_DEPS, reason="V&V runtime dependencies not installed"
    ),
]

# Reusable scenario configs for property tests
SCENARIOS = {
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


class TestBP02AgentsInBounds:
    """All agent positions must remain within walkable area bounding box."""

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_agents_within_bounds(self, scenario_name):
        sc = SCENARIOS[scenario_name]
        _, trajectory = run_vv_scenario(
            walkable_area_wkt=sc["walkable"],
            exits=sc["exits"],
            distributions=sc["distributions"],
            max_simulation_time=300.0,
        )
        min_x, min_y, max_x, max_y = sc["bounds"]
        violations = agents_within_bounds(trajectory, min_x, min_y, max_x, max_y)
        assert not violations, (
            f"Agents left bounds in '{scenario_name}':\n" + "\n".join(violations[:10])
        )


class TestBP04AllEvacuate:
    """All agents must evacuate within max simulation time."""

    @pytest.mark.parametrize("scenario_name", list(SCENARIOS.keys()))
    def test_all_agents_evacuate(self, scenario_name):
        sc = SCENARIOS[scenario_name]
        metrics, _ = run_vv_scenario(
            walkable_area_wkt=sc["walkable"],
            exits=sc["exits"],
            distributions=sc["distributions"],
            max_simulation_time=300.0,
        )
        assert metrics["agents_remaining"] == 0, (
            f"'{scenario_name}': {metrics['agents_remaining']} agents "
            f"did not evacuate within {metrics.get('evacuation_time', 'N/A')}s"
        )
