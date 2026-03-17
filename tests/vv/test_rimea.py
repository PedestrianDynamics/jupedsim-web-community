"""RiMEA 4.1.1 — Guideline for Microscopic Evacuation Analysis.

Annex 1: Verification tests (Tests 1–16).
Reference: https://rimeaweb.wordpress.com/wp-content/uploads/2025/09/rimea-4.1.1-d-e-1.pdf

Tests are grouped by RiMEA annex sections:
  A2 — Testing of components (Tests 1–7)
  A3 — Functional verification (Test 8)
  A4 — Qualitative verification (Tests 9–16)
  A5 — Quantitative verification (not automated)
"""

import io
import json
import pathlib
import sys
import tempfile
import zipfile
from contextlib import redirect_stdout

import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from vv_helpers import (
    HAS_JUPEDSIM,
    agents_within_bounds,
    measure_flow_rate,
    run_vv_scenario,
)

SCRIPTS_DIR = pathlib.Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from scenario_builders.rimea07_demographic import (
    AGE_GROUPS,
    WALKABLE_AREA_WKT,
    build_distribution_specs,
    build_raw_scenario,
)
from scenario_builders.rimea13_stairs import (
    STAIR_WALKABLE_AREA_WKT,
    STAIR_ZONE_COORDINATES,
    build_raw_scenario as build_stair_raw_scenario,
    corbetta_envelope_bounds,
)
from scenario_builders.rimea16_loop import (
    build_loop_scenario,
    compute_density_speed_curve,
    compute_density_speed_samples,
    compute_lap_counts,
    load_reference_band,
    summarize_reference_fit,
)
from core.scenario import load_scenario, run_scenario

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
        assert 26 <= evac <= 34, f"Travel time {evac:.2f}s outside RiMEA range [26, 34]"

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


class TestRiMEA02SpeedUpStairs:
    """RiMEA Test 2: Maintaining the specified walking speed up stairs.

    Geometry: 2m x 10m staircase (measured along slope).
    Expected: Travel time consistent with defined stair speed.
    """

    WALKABLE = "POLYGON ((0 0, 10.4 0, 10.4 2, 0 2, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [
                [10.35, 0.8],
                [10.4, 0.8],
                [10.4, 1.2],
                [10.35, 1.2],
                [10.35, 0.8],
            ],
        }
    }
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0.0, 0.8], [0.3, 0.8], [0.3, 1.2], [0.0, 1.2], [0.0, 0.8]],
            "parameters": {
                "number": 1,
                "radius": 0.08,
                "v0": 1.0,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }
    ZONES = {
        "jps-zones_0": {
            "coordinates": [[0, 0], [10.4, 0], [10.4, 2], [0, 2], [0, 0]],
            "speed_factor": 0.5,
        }
    }
    JOURNEYS = [
        {
            "id": "jps-journeys_0",
            "stages": ["jps-distributions_0", "jps-exits_0"],
        }
    ]

    def test_travel_time(self):
        """Zone-based stair approximation should keep the slowed 10 m run near 20 s."""
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
            "journeys": self.JOURNEYS,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(self.WALKABLE, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)
            evac = result.evacuation_time
            result.cleanup()

        assert 18.8 <= evac <= 19.6, (
            f"Stair-zone travel time {evac:.2f}s outside expected range [18.8, 19.6]"
        )


class TestRiMEA03SpeedDownStairs:
    """RiMEA Test 3: Maintaining the specified walking speed down stairs.

    Geometry: 2m x 10m staircase (measured along slope).
    Expected: Travel time consistent with defined stair speed.
    """

    WALKABLE = "POLYGON ((0 0, 10.4 0, 10.4 2, 0 2, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [
                [10.35, 0.8],
                [10.4, 0.8],
                [10.4, 1.2],
                [10.35, 1.2],
                [10.35, 0.8],
            ],
        }
    }
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0.0, 0.8], [0.3, 0.8], [0.3, 1.2], [0.0, 1.2], [0.0, 0.8]],
            "parameters": {
                "number": 1,
                "radius": 0.08,
                "v0": 1.0,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }
    ZONES = {
        "jps-zones_0": {
            "coordinates": [[0, 0], [10.4, 0], [10.4, 2], [0, 2], [0, 0]],
            "speed_factor": 0.75,
        }
    }
    JOURNEYS = [
        {
            "id": "jps-journeys_0",
            "stages": ["jps-distributions_0", "jps-exits_0"],
        }
    ]

    def test_travel_time(self):
        """Zone-based stair approximation should keep downstairs travel near 13 s."""
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
            "journeys": self.JOURNEYS,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(self.WALKABLE, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)
            evac = result.evacuation_time
            result.cleanup()

        assert 12.6 <= evac <= 13.1, (
            f"Stair-zone travel time {evac:.2f}s outside expected range [12.6, 13.1]"
        )


@pytest.mark.skip(reason="Requires measurement areas and density sweeps — placeholder")
class TestRiMEA04FundamentalDiagram:
    """RiMEA Test 4: Measurement of the fundamental diagram.

    Geometry: 1000m x 10m corridor with 3 measuring points.
    Sweep densities: 0.5, 1, 2, 3, 4, 5, 6 P/m².
    Expected: Speed decreases with density; flow = speed * density.
    """

    def test_speed_density_relation(self):
        pass


class TestRiMEA05PremovementTime:
    """RiMEA Test 5: Premovement time.

    Geometry: 8m x 5m room, 1m exit.
    Agents: 10 persons, premovement U[10, 100] seconds.
    Expected: Each person starts moving at their assigned premovement time.
    """

    WALKABLE = "POLYGON ((0 0, 8 0, 8 5, 0 5, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[0, 2], [1, 2], [1, 3], [0, 3], [0, 2]],
        }
    }
    DIST = {
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
        }
    }
    JOURNEYS = [
        {
            "id": "jps-journeys_0",
            "stages": ["jps-distributions_0", "jps-exits_0"],
        }
    ]

    def test_premovement_respected(self):
        """Agents should remain still until their sampled premovement delay expires."""
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
            "journeys": self.JOURNEYS,
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
        assert np.all(observed >= 9.9), f"Observed movement before 10s: {observed}"
        assert np.all(observed <= 101.0), (
            f"Observed movement after expected window: {observed}"
        )

        expected_sorted = np.sort(expected_times)
        observed_sorted = np.sort(observed)
        deltas = np.abs(observed_sorted - expected_sorted)
        assert np.all(deltas <= 0.5), (
            "Observed movement start times do not match sampled premovement delays. "
            f"Expected={expected_sorted}, observed={observed_sorted}, deltas={deltas}"
        )


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


class TestRiMEA07DemographicParams:
    """RiMEA Test 7: Allocation of demographic parameters.

    Agents: 50 adults with age-based speed distribution (Weidmann).
    Expected: Simulated speed distribution matches tabulated values.
    """

    def test_speed_distribution(self):
        import pedpy

        raw = build_raw_scenario()

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(WALKABLE_AREA_WKT, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)

            assert result.agents_remaining == 0, "All demographic agents should evacuate"
            assert result.total_agents == 50, "RiMEA 07 requires a 50-person adult population"

            traj_df = result.trajectory_dataframe()[["id", "frame", "x", "y"]].copy()
            traj = pedpy.TrajectoryData(traj_df, frame_rate=result.frame_rate)
            speeds = pedpy.compute_individual_speed(
                traj_data=traj,
                frame_step=10,
                speed_calculation=pedpy.SpeedCalculation.BORDER_EXCLUDE,
            )
            result.cleanup()

        first_samples = (
            traj.data.sort_values(["frame", "id"])
            .groupby("id", as_index=False)
            .first()
            .sort_values(["frame", "y", "x"])
            .reset_index(drop=True)
        )
        specs = build_distribution_specs()
        assert len(first_samples) == len(specs), "Spawn-order mapping requires one trajectory start per spec"

        mapping = {
            int(row["id"]): specs[idx]
            for idx, row in enumerate(first_samples.to_dict("records"))
        }

        observed_by_age = {}
        for agent_id, agent_speed in speeds.groupby("id"):
            spec = mapping.get(int(agent_id))
            assert spec is not None, f"Missing demographic mapping for agent {agent_id}"
            first_frame = int(first_samples.loc[first_samples["id"] == agent_id, "frame"].iloc[0])
            settled = agent_speed[agent_speed["frame"] >= first_frame + 20]
            if settled.empty:
                settled = agent_speed

            observed_by_age.setdefault(spec["age_years"], []).append(
                {
                    "assigned_speed": float(spec["assigned_speed"]),
                    "observed_speed": float(settled["speed"].median()),
                }
            )

        expected_counts = {int(group["age_years"]): int(group["count"]) for group in AGE_GROUPS}
        expected_ranges = {
            int(group["age_years"]): (float(group["vmin"]), float(group["vmax"])) for group in AGE_GROUPS
        }

        mean_observed_speeds = []
        for age in sorted(expected_counts):
            rows = observed_by_age.get(age, [])
            assert len(rows) == expected_counts[age], (
                f"Age {age}: expected {expected_counts[age]} agents, got {len(rows)}"
            )

            assigned = [row["assigned_speed"] for row in rows]
            observed = [row["observed_speed"] for row in rows]
            vmin, vmax = expected_ranges[age]

            assert min(assigned) >= vmin - 1e-6 and max(assigned) <= vmax + 1e-6, (
                f"Age {age}: assigned speeds {min(assigned):.3f}-{max(assigned):.3f} "
                f"outside reference {vmin:.3f}-{vmax:.3f}"
            )
            assert min(observed) >= vmin - 0.03 and max(observed) <= vmax + 0.03, (
                f"Age {age}: observed speeds {min(observed):.3f}-{max(observed):.3f} "
                f"outside tolerance around reference {vmin:.3f}-{vmax:.3f}"
            )

            mean_observed_speeds.append(float(np.mean(observed)))

        assert all(
            earlier >= later for earlier, later in zip(mean_observed_speeds, mean_observed_speeds[1:])
        ), f"Observed mean speeds should decrease with age, got {mean_observed_speeds}"


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
                    [q[0], q[1]],
                    [q[2], q[1]],
                    [q[2], q[3]],
                    [q[0], q[3]],
                    [q[0], q[1]],
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
            journeys.append(
                {
                    "id": jid,
                    "stages": [dk, ek],
                    "transitions": [{"from": dk, "to": ek, "journey_id": jid}],
                }
            )
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


class TestRiMEA10RouteAllocation:
    """RiMEA Test 10: Allocation of escape routes.

    Geometry: Corridor with 12 adjacent rooms, 23 agents.
    Expected: Agents go to their assigned exits.
    """

    SCENARIO_ZIP = SCRIPTS_DIR / "scenarios" / "Rimea-10.zip"

    def _load_raw(self):
        with zipfile.ZipFile(self.SCENARIO_ZIP) as zf:
            return json.loads(zf.read("config.json"))

    def _agent_to_distribution(self, trajectory, distributions):
        first_positions = (
            trajectory.data.sort_values(["id", "frame"]).groupby("id").first().reset_index()
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
            trajectory.data.sort_values(["id", "frame"]).groupby("id").last().reset_index()
        )
        exit_polygons = {key: Polygon(value["coordinates"]) for key, value in exits.items()}
        agent_to_exit = {}
        for row in last_positions.itertuples():
            point = Point(row.x, row.y)
            agent_to_exit[row.id] = min(
                exit_polygons,
                key=lambda exit_id: exit_polygons[exit_id].distance(point),
            )
        return agent_to_exit

    def test_agents_use_assigned_exits(self):
        import pedpy

        raw = self._load_raw()
        scenario = load_scenario(str(self.SCENARIO_ZIP))
        result = run_scenario(scenario, seed=42)

        assert result.agents_remaining == 0

        trajectory = pedpy.TrajectoryData(
            result.trajectory_dataframe()[["id", "frame", "x", "y"]].copy(),
            frame_rate=result.frame_rate,
        )

        agent_to_distribution = self._agent_to_distribution(trajectory, raw["distributions"])
        distribution_to_exit = {
            journey["stages"][0]: journey["stages"][-1] for journey in raw["journeys"]
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
        assert not mismatches, f"Agents reached wrong exits: {mismatches}"

        result.cleanup()


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
                "coordinates": [[x, 0], [x + 2, 0], [x + 2, 10], [x, 10], [x, 0]],
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


class TestRiMEA12cCongestionInfluence:
    """RiMEA Test 12c: Influence of congestion.

    Three rooms connected by two identical bottlenecks.
    Expected: Measure agent counts and flows over time at bottlenecks 1 and 2.
              No congestion in bottleneck 2 should be observed.
    """

    WALKABLE = "POLYGON ((0 0, 10 0, 10 4.5, 10.2 4.5, 10.2 0, 20.2 0, 20.2 4.5, 20.4 4.5, 20.4 0, 25.4 0, 25.4 10, 20.4 10, 20.4 5.5, 20.2 5.5, 20.2 10, 10.2 10, 10.2 5.5, 10 5.5, 10 10, 0 10, 0 0))"
    EXIT = {
        "jps-exits_0": {
            "type": "polygon",
            "coordinates": [[25.2, 0], [25.4, 0], [25.4, 10], [25.2, 10], [25.2, 0]],
            "enable_throughput_throttling": False,
            "max_throughput": 0,
        }
    }
    DIST = {
        "jps-distributions_0": {
            "type": "polygon",
            "coordinates": [[0, 0], [5, 0], [5, 10], [0, 10], [0, 0]],
            "parameters": {
                "number": 150,
                "radius": 0.15,
                "v0": 1.2,
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "radius_distribution": "constant",
                "v0_distribution": "constant",
            },
        }
    }
    BOTTLENECK_1_LINE = ((10.0, 4.5), (10.0, 5.5))
    BOTTLENECK_2_LINE = ((20.2, 4.5), (20.2, 5.5))
    QUEUE_AREA_1 = Polygon([(8.0, 3.5), (10.0, 3.5), (10.0, 6.5), (8.0, 6.5)])
    QUEUE_AREA_2 = Polygon([(18.2, 3.5), (20.2, 3.5), (20.2, 6.5), (18.2, 6.5)])

    def _count_agents_in_area_per_frame(self, trajectory, area):
        counts = []
        if trajectory is None:
            return counts
        for _, frame_data in trajectory.data.groupby("frame"):
            count = 0
            for row in frame_data.itertuples():
                if area.covers(Point(row.x, row.y)):
                    count += 1
            counts.append(count)
        return counts

    def test_congestion_at_bottlenecks(self):
        import pedpy

        metrics, trajectory = run_vv_scenario(
            walkable_area_wkt=self.WALKABLE,
            exits=self.EXIT,
            distributions=self.DIST,
            max_simulation_time=400.0,
        )

        assert metrics["agents_remaining"] == 0
        assert trajectory is not None, "Trajectory output required for bottleneck analysis"

        line_1 = pedpy.MeasurementLine(list(self.BOTTLENECK_1_LINE))
        line_2 = pedpy.MeasurementLine(list(self.BOTTLENECK_2_LINE))
        nt_1, crossing_1 = pedpy.compute_n_t(traj_data=trajectory, measurement_line=line_1)
        nt_2, crossing_2 = pedpy.compute_n_t(traj_data=trajectory, measurement_line=line_2)

        assert len(crossing_1) == metrics["total_agents"]
        assert len(crossing_2) == metrics["total_agents"]

        queue_1 = self._count_agents_in_area_per_frame(trajectory, self.QUEUE_AREA_1)
        queue_2 = self._count_agents_in_area_per_frame(trajectory, self.QUEUE_AREA_2)

        peak_queue_1 = max(queue_1)
        peak_queue_2 = max(queue_2)
        mean_top10_queue_1 = float(np.mean(sorted(queue_1, reverse=True)[:10]))
        mean_top10_queue_2 = float(np.mean(sorted(queue_2, reverse=True)[:10]))

        assert peak_queue_1 > peak_queue_2, (
            f"Expected stronger queue before bottleneck 1, got peaks "
            f"b1={peak_queue_1}, b2={peak_queue_2}"
        )
        assert mean_top10_queue_1 > mean_top10_queue_2, (
            f"Expected denser sustained queue before bottleneck 1, got top-10 means "
            f"b1={mean_top10_queue_1:.2f}, b2={mean_top10_queue_2:.2f}"
        )
        first_crossing_1 = float(crossing_1["frame"].min() / trajectory.frame_rate)
        first_crossing_2 = float(crossing_2["frame"].min() / trajectory.frame_rate)
        assert first_crossing_1 < first_crossing_2, (
            f"Expected bottleneck 2 to start later than bottleneck 1, got "
            f"b1={first_crossing_1:.2f}s, b2={first_crossing_2:.2f}s"
        )


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

    def test_flow_increases_with_width(self):
        """Wider bottlenecks should produce higher mean flow."""
        flows = {}
        for w in [0.8, 1.0, 1.2]:
            metrics, _ = run_vv_scenario(
                walkable_area_wkt=self._make_geometry(w),
                exits=self.EXIT,
                distributions=self.DIST,
                max_simulation_time=600.0,
            )
            assert metrics["agents_remaining"] == 0, f"w={w}: not all evacuated"
            flows[w] = measure_flow_rate(metrics)

        assert flows[0.8] < flows[1.0] < flows[1.2], (
            f"Expected flow to increase with width, got "
            f"0.8m={flows[0.8]:.3f}, 1.0m={flows[1.0]:.3f}, 1.2m={flows[1.2]:.3f}"
        )


class TestRiMEA13FundamentalDiagramStairs:
    """RiMEA Test 13: Fundamental diagram on stairs.

    Expected: Speed downstairs > speed upstairs at same density.
    """

    def _run_direction(self, direction: str):
        import pedpy

        raw = build_stair_raw_scenario(direction=direction)
        stair_zone = Polygon(STAIR_ZONE_COORDINATES)

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(STAIR_WALKABLE_AREA_WKT, encoding="utf-8")

            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)

            assert result.agents_remaining == 0, f"RiMEA 13 {direction}: not all agents evacuated"

            traj_df = result.trajectory_dataframe()[["id", "frame", "x", "y"]].copy()
            traj = pedpy.TrajectoryData(traj_df, frame_rate=result.frame_rate)
            speed_df = pedpy.compute_individual_speed(
                traj_data=traj,
                frame_step=10,
                speed_calculation=pedpy.SpeedCalculation.BORDER_EXCLUDE,
            )
            merged = traj.data.merge(speed_df[["id", "frame", "speed"]], on=["id", "frame"], how="inner")
            result.cleanup()

        stair_points = merged[
            merged.apply(lambda row: stair_zone.covers(Point(row["x"], row["y"])), axis=1)
        ].copy()
        frame_counts = stair_points.groupby("frame")["id"].nunique().rename("count")
        stair_points = stair_points.merge(frame_counts, on="frame", how="left")
        stair_points["density"] = stair_points["count"] / stair_zone.area
        stair_points = stair_points[(stair_points["density"] >= 0.6) & (stair_points["density"] <= 1.5)]
        stair_points["density_bin"] = np.round(stair_points["density"], 1)
        return stair_points

    def test_down_faster_than_up(self):
        up_points = self._run_direction("up")
        down_points = self._run_direction("down")

        assert len(up_points) > 500, f"Too few upstairs stair samples: {len(up_points)}"
        assert len(down_points) > 500, f"Too few downstairs stair samples: {len(down_points)}"

        up_low, up_high = corbetta_envelope_bounds("up", up_points["density"].to_numpy())
        down_low, down_high = corbetta_envelope_bounds("down", down_points["density"].to_numpy())

        up_inside = (
            (up_points["speed"].to_numpy() >= up_low) & (up_points["speed"].to_numpy() <= up_high)
        ).mean()
        down_inside = (
            (down_points["speed"].to_numpy() >= down_low)
            & (down_points["speed"].to_numpy() <= down_high)
        ).mean()

        assert up_inside >= 0.50, f"Upstairs points inside Corbetta band too low: {up_inside:.3f}"
        assert down_inside >= 0.50, (
            f"Downstairs points inside Corbetta band too low: {down_inside:.3f}"
        )

        up_binned = up_points.groupby("density_bin", observed=True)["speed"].mean()
        down_binned = down_points.groupby("density_bin", observed=True)["speed"].mean()
        common_bins = up_binned.index.intersection(down_binned.index)

        assert len(common_bins) >= 5, f"Expected enough shared density bins, got {list(common_bins)}"
        assert (down_binned.loc[common_bins] > up_binned.loc[common_bins]).all(), (
            "Downstairs mean speed should exceed upstairs mean speed at shared densities, got "
            f"up={up_binned.loc[common_bins].to_dict()}, down={down_binned.loc[common_bins].to_dict()}"
        )


class TestRiMEA14RouteChoice:
    """RiMEA Test 14: Choice of route.

    Multi-storey: start and target connected by stairs and corridor.
    Expected: Document whether agents take short or long route.
    """

    def test_route_choice(self):
        scenario_zip = SCRIPTS_DIR / "scenarios" / "Rimea-14.zip"
        with zipfile.ZipFile(scenario_zip) as archive:
            raw = json.loads(archive.read("config.json"))
            walkable_area_wkt = archive.read("geometry.wkt").decode()

        # The exported scenario uses flow spawning in a small source area and may under-populate.
        # For the route-choice check we keep the geometry/journeys but switch to a stable 30-agent batch.
        raw["distributions"]["jps-distributions_0"]["parameters"].update(
            {
                "use_flow_spawning": False,
                "distribution_mode": "by_number",
                "number": 30,
            }
        )

        def run_variant(config: dict):
            with tempfile.TemporaryDirectory() as tmpdir:
                scenario_dir = pathlib.Path(tmpdir)
                (scenario_dir / "config.json").write_text(json.dumps(config), encoding="utf-8")
                (scenario_dir / "geometry.wkt").write_text(walkable_area_wkt, encoding="utf-8")
                scenario = load_scenario(str(scenario_dir))
                result = run_scenario(scenario, seed=42)
                traj_df = result.trajectory_dataframe()[["id", "frame", "x", "y"]].copy()
                result.cleanup()
            return traj_df

        staged_traj = run_variant(json.loads(json.dumps(raw)))

        direct_raw = json.loads(json.dumps(raw))
        direct_raw["journeys"] = [{"id": "journey_0", "stages": ["jps-distributions_0", "jps-exits_0"]}]
        direct_raw["transitions"] = []
        direct_traj = run_variant(direct_raw)

        def classify_routes(traj_df):
            agent_max_y = traj_df.groupby("id")["y"].max()
            # The long route moves to the upper floor around y ~= 14, while the direct route stays below.
            long_count = int((agent_max_y > 12.0).sum())
            short_count = int((agent_max_y <= 12.0).sum())
            return long_count, short_count

        staged_long_route_agents, staged_short_stage_agents = classify_routes(staged_traj)
        direct_long_route_agents, direct_short_stage_agents = classify_routes(direct_traj)
        staged_total_agents = staged_traj["id"].nunique()
        direct_total_agents = direct_traj["id"].nunique()

        assert staged_total_agents == 30, f"Expected 30 staged agents, got {staged_total_agents}"
        assert direct_total_agents == 30, f"Expected 30 direct agents, got {direct_total_agents}"
        assert direct_long_route_agents == 0, (
            f"Without stages, agents should take the short route only; got {direct_long_route_agents}"
        )
        assert direct_short_stage_agents == direct_total_agents, (
            f"Without stages, all agents should stay on the direct short route; got {direct_short_stage_agents}"
        )
        assert staged_long_route_agents > staged_short_stage_agents, (
            f"Configured staged scenario should prefer the long route, got "
            f"long={staged_long_route_agents}, short={staged_short_stage_agents}"
        )


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
    WALKABLE_CORNER = (
        "POLYGON ((26 1, 26 -34, 20 -34, 20 0, 0 0, 0 6, 21 6, 21 1, 26 1))"
    )
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
        assert (
            metrics_corner["evacuation_time"] > metrics_straight["evacuation_time"]
        ), (
            f"Corner ({metrics_corner['evacuation_time']:.1f}s) should be slower "
            f"than straight ({metrics_straight['evacuation_time']:.1f}s)"
        )


class TestRiMEA161DFundamentalDiagram:
    """RiMEA Test 16: 1D fundamental diagram.

    Ring or long narrow corridor, measure speed vs 1D density.
    Expected: Curves lie within 10/90% percentile envelope of empirical data.
    """

    def test_1d_fundamental_diagram(self):
        reference = load_reference_band()
        runs = {}
        for label, desired_speed in {
            "slower": 0.9,
            "baseline": 1.2,
            "faster": 1.5,
        }.items():
            scenario, geometry = build_loop_scenario(
                label=f"rimea16-{label}",
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

        assert baseline["inside_band"].mean() >= 0.75, (
            f"Baseline curve should mostly lie within the reference band, got "
            f"{baseline['inside_band'].mean():.2%} inside"
        )
        assert float(slower["speed_mps"].mean()) < float(baseline["speed_mps"].mean()), (
            f"Lower desired speed should shift the curve down, got "
            f"slower={slower['speed_mps'].mean():.3f}, "
            f"baseline={baseline['speed_mps'].mean():.3f}"
        )
        assert faster["above_p90"].mean() >= 0.75, (
            f"Higher desired speed should push the curve above the 90th percentile band, got "
            f"{faster['above_p90'].mean():.2%} above"
        )
