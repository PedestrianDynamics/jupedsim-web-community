## V&V Standards Coverage

*Last updated: 2026-11-02 14:28 UTC*

### IMO MSC.1/Circ.1533 — 12/12 covered

| Test | Criterion | Status |
|------|-----------|--------|
| IMO 1 Walking speed in a corridor | Evacuation time ~ d/v +/- 20% | :white_check_mark: PASS |
| IMO 2 Walking speed up stairs | Maintain assigned speed upstairs | :white_check_mark: PASS |
| IMO 3 Walking speed down stairs | Maintain assigned speed downstairs | :white_check_mark: PASS |
| IMO 4 Exit flow rate | Flow through a 1 m exit is physically plausible | :white_check_mark: PASS |
| IMO 5 Response time | Agents respect an assigned pre-movement distribution | :white_check_mark: PASS |
| IMO 6 Rounding corners | Navigate a 90-degree corner, stay in geometry | :white_check_mark: PASS |
| IMO 7 Assignment of population demographics | Assign 50 agents walking speeds consistent with IMO Table 3.4 for males aged 30–50 years (uniform distribution, 0.97–1.62 m/s) | :white_check_mark: PASS |
| IMO 8 Counterflow | Counterflow increases evacuation time | :white_check_mark: PASS |
| IMO 9 Exit-count sensitivity | Halving the exits increases evacuation time | :white_check_mark: PASS |
| IMO 10 Exit route allocation | Agents use assigned escape routes | :white_check_mark: PASS |
| IMO 11 Congestion / flow on stairs | Congestion develops at the room exit and staircase base, with approximately steady flow through the corridor | :white_check_mark: PASS |
| IMO 12 Flow-density relation | Pedestrian flow at very high density is lower than at moderate density in an unobstructed corridor | :white_check_mark: PASS |

### RiMEA 4.1.1 — 18/19 covered

| Test | Criterion | Status |
|------|-----------|--------|
| Test 1 Speed in a corridor | Travel time at 1.33 m/s in [26, 34] s | :white_check_mark: PASS |
| Test 2 Speed up stairs | Walking speed maintained upstairs | :white_check_mark: PASS |
| Test 3 Speed down stairs | Walking speed maintained downstairs | :white_check_mark: PASS |
| Test 4 Fundamental diagram | Local density increases with population and walking speed does not increase with density | :white_check_mark: PASS |
| Test 5 Pre-movement time | Agents respect assigned pre-movement delay | :white_check_mark: PASS |
| Test 6 Corner movement | Navigate a corner without wall penetration | :white_check_mark: PASS |
| Test 7 Demographic parameters | Speed distribution matches the age-based table | :white_check_mark: PASS |
| Test 8 Parameter study | Evacuation time varies with parameters | :white_check_mark: PASS |
| Test 9 Large public space | Closing 2/4 exits increases evacuation time | :white_check_mark: PASS |
| Test 10 Route allocation | Agents use assigned escape routes | :white_check_mark: PASS |
| Test 11 Escape route choice | Agents prefer the closer exit while some use the alternative exit | :label: TEST-LOCAL |
| Test 12a Goal position | Closer goal yields shorter evacuation | :white_check_mark: PASS |
| Test 12b Bottleneck length | Longer bottleneck increases evacuation time | :white_check_mark: PASS |
| Test 12c Congestion influence | Measure congestion influence at bottlenecks | :white_check_mark: PASS |
| Test 12d Bottleneck width | Wider bottleneck yields faster evacuation | :white_check_mark: PASS |
| Test 13 Fundamental diagram on stairs | Down-stair speed > up-stair speed | :white_check_mark: PASS |
| Test 14 Route choice | Document short vs long route preference | :white_check_mark: PASS |
| Test 15 Large crowd around a corner | Corner slows evacuation vs a straight path | :white_check_mark: PASS |
| Test 16 1D fundamental diagram | 1D speed-density within the empirical envelope | :white_check_mark: PASS |

### NIST TN 1822 — 10/18 covered

| Test | Criterion | Status |
|------|-----------|--------|
| Verif.1.1 Pre-evacuation time distributions | KS p > 0.05 for uniform/gamma/lognormal/weibull | :white_check_mark: PASS |
| Verif.2.1 Speed in a corridor | Walking speed 1.0 +/- 0.05 m/s over the 40 m segment | :white_check_mark: PASS |
| Verif.2.2 Speed on stairs (up) | Stair speed 1.0 +/- 0.05 m/s upward over 100 m | :white_check_mark: PASS |
| Verif.2.2 Speed on stairs (down) | Stair speed 1.0 +/- 0.05 m/s downward over 100 m | :white_check_mark: PASS |
| Verif.2.3 Movement around a corner | All 20 agents evacuate; no boundary penetration | :white_check_mark: PASS |
| Verif.2.4 Assigned demographics | Gaussian(1.2, 0.2) desired-speed distribution configured | :white_check_mark: PASS |
| Verif.2.5 Reduced visibility vs walking speed | At K_s = 1/m, simulated 100 m travel time matches the declared Equation 2 smoke-speed correlation | :label: TEST-LOCAL |
| Verif.2.6 Agent incapacitation (FED) | Timestep-integrated FDS+Evac FED reaches 1 within one timestep of the independent constant-hazard calculation | :label: TEST-LOCAL |
| Verif.2.7 Elevator usage | Agents evacuate via elevator | no_entry_sign NOT SUPPORTED |
| Verif.2.8 Horizontal counter-flows | Primary completions non-increasing as counterflow grows (equal budget) | :white_check_mark: PASS |
| Verif.2.9 Group behaviours | Group 1 arrival spread <= 10 s | no_entry_sign NOT SUPPORTED |
| Verif.2.10 Agents with movement disabilities | Reduced-mobility agent profiles | :white_check_mark: PASS |
| Verif.3.1 Exit route allocation | Each of 13 agents reaches its allocated exit | :white_check_mark: PASS |
| Verif.3.2 Social influence | Exit choice shifts under social influence | no_entry_sign NOT SUPPORTED |
| Verif.3.3 Affiliation | Agents prefer familiar exits | :label: TEST-LOCAL |
| Verif.4.1 Dynamic availability of exits | Agents reroute when an exit closes at runtime | no_entry_sign NOT SUPPORTED |
| Verif.5.1 Congestion | Peak density at the room exit > corridor midsection | :white_check_mark: PASS |
| Verif.5.2 Maximum flow rates | Sustained specific flow <= 1.33 p/m/s (IMO) | :warning: XFAIL |

### ISO 20414:2020 — 14/21 covered

| Test | Criterion | Status |
|------|-----------|--------|
| Test 1 Pre-evacuation time assignment | Start times fit the assigned distribution (two-sample KS, alpha=0.05) | :white_check_mark: PASS |
| Test 2 Walking speed in a corridor | Walking speed 1.0 +/- 0.05 m/s over the 40 m segment | :white_check_mark: PASS |
| Test 3 Walking speed on stairs | Stair speed 0.5 +/- 0.05 m/s in the speed-factor zone | :white_check_mark: PASS |
| Test 4 Movement around a corner | All agents evacuate; no boundary penetration | :white_check_mark: PASS |
| Test 5 Assigned occupant demographics | Realized speeds match Gaussian(1.2, 0.2) (pooled mean/std + KS) | :white_check_mark: PASS |
| Test 6 Horizontal counter-flows | Crossing time increases with counterflow | :warning: XFAIL |
| Test 7 Overtaking people with movement disabilities | A slower, larger occupant increases total evacuation time | :white_check_mark: PASS |
| Test 8 Exit route allocation | Every agent reaches its allocated exit | :white_check_mark: PASS |
| Test 9 Dynamic availability of exits | An occupant reroutes when its initially selected exit becomes unavailable | no_entry_sign NOT SUPPORTED |
| Test 10 Congestion in front of a stair | Peak density at the room->neck entrance and stair base exceeds the mid-corridor | :white_check_mark: PASS |
| Test 11 Maximum flow rates at an exit | Sustained specific flow <= 1.33 p/m/s (IMO) | :warning: XFAIL |
| Test 12 Stair flow rates | Flow rate increases with stair width (positive slope) | :white_check_mark: PASS |
| Test 13 Flow, density and speed in a corridor | Zone-2 speed stays within the assigned free speed | :warning: XFAIL |
| Test 14 Group behaviour | The homogeneous fast group reaches the exit together (spread <= 10 s) | :white_check_mark: PASS |
| Test 15 Social influence on exit choice | A deterministic exit-2 occupant raises free occupants' exit-2 usage | no_entry_sign NOT SUPPORTED |
| Test 16 Affiliation to familiar exits | Journey-weighted affiliation biases exit choice (80/20 -> exit 2 preferred) | :white_check_mark: PASS |
| Test 17 Route choice from geometric layout | Agents prefer the geometrically shorter route | :white_check_mark: PASS |
| Test 18 Reduced visibility | Reuse the NIST 2.5 reduced-visibility verification adapter | :white_check_mark: PASS |
| Test 19 Occupant incapacitation | Reuse the NIST 2.6 FED verification adapter | :white_check_mark: PASS |
| Test 20 Elevator usage | Occupants evacuate using an elevator | no_entry_sign NOT SUPPORTED |
| Test 21 Escalator usage | Occupants evacuate using an escalator | no_entry_sign NOT SUPPORTED |

Legend: :white_check_mark: pass · :warning: xfail (criterion not met) · :ledger: notebook-demonstrated · :fast_forward: skip (placeholder) · :label: test-local (tests adapter or configuration logic) · :hourglass_flowing_sand: pending (feasible but not implemented) · :no_entry_sign: not-supported (required functionality unavailable) · :x: fail
