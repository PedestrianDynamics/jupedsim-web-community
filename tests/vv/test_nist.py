"""NIST TN 1822 - Verification and validation of building fire evacuation models.

Reference: Ronchi, Kuligowski, Reneke, Peacock, Nilsson (2013). NIST Technical
Note 1822. https://nvlpubs.nist.gov/nistpubs/technicalnotes/NIST.TN.1822.pdf

The contributions exercise 16 of the 17 NIST verification tests. Verif.2.7 is
blocked on an elevator component and is documented in standards/nist/README.md.

Two shipped tests exercise NIST's numeric criterion but xfail because it needs a
CollisionFreeSpeedModel capability the model lacks: Verif.2.9 (no group-cohesion
model) and Verif.5.2 (no door-flow limiter). See issue #151.

Per-scenario deviations from the NIST originals are logged in
standards/nist/MODIFICATIONS.md.
"""

from __future__ import annotations

import json
import math
import pathlib
import sys
import tempfile
from copy import deepcopy

import jupedsim as jps
import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from vv_helpers import (
    FLOW_CAP_TOLERANCE,
    HAS_VV_DEPS,
    MAX_SPECIFIC_FLOW,
    specific_exit_flow,
    with_flow_limiter,
)

STANDARDS_DIR = pathlib.Path(__file__).resolve().parents[2] / "standards"
SCENARIO_FILES = STANDARDS_DIR / "nist" / "scenario_files"

for extra in (STANDARDS_DIR / "nist", STANDARDS_DIR):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from jupedsim_scenarios import (
    load_scenario,
    run_scenario,
    run_sweep,
)
from utils import movement_disability


def _load_builder(module_name: str):
    """Load standards/nist/scenario_builders/<module_name>.py by explicit
    file path. Avoids the package-name clash with
    standards/rimea/scenario_builders/ when vv.yml runs both test modules
    in the same pytest invocation (Python's import cache otherwise pins
    `scenario_builders` to whichever test loaded it first)."""
    import importlib.util

    path = STANDARDS_DIR / "nist" / "scenario_builders" / f"{module_name}.py"
    synthetic_name = f"_nist_{module_name}"
    spec = importlib.util.spec_from_file_location(synthetic_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    # Register before exec so @dataclass (which looks the module up in
    # sys.modules via __module__) can attach the class correctly.
    sys.modules[synthetic_name] = mod
    spec.loader.exec_module(mod)
    return mod


pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(
        not HAS_VV_DEPS, reason="V&V runtime dependencies not installed"
    ),
]


def _load(stem: str):
    return load_scenario(str(SCENARIO_FILES / f"{stem}.zip"))


def _trajectory_first_move_times(df, frame_rate, threshold_m=0.05):
    """Return per-agent observed start time = first frame where the agent has
    moved more than ``threshold_m`` (L1) from its spawn position."""
    starts = []
    for _agent_id, sub in df.sort_values(["id", "frame"]).groupby("id"):
        x0, y0 = sub.iloc[0][["x", "y"]]
        moved = sub[(sub.x - x0).abs() + (sub.y - y0).abs() > threshold_m]
        t = (moved.iloc[0]["frame"] / frame_rate) if len(moved) else float("nan")
        starts.append(t)
    return np.array(starts)


# ---------------------------------------------------------------------------
# Verif.1.1 - Pre-evacuation time distributions
# ---------------------------------------------------------------------------


class TestNist11Premovement:
    """NIST Verif.1.1 - 10 agents, four pre-evacuation distributions.

    Geometry: 8 m x 5 m room, 1 m exit centred on the 5 m wall.
    Acceptance: aggregated observed start times fit each NIST distribution
    (Kolmogorov-Smirnov, alpha = 0.05). Sample size per case in this test is
    n = 10 (one sim x 10 agents); the publishable n = 1000 (100 seeds) is
    available by wrapping the sweep loop.
    """

    def test_base_zip_loads(self):
        scenario = _load("Nist-1-1-premovement")
        assert scenario.model_type == "CollisionFreeSpeedModel"
        params = scenario.distributions["jps-distributions_0"]["parameters"]
        assert params["use_premovement"] is True
        # Base ZIP defaults to the uniform case.
        assert params["premovement_distribution"] == "uniform"

    @pytest.mark.parametrize("case_id", ["uniform", "gamma", "lognormal", "weibull"])
    def test_distribution_fits(self, case_id):
        build_variants = _load_builder("nist1_1_premovement").build_variants
        from scipy import stats as _stats

        base = _load("Nist-1-1-premovement")
        case, variant = next(
            (c, s) for c, s in build_variants(base) if c.name == case_id
        )
        result = run_scenario(variant, seed=42)
        try:
            df = result.trajectory_dataframe()
            observed = _trajectory_first_move_times(df, result.frame_rate)
            observed = observed[~np.isnan(observed)]
            assert len(observed) > 0, f"{case_id}: no agents moved"
            # Build the analytic CDF for the active distribution.
            if case.name == "uniform":
                cdf = lambda v: _stats.uniform.cdf(v, case.param_a, case.param_b - case.param_a)
            elif case.name == "gamma":
                cdf = lambda v: _stats.gamma.cdf(v, case.param_a, scale=case.param_b)
            elif case.name == "lognormal":
                cdf = lambda v: _stats.lognorm.cdf(v, case.param_b, scale=np.exp(case.param_a))
            elif case.name == "weibull":
                cdf = lambda v: _stats.weibull_min.cdf(v, case.param_b, scale=case.param_a)
            else:
                pytest.fail(f"unknown distribution: {case.name}")
            p = _stats.kstest(observed, cdf).pvalue
            assert p > 0.05, f"{case_id}: KS p={p:.3f} below alpha=0.05"
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.2.1 - Speed in a corridor
# ---------------------------------------------------------------------------


class TestNist21CorridorSpeed:
    """NIST Verif.2.1 - single agent walks 40 m of corridor at v0 = 1.0 m/s.

    The corridor is extended to 60 m with 10 m buffers; speed is measured over
    the NIST 40 m segment (x = 10 .. 50).
    """

    MEAS_START_X = 10.0
    MEAS_END_X = 50.0
    TARGET_SPEED = 1.0
    TOL = 0.05

    def test_walking_speed(self):
        scenario = _load("Nist-2-1-corridor-speed")
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            sub = df.reset_index(drop=True)
            t_in_idx = sub[sub.x >= self.MEAS_START_X].index.min()
            t_out_idx = sub[sub.x >= self.MEAS_END_X].index.min()
            assert t_in_idx is not None and t_out_idx is not None
            t_in = sub.loc[t_in_idx, "frame"] / result.frame_rate
            t_out = sub.loc[t_out_idx, "frame"] / result.frame_rate
            speed = (self.MEAS_END_X - self.MEAS_START_X) / (t_out - t_in)
            assert abs(speed - self.TARGET_SPEED) <= self.TOL, (
                f"observed {speed:.3f} m/s, target {self.TARGET_SPEED} +/- {self.TOL}"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.2.2 - Speed on stairs (upward + downward)
# ---------------------------------------------------------------------------


class _StairSpeedBase:
    SCENARIO: str
    MEAS_START_X: float
    MEAS_END_X: float
    DIRECTION: str  # "right" or "left"
    TARGET_SPEED = 1.0
    TOL = 0.05

    def test_walking_speed(self):
        scenario = _load(self.SCENARIO)
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            sub = df.reset_index(drop=True)
            if self.DIRECTION == "right":
                t_in_idx = sub[sub.x >= self.MEAS_START_X].index.min()
                t_out_idx = sub[sub.x >= self.MEAS_END_X].index.min()
            else:
                t_in_idx = sub[sub.x <= self.MEAS_START_X].index.min()
                t_out_idx = sub[sub.x <= self.MEAS_END_X].index.min()
            assert t_in_idx is not None and t_out_idx is not None
            t_in = sub.loc[t_in_idx, "frame"] / result.frame_rate
            t_out = sub.loc[t_out_idx, "frame"] / result.frame_rate
            distance = abs(self.MEAS_END_X - self.MEAS_START_X)
            speed = distance / (t_out - t_in)
            assert abs(speed - self.TARGET_SPEED) <= self.TOL, (
                f"observed {speed:.3f} m/s, target {self.TARGET_SPEED} +/- {self.TOL}"
            )
        finally:
            result.cleanup()


class TestNist22StairsUp(_StairSpeedBase):
    """NIST Verif.2.2 (up) - 100 m measurement segment, 1 m/s target."""

    SCENARIO = "Nist-2-2-stairs-up"
    MEAS_START_X = 10.0
    MEAS_END_X = 110.0
    DIRECTION = "right"


class TestNist22StairsDown(_StairSpeedBase):
    """NIST Verif.2.2 (down) - same 100 m segment, traversed right-to-left."""

    SCENARIO = "Nist-2-2-stairs-down"
    MEAS_START_X = 110.0
    MEAS_END_X = 10.0
    DIRECTION = "left"


# ---------------------------------------------------------------------------
# Verif.2.3 - Movement around a corner
# ---------------------------------------------------------------------------


class TestNist23Corner:
    """NIST Verif.2.3 - 20 agents navigate an L-shaped corridor without
    penetrating the boundary (centre at least its radius minus 1 mm inside)."""

    def test_full_evacuation(self):
        scenario = _load("Nist-2-3-corner")
        result = run_scenario(scenario, seed=42)
        try:
            assert result.agents_remaining == 0
            assert result.evacuation_time < 60.0
        finally:
            result.cleanup()

    def test_no_boundary_penetration(self):
        scenario = _load("Nist-2-3-corner")
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe()
            radius = next(iter(scenario.distributions.values()))["parameters"]["radius"]
            walkable_with_tol = scenario.walkable_polygon.buffer(0.001 - radius)
            outside = sum(
                1
                for row in df.itertuples()
                if not walkable_with_tol.contains(Point(row.x, row.y))
            )
            assert outside == 0, (
                f"{outside} / {len(df)} trajectory points outside walkable area"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.2.4 - Assigned agent demographics
# ---------------------------------------------------------------------------


class TestNist24Demographics:
    """NIST Verif.2.4 - 100 agents with Gaussian-distributed desired speed.

    The NIST test has no exit, so the loader refuses to start a simulation.
    We verify the config is wired up to sample from Gaussian(1.2, 0.2).
    """

    def test_gaussian_distribution_configured(self):
        scenario = _load("Nist-2-4-demographics")
        params = scenario.distributions["jps-distributions_0"]["parameters"]
        assert params["v0_distribution"] == "gaussian"
        assert abs(params["v0"] - 1.2) < 1e-6
        assert abs(params["v0_std"] - 0.2) < 1e-6
        assert int(params["number"]) == 100


# ---------------------------------------------------------------------------
# Verif.2.5 - Reduced visibility versus walking speed
# ---------------------------------------------------------------------------


class TestNist25ReducedVisibility:
    """NIST Verif.2.5 - smoke extinction reduces walking speed.

    CollisionFreeSpeedModel has no native smoke field.  The V&V adapter uses
    NIST Equation 2, ``v_smoke = max(v_min, v_clear * c(K_s))``, and maps the resulting
    factor to a zone covering the smoky corridor.  The selected model-specific
    correlation is declared explicitly below so that the expected travel time
    is calculated independently from the trajectory.

    The NIST 100 m measurement corridor has 10 m acceleration/isolation
    buffers.  At K_s = 1 /m, c(K_s) = 0.5, hence the expected speed is
    1.25 * 0.5 = 0.625 m/s and the expected time over 100 m is 160 s.
    """

    CLEAR_SPEED_M_S = 1.25
    EXTINCTION_COEFFICIENT_PER_M = 1.0
    SPEED_REDUCTION_PER_EXTINCTION_M = 0.5
    MINIMUM_SPEED_M_S = 0.30
    MEAS_START_X = 10.0
    MEAS_END_X = 110.0
    TIME_TOLERANCE_S = 0.5

    @classmethod
    def smoke_speed_factor(cls, extinction_coefficient_per_m: float) -> float:
        """Return c(K_s) for the correlation selected by this model adapter.

        This is NIST Equation 2: a linear fractional reduction with the common
        dense-smoke minimum speed recommended by TN 1822 (0.3--0.4 m/s).
        """
        if extinction_coefficient_per_m < 0:
            raise ValueError("extinction coefficient must be non-negative")
        linear_factor = (
            1.0 - cls.SPEED_REDUCTION_PER_EXTINCTION_M * extinction_coefficient_per_m
        )
        minimum_factor = cls.MINIMUM_SPEED_M_S / cls.CLEAR_SPEED_M_S
        return max(minimum_factor, linear_factor)

    def test_smoke_reduced_travel_time(self):
        speed_factor = self.smoke_speed_factor(self.EXTINCTION_COEFFICIENT_PER_M)
        expected_speed = self.CLEAR_SPEED_M_S * speed_factor
        expected_time = (self.MEAS_END_X - self.MEAS_START_X) / expected_speed

        raw = {
            "config": {
                "simulation_settings": {
                    "baseSeed": 42,
                    "simulationParams": {
                        "model_type": "CollisionFreeSpeedModel",
                        "max_simulation_time": 220,
                    },
                }
            },
            "distributions": {
                "jps-distributions_0": {
                    "type": "polygon",
                    "coordinates": [
                        [0.5, 0.75],
                        [1.5, 0.75],
                        [1.5, 1.25],
                        [0.5, 1.25],
                        [0.5, 0.75],
                    ],
                    "parameters": {
                        "number": 1,
                        "radius": 0.15,
                        "v0": self.CLEAR_SPEED_M_S,
                        "distribution_mode": "by_number",
                        "radius_distribution": "constant",
                        "v0_distribution": "constant",
                        "use_flow_spawning": False,
                    },
                    "journey_weights": [
                        {"journey_id": "jps-journeys_0", "weight": 100}
                    ],
                }
            },
            "exits": {
                "jps-exits_0": {
                    "type": "polygon",
                    # NIST specifies a 1 m opening at the corridor end.
                    "coordinates": [
                        [119.7, 0.5],
                        [120.0, 0.5],
                        [120.0, 1.5],
                        [119.7, 1.5],
                        [119.7, 0.5],
                    ],
                }
            },
            "zones": {
                "jps-zones_0": {
                    "coordinates": [
                        [0.0, 0.0],
                        [120.0, 0.0],
                        [120.0, 2.0],
                        [0.0, 2.0],
                        [0.0, 0.0],
                    ],
                    # Explicit K_s -> c(K_s) assignment for uniform smoke.
                    "speed_factor": speed_factor,
                }
            },
            "journeys_v2": [
                {
                    "id": "jps-journeys_0",
                    "name": "jps-journeys_0",
                    "color": "#888888",
                    "sequence": ["jps-exits_0"],
                }
            ],
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            scenario_dir = pathlib.Path(tmpdir)
            (scenario_dir / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (scenario_dir / "geometry.wkt").write_text(
                "POLYGON ((0 0, 120 0, 120 2, 0 2, 0 0))",
                encoding="utf-8",
            )
            scenario = load_scenario(str(scenario_dir))
            result = run_scenario(scenario, seed=42)
            try:
                trajectory = result.trajectory_dataframe().sort_values(["id", "frame"])
                t_in = (
                    trajectory.loc[trajectory.x >= self.MEAS_START_X, "frame"].iloc[0]
                    / result.frame_rate
                )
                t_out = (
                    trajectory.loc[trajectory.x >= self.MEAS_END_X, "frame"].iloc[0]
                    / result.frame_rate
                )
                observed_time = t_out - t_in
            finally:
                result.cleanup()

        assert observed_time == pytest.approx(
            expected_time, abs=self.TIME_TOLERANCE_S
        ), (
            f"K_s={self.EXTINCTION_COEFFICIENT_PER_M}/m produced "
            f"{observed_time:.2f}s over 100 m; correlation predicts "
            f"{expected_time:.2f}s at {expected_speed:.3f} m/s"
        )


# ---------------------------------------------------------------------------
# Verif.2.6 - Occupant incapacitation (Fractional Effective Dose)
#
# FDS+Evac reference mentinoed in NIST TN 1822:
# "Fire Dynamics Simulator with Evacuation: FDS+Evac Technical Reference and
# User's Guide", section 3.3, equations 11--14 (pp. 15--16), and the FED
# verification case in section 4.2 (p. 20):
# ---------------------------------------------------------------------------


class TestNist26OccupantIncapacitation:
    """NIST Verif.2.6 - simulated and hand-calculated FED=1 times agree.

    A single occupant is held at the centre of the specified 10 m x 10 m room
    by an indefinite waiting stage, equivalent to NIST's >10,000,000 s
    pre-evacuation time. Constant CO, CO2, and O2 conditions are sampled at the
    occupant position. A small V&V adapter accumulates the FDS+Evac/Purser FED
    equations on every JuPedSim timestep and sets desired speed to zero at
    incapacitation. JuPedSim itself does not provide a native FED model.
    """

    CO_PPM = 5000.0
    CO2_PERCENT = 2.0
    O2_PERCENT = 18.0
    INITIAL_DESIRED_SPEED_M_S = 1.25
    DT_S = 0.05
    MAX_TIME_S = 300.0
    CENTRE = (5.0, 5.0)

    @staticmethod
    def _co_fed_rate(co_ppm: float) -> float:
        """FDS+Evac equation 12, returned as FED per second."""
        return 4.607e-7 * co_ppm**1.036

    @staticmethod
    def _o2_fed_rate(o2_percent: float) -> float:
        """FDS+Evac equation 13, returned as FED per second."""
        return 1.0 / (60.0 * math.exp(8.13 - 0.54 * (20.9 - o2_percent)))

    @staticmethod
    def _co2_hyperventilation(co2_percent: float) -> float:
        """FDS+Evac equation 14, dimensionless CO2 multiplier."""
        return math.exp(0.1930 * co2_percent + 2.0004) / 7.1

    def test_time_to_fed_one(self):
        simulation = jps.Simulation(
            model=jps.CollisionFreeSpeedModel(),
            geometry=Polygon([(0, 0), (10, 0), (10, 10), (0, 10)]),
            dt=self.DT_S,
        )
        waiting_stage = simulation.add_waiting_set_stage([self.CENTRE])
        journey = simulation.add_journey(jps.JourneyDescription([waiting_stage]))
        agent_id = simulation.add_agent(
            jps.CollisionFreeSpeedModelAgentParameters(
                journey_id=journey,
                stage_id=waiting_stage,
                position=self.CENTRE,
                desired_speed=self.INITIAL_DESIRED_SPEED_M_S,
                radius=0.15,
            )
        )

        fed_co = 0.0
        fed_o2 = 0.0
        total_fed = 0.0
        incapacitation_time = None

        while simulation.elapsed_time() < self.MAX_TIME_S:
            simulation.iterate()

            # The test hazard is spatially and temporally constant, but it is
            # sampled per agent and integrated per timestep just as a future
            # spatial hazard field would be.
            _position = simulation.agent(agent_id).position
            fed_co += self._co_fed_rate(self.CO_PPM) * simulation.delta_time()
            fed_o2 += self._o2_fed_rate(self.O2_PERCENT) * simulation.delta_time()
            total_fed = fed_co * self._co2_hyperventilation(self.CO2_PERCENT) + fed_o2

            if total_fed >= 1.0:
                agent = simulation.agent(agent_id)
                assert agent.model.desired_speed == pytest.approx(
                    self.INITIAL_DESIRED_SPEED_M_S
                )
                agent.model.desired_speed = 0.0
                incapacitation_time = simulation.elapsed_time()
                break

        assert incapacitation_time is not None, (
            f"FED reached only {total_fed:.3f} by {self.MAX_TIME_S}s"
        )
        assert simulation.agent(agent_id).model.desired_speed == 0.0
        assert simulation.agent(agent_id).position == pytest.approx(self.CENTRE)

        # Independent closed-form calculation for constant concentrations.
        # It intentionally does not call the timestep adapter methods above.
        expected_rate = 4.607e-7 * self.CO_PPM**1.036 * math.exp(
            0.1930 * self.CO2_PERCENT + 2.0004
        ) / 7.1 + 1.0 / (60.0 * math.exp(8.13 - 0.54 * (20.9 - self.O2_PERCENT)))
        expected_time = 1.0 / expected_rate

        # A discrete integrator crosses FED=1 on the first timestep at or
        # after the analytical value, so one simulation step is the limit.
        assert expected_time <= incapacitation_time
        assert incapacitation_time - expected_time <= simulation.delta_time(), (
            f"FED=1 at {incapacitation_time:.3f}s; independent calculation "
            f"predicts {expected_time:.3f}s"
        )


# ---------------------------------------------------------------------------
# Verif.2.8 - Horizontal counter-flows
# ---------------------------------------------------------------------------


class TestNist28Counterflow:
    """NIST Verif.2.8 - 100 primary agents cross a 10 m x 2 m corridor against
    0/10/50/100 counterflow agents in the far room.

    NIST criterion: the completion time (last primary agent to reach the far
    room) increases with counterflow. Under CollisionFreeSpeedModel the high
    counterflow branches deadlock, so a last-arrival *time* is undefined; the
    equivalent monotone quantity is the number of primary agents that reach the
    far room within a fixed, *equal* time budget, which must be non-increasing
    as counterflow grows. Observed on seed 42: [100, 100, 1, 0].
    """

    BUDGET_S = 400
    # The two 10 m rooms bracket a 10 m corridor, so the far room starts at
    # x = 20 m. An agent that reaches it is absorbed at the far exit (x >= 29.7),
    # so its *last* recorded position lies in the far room - counting a fixed
    # crossing frame (x >= 29.7) misses every absorbed agent and reads 0.
    FAR_ROOM_X = 20.0

    def test_completion_non_increasing_with_counterflow(self):
        load_branches = _load_builder("nist2_8_counterflow").load_branches

        reached = []
        for branch in load_branches():
            scenario = load_scenario(str(branch.scenario_zip))
            scenario.max_simulation_time = self.BUDGET_S
            result = run_scenario(scenario, seed=42)
            try:
                df = result.trajectory_dataframe().sort_values(["id", "frame"])
                grouped = df.groupby("id")
                first_x = grouped.first().x
                last_x = grouped.last().x
                # Primary population spawns in the near room (x < 5).
                primary = first_x[first_x < 5.0].index
                reached.append(
                    sum(1 for aid in primary if last_x[aid] >= self.FAR_ROOM_X)
                )
            finally:
                result.cleanup()

        deltas = np.diff(reached)
        assert (deltas <= 0).all(), (
            f"primary completions not non-increasing in counterflow: {reached}"
        )


# ---------------------------------------------------------------------------
# Verif.2.9 - Group behaviours
# ---------------------------------------------------------------------------


class TestNist29Groups:
    """NIST Verif.2.9 - Group 1 (4 @1.25 m/s + 1 @0.5 m/s) must reach the exit
    together (arrival spread <= 10 s); Group 2 (10 @0.2 m/s) shares the room.
    """

    GROUP1_SIZE = 5
    MAX_SPREAD_S = 10.0  # NIST TN 1822 section 3.1.2, Verif.2.9.

    def test_runs_without_error(self):
        scenario = _load("Nist-2-9-groups")
        result = run_scenario(scenario, seed=42)
        try:
            assert result.total_agents == 15
            df = result.trajectory_dataframe()
            assert len(df) > 0
        finally:
            result.cleanup()

    @pytest.mark.xfail(
        reason="CollisionFreeSpeedModel has no group-cohesion model, so the "
        "0.5 m/s member of Group 1 lags the 1.25 m/s members (spread ~24 s > "
        "10 s). Needs a group sub-model; tracked in issue #151.",
        raises=AssertionError,
        strict=True,
    )
    def test_group1_arrives_together(self):
        scenario = _load("Nist-2-9-groups")
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            grouped = df.groupby("id")
            first_y = grouped.first().y
            # Exit time per agent = last recorded frame (absorbed at the exit).
            exit_time = grouped.frame.max() / result.frame_rate
            # Group 1 spawns in the top zone (y ~16-19.5); Group 2 sits lower
            # (y ~8-12), so the five highest spawn-y agents are exactly Group 1.
            group1 = first_y.sort_values(ascending=False).head(self.GROUP1_SIZE).index
            spread = exit_time[group1].max() - exit_time[group1].min()
            assert spread <= self.MAX_SPREAD_S, (
                f"Group 1 arrival spread {spread:.1f} s exceeds {self.MAX_SPREAD_S} s"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.2.10 - Agents with movement disabilities
# ---------------------------------------------------------------------------


class TestNist210Disabilities:
    """NIST Verif.2.10 - 24 occupants overtake one slower, larger occupant.

    Geometry and population follow NIST TN 1822 Verif.2.10 and Figure 7: room 1
    (5 m x 4 m), a 2 m x 1.5 m ramp, room 2 (5 m x 4 m) and a 1 m exit.
    In the disability case the zone-2 occupant walks at 0.8 m/s on the flat
    and 0.4 m/s on the ramp and is 0.8 m wide; in the control case it is
    like the 24 zone-1 occupants (1.25 m/s, default size). The scenario is
    built by standards/utils/movement_disability.py, which sets the disabled
    occupant's ramp speed every step because jupedsim-scenarios 0.7.3 applies
    speed zones to every agent.

    Acceptance (decision D8): the zone-1 occupants reach the exit later in
    the disability case, for both the last and the mean zone-1 exit time.
    """

    def test_movement_disability_increases_evacuation_time(self):
        disability = movement_disability.run("nist", disabled=True, seed=42)
        control = movement_disability.run("nist", disabled=False, seed=42)
        for result in (disability, control):
            assert len(result.exit_times) == movement_disability.N_ZONE1 + 1, (
                f"only {len(result.exit_times)} of 25 occupants evacuated "
                f"(disabled={result.disabled})"
            )
        disability_zone1 = disability.zone1_exit_times()
        control_zone1 = control.zone1_exit_times()
        assert max(disability_zone1) > max(control_zone1), (
            "expected the last zone-1 occupant to exit later with the "
            f"slow occupant: {max(disability_zone1):.2f}s versus "
            f"{max(control_zone1):.2f}s"
        )
        assert np.mean(disability_zone1) > np.mean(control_zone1), (
            "expected the mean zone-1 exit time to be later with the "
            f"slow occupant: {np.mean(disability_zone1):.2f}s versus "
            f"{np.mean(control_zone1):.2f}s"
        )


# ---------------------------------------------------------------------------
# Verif.3.1 - Exit route allocation
# ---------------------------------------------------------------------------


class TestNist31RouteAllocation:
    """NIST Verif.3.1 - 12 rooms around a 1 m corridor; main exit serves rooms
    1-4 and 7-10, secondary exit serves rooms 5, 6, 11, 12.

    Population is 23 agents (two per room, one in room 3), as NIST TN 1822
    section 3.1.3 and Figure 8 specify. The allocation split
    (8 rooms -> main, 4 -> secondary) matches the standard.

    Acceptance: every agent must end at the exit allocated by its journey.
    The walkable polygon is rebuilt parametrically; see MODIFICATIONS.md
    section B12.
    """

    def test_each_agent_reaches_allocated_exit(self):
        """Every agent must end at the exit allocated by its journey. The
        ZIP carries both the legacy `journeys`/`stages` block (web-app
        export shape) and a derived `journeys_v2` + `journey_weights`
        block (the canonical loader's required shape); the latter is
        what makes the canonical loader enforce route allocation
        strictly. See MODIFICATIONS.md section A4.
        """
        scenario = _load("Nist-3-1-route-allocation")
        raw = scenario.raw
        if "journeys" in raw:
            journey_map = {
                j["stages"][0]: j["stages"][-1] for j in raw["journeys"]
            }
        else:
            j_to_exit = {
                j["id"]: j["sequence"][-1] for j in raw.get("journeys_v2", [])
            }
            journey_map = {}
            for did, d in scenario.distributions.items():
                jw = d.get("journey_weights", [])
                if jw:
                    journey_map[did] = j_to_exit.get(jw[0]["journey_id"], "")
        dist_polys = {
            did: Polygon(d["coordinates"])
            for did, d in scenario.distributions.items()
        }
        exit_polys = {
            eid: Polygon(e["coordinates"]) for eid, e in scenario.exits.items()
        }
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            assert result.agents_remaining == 0, "not all agents evacuated"
            first = df.groupby("id").first().reset_index()
            last = df.groupby("id").last().reset_index()
            matches = 0
            total = 0
            mismatches = []
            for row_first, row_last in zip(
                first.itertuples(), last.itertuples()
            ):
                p0 = Point(row_first.x, row_first.y)
                p1 = Point(row_last.x, row_last.y)
                home = next(
                    (did for did, poly in dist_polys.items() if poly.covers(p0)),
                    None,
                )
                assert home is not None, (
                    f"agent {row_first.id} did not spawn in any distribution"
                )
                expected = journey_map[home]
                actual = min(
                    exit_polys, key=lambda eid: exit_polys[eid].distance(p1)
                )
                total += 1
                if actual == expected:
                    matches += 1
                else:
                    mismatches.append(
                        f"agent {row_first.id} from {home}: "
                        f"expected {expected}, got {actual}"
                    )
            match_rate = matches / total if total else 0.0
            assert match_rate == 1.0, (
                f"allocation match rate {match_rate:.1%} below 100%\n"
                + "\n".join(mismatches[:5])
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.3.2 - Social influence on exit choice (same logic as ISO Test 15)
# ---------------------------------------------------------------------------


class TestNist32SocialInfluence:
    """NIST Verif.3.2 - occupants are influenced by another occupant's exit.

    Two free occupants first choose between equidistant exits with balanced
    journey weights. The second scenario adds an occupant deterministically
    assigned to exit 2. NIST expects that occupant to increase exit-2 use among
    the two free occupants. CollisionFreeSpeedModel has no social-influence
    model, so this criterion is retained as a strict expected failure.
    """

    SEEDS = range(1, 41)

    @staticmethod
    def _exit2_fraction(scenario, seeds, track=None):
        sweep = run_sweep(scenario, seeds=seeds, workers=4)
        try:
            e1 = e2 = other = 0
            for trial in sweep.trials:
                df = trial.result.trajectory_dataframe()
                if track:
                    initial = df[df.frame == df.frame.min()]
                    ids = (
                        initial[initial.y < 1.5].id.tolist()
                        if track == "behind"
                        else initial[initial.y >= 1.5].id.tolist()
                    )
                    finals = [
                        df[df.id == agent_id].sort_values("frame").iloc[-1]
                        for agent_id in ids
                    ]
                else:
                    last = df.sort_values("frame").groupby("id").last()
                    finals = [row for _, row in last.iterrows()]

                for last in finals:
                    if last.y >= 11.0 and last.x <= 1.5:
                        e1 += 1
                    elif last.y >= 11.0 and last.x >= 8.5:
                        e2 += 1
                    else:
                        other += 1

            total = e1 + e2 + other
            return e2 / total if total else float("nan")
        finally:
            sweep.cleanup()

    @pytest.mark.xfail(
        reason="CollisionFreeSpeedModel has no social-influence model: adding "
        "a deterministic exit-2 occupant leaves the free occupants' exit-2 "
        "usage unchanged, so the NIST-expected increase does not occur.",
        raises=AssertionError,
        strict=True,
    )
    def test_social_influence_raises_exit2_usage(self):
        scenario_1 = _load("Nist-3-2-social-influence-1")
        scenario_2 = _load("Nist-3-2-social-influence-2")
        baseline = self._exit2_fraction(scenario_1, self.SEEDS)
        behind = self._exit2_fraction(scenario_2, self.SEEDS, track="behind")
        assert behind > baseline, (
            f"free-occupant exit-2 usage {behind:.3f} not increased over "
            f"baseline {baseline:.3f}"
        )


# ---------------------------------------------------------------------------
# Verif.3.3 - Affiliation to familiar exits (same logic as ISO Test 16)
# ---------------------------------------------------------------------------


class TestNist33Affiliation:
    """NIST Verif.3.3 - occupants prefer an assigned familiar exit.

    CollisionFreeSpeedModel has no intrinsic familiarity state. As in ISO Test
    16, affiliation is represented through journey weights. Balanced 50/50
    weights should use both exits approximately equally; changing the weights
    to 20/80 should make exit 2 strictly preferred over a 20-seed sweep.
    """

    DIST_ID = "jps-distributions_0"
    JOURNEY_EXIT1 = "journey-1781167866144-fp65t3"
    JOURNEY_EXIT2 = "journey-1781167890022-3hfgs4"
    SEEDS = range(1, 21)

    @classmethod
    def _set_weight(cls, scenario, journey_id, weight):
        for entry in scenario.raw["distributions"][cls.DIST_ID]["journey_weights"]:
            if entry["journey_id"] == journey_id:
                entry["weight"] = weight

    def _exit_counts(self, scenario):
        sweep = run_sweep(scenario, seeds=self.SEEDS, workers=4)
        try:
            e1 = e2 = 0
            for trial in sweep.trials:
                df = trial.result.trajectory_dataframe()
                last = df.sort_values("frame").groupby("id").last()
                for _, row in last.iterrows():
                    if row.y >= 11.0 and row.x <= 1.5:
                        e1 += 1
                    elif row.y >= 11.0 and row.x >= 8.5:
                        e2 += 1
            return e1, e2
        finally:
            sweep.cleanup()

    def test_affiliation_biases_exit_choice(self):
        balanced = _load("Nist-3-3-familiar-exits")
        e1_balanced, e2_balanced = self._exit_counts(balanced)
        total_balanced = e1_balanced + e2_balanced
        assert total_balanced > 0, "no agent reached an exit in the balanced case"
        fraction_difference = abs(e1_balanced - e2_balanced) / total_balanced
        assert fraction_difference <= 0.10, (
            f"50/50 weights not balanced: {e1_balanced} vs {e2_balanced}"
        )

        affiliated = _load("Nist-3-3-familiar-exits")
        self._set_weight(affiliated, self.JOURNEY_EXIT1, 20)
        self._set_weight(affiliated, self.JOURNEY_EXIT2, 80)
        e1_affiliated, e2_affiliated = self._exit_counts(affiliated)
        assert e2_affiliated > e1_affiliated, (
            "exit 2 not preferred under affiliation: "
            f"{e1_affiliated} vs {e2_affiliated}"
        )


# ---------------------------------------------------------------------------
# Verif.4.1 - Dynamic availability of exits (same logic as ISO Test 9)
# ---------------------------------------------------------------------------


class TestNist41DynamicExitAvailability:
    """NIST Verif.4.1 - an occupant reroutes when Exit 1 closes at runtime.

    Both exits begin available and the occupant initially targets Exit 1. At
    t = 1 s, the runtime adapter redirects the active occupant to Exit 2,
    representing Exit 1 becoming unavailable. The occupant must evacuate
    through Exit 2.
    """

    EXIT_1 = "jps-exits_0"
    EXIT_2 = "jps-exits_1"

    @staticmethod
    def _redirect_agent(runner, agent_id, exit_key):
        """Change the active agent's current destination."""
        from jupedsim_scenarios.direct_steering_runtime import pick_stage_target

        state = runner._agent_wait_info[agent_id]
        exit_config = state["stage_configs"][exit_key]
        state["current_target_stage"] = exit_key
        state["target"] = pick_stage_target(state, exit_config)
        state["target_assigned"] = False
        state["state"] = "to_target"
        state["inside_since"] = None
        state["wait_until"] = None

    @pytest.mark.xfail(
        reason=(
            "The harness cannot make an exit unavailable through the public "
            "API: jupedsim-scenarios 0.7.3 only removes exits before a run "
            "and has no runtime exit closure. This test forces the reroute by "
            "writing the runner's private agent state, so the public API "
            "reroute is not exercised."
        ),
        run=False,  # the body writes private state and would pass unchecked
    )
    def test_agent_uses_available_exit(self):
        from jupedsim_scenarios import ScenarioRunner

        scenario = _load("Nist-4-1-dynamic-exits")
        with ScenarioRunner(scenario, seed=42, every_nth_frame=1) as runner:
            agent = next(iter(runner.agents()))
            self._redirect_agent(runner, agent.id, self.EXIT_1)
            runner.run_until(1.0)
            self._redirect_agent(runner, agent.id, self.EXIT_2)
            runner.run_until()
            result = runner.result()

        try:
            assert result.agents_remaining == 0, "occupant did not evacuate"
            trajectory = result.trajectory_dataframe().sort_values("frame")
            assert len(trajectory) > 0, "occupant trajectory is empty"

            exit_polygons = {
                exit_id: Polygon(exit_data["coordinates"])
                for exit_id, exit_data in scenario.raw["exits"].items()
            }
            last_position = trajectory.iloc[-1]
            last_point = Point(last_position.x, last_position.y)
            actual_exit = min(
                exit_polygons,
                key=lambda exit_id: exit_polygons[exit_id].distance(last_point),
            )
            assert actual_exit == self.EXIT_2, (
                "expected Exit 2 after Exit 1 became unavailable, "
                f"but the occupant used {actual_exit}"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.5.1 - Congestion
# ---------------------------------------------------------------------------


class TestNist51Congestion:
    """NIST Verif.5.1 - 100 agents in an 8 m x 5 m room with a 2 m corridor.

    Acceptance: peak density at the room exit > peak density in the
    downstream corridor window (y 15-17 m), i.e. congestion forms at the
    room exit and flow is steadier in the corridor.

    NIST also expects congestion at the base of the stairs, but this ZIP models
    a plain room -> corridor -> exit with no stair speed-factor zone, so that
    half of the expected result is not exercised. Adding a stair zone (JuPedSim
    supports it) is scope-deferred; see issue #151.
    """

    def test_peak_density_higher_at_room_exit(self):
        scenario = _load("Nist-5-1-congestion")
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe()
            area_room_exit = 2.0 * 0.5
            area_corridor = 2.0 * 2.0
            peak_room_exit = 0.0
            peak_corridor = 0.0
            for _frame, sub in df.groupby("frame"):
                room_count = (
                    (sub.x.between(3.0, 5.0)) & (sub.y.between(4.5, 5.0))
                ).sum()
                corr_count = (
                    (sub.x.between(3.0, 5.0)) & (sub.y.between(15.0, 17.0))
                ).sum()
                peak_room_exit = max(peak_room_exit, room_count / area_room_exit)
                peak_corridor = max(peak_corridor, corr_count / area_corridor)
            assert peak_room_exit > peak_corridor, (
                f"peak room-exit density {peak_room_exit:.2f} <= "
                f"peak corridor density {peak_corridor:.2f}"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Verif.5.2 - Maximum flow rates
# ---------------------------------------------------------------------------


class TestNist52MaxFlow:
    """NIST Verif.5.2 - 100 agents evacuate an 8 m x 5 m room through a 1 m exit.

    NIST section 3.1.5 sets the IMO MSC/Circ.1238 maximum specific flow of
    1.33 p/m/s as a limit the exit flow must not exceed. The run enables the
    jupedsim-scenarios exit throughput limiter at that value and checks that
    the measured flow stays at or below it (restricted-flow reading). The
    emergent flow without the limiter (~5 p/m/s on seed 42) is reported, not
    asserted.
    """

    EXIT_WIDTH_M = 1.0

    def _run(self, limiter_on):
        scenario = _load("Nist-5-2-max-flow")
        scenario.raw["exits"] = with_flow_limiter(
            scenario.raw["exits"], limiter_on, MAX_SPECIFIC_FLOW
        )
        result = run_scenario(scenario, seed=42)
        try:
            assert result.agents_remaining == 0, "not all agents evacuated"
            df = result.trajectory_dataframe()
            return specific_exit_flow(df, result.frame_rate, self.EXIT_WIDTH_M)
        finally:
            result.cleanup()

    def test_flow_within_cap_with_limiter(self):
        flow = self._run(limiter_on=True)
        cap = MAX_SPECIFIC_FLOW * (1 + FLOW_CAP_TOLERANCE)
        assert flow <= cap, (
            f"specific flow {flow:.3f} p/m/s exceeds the {MAX_SPECIFIC_FLOW} "
            f"p/m/s cap (+{FLOW_CAP_TOLERANCE:.0%} tolerance)"
        )

    def test_emergent_flow_without_limiter_is_reported(self):
        """Information only: the emergent flow is printed, not asserted."""
        flow = self._run(limiter_on=False)
        print(f"NIST 5.2 emergent specific flow, limiter off: {flow:.2f} p/m/s")
