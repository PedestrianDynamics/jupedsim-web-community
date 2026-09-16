# JuPedSim Web Config JSON Template

This folder contains a reusable scenario template for LLM clients:

- `config.template.json` is a valid JuPedSim Web `config.json`.
- `geometry.template.wkt` is the matching walkable-area geometry.
- `LLM_BRIDGE_USAGE.md` explains how to send both files through the HTTP bridge.

## Bridge Payload Shape

The bridge accepts `config.json` plus WKT in one JSON request:

```json
{
  "config": {
    "project_version": "2.0",
    "config": {},
    "exits": {},
    "distributions": {},
    "checkpoints": {},
    "zones": {},
    "obstacles": {},
    "journeys_v2": [],
    "journeys": [],
    "transitions": []
  },
  "geometry_wkt": "POLYGON((...))"
}
```

The actual project export keeps `geometry.wkt` separate from `config.json`. The bridge wrapper puts them together for convenience.

## Main Sections

### Settings

Settings live under:

```text
$.config.simulation_settings
```

Important fields:

- `simulationParams.max_simulation_time`: simulation cutoff in seconds.
- `simulationParams.dt`: timestep.
- `simulationParams.model_type`: usually `CollisionFreeSpeedModel`, `GeneralizedCentrifugalForceModel`, or another model supported by the viewer.
- model-specific parameters such as `tau`, `mass`, `agent_strength`, `gcfm_strength_neighbor_repulsion`, etc.
- `numberOfSimulations`: number of runs the UI should execute.
- `baseSeed`: random seed.

Some UI-only settings live under:

```text
$.config.ui_state
```

For example, `useShortestPaths` and manual boundary mode.

### Elements

Element groups are object maps keyed by stable IDs:

```text
$.exits
$.distributions
$.checkpoints
$.zones
$.obstacles
```

Each polygon element should use a closed ring:

```json
{
  "type": "polygon",
  "coordinates": [[0, 0], [1, 0], [1, 1], [0, 1], [0, 0]]
}
```

Element meanings:

- `exits`: destination/exit polygons. Optional throttling fields are `enable_throughput_throttling` and `max_throughput`.
- `distributions`: start areas. Agent count, speed, radius, premovement, flow spawning, and route weights live in this section.
- `checkpoints`: stages/intermediate targets. Waiting stage fields include `waiting_time` and `waiting_time_distribution`.
- `zones`: speed-modifier areas. Use `speed_factor`, for example `0.5` for half speed.
- `obstacles`: non-walkable polygon obstacles inside the walkable area.

### Journeys

Use the current routing schema:

```text
$.journeys_v2
```

Each journey has a route sequence of stage IDs and/or exit IDs:

```json
{
  "id": "jps-journeys_0",
  "name": "Main route",
  "color": "#10b981",
  "sequence": ["jps-checkpoints_0", "jps-exits_0"]
}
```

Start areas are assigned to journeys through `journey_weights`:

```json
"journey_weights": [
  {
    "journey_id": "jps-journeys_0",
    "weight": 100
  }
]
```

Keep legacy `journeys` and `transitions` as empty arrays for new scenarios.

## Rules For LLMs

- Keep IDs consistent. If a route references `jps-exits_0`, that ID must exist in `exits`.
- Put every polygon inside the walkable area WKT.
- Close every polygon ring by repeating the first coordinate as the final coordinate.
- Avoid overlaps between starts, exits, stages, zones, and obstacles unless the UI specifically allows the overlap.
- Prefer `journeys_v2`; do not generate legacy `journeys` unless converting an old scenario.
- For a pure empty section, use `{}` for element maps and `[]` for journey arrays.
- Validate with `POST /api/validate` before asking the viewer to run a simulation.
