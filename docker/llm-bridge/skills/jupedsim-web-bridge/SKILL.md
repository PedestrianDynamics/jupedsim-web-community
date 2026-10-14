---
name: jupedsim-web-bridge
description: Operate the local JuPedSim Web HTTP bridge for LLM-driven scenario work. Use when Codex needs to validate or publish JuPedSim Web config JSON plus geometry WKT, clear the viewer scene through the UI's delete controls, inspect the current web UI state, run simulations through the viewer, open the results view, read result summaries, or query captured SQLite/CSV result outputs from the local bridge.
---

# JuPedSim Web Bridge

## Overview

Use the local bridge to control JuPedSim Web through HTTP while leaving the viewer responsible for normal UI validation, simulation execution, and result navigation.

Default endpoints:

```text
Viewer: http://localhost:8081/draw
Bridge: http://127.0.0.1:8090
```

The viewer must be open, the Bridge button must be configured to port `8090`, and all bridge requests should stay local to `127.0.0.1`.

## Core Workflow

1. Check bridge health.

```bash
curl http://127.0.0.1:8090/api/health
```

2. Inspect the currently loaded UI scenario when needed.

```bash
curl http://127.0.0.1:8090/api/ui-state/latest
```

3. Clear the viewer scene when the user asks for a true empty scene.

```bash
curl -X POST http://127.0.0.1:8090/api/scenarios/clear
curl http://127.0.0.1:8090/api/scenarios/clear/latest
```

Use this instead of sending a replacement blank geometry. The viewer consumes the command and deletes boundaries, exits, start areas, stages, zones, and obstacles through the Elements panel.

4. Validate and publish a scenario.

```bash
curl -X POST http://127.0.0.1:8090/api/validate \
  -H "Content-Type: application/json" \
  -d @scenario.payload.json
```

Use this payload shape:

```json
{
  "config": {},
  "geometry_wkt": "POLYGON((...))"
}
```

5. Run a simulation through the viewer.

```bash
curl -X POST http://127.0.0.1:8090/api/simulations/run
curl http://127.0.0.1:8090/api/simulations/latest
```

Bridge-triggered simulations automatically click **View Results** after completion. Use `/api/results/view` only as a manual recovery/action endpoint.

6. Read results.

```bash
curl http://127.0.0.1:8090/api/results/latest
curl -X POST http://127.0.0.1:8090/api/results/view
curl http://127.0.0.1:8090/api/results/latest/sqlite
```

7. Query SQLite tables as JSON.

```bash
curl "http://127.0.0.1:8090/api/results/latest/sqlite/0/tables/trajectory_data?limit=100"
```

Use `agents.csv` from the captured result files for exact per-agent evacuation times when available.

## Config Rules

- Treat `geometry_wkt` as the walkable area, not as DXF/IFC layers.
- Put scenario elements in `config.exits`, `config.distributions`, `config.checkpoints`, `config.zones`, and `config.obstacles`.
- Use `journeys_v2` for new routing. Keep legacy `journeys` and `transitions` empty unless converting an old scenario.
- Assign start areas to routes with `distribution.journey_weights`.
- Keep polygon rings closed by repeating the first coordinate at the end.
- Keep every element inside the walkable area.

## Troubleshooting

- If the UI does not react, confirm the viewer page is open and the Bridge button is set to port `8090`.
- If a clear command completes, `/api/ui-state/latest` may be `null` until a new scenario is loaded because a truly empty scene has no exportable boundary.
- If `/api/simulations/latest` reports `rejected`, inspect validation errors and the viewer state; the scenario may be incomplete or quota may be unavailable.
- If result archives or SQLite files are missing, wait for the simulation-completed modal while the viewer remains open.
- Restarting the bridge clears in-memory state.
