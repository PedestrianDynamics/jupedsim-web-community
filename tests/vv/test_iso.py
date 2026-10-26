"""ISO 20414:2020 - Verification and validation of evacuation models.

Reference: ISO 20414:2020(E), "Fire safety engineering - Verification and
validation protocol for building fire evacuation models".

This module ports the acceptance criteria encoded in the notebooks under
``standards/iso/`` into live pytest assertions, mirroring the structure of
``tests/vv/test_nist.py`` (the ISO scenarios closely track the NIST TN 1822
ones). Each notebook carries an "## Acceptance" cell that is authoritative for
the criterion and thresholds reproduced here.

Five scenarios exercise a genuine ISO criterion that CollisionFreeSpeedModel
cannot satisfy and are therefore strict xfails (they assert the real criterion
and fail with AssertionError):

* Test 6  - counter-flow: no lane formation, the primary crowd deadlocks.
* Test 11 - maximum flow: no door-flow limiter, emergent flow >> IMO 1.33 p/m/s.
* Test 13 - corridor fundamental diagram: zone-2 speed exceeds the free speed.
* Test 15 - social influence: no social-influence model.

Runtime-reducing deviations from the notebooks are noted in the relevant
docstrings (Test 5 uses 3 runs, Test 13 a 3-density subset, Tests 15/16 a
40/20-seed sweep).
"""

from __future__ import annotations

import pathlib
import sys

import numpy as np
import pytest
from shapely.geometry import Point, Polygon
from vv_helpers import HAS_VV_DEPS
from test_nist import (
    TestNist25ReducedVisibility,
    TestNist26OccupantIncapacitation,
)


STANDARDS_DIR = pathlib.Path(__file__).resolve().parents[2] / "standards"
SCENARIO_FILES = STANDARDS_DIR / "iso" / "scenario_files"

for extra in (STANDARDS_DIR / "iso", STANDARDS_DIR):
    if str(extra) not in sys.path:
        sys.path.insert(0, str(extra))

from jupedsim_scenarios import (  # noqa: E402
    load_scenario,
    run_scenario,
    run_sweep,
)


def _load_builder(module_name: str):
    """Load standards/iso/scenario_builders/<module_name>.py by explicit file
    path under a synthetic ``_iso_`` name. Avoids the package-name clash with
    standards/nist and standards/rimea scenario_builders when vv.yml runs the
    suites in one pytest invocation (Python's import cache otherwise pins
    `scenario_builders` to whichever test loaded it first)."""
    import importlib.util

    path = STANDARDS_DIR / "iso" / "scenario_builders" / f"{module_name}.py"
    synthetic_name = f"_iso_{module_name}"
    spec = importlib.util.spec_from_file_location(synthetic_name, path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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


def _segment_speed(df, frame_rate, start_x, end_x, direction="right"):
    """Mean per-agent transit speed over the [start_x, end_x] x-segment."""
    speeds = []
    for _agent_id, sub in df.sort_values(["id", "frame"]).groupby("id"):
        if direction == "right":
            a = sub[sub.x >= start_x]
            b = sub[sub.x >= end_x]
        else:
            a = sub[sub.x <= start_x]
            b = sub[sub.x <= end_x]
        if a.empty or b.empty:
            continue
        t_in = a.iloc[0].frame / frame_rate
        t_out = b.iloc[0].frame / frame_rate
        if t_out > t_in:
            speeds.append(abs(end_x - start_x) / (t_out - t_in))
    return np.array(speeds)


# ---------------------------------------------------------------------------
# Test 1 - Pre-evacuation time assignment
# ---------------------------------------------------------------------------


class TestIso01Premovement:
    """ISO Test 1 - 10 occupants, several pre-evacuation distributions.

    Acceptance: each occupant starts within the distribution's support and the
    observed start times are consistent with the assigned family (two-sample
    KS against a 10 000-sample analytic reference, alpha = 0.05). Start times
    are pooled over 6 seeds (60 samples per case) to give the KS test signal.
    """

    N_RUNS = 6
    ALPHA = 0.05

    def test_base_zip_loads(self):
        scenario = _load("Iso-01-premovement")
        assert scenario.model_type == "CollisionFreeSpeedModel"
        params = scenario.distributions["jps-distributions_0"]["parameters"]
        assert params["use_premovement"] is True

    @pytest.mark.parametrize("case_id", ["uniform", "normal", "lognormal"])
    def test_distribution_fits(self, case_id):
        builder = _load_builder("iso01_premovement")
        from scipy import stats as _stats

        base = _load("Iso-01-premovement")
        observed = []
        case_obj = None
        for seed in (42 + i for i in range(self.N_RUNS)):
            for case, variant in builder.build_variants(base):
                if case.name != case_id:
                    continue
                case_obj = case
                result = run_scenario(variant, seed=seed)
                try:
                    df = result.trajectory_dataframe().sort_values(["id", "frame"])
                    for _aid, sub in df.groupby("id"):
                        x0, y0 = sub.iloc[0][["x", "y"]]
                        moved = sub[(sub.x - x0).abs() + (sub.y - y0).abs() > 0.05]
                        if len(moved):
                            observed.append(moved.iloc[0]["frame"] / result.frame_rate)
                finally:
                    result.cleanup()
        observed = np.array(observed)
        assert len(observed) > 0, f"{case_id}: no agents moved"
        reference = builder.sample_reference(case_obj, 10000, seed=2)
        p = _stats.ks_2samp(observed, reference).pvalue
        assert p > self.ALPHA, f"{case_id}: two-sample KS p={p:.3f} below alpha"


# ---------------------------------------------------------------------------
# Test 2 - Speed in a corridor
# ---------------------------------------------------------------------------


class TestIso02CorridorSpeed:
    """ISO Test 2 - single agent walks a 40 m corridor segment at 1.0 m/s.

    Acceptance: mean transit speed over the x = 10..50 measurement segment is
    1.0 +/- 0.05 m/s.
    """

    def test_walking_speed(self):
        scenario = _load("Iso-02-corridor")
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe()
            speeds = _segment_speed(df, result.frame_rate, 10.0, 50.0)
            assert len(speeds) > 0, "no agent crossed the measurement segment"
            observed = speeds.mean()
            assert abs(observed - 1.0) <= 0.05, f"observed {observed:.3f} m/s"
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Test 3 - Speed on stairs (speed-factor zone)
# ---------------------------------------------------------------------------


class TestIso03Stairs:
    """ISO Test 3 - agent crosses a 10 m stair inside a speed_factor = 0.5 zone.

    Acceptance: mean transit speed over the x = 10..20 stair segment is
    1.0 m/s x 0.5 = 0.5 +/- 0.05 m/s.
    """

    def test_stair_speed(self):
        scenario = _load("Iso-03-stairs")
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe()
            speeds = _segment_speed(df, result.frame_rate, 10.0, 20.0)
            assert len(speeds) > 0, "no agent crossed the stair segment"
            observed = speeds.mean()
            assert abs(observed - 0.5) <= 0.05, f"observed {observed:.3f} m/s"
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Test 4 - Movement around a corner
# ---------------------------------------------------------------------------


class TestIso04Corner:
    """ISO Test 4 - agents navigate an L-shaped corridor.

    Acceptance: all agents evacuate and no trajectory point penetrates the
    walkable boundary (walkable polygon buffered by 1 mm contains every point).
    """

    def test_full_evacuation_no_penetration(self):
        scenario = _load("Iso-04-corner")
        result = run_scenario(scenario, seed=42)
        try:
            assert result.agents_remaining == 0, "not all agents evacuated"
            df = result.trajectory_dataframe()
            walkable_with_tol = scenario.walkable_polygon.buffer(0.001)
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
# Test 5 - Assigned agent demographics
# ---------------------------------------------------------------------------


class TestIso05Demographics:
    """ISO Test 5 - 100 agents with Gaussian-distributed desired speed.

    The shipped ZIP has no exit, so (following the notebook) a full-width exit
    is added on the far x = 100 wall and the crowd walks the 100 m room in free
    flow. Acceptance: the represented walking-speed distribution matches the
    assigned Gaussian(1.2, 0.2) - pooled mean and std within 0.05 of the
    parameters and the per-run shape Gaussian-consistent (median two-sample
    KS p > 0.05). Pooled over 3 seeds (42/43/44); the notebook uses 5.
    """

    MU = 1.2
    SIGMA = 0.2
    SEEDS = [42, 43, 44]

    def _measure(self, seed):
        s = _load("Iso-05-demographics")
        s.add_exit([(100, 0), (100, 100), (99.7, 100), (99.7, 0)])
        result = run_scenario(s, seed=seed)
        try:
            traj = result.trajectory_dataframe().sort_values(["id", "frame"])
            fr = result.frame_rate
            speeds = []
            for _aid, sub in traj.groupby("id"):
                step = np.hypot(np.diff(sub.x), np.diff(sub.y)) * fr
                step = step[step > 0.05]
                if len(step):
                    speeds.append(np.median(step))
            return np.array(speeds)
        finally:
            result.cleanup()

    def test_represented_speed_matches_gaussian(self):
        from scipy import stats as _stats

        scenario = _load("Iso-05-demographics")
        params = scenario.distributions["jps-distributions_0"]["parameters"]
        assert params["v0_distribution"] == "gaussian"
        assert int(params["number"]) == 100

        assigned = np.random.default_rng(0).normal(self.MU, self.SIGMA, 100000)
        all_speeds = []
        ks_p = []
        for seed in self.SEEDS:
            sp = self._measure(seed)
            assert len(sp) > 0, f"seed {seed}: no agent moved"
            all_speeds.append(sp)
            ks_p.append(_stats.ks_2samp(sp, assigned).pvalue)
        all_speeds = np.concatenate(all_speeds)
        assert abs(all_speeds.mean() - self.MU) < 0.05, all_speeds.mean()
        assert abs(all_speeds.std() - self.SIGMA) < 0.05, all_speeds.std()
        assert np.median(ks_p) > 0.05, ks_p


# ---------------------------------------------------------------------------
# Test 6 - Horizontal counter-flows
# ---------------------------------------------------------------------------


class TestIso06Counterflow:
    """ISO Test 6 - 100 primary agents cross a corridor against 0/10/50/100
    counter-flow agents.

    ISO criterion: the crossing time (primary crowd reaching room 2) must
    increase with counter-flow. CollisionFreeSpeedModel forms no lanes, so at
    >= 50 counter-flow agents the primary crowd deadlocks and almost none reach
    room 2 - the crossing time is undefined, so the expected increase cannot be
    produced. The model fails ISO Test 6.
    """

    CF_ZIPS = {
        0: "Iso-06-counterflow-0",
        10: "Iso-06-counterflow-10",
        50: "Iso-06-counterflow-50",
        100: "Iso-06-counterflow-100",
    }
    BUDGET_S = 200
    ROOM2_X = 20.0

    @pytest.mark.xfail(
        reason="CollisionFreeSpeedModel forms no lanes: the primary crowd "
        "deadlocks at >= 50 counter-flow agents, so a room-2 crossing time is "
        "undefined and the ISO-expected increase with counter-flow cannot be "
        "produced. Needs AnticipationVelocityModel.",
        raises=AssertionError,
        strict=True,
    )
    def test_crossing_time_increases_with_counterflow(self):
        crossing_times = []
        for cf, stem in self.CF_ZIPS.items():
            scenario = _load(stem)
            scenario.max_simulation_time = self.BUDGET_S
            result = run_scenario(scenario, seed=42)
            try:
                df = result.trajectory_dataframe().sort_values(["id", "frame"])
                first_x = df.groupby("id").first().x
                primary = first_x[first_x < 5.0].index
                reach = []
                for aid in primary:
                    sub = df[df.id == aid]
                    hit = sub[sub.x >= self.ROOM2_X]
                    if len(hit):
                        reach.append(hit.iloc[0].frame / result.frame_rate)
                # Crossing time is only defined when the whole primary crowd
                # crosses; a deadlocked branch yields nan.
                if len(reach) == len(primary) and len(reach) > 0:
                    crossing_times.append(float(np.mean(reach)))
                else:
                    crossing_times.append(float("nan"))
            finally:
                result.cleanup()
        deltas = np.diff(crossing_times)
        assert (deltas > 0).all(), (
            f"crossing time not increasing with counter-flow: {crossing_times}"
        )


# ---------------------------------------------------------------------------
# Test 7 - Overtaking people with movement disabilities
# ---------------------------------------------------------------------------


class TestIso07MovementDisabilities:
    """ISO Test 7 - 24 occupants overtake one slower, larger occupant.

    Two otherwise identical scenarios are compared. In the disability case,
    the Zone-2 occupant has a lower desired speed and a larger radius. In the
    control case, that occupant has the same characteristics as the other
    occupants. Acceptance: the disability case has a longer total evacuation
    time than the control case.
    """

    def test_movement_disability_increases_evacuation_time(self):
        disability = _load("Iso-07-movement-disabilities")
        control = _load("Iso-07-movement-disabilities-no-disability")

        disability_result = run_scenario(disability, seed=42)
        control_result = run_scenario(control, seed=42)
        try:
            assert disability_result.agents_remaining == 0, (
                "not all agents evacuated in the movement-disability scenario"
            )
            assert control_result.agents_remaining == 0, (
                "not all agents evacuated in the control scenario"
            )
            assert disability_result.evacuation_time > control_result.evacuation_time, (
                "expected the movement-disability scenario to take longer: "
                f"{disability_result.evacuation_time:.2f}s versus "
                f"{control_result.evacuation_time:.2f}s"
            )
        finally:
            disability_result.cleanup()
            control_result.cleanup()


# ---------------------------------------------------------------------------
# Test 8 - Exit route allocation
# ---------------------------------------------------------------------------


class TestIso08RouteAllocation:
    """ISO Test 8 - agents in several rooms are allocated to exits by journey.

    Acceptance: every agent ends at the exit allocated by its journey (100%
    match between spawn-distribution -> journey -> exit and the nearest exit to
    the agent's final position).
    """

    def test_each_agent_reaches_allocated_exit(self):
        scenario = _load("Iso-08-route-allocation")
        raw = scenario.raw
        if "journeys" in raw:
            journey_map = {
                j["stages"][0]: j["stages"][-1] for j in raw["journeys"]
            }
        else:
            j_to_exit = {
                j["id"]: j["sequence"][-1] for j in raw.get("journeys_v2", [])
            }
            journey_map = {
                did: j_to_exit.get(
                    (d.get("journey_weights") or [{}])[0].get("journey_id"), ""
                )
                for did, d in scenario.distributions.items()
            }
        dist_polys = {
            did: Polygon(d["coordinates"])
            for did, d in scenario.distributions.items()
        }
        exit_polys = {
            eid: Polygon(e["coordinates"]) for eid, e in scenario.exits.items()
        }
        result = run_scenario(scenario, seed=42)
        try:
            assert result.agents_remaining == 0, "not all agents evacuated"
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            first = df.groupby("id").first().reset_index()
            last = df.groupby("id").last().reset_index()
            mismatches = []
            for row_first, row_last in zip(first.itertuples(), last.itertuples()):
                p0 = Point(row_first.x, row_first.y)
                p1 = Point(row_last.x, row_last.y)
                home = next(
                    (did for did, poly in dist_polys.items() if poly.covers(p0)),
                    None,
                )
                assert home is not None, f"agent {row_first.id} spawned nowhere"
                expected = journey_map[home]
                actual = min(
                    exit_polys, key=lambda eid: exit_polys[eid].distance(p1)
                )
                if actual != expected:
                    mismatches.append(
                        f"agent {row_first.id} from {home}: "
                        f"expected {expected}, got {actual}"
                    )
            assert not mismatches, "allocation mismatches:\n" + "\n".join(
                mismatches[:5]
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Test 9 - Dynamic availability of exits
# ---------------------------------------------------------------------------


class TestIso09DynamicExitAvailability:
    """ISO Test 9 - an occupant reroutes when Exit 1 becomes unavailable.

    Both exits are initially available and the occupant starts by targeting
    Exit 1. At t = 1 s, Exit 1 is made unavailable by redirecting the active
    agent to Exit 2. Acceptance: the occupant evacuates through Exit 2.
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

        scenario = _load("Iso-09-dynamic-exits")
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
# Test 10 - Congestion
# ---------------------------------------------------------------------------


class TestIso10Congestion:
    """ISO Test 10 - a room funnels into a 2 m neck leading to a stair.

    Following the notebook, the upper half of the corridor (y = 14..20) is
    modelled as the stair via a speed_factor = 0.5 zone. Acceptance: peak
    density builds both at the room->neck entrance and at the stair base, each
    exceeding the steady mid-corridor density.
    """

    def test_congestion_at_neck_and_stair(self):
        scenario = _load("Iso-10-congestion")
        scenario.add_zone(
            [(3, 14.0), (5, 14.0), (5, 20), (3, 20)],
            key="jps-zones_stair",
            speed_factor=0.5,
        )
        result = run_scenario(scenario, seed=42)
        try:
            df = result.trajectory_dataframe()

            def rho(sub, x0, x1, y0, y1):
                n = len(
                    sub[
                        (sub.x >= x0)
                        & (sub.x <= x1)
                        & (sub.y >= y0)
                        & (sub.y <= y1)
                    ]
                )
                return n / ((x1 - x0) * (y1 - y0))

            peak_neck = peak_corr = peak_stair = 0.0
            for _frame, sub in df.groupby("frame"):
                peak_neck = max(peak_neck, rho(sub, 3.0, 5.0, 4.5, 5.5))
                peak_corr = max(peak_corr, rho(sub, 3.0, 5.0, 8.0, 10.0))
                peak_stair = max(peak_stair, rho(sub, 3.0, 5.0, 13.0, 14.0))
            assert peak_neck > peak_corr, (peak_neck, peak_corr)
            assert peak_stair > peak_corr, (peak_stair, peak_corr)
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Test 11 - Maximum flow rates
# ---------------------------------------------------------------------------


class TestIso11MaxFlow:
    """ISO Test 11 - 100 agents evacuate a room through a 1 m exit.

    ISO cites the IMO MSC/Circ.1238 maximum specific flow of 1.33 p/m/s.
    CollisionFreeSpeedModel has no door-flow limiter, so the sustained emergent
    specific flow (~5 p/m/s on seed 42) exceeds the reference. The model fails
    the IMO non-exceedance criterion (mirrors NIST Verif.5.2).
    """

    EXIT_WIDTH_M = 1.0
    IMO_MAX_FLOW = 1.33

    @pytest.mark.xfail(
        reason="CollisionFreeSpeedModel has no door-flow limiter, so the "
        "emergent specific flow through the 1 m exit (~5 p/m/s) exceeds the "
        "IMO 1.33 p/m/s reference. Needs a flow-limiting exit model.",
        raises=AssertionError,
        strict=True,
    )
    def test_flow_within_imo_reference(self):
        scenario = _load("Iso-11-max-flow")
        result = run_scenario(scenario, seed=42)
        try:
            assert result.agents_remaining == 0, "not all agents evacuated"
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            exit_times = np.sort(
                (df.groupby("id").frame.max() / result.frame_rate).values
            )
            assert len(exit_times) > 0, "no agent reached the exit"
            span = exit_times.max() - exit_times.min()
            flow = result.agents_evacuated / span / self.EXIT_WIDTH_M
            assert flow <= self.IMO_MAX_FLOW, (
                f"specific flow {flow:.2f} p/m/s exceeds IMO {self.IMO_MAX_FLOW}"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Test 12 - Stair flow rates
# ---------------------------------------------------------------------------


class TestIso12StairFlow:
    """ISO Test 12 - flow rate versus stair width (1.0..1.8 m, 100 occupants).

    Acceptance: flow increases with stair width - the widest stair exceeds the
    narrowest and the linear trend has positive slope.
    """

    def test_flow_increases_with_width(self):
        import json
        import tempfile

        builder = _load_builder("iso12_stair_flow")
        rows = []
        for width in builder.STAIR_WIDTHS:
            raw = builder.build_raw_scenario(width)
            with tempfile.TemporaryDirectory() as d:
                p = pathlib.Path(d)
                (p / "config.json").write_text(
                    json.dumps(raw), encoding="utf-8"
                )
                (p / "geometry.wkt").write_text(
                    builder.build_geometry_wkt(width), encoding="utf-8"
                )
                scenario = load_scenario(str(p))
                result = run_scenario(scenario, seed=42)
                try:
                    rows.append(
                        builder.measure_flow(
                            result.trajectory_dataframe(),
                            result.frame_rate,
                            width,
                        )
                    )
                finally:
                    result.cleanup()
        widths = np.array([r["width"] for r in rows])
        flows = np.array([r["flow_p_s"] for r in rows])
        valid = ~np.isnan(flows)
        widths, flows = widths[valid], flows[valid]
        assert len(flows) >= 2, "not enough valid flow measurements"
        slope = np.polyfit(widths, flows, 1)[0]
        assert slope > 0, f"flow-vs-width slope {slope:.3f} not positive"
        assert flows[-1] > flows[0], (
            f"widest flow {flows[-1]:.3f} <= narrowest {flows[0]:.3f}"
        )


# ---------------------------------------------------------------------------
# Test 13 - Corridor fundamental diagram
# ---------------------------------------------------------------------------


class TestIso13CorridorFd:
    """ISO Test 13 - corridor speed/flow/density fundamental diagram.

    ISO expects the zone-2 speed-density relationship to be consistent with the
    model's own assumptions. It is not: every measured mean zone-2 speed
    exceeds the 1.0 m/s free speed assigned to the crowd, so the model does not
    reproduce a valid fundamental diagram and fails ISO Test 13. A 3-density
    subset {60, 120, 240} of the notebook's 5-point sweep is used for runtime;
    the free-speed violation holds at every density.
    """

    FREE_SPEED = 1.0
    DENSITIES = [60, 120, 240]

    @pytest.mark.xfail(
        reason="CollisionFreeSpeedModel does not reproduce an empirical "
        "fundamental diagram: measured zone-2 speeds (1.9-3.1 m/s) exceed the "
        "1.0 m/s free speed assigned to the crowd, contradicting the model's "
        "own assumptions.",
        raises=AssertionError,
        strict=True,
    )
    def test_zone2_speed_within_free_speed(self):
        import json
        import tempfile

        builder = _load_builder("iso13_corridor_fd")
        speeds = []
        for n in self.DENSITIES:
            raw = builder.build_raw_scenario(n)
            with tempfile.TemporaryDirectory() as d:
                p = pathlib.Path(d)
                (p / "config.json").write_text(
                    json.dumps(raw), encoding="utf-8"
                )
                (p / "geometry.wkt").write_text(
                    builder.CORRIDOR_WKT, encoding="utf-8"
                )
                scenario = load_scenario(str(p))
                result = run_scenario(scenario, seed=42)
                try:
                    m = builder.measure_zone2(
                        result.trajectory_dataframe(), result.frame_rate
                    )
                    speeds.append(m["mean_speed_zone2_m_s"])
                finally:
                    result.cleanup()
        speeds = np.array(speeds)
        assert (speeds <= self.FREE_SPEED + 0.05).all(), (
            f"zone-2 speeds {speeds.round(2)} exceed free speed "
            f"{self.FREE_SPEED} m/s"
        )


# ---------------------------------------------------------------------------
# Test 14 - Group behaviour
# ---------------------------------------------------------------------------


class TestIso14Groups:
    """ISO Test 14 - a fast homogeneous group (5 @ 1.25 m/s) shares the room
    with a slow group (10 @ 0.5 m/s).

    Acceptance: the fast group reaches the exit together (arrival spread
    <= 10 s). Unlike NIST Verif.2.9 (a mixed-speed group that the model cannot
    hold together), Group 1 here is homogeneous in speed and spawns together,
    so it stays coherent (~4.5 s spread) without any group-cohesion sub-model.
    """

    GROUP1_SIZE = 5
    MAX_SPREAD_S = 10.0

    def test_group1_arrives_together(self):
        scenario = _load("Iso-14-group-behaviour")
        result = run_scenario(scenario, seed=42)
        try:
            assert result.total_agents == 15
            df = result.trajectory_dataframe().sort_values(["id", "frame"])
            grouped = df.groupby("id")
            first_y = grouped.first().y
            exit_time = grouped.frame.max() / result.frame_rate
            # Group 1 (fast) spawns highest (y ~ 20); Group 2 sits at y ~ 11.
            group1 = first_y.sort_values(ascending=False).head(self.GROUP1_SIZE).index
            spread = exit_time[group1].max() - exit_time[group1].min()
            assert spread <= self.MAX_SPREAD_S, (
                f"Group 1 arrival spread {spread:.1f} s exceeds "
                f"{self.MAX_SPREAD_S} s"
            )
        finally:
            result.cleanup()


# ---------------------------------------------------------------------------
# Test 15 - Social influence on exit choice
# ---------------------------------------------------------------------------


class TestIso15SocialInfluence:
    """ISO Test 15 - two free occupants choose between two equidistant exits
    (50/50). Scenario 2 adds a third occupant deterministically bound to
    exit 2.

    ISO criterion: social influence should raise exit-2 usage among the free
    ("behind") occupants in Scenario 2 relative to the Scenario 1 baseline.
    CollisionFreeSpeedModel has no social-influence model, so the free
    occupants' exit-2 fraction is unchanged (0.5 in both), and the expected
    increase does not occur. The model fails ISO Test 15.
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
                    init = df[df.frame == df.frame.min()]
                    ids = (
                        init[init.y < 1.5].id.tolist()
                        if track == "behind"
                        else init[init.y >= 1.5].id.tolist()
                    )
                    finals = [
                        df[df.id == a].sort_values("frame").iloc[-1] for a in ids
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
        "usage unchanged (0.5 in both scenarios), so the ISO-expected increase "
        "does not occur.",
        raises=AssertionError,
        strict=True,
    )
    def test_social_influence_raises_exit2_usage(self):
        scenario_1 = _load("Iso-15-social-influence-1")
        scenario_2 = _load("Iso-15-social-influence-2")
        baseline = self._exit2_fraction(scenario_1, self.SEEDS)
        behind = self._exit2_fraction(scenario_2, self.SEEDS, track="behind")
        assert behind > baseline, (
            f"free-occupant exit-2 usage {behind:.3f} not increased over "
            f"baseline {baseline:.3f}"
        )


# ---------------------------------------------------------------------------
# Test 16 - Affiliation to familiar exits
# ---------------------------------------------------------------------------


class TestIso16FamiliarExits:
    """ISO Test 16 - occupants choose between two equidistant exits.

    CollisionFreeSpeedModel has no intrinsic affiliation model; affiliation to a
    familiar exit is emulated via the journey weight. Acceptance: with balanced
    50/50 weights the two exits are used equally, and raising the exit-2 journey
    weight to 80 makes exit 2 strictly preferred. Evaluated over 20 seeds.
    """

    DIST_ID = "jps-distributions_0"
    JOURNEY_EXIT1 = "journey-1781167866144-fp65t3"
    JOURNEY_EXIT2 = "journey-1781167890022-3hfgs4"
    SEEDS = range(1, 21)

    @staticmethod
    def _set_weight(scenario, journey_id, weight):
        for entry in scenario.raw["distributions"][
            TestIso16FamiliarExits.DIST_ID
        ]["journey_weights"]:
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
        balanced = _load("Iso-16-familiar-exits")
        e1_bal, e2_bal = self._exit_counts(balanced)
        total_bal = e1_bal + e2_bal
        assert total_bal > 0, "no agent reached an exit in the balanced case"
        frac_diff = abs(e1_bal - e2_bal) / total_bal
        assert frac_diff <= 0.10, (
            f"50/50 weights not balanced: {e1_bal} vs {e2_bal}"
        )

        affiliated = _load("Iso-16-familiar-exits")
        self._set_weight(affiliated, self.JOURNEY_EXIT1, 20)
        self._set_weight(affiliated, self.JOURNEY_EXIT2, 80)
        e1_aff, e2_aff = self._exit_counts(affiliated)
        assert e2_aff > e1_aff, (
            f"exit 2 not preferred under affiliation: {e1_aff} vs {e2_aff}"
        )


# ---------------------------------------------------------------------------
# Test 17 - Route choice based on geometric layout
# ---------------------------------------------------------------------------


class TestIso17RouteChoice:
    """ISO Test 17 - two routes to the target: a long way around a ring or a
    shorter stair shortcut.

    Acceptance: the shortcut is geometrically shorter than the long way, and the
    large majority of agents take it (fraction via shortcut > 0.5).
    """

    def test_agents_prefer_shorter_route(self):
        import json
        import tempfile

        builder = _load_builder("iso17_route_choice")
        raw = builder.build_raw_scenario()
        with tempfile.TemporaryDirectory() as d:
            p = pathlib.Path(d)
            (p / "config.json").write_text(json.dumps(raw), encoding="utf-8")
            (p / "geometry.wkt").write_text(
                builder.build_geometry_wkt(), encoding="utf-8"
            )
            scenario = load_scenario(str(p))
            result = run_scenario(scenario, seed=42)
            try:
                choice = builder.measure_route_choice(
                    result.trajectory_dataframe(), result.frame_rate
                )
            finally:
                result.cleanup()
        ref = builder.route_length_reference()
        assert ref["shortcut_m"] < ref["long_way_m"], (
            "geometry error: shortcut is not shorter than the long way"
        )
        assert choice["frac_shortcut"] > 0.5, choice


# ---------------------------------------------------------------------------
# Test 18 - Reduced visibility vs walking speed
# ---------------------------------------------------------------------------


class TestIso18ReducedVisibility(TestNist25ReducedVisibility):
    """ISO 20414:2020 Test 18 - Reduced visibility vs walking speed.

    Reuses the NIST 2.5 simulation and smoke-speed adapter.

    The test compares simulated travel time with the analytical
    prediction obtained from the selected speed-reduction correlation.

    Limitation:
        The reused NIST scenario has a 120 m corridor with a
        100 m measurement section, rather than the 100 m corridor
        specified by ISO Table 21.

        Smoke effects are implemented through an external adapter,
        not a native JuPedSim smoke model.
    """


# ---------------------------------------------------------------------------
# Test 19 - Occupant incapacitation by fire/smoke
# ---------------------------------------------------------------------------


class TestIso19OccupantIncapacitation(TestNist26OccupantIncapacitation):
    """ISO 20414:2020 Test 19 - Occupant incapacitation by fire/smoke.

    Reuses the NIST 2.6 FED verification implementation.

    The test compares the simulated time to FED=1 against an
    independent analytical calculation under constant exposure.

    The occupant remains stationary in a 10 m x 10 m room.

    Limitation:
        FED accumulation is implemented by an external V&V
        adapter rather than a native JuPedSim hazard model.
    """