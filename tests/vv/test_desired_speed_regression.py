"""Regression test: desired_speed mean and spread must reach the agents.

Bug (fixed in jupedsim-scenarios 0.6.5, #73/#74): the reduced
distribution-parameter views in ``simulation_init`` (``_process_distributions``
and the flow-spawn builder) whitelisted only the legacy ``v0``, ``v0_std`` and
``v0_distribution`` keys, while the public API stores the canonical
``desired_speed``, ``desired_speed_std`` and ``desired_speed_distribution``
keys. A Gaussian speed spread authored with the canonical names was silently
dropped: every agent spawned at the 1.2 m/s default with no spread. The fix
reads the canonical keys first and falls back to the legacy names.

The configuration-level checks in ``test_nist.py`` and ``test_iso.py`` read the
scenario JSON, not the speeds the simulation uses, so they pass on the affected
version. This test measures the speeds the agents actually walk at.

Expected behaviour on a fixed version: 40 agents with desired speed
N(1.0, 0.2) m/s walk a free corridor; their settled speeds have mean near 1.0
m/s and a clearly non-zero spread. On the affected version the agents all walk
at 1.2 m/s and the spread is only what crowd interaction produces.
"""

import numpy as np
import pytest
from vv_helpers import HAS_VV_DEPS, run_vv_scenario

pytestmark = [
    pytest.mark.vv,
    pytest.mark.skipif(not HAS_VV_DEPS, reason="V&V dependencies not installed"),
]

WALKABLE = "POLYGON ((0 0, 40 0, 40 10, 0 10, 0 0))"
EXITS = {
    "jps-exits_0": {
        "type": "polygon",
        "coordinates": [[38, 0], [40, 0], [40, 10], [38, 10], [38, 0]],
        "enable_throughput_throttling": False,
        "max_throughput": 0,
    }
}
N_AGENTS = 40
SEED = 42
MU = 1.0  # configured desired-speed mean, m/s (differs from the 1.2 default)
SIGMA = 0.2  # configured desired-speed standard deviation, m/s
WINDOW = (5.0, 15.0)  # s; after the initial spawn push, before the exit

# Mean tolerance: the standard error of a 40-sample mean is SIGMA / sqrt(40),
# about 0.03 m/s. Crowd interaction in the 8 m x 10 m spawn block shifts the
# fixed version's mean by about 0.01 m/s on seed 42 and up to about 0.1 m/s on
# other seeds. The affected version's mean sits near the 1.2 m/s default,
# outside this tolerance.
MEAN_TOL = 0.1
# Spread floor: half the configured SIGMA. The sample std of 40 Gaussian draws
# is about 0.2 +/- 0.02, and crowd interaction only partly flattens the
# spread, so a fixed version clears this comfortably. A version that drops the
# spread leaves only crowd-induced variation, far below this floor.
MIN_STD = 0.5 * SIGMA

DIST = {
    "jps-distributions_0": {
        "type": "polygon",
        "coordinates": [[0, 0], [8, 0], [8, 10], [0, 10], [0, 0]],
        "parameters": {
            "number": N_AGENTS,
            "radius": 0.15,
            "desired_speed": MU,
            "desired_speed_std": SIGMA,
            "desired_speed_distribution": "gaussian",
            "use_flow_spawning": False,
            "distribution_mode": "by_number",
            "radius_distribution": "constant",
        },
    }
}


def _settled_speeds(trajectory) -> np.ndarray:
    """Mean walking speed per agent over WINDOW, from its in-window path."""
    df = trajectory.data
    t = df["frame"] / trajectory.frame_rate
    window = df[(t >= WINDOW[0]) & (t <= WINDOW[1])].sort_values(["id", "frame"])
    speeds = []
    for _, agent in window.groupby("id"):
        dt = (agent["frame"].iloc[-1] - agent["frame"].iloc[0]) / trajectory.frame_rate
        if dt <= 0:
            continue
        steps = np.hypot(np.diff(agent["x"]), np.diff(agent["y"]))
        speeds.append(steps.sum() / dt)
    return np.array(speeds)


class TestDesiredSpeedReachesAgents:
    """Canonical desired_speed* keys must set the speeds agents walk at."""

    def test_speed_mean_and_spread_match_configuration(self):
        _, trajectory = run_vv_scenario(
            walkable_area_wkt=WALKABLE,
            exits=EXITS,
            distributions=DIST,
            max_simulation_time=60.0,
            seed=SEED,
        )
        assert trajectory is not None, "trajectory could not be loaded"

        speeds = _settled_speeds(trajectory)
        assert len(speeds) >= N_AGENTS // 2, (
            f"only {len(speeds)} agents present across the settled window"
        )

        mean = speeds.mean()
        std = speeds.std(ddof=1)
        assert std >= MIN_STD, (
            f"settled speed std {std:.3f} m/s below floor {MIN_STD:.3f} m/s "
            f"(configured sigma {SIGMA} m/s); mean {mean:.3f} m/s. "
            "Desired-speed spread did not reach the agents."
        )
        assert abs(mean - MU) <= MEAN_TOL, (
            f"mean settled speed {mean:.3f} m/s, configured {MU} m/s "
            f"(tolerance {MEAN_TOL}); std {std:.3f} m/s"
        )
