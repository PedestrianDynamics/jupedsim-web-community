import copy
from pathlib import Path

from jupedsim_scenario import Scenario, load_scenario, run_scenario


SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def _scenario_clone(scenario: Scenario, raw):
    return Scenario(
        raw=raw,
        walkable_area_wkt=scenario.walkable_area_wkt,
        model_type=raw.get("config", {})
        .get("simulation_settings", {})
        .get("simulationParams", {})
        .get("model_type", scenario.model_type),
        seed=raw.get("config", {})
        .get("simulation_settings", {})
        .get("baseSeed", scenario.seed),
        sim_params=copy.deepcopy(
            raw.get("config", {})
            .get("simulation_settings", {})
            .get("simulationParams", scenario.sim_params)
        ),
        source_path=scenario.source_path,
    )


def test_bottleneck_zone_slows_down_agents():
    scenario = load_scenario(str(SCENARIOS_DIR / "bottleneck-zone"))
    baseline_raw = copy.deepcopy(scenario.raw)
    baseline_raw["zones"] = {}

    baseline = _scenario_clone(scenario, baseline_raw)
    baseline_result = run_scenario(baseline, seed=42)
    zone_result = run_scenario(scenario, seed=42)

    try:
        assert baseline_result.success
        assert zone_result.success
        assert zone_result.evacuation_time > baseline_result.evacuation_time
    finally:
        baseline_result.cleanup()
        zone_result.cleanup()


def test_waiting_stage_holds_agents_before_exit():
    scenario = load_scenario(str(SCENARIOS_DIR / "waiting-stage-corridor"))
    baseline_raw = copy.deepcopy(scenario.raw)
    baseline_raw["checkpoints"]["jps-checkpoints_0"]["waiting_time"] = 0.0

    baseline = _scenario_clone(scenario, baseline_raw)
    baseline_result = run_scenario(baseline, seed=42)
    waiting_result = run_scenario(scenario, seed=42)

    try:
        assert baseline_result.success
        assert waiting_result.success
        assert waiting_result.evacuation_time >= baseline_result.evacuation_time + 3.0
    finally:
        baseline_result.cleanup()
        waiting_result.cleanup()
