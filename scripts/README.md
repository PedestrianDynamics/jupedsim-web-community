# JuPedSim Scenario Scripting

Run large-scale pedestrian simulations locally using scenarios designed
in the JuPedSim web editor.

The web app is used for **scenario design**. Python is used for
**simulation experiments and analysis**.

**Typical workflow**

    Design scenario (web app)
            ↓
    Export scenario ZIP (config.json + geometry.wkt)
            ↓
    load_scenario("jps_2025_03_07.zip")
            ↓
    Run simulations in Python
            ↓
    Analyze trajectories with pedpy/pandas

This approach enables:

-   parameter sweeps
-   Monte Carlo simulations
-   model comparisons
-   sensitivity studies

All simulations run **locally**, without relying on the remote server.

------------------------------------------------------------------------

## Setup

This project uses [uv](https://docs.astral.sh/uv/getting-started/installation/) for dependency management.

Create venv and install dependencies:

```bash
uv venv
uv sync
```

Run Jupyter directly:

```bash
uv run jupyter notebook
```

> [!TIP]
> VS Code users: select the interpreter `.venv/bin/python`

------------------------------------------------------------------------

## Quick Start

Load a scenario exported from the web editor and run a simulation. `load_scenario()`
accepts either an exported ZIP file or a scenario directory containing one JSON file
and one WKT file.

``` python
from jupedsim_scenario import load_scenario, run_scenario

scenario = load_scenario("jps_2025_03_07.zip")

print(scenario.summary())

result = run_scenario(scenario)

print(f"Evacuation time: {result.evacuation_time:.2f}s")
print(f"All evacuated: {result.agents_remaining == 0}")
```

You can also load one of the repository examples directly:

``` python
scenario = load_scenario("scenarios/bottleneck-zone")
```

------------------------------------------------------------------------

## Modifying a Scenario

Before running the simulation you can adjust parameters
programmatically.

### Agent count

``` python
scenario.set_agent_count("jps-distributions_0", 50)
```

### Simulation duration

``` python
scenario.set_max_time(300)
```

### Random seed

``` python
scenario.set_seed(123)
```

### Switch simulation model

``` python
scenario.set_model_type("GeneralizedCentrifugalForceModel")
```

### Adjust model parameters

``` python
scenario.set_model_params(
    strength_neighbor_repulsion=3.0,
    range_neighbor_repulsion=0.15,
)
```

### Modify agent properties

``` python
scenario.set_agent_params(
    "jps-distributions_0",
    desired_speed=1.5,
    radius=0.18,
)
```

### Enable flow spawning

``` python
scenario.set_agent_params(
    "jps-distributions_0",
    use_flow_spawning=True,
    flow_start_time=0,
    flow_end_time=30,
)
```

### Modify zones or stages safely

High-level setters such as `set_agent_count()` mutate the loaded scenario in place.
That is convenient for one-off changes, but it also means that nested edits to
`scenario.raw`, `scenario.zones`, `scenario.stages`, or `scenario.journeys` will
persist into later runs unless you copy the scenario data first.

Use a deep copy when you want to build multiple independent variants from one base
scenario:

``` python
from copy import deepcopy

base = load_scenario("scenarios/bottleneck-zone")

variant_raw = deepcopy(base.raw)
variant_raw["zones"]["jps-zones_0"]["speed_factor"] = 0.5
```

This pattern is used in
[`bottleneck_zone_nt_diagram.ipynb`](bottleneck_zone_nt_diagram.ipynb) so the
baseline and modified runs do not accidentally share mutated nested dictionaries.

------------------------------------------------------------------------

## Inspecting Scenario Data

The `Scenario` object exposes internal configuration.

| Property | Description |
|---|---|
| `scenario.walkable_polygon` | Shapely polygon of the walkable area |
| `scenario.walkable_area_wkt` | Geometry as WKT |
| `scenario.model_type` | Active simulation model |
| `scenario.exits` | Exit definitions |
| `scenario.distributions` | Agent distribution regions |
| `scenario.stages` | Stage/waypoint definitions |
| `scenario.zones` | Speed-reduction zones |
| `scenario.journeys` | Journey stage sequences |
| `scenario.sim_params` | Simulation parameter dictionary |
| `scenario.max_simulation_time` | Maximum allowed simulation time |

------------------------------------------------------------------------

## Simulation Results

`run_scenario()` returns a **ScenarioResult** object.

``` python
result = run_scenario(scenario, seed=42)
```

### Result properties

| Property | Description |
|------|-------------|
|  `success`         |   Simulation finished successfully|
|  `evacuation_time` |   Time until last agent exits|
|  `total_agents`   |    Number of spawned agents|
| `agents_evacuated` |  Agents that reached an exit|
|  `agents_remaining`  | Agents still inside simulation|
|  `frame_rate`       |  Trajectory frame rate|
|  `dt`             |    Simulation timestep|
|  `seed`            |   Random seed used|
| `walkable_polygon` |  Walkable geometry|

------------------------------------------------------------------------

## Trajectory Data

Trajectories are available as a **pandas dataframe**.

``` python
df = result.trajectory_dataframe()
```

| Column | Description |
|---|---|
| `frame` | Frame number |
| `id` | Agent identifier |
| `x` | X coordinate |
| `y` | Y coordinate |
| `ori_x` | X component of orientation vector |
| `ori_y` | Y component of orientation vector |

------------------------------------------------------------------------

## Analysis with PedPy

The repository includes [pedpy](https://pedpy.readthedocs.io) for trajectory analysis.

``` python
import pedpy

traj = pedpy.TrajectoryData(
    result.trajectory_dataframe(),
    frame_rate=result.frame_rate
)

walkable_area = pedpy.WalkableArea(result.walkable_polygon)

pedpy.plot_trajectories(
    walkable_area=walkable_area,
    traj=traj
)
```

PedPy supports:

-   trajectory visualization
-   density maps
-   speed analysis
-   flow measurements
-   fundamental diagrams

Documentation:\
https://pedpy.readthedocs.io

See also [`bottleneck_zone_nt_diagram.ipynb`](bottleneck_zone_nt_diagram.ipynb)
for a complete example that compares two zone speed factors side by side with an
$N$-$T$ diagram.

------------------------------------------------------------------------

## Cleaning Up

Each simulation creates a temporary SQLite database storing
trajectories.

Remove it after use:

``` python
result.cleanup()
```

------------------------------------------------------------------------

## Example: Monte Carlo Simulation

``` python
seeds = range(1, 101)
evac_times = []

for s in seeds:
    r = run_scenario(scenario, seed=s)
    evac_times.append(r.evacuation_time)
    r.cleanup()

import numpy as np

print(f"Mean: {np.mean(evac_times):.2f}s")
print(f"Std: {np.std(evac_times):.2f}s")
print(f"Min: {np.min(evac_times):.2f}s")
print(f"Max: {np.max(evac_times):.2f}s")
```

------------------------------------------------------------------------

## Example: Parameter Sweep

``` python
results = {}

for speed in [0.8, 1.0, 1.2, 1.5, 2.0]:
    scenario.set_agent_params("jps-distributions_0", desired_speed=speed)

    r = run_scenario(scenario)
    results[speed] = r.evacuation_time

    r.cleanup()

for speed, t in results.items():
    print(f"{speed:.1f} m/s → {t:.2f}s")
```

------------------------------------------------------------------------

## Example: Model Comparison

``` python
models = [
    "CollisionFreeSpeedModel",
    "CollisionFreeSpeedModelV2",
    "GeneralizedCentrifugalForceModel",
    "SocialForceModel",
]

for model in models:
    scenario.set_model_type(model)

    r = run_scenario(scenario)

    print(f"{model}: {r.evacuation_time:.2f}s")

    r.cleanup()
```

------------------------------------------------------------------------

## Repository Structure

    .
    ├── jupedsim_scenario.py
    ├── requirements.txt
    ├── scenario_scripting.ipynb
    └── scenarios/

    
| File | Description |
|------|-------------|
| `jupedsim_scenario.py` | Scenario loading and simulation interface |
| `requirements.txt` | Project dependencies |
| `scenario_scripting.ipynb` | Example analysis notebook |
| `scenarios/` | Example scenario ZIPs exported from the app |
