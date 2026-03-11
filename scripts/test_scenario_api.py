from pathlib import Path

import pytest

from core.scenario import load_scenario


SCENARIOS_DIR = Path(__file__).resolve().parent / "scenarios"


def test_runtime_mutators_keep_raw_config_in_sync():
    scenario = load_scenario(str(SCENARIOS_DIR / "bottleneck-zone"))

    scenario.set_seed(123)
    scenario.set_max_time(456)
    scenario.set_model_type("GeneralizedCentrifugalForceModel")
    scenario.set_model_params(gcfm_strength_neighbor_repulsion=0.7)

    settings = scenario.raw["config"]["simulation_settings"]
    params = settings["simulationParams"]

    assert scenario.seed == 123
    assert settings["baseSeed"] == 123
    assert scenario.max_simulation_time == 456
    assert params["max_simulation_time"] == 456
    assert scenario.model_type == "GeneralizedCentrifugalForceModel"
    assert params["model_type"] == "GeneralizedCentrifugalForceModel"
    assert scenario.sim_params["gcfm_strength_neighbor_repulsion"] == 0.7
    assert params["gcfm_strength_neighbor_repulsion"] == 0.7


def test_agent_param_aliases_are_mirrored_consistently():
    scenario = load_scenario(str(SCENARIOS_DIR / "bottleneck-zone"))

    scenario.set_agent_params(
        0,
        v0=1.7,
        v0_std=0.15,
        v0_distribution="gaussian",
        number=12,
    )

    params = scenario.distributions["jps-distributions_0"]["parameters"]
    assert params["v0"] == pytest.approx(1.7)
    assert params["desired_speed"] == pytest.approx(1.7)
    assert params["v0_std"] == pytest.approx(0.15)
    assert params["desired_speed_std"] == pytest.approx(0.15)
    assert params["v0_distribution"] == "gaussian"
    assert params["desired_speed_distribution"] == "gaussian"
    assert params["number"] == 12


def test_index_based_zone_and_stage_mutators_hit_expected_objects():
    zone_scenario = load_scenario(str(SCENARIOS_DIR / "bottleneck-zone"))
    waiting_scenario = load_scenario(str(SCENARIOS_DIR / "waiting-stage-corridor"))

    zone_scenario.set_zone_speed_factor(0, 0.42)
    waiting_scenario.set_checkpoint_waiting_time(0, 8.5)
    waiting_scenario.set_agent_count(0, 17)

    assert zone_scenario.raw["zones"]["jps-zones_0"]["speed_factor"] == pytest.approx(0.42)
    assert waiting_scenario.raw["checkpoints"]["jps-checkpoints_0"]["waiting_time"] == pytest.approx(8.5)
    assert waiting_scenario.raw["distributions"]["jps-distributions_0"]["parameters"]["number"] == 17


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"v0": -1.0}, "desired_speed/v0"),
        ({"v0_std": -0.1}, "desired_speed_std/v0_std"),
        ({"v0_distribution": "lognormal"}, "desired_speed_distribution/v0_distribution"),
    ],
)
def test_invalid_agent_param_aliases_raise_clear_errors(kwargs, message):
    scenario = load_scenario(str(SCENARIOS_DIR / "bottleneck-zone"))

    with pytest.raises(ValueError, match=message):
        scenario.set_agent_params(0, **kwargs)
