# Scripting with JuPedSim Scenarios

## The idea

The web app is a **scenario editor**. You design geometries, place exits, define agent distributions, configure journeys, and run a quick simulation to verify everything looks right. Once the scenario behaves as expected, you export the JSON and move on to the real work.

The real work — parameter sweeps, Monte Carlo studies, model comparisons, sensitivity analyses — happens here, in Python, on your own machine. No internet connection required. No waiting for a remote server. Just your scenario file, a few lines of code, and [JuPedSim](https://www.jupedsim.org) running locally at full speed.

**Design in the app. Explore in code.**

## Who is this for?

- **Engineers** validating evacuation plans across a range of conditions
- **Crowd managers** testing how changes in layout or crowd size affect flow
- **Scientists** running reproducible simulation studies with statistical rigor
- **Students** learning pedestrian dynamics with real tools

## Setup

```bash
pip install -r requirements.txt
```

Dependencies: `jupedsim`, `shapely`, `numpy`, `pandas`, `matplotlib`, `pedpy` — all standard scientific Python packages. No backend server, no database, no Docker.

## Quick start

```python
from jupedsim_scenario import load_scenario, run_scenario

# Load a scenario exported from the web UI
scenario = load_scenario("scenarios/corridor_simple.json")
print(scenario.summary())

# Run it
result = run_scenario(scenario)
print(f"Evacuation time: {result.evacuation_time:.2f}s")
print(f"All evacuated:   {result.agents_remaining == 0}")
```

## What you can control

The `Scenario` object exposes everything you need to modify before running:

```python
# Agent counts
scenario.set_agent_count("jps-distributions_0", 50)

# Simulation time
scenario.set_max_time(300)

# Random seed
scenario.set_seed(123)

# Switch simulation model
scenario.set_model_type("GeneralizedCentrifugalForceModel")

# Tune model parameters
scenario.set_model_params(
    strength_neighbor_repulsion=3.0,
    range_neighbor_repulsion=0.15,
)

# Change agent properties per distribution
scenario.set_agent_params("jps-distributions_0",
    desired_speed=1.5,
    radius=0.18,
    radius_distribution="gaussian",
    radius_std=0.03,
)

# Enable or reconfigure flow spawning
scenario.set_agent_params("jps-distributions_0",
    use_flow_spawning=True,
    flow_start_time=0,
    flow_end_time=30,
)
```

You can also read scenario structure directly:

| Property | Description |
|---|---|
| `scenario.walkable_polygon` | Shapely Polygon of the walkable area |
| `scenario.walkable_area_wkt` | WKT string of the geometry |
| `scenario.model_type` | Active simulation model name |
| `scenario.exits` | Exit definitions (coordinates, throttling) |
| `scenario.distributions` | Agent distribution areas and parameters |
| `scenario.checkpoints` | Checkpoint/waypoint definitions |
| `scenario.journeys` | Journey stage sequences |
| `scenario.sim_params` | Full simulation parameter dict |
| `scenario.max_simulation_time` | Max allowed simulation time (seconds) |

## What you get back

Every `run_scenario()` call returns a `ScenarioResult`:

```python
result = run_scenario(scenario, seed=42)

result.success            # bool — simulation completed
result.evacuation_time    # float — time until last agent exited (seconds)
result.total_agents       # int — total agents spawned
result.agents_evacuated   # int — agents that reached an exit
result.agents_remaining   # int — agents still in the simulation
result.frame_rate         # float — trajectory frame rate (fps)
result.dt                 # float — simulation timestep (seconds)
result.seed               # int — random seed used for this run
result.walkable_polygon   # Shapely Polygon — for analysis without keeping the Scenario
```

### Trajectory data

```python
df = result.trajectory_dataframe()
# Columns: frame, id, x, y, ori_x, ori_y
```

### Analysis with pedpy

[pedpy](https://pedpy.readthedocs.io/) is included for trajectory analysis and plotting:

```python
import pedpy

traj = pedpy.TrajectoryData(result.trajectory_dataframe(), frame_rate=result.frame_rate)
walkable_area = pedpy.WalkableArea(result.walkable_polygon)

pedpy.plot_trajectories(walkable_area=walkable_area, traj=traj)
```

From here you have access to pedpy's full analysis toolbox — density maps, speed profiles, flow measurements, fundamental diagrams, and more.

### Cleanup

Each run writes a temporary SQLite file for trajectory storage. Clean it up when done:

```python
result.cleanup()
```

## Example: Monte Carlo seed sweep

```python
seeds = range(1, 101)
evac_times = []

for s in seeds:
    r = run_scenario(scenario, seed=s)
    evac_times.append(r.evacuation_time)
    r.cleanup()

import numpy as np
print(f"Mean: {np.mean(evac_times):.2f}s")
print(f"Std:  {np.std(evac_times):.2f}s")
print(f"Min:  {np.min(evac_times):.2f}s")
print(f"Max:  {np.max(evac_times):.2f}s")
```

## Example: Parameter sweep over agent speed

```python
results = {}
for speed in [0.8, 1.0, 1.2, 1.5, 2.0]:
    scenario.set_agent_params("jps-distributions_0", desired_speed=speed)
    r = run_scenario(scenario)
    results[speed] = r.evacuation_time
    r.cleanup()

for speed, t in results.items():
    print(f"  v={speed:.1f} m/s → {t:.2f}s")
```

## Example: Compare simulation models

```python
models = [
    "CollisionFreeSpeedModel",
    "CollisionFreeSpeedModelV2",
    "GeneralizedCentrifugalForceModel",
    "SocialForceModel",
]

for model in models:
    scenario.set_model_type(model)
    r = run_scenario(scenario)
    print(f"  {model}: {r.evacuation_time:.2f}s")
    r.cleanup()
```

## Workflow summary

```
┌─────────────────────────────────┐
│         Web App                 │
│                                 │
│  Draw geometry                  │
│  Place exits & checkpoints      │
│  Define agent distributions     │
│  Configure journeys             │
│  Run quick test → looks right?  │
│  Export JSON                    │
└──────────────┬──────────────────┘
               │
               ▼
┌─────────────────────────────────┐
│      Python Scripting           │
│                                 │
│  load_scenario("my_scene.json") │
│  Modify parameters              │
│  run_scenario() × 1000          │
│  Analyze with pedpy + pandas    │
│  Plot, compare, publish         │
└─────────────────────────────────┘
```

## Files

| File | Purpose |
|---|---|
| `jupedsim_scenario.py` | Standalone module — `load_scenario()`, `run_scenario()`, `Scenario`, `ScenarioResult` |
| `requirements.txt` | Python dependencies |
| `scenario_scripting.ipynb` | Example notebook walking through the full workflow |
| `scenarios/` | Example scenario JSONs exported from the web app |
