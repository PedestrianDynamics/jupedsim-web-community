# NIST TN 1822 — JuPedSim verification scenarios

Scenarios and notebooks based on **NIST Technical Note 1822** (Ronchi,
Kuligowski, Reneke, Peacock, Nilsson, 2013 — *The process of verification and
validation of building fire evacuation models*). Reference:
<https://nvlpubs.nist.gov/nistpubs/technicalnotes/NIST.TN.1822.pdf>.

Notebooks live alongside this README; scenario ZIPs live in
`scenario_files/`; parameter-sweep helpers live in `scenario_builders/`;
reference data lives in CSVs at this folder's root.

For deviations from the NIST original specifications see
[`MODIFICATIONS.md`](MODIFICATIONS.md).

## Coverage

| NIST id    | Title                                  | Status   | ZIP / notebook |
|------------|----------------------------------------|----------|----------------|
| Verif.1.1  | Pre-evacuation time distributions      | covered  | `Nist-1-1-premovement.zip` + `nist1_1_premovement.ipynb` (sweeps the 4 NIST distributions via `standards/utils/premovement_distributions`) |
| Verif.2.1  | Speed in a corridor                    | covered  | `Nist-2-1-corridor-speed.zip` |
| Verif.2.2  | Speed on stairs (upward)               | covered  | `Nist-2-2-stairs-up.zip` |
| Verif.2.2  | Speed on stairs (downward)             | covered  | `Nist-2-2-stairs-down.zip` |
| Verif.2.3  | Movement around a corner               | covered  | `Nist-2-3-corner.zip` |
| Verif.2.4  | Assigned agent demographics            | test-local | `Nist-2-4-demographics.zip` (checks the configured distribution; no simulation) |
| Verif.2.5  | Reduced visibility vs walking speed    | test-local | |
| Verif.2.6  | Agent incapacitation (FED)             | test-local | |
| Verif.2.7  | Elevator usage                         | pending  | requires elevator component |
| Verif.2.8  | Horizontal counter-flows               | covered  | `Nist-2-8-counterflow-{0,10,50,100}.zip` + `scenario_files/nist2_8_counterflow_reference.csv` |
| Verif.2.9  | Group behaviours                       | xfail*   | `Nist-2-9-groups.zip` |
| Verif.2.10 | Agents with movement disabilities      | covered  |  |
| Verif.3.1  | Exit route allocation                  | covered  | `Nist-3-1-route-allocation.zip` |
| Verif.3.2  | Social influence                       | xfail*   | `Nist-3-2-social-influence-{1,2}.zip` (strict xfail: no social-influence model) |
| Verif.3.3  | Affiliation                            | test-local | `Nist-3-3-familiar-exits.zip` (affiliation represented by journey weights) |
| Verif.4.1  | Dynamic availability of exits          | pending  | `Nist-4-1-dynamic-exits.zip` (no public runtime exit closure; test not run) |
| Verif.5.1  | Congestion                             | covered  | `Nist-5-1-congestion.zip` |
| Verif.5.2  | Maximum flow rates                     | xfail*   | `Nist-5-2-max-flow.zip` (emergent-flow non-exceedance check) |

\* **Verif.2.9**, **Verif.3.2**, and **Verif.5.2** run and produce trajectories,
but their NIST criteria need capabilities the model lacks, so each is a
strict `xfail` (see issue #151):

- Verif.2.9 expects Group 1 to reach the exit together (arrival spread ≤ 10 s).
  There is no native group-cohesion model, so the 0.5 m/s member lags the
  1.25 m/s members (spread ~24 s). The original config's custom `group_id`
  parameter was stripped during cleaning.
- Verif.3.2 expects a person committed to Exit 2 to increase Exit 2 usage among
  nearby free occupants. There is no social-influence model, so their exit
  usage remains unchanged.
- Verif.5.2 (emergent-flow reading of NIST §3.1.5) expects the sustained
  specific flow through the 1 m exit to stay under the IMO 1.33 p/m/s
  reference. There is no door-flow limiter, so the emergent flow (~5 p/m/s)
  exceeds it.

`test-local` rows (Verif.2.5, 2.6, 3.3) assert logic implemented in the
test files (a smoke speed-factor adapter, a FED adapter and journey-weight
input), not simulator output. The notes in `standards/coverage.json` say
what each one verifies.

## Modification highlights vs NIST originals

See [`MODIFICATIONS.md`](MODIFICATIONS.md) for the full audit trail. Summary:

- **Exit-capture polygon thickness** normalised to 0.3 m across every scenario
  for consistency with `Nist-1-1-premovement.zip`. The exit opening width
  (along the wall) follows the NIST figures unchanged.
- **Single-agent speed tests** (Verif.2.1, 2.2, 2.5) extend the corridor or
  stair by 10 m at each end (upstream acceleration buffer + downstream
  isolation buffer). The measurement segment length remains exactly as NIST
  specifies.
- **Verif.3.1 (S12)** geometry was rebuilt to NIST Figure 8 (12 rooms around a
  1 m corridor + vestibule); the previous tree had a bare 18 m x 11 m
  rectangle. Population is **13 agents** (one per room, plus one). NIST TN 1822
  §3.1.3 and Figure 8 specify 23 persons (two per room, one in room 3), so the
  population deviates from the standard. The allocation split
  (8 rooms → main exit, 4 → secondary) matches the standard.
- **Verif.5.2 (S17)** uses the emergent-flow non-exceedance interpretation of
  NIST §3.1.5, because the JuPedSim CollisionFreeSpeedModel has no built-in
  door-flow limiter. The 1.33 p/m/s value (IMO MSC/Circ.1238) is a post-run
  comparison threshold, not an input cap applied to the exit.
- **Terminology**: "pre-evacuation time" used throughout in place of mixed
  "pre-movement / immediate response / instant response / response time 0"
  wording from earlier V&V iterations. NIST TN 1822 uses "pre-evacuation
  time" as the canonical term.
- **Schema cleanup**: every `config.json` has been filtered to canonical
  JuPedSim-scenarios loader fields (verified against
  `src/jupedsim_scenarios/simulation_init.py` and
  `rimea/scenario_files/rimea-12d-bottleneck/config.json`). Custom non-loader
  keys (`group_id`, `room_number`, `desired_speed_distribution`,
  `desired_speed_std`, `premovement_cases_file`, top-level `metadata`
  blocks) were removed from the ZIPs and either moved to reference CSVs /
  `scenario_builders/` modules or scheduled for re-injection inside the
  notebooks. Loader-canonical fields (`use_premovement`, `premovement_*`,
  `max_throughput`, `enable_throughput_throttling`) are kept in the ZIPs.

## Pending NIST tests

Verif.2.7 remains pending because JuPedSim has no elevator component.

## Citation

Ronchi, E., Kuligowski, E. D., Reneke, P. A., Peacock, R. D., & Nilsson, D.
(2013). *The process of verification and validation of building fire
evacuation models.* NIST Technical Note 1822.
