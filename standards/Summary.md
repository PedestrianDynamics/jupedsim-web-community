
## Verification Standards Overview

This project covers verification tests from four standards and guidelines for pedestrian and evacuation simulations:

| Standard | Tests | Main focus |
|---|---:|---|
| [RiMEA 4.1.1](standards/rimea/) | 19 | Pedestrian movement, evacuation routes, bottlenecks and crowd dynamics |
| [ISO 20414:2020](standards/iso/) | 21 | Building evacuation, occupant behaviour and fire-related conditions |
| [IMO MSC.1/Circ.1533](standards/imo/) | 12 | Evacuation simulations for passenger ships |
| [NIST TN 1822](standards/nist/) | 18 | Verification and validation of building evacuation models |
| **Total** | **70** | |

The standards share many verification objectives. Basic movement tests, such as walking along a corridor or using stairs, appear in all four. ISO and NIST also overlap considerably in their treatment of occupant behaviour and fire-related conditions.

### Cross-reference of verification tests

The table below identifies tests addressing the same or closely related capabilities. A dash indicates that no direct counterpart was identified.

| Verification objective | RiMEA | ISO | IMO | NIST |
|---|---|---|---|---|
| Walking speed in a corridor | 1 | 2 | 1 | 2.1 |
| Walking speed upstairs | 2 | 3 | 2 | 2.2 |
| Walking speed downstairs | 3 | 3 | 3 | 2.2 |
| Pre-evacuation / response time | 5 | 1 | 5 | 1.1 |
| Movement around a corner | 6 | 4 | 6 | 2.3 |
| Assignment of demographic parameters | 7 | 5 | 7 | 2.4 |
| Horizontal counterflow | — | 6 | 8 | 2.8 |
| Crowd leaving a large public space | 9 | — | 9 | — |
| Exit route allocation | 10 | 8 | 10 | 3.1 |
| Congestion at a staircase | 13* | 10 | 11 | 5.1 |
| Maximum flow through an exit | 12d* | 11 | 4 | 5.2 |
| Flow–density–speed relationship | 4, 16 | 13 | 12 | — |
| Movement around corners in crowds | 15 | 4* | 6* | 2.3* |
| Route choice based on geometry | 11, 14 | 17 | — | — |
| People with movement disabilities | — | 7 | — | 2.10 |
| Group behaviour | — | 14 | — | 2.9 |
| Social influence on exit choice | — | 15 | — | 3.2 |
| Affiliation to familiar exits | — | 16 | — | 3.3 |
| Dynamic availability of exits | — | 9 | — | 4.1 |
| Reduced visibility and walking speed | — | 18 | — | 2.5 |
| Occupant incapacitation | — | 19 | — | 2.6 |
| Elevator / lift usage | — | 20 | — | 2.7 |
| Stair flow rates | 13* | 12 | — | — |

*Related tests with different verification objectives or conditions are marked.*

### What does this overlap mean?

Although the four standards define **70 test entries**, they do not represent 70 entirely different simulation capabilities. Many tests can share geometries, simulation setups or evaluation methods.

However, similar tests are not always interchangeable. They may prescribe different walking speeds, population sizes, geometries or acceptance criteria. Therefore, each standard's requirements must still be evaluated separately.

The tests presented here concern **verification**, not validation against experimental data.