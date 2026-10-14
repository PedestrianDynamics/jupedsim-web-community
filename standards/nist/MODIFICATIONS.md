# Modifications relative to NIST TN 1822

This file lists every change applied to the original NIST TN 1822
verification scenarios in producing this contribution. Reviewers should
treat each entry as the *minimum* delta needed to make the test runnable in
the JuPedSim CollisionFreeSpeedModel via the jupedsim-web-community
workflow.

The original V&V tree (Codex-produced, audited locally) lives separately and
is not part of this PR; it is referenced here as "source tree".

NIST source: <https://nvlpubs.nist.gov/nistpubs/technicalnotes/NIST.TN.1822.pdf>

## A. Project-wide normalisations

These apply uniformly to every scenario in this PR.

### A1. Exit-capture polygon thickness = 0.3 m

NIST figures specify exit *width* (opening length along the wall) but not the
solver-side capture thickness, which is an implementation detail. In the
source tree this thickness varied from 0.25 m to 1.0 m. **All exit polygons
have been normalised to 0.3 m thickness**, matching the value used in S1
(Verif.1.1).

Exit opening width (along the wall) is **unchanged** and continues to follow
each NIST figure.

### A2. Terminology: "pre-evacuation time"

The source tree used several near-synonyms inconsistently ("immediate
response", "response time 0", "instant response", "pre-movement"). All
human-readable strings (READMEs, notebook prose, CSV labels) now use the
canonical NIST TN 1822 term **"pre-evacuation time"**.

Note: any custom JSON keys named `premovement_*` were *removed* during
schema cleanup (see section A3), so this terminology change has no effect on
machine-readable artifacts shipped in the ZIPs.

### A3. config.json schema cleanup

Every `config.json` was filtered to canonical JuPedSim-web-editor fields
only. Per-ZIP the following custom keys were stripped (full list in
`_staging/conversion_report.txt` of the upload package):

| Scenario           | Stripped key(s)                                                       |
|--------------------|-----------------------------------------------------------------------|
| Verif.1.1          | `parameters.premovement_cases_file` (custom file pointer; the four NIST distributions are stored in the reference CSV + builder instead) |
| Verif.2.1, 2.2     | top-level `metadata` (corridor-extension explanation)                 |
| Verif.2.4          | `parameters.desired_speed_distribution`, `desired_speed_std`          |
| Verif.2.9          | `parameters.group_id` (all 3 distributions), top-level `metadata`     |
| Verif.3.1          | `parameters.room_number` (all 12 distributions), top-level `metadata` |
| Verif.5.1          | `parameters.desired_speed_distribution`, `desired_speed_std`, `metadata` |
| Verif.5.2          | top-level `metadata`                                                  |

Note: `use_premovement`, `premovement_distribution`, `premovement_param_a`,
`premovement_param_b`, `max_throughput`, and `enable_throughput_throttling`
are **kept** in the ZIPs — they are part of the canonical JuPedSim-scenarios
loader schema (verified against `src/jupedsim_scenarios/simulation_init.py`
and against `rimea/scenario_files/rimea-12d-bottleneck/config.json`).

Stripped semantics are preserved by:

- `Verif.1.1`: `nist1_1_premovement_reference.csv` +
  `scenario_builders/nist1_1_premovement.py` enumerate the 4 distributions
  and provide a `sample_pre_evac_times` helper.
- `Verif.2.8` (no keys stripped from JSON but conceptually a sweep): the 4
  counterflow values are captured in `scenario_files/nist2_8_counterflow_reference.csv` +
  `scenario_builders/nist2_8_counterflow.py`.
- `Verif.2.9`, `Verif.5.2`, etc.: the notebook is expected to re-apply the
  removed semantics through whatever loader API exists. The mapping from
  removed key to expected notebook behaviour will be in the notebook prose.

### A4. Journey schema upgrade: `journeys_v2` + `journey_weights`

The JuPedSim web app exports the legacy `journeys` / `stages` schema. The
canonical `jupedsim_scenarios` loader, however, only enforces route
allocation when the ZIP also contains a `journeys_v2` block (with
`id` / `name` / `color` / `sequence`) plus per-distribution
`journey_weights`. When only legacy `journeys` is present the loader
falls back to nearest-exit routing, which silently misroutes agents in
multi-exit scenarios (notably Verif.3.1, where 2/13 agents from Room 10
went to the secondary exit instead of their allocated main exit).

Every ZIP in this contribution therefore ships **both schemas**:

- The original `journeys` / `stages` block is preserved (web-app export
  shape).
- A derived `journeys_v2` block is added at conversion time: each
  journey's `sequence` is the legacy `stages` list with the originating
  distribution removed. Each distribution gets a
  `journey_weights: [{journey_id: ..., weight: 100}]` entry tagging it
  to its single journey.

With this dual-schema ZIP the canonical loader enforces the NIST-allocated
routes strictly (verified: 13/13 agents reach their allocated exit on
Verif.3.1). The legacy block keeps the ZIP round-trippable through the
web app.

See `_staging/convert.py::clean_config` for the synthesis code.

## B. Per-scenario modifications

### B1. Verif.1.1 — Pre-evacuation time distributions

- **Schema cleanup** (A3): the custom `premovement_cases_file` pointer was
  removed (the loader has no such hook). The base ZIP keeps the uniform
  U(10, 100) case as default.
- **Reuse**: the three additional NIST distributions (gamma, lognormal,
  weibull) come from `standards/utils/premovement_distributions`'s
  `PREMOVEMENT_PRESETS`, which already encode the canonical NIST
  parameters (Gamma a=1.291 b=103.901, Lognormal a=4.586 b=0.967,
  Weibull a=139.285 b=1.195). The local reference CSV that previously held
  these values was removed as redundant.
- `scenario_builders/nist1_1_premovement.build_variants(base)` deep-copies
  the base scenario and applies each case via `set_agent_params(..., 
  use_premovement=True, premovement_distribution=..., premovement_param_a=...,
  premovement_param_b=...)` — the loader's documented override API.
- **Exit thickness** (A1): already 0.3 m in the source tree, no change.

### B2. Verif.2.1 — Speed in a corridor (S2)

- **Geometry extended** from NIST's 40 m measurement corridor to 60 m total:
  10 m upstream acceleration buffer + 40 m measurement segment +
  10 m downstream isolation buffer. Measurement-segment length is unchanged.
- **Exit thickness** (A1): 0.5 m -> 0.3 m.
- **Schema cleanup** (A3): `metadata` block removed.

### B3. Verif.2.2 — Speed on stairs (S3.1, S3.2)

- **Geometry extended** from NIST's 100 m measurement stair to 120 m total
  (10 m + 100 m + 10 m). Same upstream/downstream rationale as Verif.2.1.
- **Exit thickness** (A1): 0.5 m -> 0.3 m on both branches.
- **Schema cleanup** (A3): `metadata` block removed on both branches.

### B4. Verif.2.3 — Movement around a corner (S4)

- **Exit thickness** (A1): 1.0 m -> 0.3 m. Exit opening width (2 m along the
  corridor head) unchanged.

### B5. Verif.2.4 — Assigned agent demographics (S5)

- **No exit polygon**: NIST's test is a pure demographic-distribution check;
  agents do not need to evacuate, so the scenario has no `exits` block.
- **Schema cleanup** (A3): `desired_speed_distribution` and
  `desired_speed_std` removed (duplicates of `v0_distribution` and `v0_std`).

### B6. Verif.2.5 — Reduced visibility (S6)

- `CollisionFreeSpeedModel` has no native smoke field. The pytest V&V adapter
  therefore makes the smoke assignment explicit: it converts the uniform
  extinction coefficient to a zone `speed_factor` before simulation.
- The selected model-specific correlation is NIST Equation 2 with a linear
  fractional reduction, `c(K_s) = max(0.3 / 1.25, 1 - 0.5 K_s)`. At the NIST
  value `K_s = 1 /m`, the assigned speed is `1.25 * 0.5 = 0.625 m/s`.
- The geometry is extended to 120 m (10 m + the NIST 100 m measurement segment
  + 10 m), consistent with the other single-agent speed tests. The exit opening
  remains the NIST-specified 1 m wide.
- This verifies the web-community smoke-to-speed assignment and the resulting
  travel time. It does not claim native, spatially evolving smoke transport in
  JuPedSim.

### B7. Verif.2.6 — Agent incapacitation (S7.x)

- `CollisionFreeSpeedModel` has no native hazard or FED state. The pytest V&V
  adapter accumulates the CO, CO2-hyperventilation, and low-O2 equations from
  FDS+Evac section 3.3 (equations 11--14) on every JuPedSim timestep.
- One occupant is held at `(5, 5)` by an indefinite waiting stage inside the
  NIST 10 m x 10 m room. This is equivalent to the specified pre-evacuation
  time greater than 1,000,000 s without requiring the test to run that long.
- Constant conditions are `CO=5000 ppm`, `CO2=2%`, and `O2=18%`. When FED
  reaches one, the adapter sets the agent's desired speed to zero. The observed
  threshold time must be within one simulation timestep of a separate
  closed-form calculation.
- This verifies the web-community FED assignment; it does not claim native
  fire, gas-transport, or FED capabilities in JuPedSim.

### B8. Verif.2.7 — Elevator usage (S8) — PENDING

Not shipped. Requires elevator component (no 2D geometry can express this
test alone).

### B9. Verif.2.8 — Horizontal counter-flows (S9.1-S9.4)

- **Exit thickness** (A1): 1.0 m -> 0.3 m on both exits across all four
  branches.
- **No semantic changes**.

### B10. Verif.2.9 — Group behaviours (S10)

- **Schema cleanup** (A3): `group_id` parameter stripped from each of the 3
  distributions. The notebook will need to re-introduce group cohesion
  through the loader's group-behaviour API (when available) or by post-hoc
  analysis of trajectory bunching.

### B11. Verif.2.10 — Movement disabilities (S11.x)

- **Scenario reuse:** the two NIST scenario ZIPs are copied and renamed from
  the corresponding ISO Test 7 scenarios because they exercise the same
  overtaking behaviour required here: 24 occupants move past one occupant
  with reduced mobility. The pytest test loads
  `Nist-2-10-movement-disabilities.zip` and
  `Nist-2-10-movement-disabilities-no-disability.zip` from
  `standards/nist/scenario_files/`.
- In `Nist-2-10-movement-disabilities.zip`, the 24 reference occupants have
  `v0 = 1.25 m/s` and `radius = 0.2 m`; the reduced-mobility occupant has
  `v0 = 0.8 m/s` and `radius = 0.4 m`.
- The control scenario,
  `Nist-2-10-movement-disabilities-no-disability.zip`, keeps the same geometry,
  population, routes, and model parameters, but assigns the comparison
  occupant the reference values `v0 = 1.25 m/s` and `radius = 0.2 m`.
- Both scenarios must evacuate all 25 occupants. Acceptance is comparative:
  total evacuation time with the slower, larger occupant must exceed the
  control evacuation time. This tests the effect of per-agent speed and size
  parameters; it does not introduce a separate physiological disability model.

### B12. Verif.3.1 — Exit route allocation (S12)

- **Geometry sourced from `standards/rimea/scenario_files/Rimea-10.zip`.**
  RiMEA 4.1.1 Test 10 is the same Figure-8 / 12-rooms / 2-exits route
  allocation scenario; its config already ships with the canonical
  `journeys_v2` + `journey_weights` block plus a working walled-room
  geometry. The route-allocation split (8 distributions -> main exit,
  4 -> secondary exit) matches the NIST TN 1822 Verif.3.1 specification
  exactly, so the rimea-10 scenario is reused wholesale (config + WKT)
  rather than re-authored from scratch.
- This supersedes earlier attempts that rebuilt the geometry
  parametrically — see `_staging/replace_s12_with_rimea10.py` for the
  one-line `shutil.copy` that wires rimea-10 in.
- Agent count: 13 (one per room, plus a second in one room; rimea-10's
  value). NIST TN 1822 §3.1.3 says "23 persons", but that is a typo in the
  guideline — the Figure 8 layout has 12 rooms, so 13 is the faithful count.
  The allocation split (8 rooms → main, 4 → secondary) matches the standard.

### B13. Verif.3.2 — Social influence (S13.x)

- **Scenario reuse:** `Nist-3-2-social-influence-1.zip` and
  `Nist-3-2-social-influence-2.zip` are copied and renamed from the equivalent
  ISO Test 15 scenarios. Their config and geometry contents are unchanged.
- Scenario 1 gives two free occupants balanced choices between two equidistant
  exits. Scenario 2 adds a third occupant deterministically assigned to Exit 2.
- The NIST criterion is evaluated over 40 seeds: the committed occupant should
  increase Exit 2 usage among the two free occupants. CollisionFreeSpeedModel
  has no social-influence mechanism, so usage is unchanged and the real
  criterion is retained as a strict `xfail` rather than replaced by a weaker
  passing assertion.

### B14. Verif.3.3 — Affiliation (S14.x)

- **Scenario reuse:** `Nist-3-3-familiar-exits.zip` is copied and renamed from
  the equivalent ISO Test 16 scenario without changes to its config or
  geometry.
- CollisionFreeSpeedModel has no intrinsic familiarity state. Affiliation is
  therefore represented by the existing journey weights: a 50/50 baseline
  must use the two equidistant exits within a 10% difference, while a 20/80
  assignment must make Exit 2 strictly preferred over a 20-seed sweep.
- This verifies assigned familiar-exit preference through route weights; it
  does not introduce a behavioural affiliation sub-model.

### B15. Verif.4.1 — Dynamic exit availability (S15)

- **Scenario reuse:** `Nist-4-1-dynamic-exits.zip` is copied and renamed from
  the equivalent ISO Test 9 scenario without changes to its config or
  geometry.
- The occupant initially targets Exit 1. At `t = 1 s`, the runtime adapter
  changes that agent's active target to Exit 2, and the final trajectory must
  terminate at Exit 2.
- The runner does not globally close or disable the Exit 1 stage. Redirecting
  the affected occupant is the web-community representation of that exit
  becoming unavailable, so this verifies runtime rerouting but not a native
  JuPedSim exit-toggle API.

### B16. Verif.5.1 — Congestion (S16)

- **Exit thickness** (A1): 0.5 m -> 0.3 m. Exit opening width (2 m at top of
  vertical corridor) unchanged.
- **Schema cleanup** (A3): `desired_speed_distribution`,
  `desired_speed_std`, and `metadata` removed.

### B17. Verif.5.2 — Maximum flow rates (S17)

- **The emergent-flow interpretation** is the default because the
  CollisionFreeSpeedModel has no built-in door-flow limiter. NIST TN 1822
  Verif.5.2 offers two readings: (1) verification of a restricted-flow model —
  set the exit cap and check it is enforced; or (2) validation of emergent
  flow — let flow emerge and check it stays below the threshold. The notebook
  compares the recorded flow against the 1.33 p/m/s reference
  (IMO MSC/Circ.1238) as a post-run threshold. ("Mode A/B" naming used in
  earlier drafts is not NIST terminology and has been dropped.)
- The exit retains the canonical loader fields `max_throughput: 1.33` and
  `enable_throughput_throttling: false`. To exercise the restricted-flow
  reading (when the runner under test supports throttled exits), the notebook
  can flip `enable_throughput_throttling` to `true` after loading.
- **Schema cleanup** (A3): only the top-level `metadata` block was removed.

## C. Items intentionally NOT changed

To make reviewer audit easier, these stayed identical to the source tree:

- All NIST-figure geometry dimensions (room sizes, corridor widths, stair
  lengths, opening widths) except S12's interior walls (B12) and the
  upstream/downstream buffers on single-agent speed tests (B2/B3).
- All journey definitions (distribution -> exit mappings).
- Distribution polygon shapes and agent counts.
- Agent radius (0.15 m) and configured walking speeds.
- Model type (`CollisionFreeSpeedModel`) and base seed (42).
