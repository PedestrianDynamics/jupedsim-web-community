"""Pre-evacuation time distributions used by the shared scenario runtime."""

from typing import Dict, Optional

import numpy as np


class PreMovementDistribution:
    """Base class for pre-evacuation time distributions."""

    def __init__(self, seed: Optional[int] = None):
        self.rng = np.random.default_rng(seed)

    def sample(self, n_samples: int) -> np.ndarray:
        raise NotImplementedError


class GammaDistribution(PreMovementDistribution):
    def __init__(self, a: float = 1.291, b: float = 103.901, seed: Optional[int] = None):
        super().__init__(seed)
        self.a = a
        self.b = b

    def sample(self, n_samples: int) -> np.ndarray:
        return self.rng.gamma(self.a, self.b, n_samples)


class LognormalDistribution(PreMovementDistribution):
    def __init__(self, a: float = 4.586, b: float = 0.967, seed: Optional[int] = None):
        super().__init__(seed)
        self.a = a
        self.b = b

    def sample(self, n_samples: int) -> np.ndarray:
        return self.rng.lognormal(self.a, self.b, n_samples)


class WeibullDistribution(PreMovementDistribution):
    def __init__(self, a: float = 139.285, b: float = 1.195, seed: Optional[int] = None):
        super().__init__(seed)
        self.a = a
        self.b = b

    def sample(self, n_samples: int) -> np.ndarray:
        return self.a * self.rng.weibull(self.b, n_samples)


class UniformDistribution(PreMovementDistribution):
    """Uniform distribution for RiMEA-style premovement windows."""

    def __init__(self, a: float = 0.0, b: float = 60.0, seed: Optional[int] = None):
        super().__init__(seed)
        self.a = a
        self.b = b

    def sample(self, n_samples: int) -> np.ndarray:
        return self.rng.uniform(self.a, self.b, n_samples)


def create_premovement_distribution(
    distribution_type: str,
    params: Dict[str, float],
    seed: Optional[int] = None,
) -> PreMovementDistribution:
    distributions = {
        "gamma": GammaDistribution,
        "lognormal": LognormalDistribution,
        "weibull": WeibullDistribution,
        "uniform": UniformDistribution,
    }

    if distribution_type not in distributions:
        raise ValueError(
            f"Unknown distribution type: {distribution_type}. "
            f"Must be one of: {list(distributions.keys())}"
        )

    dist_class = distributions[distribution_type]
    a = params.get("a")
    b = params.get("b")

    if a is not None and b is not None:
        return dist_class(a=a, b=b, seed=seed)
    if a is not None:
        return dist_class(a=a, seed=seed)
    if b is not None:
        return dist_class(b=b, seed=seed)
    return dist_class(seed=seed)


PREMOVEMENT_PRESETS = {
    "gamma": {"a": 1.291, "b": 103.901},
    "lognormal": {"a": 4.586, "b": 0.967},
    "weibull": {"a": 139.285, "b": 1.195},
    "uniform": {"a": 0.0, "b": 60.0},
}
