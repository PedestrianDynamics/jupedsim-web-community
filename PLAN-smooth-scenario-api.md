# Plan: Smooth Rough Edges in `scripts/core/scenario.py`

## Status: COMPLETE

## Engineering Classification: APPROPRIATELY ENGINEERED

Reasoning:
- The branch goal is small and local: make `Scenario` easier to use from notebooks without redesigning the runner.
- The original additive API work was useful, but it was underengineered for correctness because some setters updated only convenience fields and not the serialized scenario config.
- The final version keeps the convenience surface small, preserves backward compatibility, and adds targeted tests to prevent silent divergence between notebook-visible state and executed state.

All changes in `scripts/core/scenario.py`. Additive and backward-compatible.

---

## Task 1: Add `Scenario.copy()` — DONE

- Add `copy()` method returning `copy.deepcopy(self)`
- Placed after setters block
- Enables safe Monte Carlo / parameter sweep workflows

## Task 2: Distribution/zone/stage ID discovery + index aliases — DONE

### 2a: Resolver helpers (private)
- `_resolve_distribution_id(id: int | str) -> str` — accept `0`, `1`, … as aliases for dict keys
- `_resolve_zone_id(id: int | str) -> str` — same for zones
- `_resolve_stage_id(id: int | str) -> str` — same for stages/checkpoints
- All raise `IndexError` for out-of-range ints, `KeyError` for missing strings

### 2b: Discovery methods
- `list_distributions()` → `[{"index", "id", "agents", "flow"}, ...]`
- `list_zones()` → `[{"index", "id", "speed_factor"}, ...]`
- `list_stages()` → `[{"index", "id", "waiting_time"}, ...]`

### 2c: Update existing setters to use resolvers
- `set_agent_count(distribution_id, ...)` — calls `_resolve_distribution_id`
- `set_agent_params(distribution_id, ...)` — calls `_resolve_distribution_id`
- Both now accept `int | str` instead of only `str`

## Task 3: New setters for zones and checkpoints — DONE

- `set_zone_speed_factor(zone_id: int | str, factor: float)` — validates non-negative
- `set_checkpoint_waiting_time(checkpoint_id: int | str, waiting_time: float)` — validates non-negative
- Both use resolvers, modify `self.raw` in-place

## Task 4: Input validation in existing setters — DONE

| Setter | Validation |
|---|---|
| `set_agent_count` | `count` must be positive int |
| `set_seed` | `seed` must be non-negative int |
| `set_max_time` | `seconds` must be positive number |
| `set_model_params` | numeric values must be non-negative |
| `set_agent_params` | `radius` ∈ (0, 1.0], `desired_speed` ∈ (0, 5.0], `number` positive int |

Bounds match `_sample_agent_values` clipping (lines 143–154).

## Task 5: Post-Review Hardening — DONE

### 5a: Keep `Scenario` runtime state and `raw` config in sync

Problem:
- `run_scenario()` serializes `scenario.raw` to JSON before execution.
- Some new setters only updated `self.seed`, `self.model_type`, or `self.sim_params`.
- That could leave the object internally inconsistent and create silent surprises for users inspecting `scenario.raw` or reusing saved config.

Changes:
- Added `_simulation_settings()` and `_simulation_params()` helpers.
- Added `_sync_runtime_to_raw()` and called it from `__post_init__()`.
- Updated these setters to write through to `raw["config"]["simulation_settings"]` as well as runtime fields:
  - `set_seed()`
  - `set_max_time()`
  - `set_model_type()`
  - `set_model_params()`

Reasoning:
- There should be one effective source of truth after every mutation.
- Convenience setters are only safe if the executed scenario and the visible scenario stay identical.

### 5b: Alias-safe agent parameter updates

Problem:
- `set_agent_params()` documented support for both `desired_speed` and `v0`, plus their std/distribution aliases.
- Validation only covered the `desired_speed` spelling, so invalid `v0` values could bypass checks.
- Mixing aliases could also leave only one spelling updated, which is error-prone for later consumers.

Changes:
- Unified validation across:
  - `desired_speed` / `v0`
  - `desired_speed_std` / `v0_std`
  - `desired_speed_distribution` / `v0_distribution`
- Mirrored accepted values back into both spellings in the distribution parameters.

Reasoning:
- Users should not need to remember the “correct” spelling to get safe behavior.
- A smooth API must prevent alias-dependent surprises.

### 5c: Regression tests for the convenience API

Added:
- `scripts/test_scenario_api.py`

Coverage:
- runtime setters keep `raw` config in sync
- speed aliases are mirrored consistently
- index-based zone/stage/distribution setters modify the expected objects
- invalid alias-based inputs raise clear `ValueError`s

Reasoning:
- The branch introduces a public convenience API.
- Notebook demos are not enough to guarantee correctness.
- Small, focused tests are the cheapest way to prevent silent API drift.

## Task 6: Verification — DONE

1. Focused API regression tests:
   ```bash
   uv run --project /Users/chraibi/workspace/PedestrianDynamics/Web-Based-Jupedsim-issues --extra dev pytest \
     /Users/chraibi/workspace/PedestrianDynamics/Web-Based-Jupedsim-issues/scripts/test_scenario_api.py \
     /Users/chraibi/workspace/PedestrianDynamics/Web-Based-Jupedsim-issues/scripts/test_shared_examples.py -q
   ```
   Result: `8 passed`
2. Smoke test in REPL:
   ```python
   from core.scenario import load_scenario
   s = load_scenario("some_scenario.zip")
   s.list_distributions()        # discovery works
   s.set_agent_count(0, 50)      # index alias works
   s2 = s.copy()                 # independent copy
   s.set_agent_count(0, 100)
   assert s2.distributions[list(s2.distributions)[0]]["parameters"]["number"] == 50
   ```
3. Validation examples:
   - `s.set_agent_count(0, -1)` raises `ValueError`
   - `s.set_agent_params(0, v0=-1)` raises `ValueError`
   - `s.set_seed(123)` updates both `s.seed` and `s.raw["config"]["simulation_settings"]["baseSeed"]`
