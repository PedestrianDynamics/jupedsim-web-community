# JuPedSim Web HTTP Bridge Usage for LLMs

This bridge lets an LLM drive the local JuPedSim Web viewer through HTTP.

Base URL:

```text
http://127.0.0.1:8090
```

Viewer URL:

```text
http://localhost:8081/draw
```

The LLM-facing API is JSON-first. The viewer may internally produce project/result archives, but client LLMs usually do not need to handle those directly.

For first-time local setup, see `LLM_BRIDGE_SETUP.md`.

## What The Bridge Can Do

1. Validate a scenario.
2. Send a scenario into the open JuPedSim Web UI.
3. Clear the open viewer scene using the viewer's existing delete controls.
4. Read the current UI scenario back as `config` plus `geometry_wkt`.
5. Trigger the viewer's existing **Run Simulation** action.
6. Automatically open **View Results** after a bridge-triggered simulation completes.
7. Read the latest simulation summary.
8. Read the latest SQLite output file metadata, table schemas, row counts, and table rows.
9. Download captured result files if needed.

## Health Check

```bash
curl http://127.0.0.1:8090/api/health
```

Returns the active endpoint list.

## Send A Scenario

Use `POST /api/validate` with a JSON body:

```json
{
  "config": {
    "project_version": "2.0",
    "config": {},
    "distributions": {},
    "exits": {},
    "checkpoints": {},
    "zones": {},
    "journeys": [],
    "journeys_v2": [],
    "transitions": [],
    "obstacles": {}
  },
  "geometry_wkt": "POLYGON ((0 0, 10 0, 10 10, 0 10, 0 0))"
}
```

Example:

```bash
curl -X POST http://127.0.0.1:8090/api/validate \
  -H "Content-Type: application/json" \
  -d @scenario.json
```

If valid, the bridge publishes the scenario. The open viewer polls the bridge and loads it automatically.

For a fuller editable scenario shape, see:

- `config.template.json`
- `geometry.template.wkt`
- `CONFIG_JSON_TEMPLATE.md`

Response shape:

```json
{
  "ok": true,
  "errors": [],
  "warnings": [],
  "routing": {
    "schema": "journeys_v2",
    "converted_from_legacy": false
  },
  "scenario": {
    "id": "1780000000000-abc12345",
    "bundle_url": "/api/scenarios/1780000000000-abc12345/bundle",
    "filename": "jupedsim-bridge-1780000000000-abc12345.zip"
  }
}
```

If invalid, `ok` is `false` and `errors` explains what to fix.

## Read Current UI Scenario

```bash
curl http://127.0.0.1:8090/api/ui-state/latest
```

Returns the latest scenario currently captured from the viewer:

```json
{
  "ok": true,
  "ui_state": {
    "id": "...",
    "config": {},
    "geometry_wkt": "POLYGON ((...))"
  }
}
```

Use this when you want to inspect what is actually in the UI.

## Clear The Viewer Scene

Use this when you want a true empty scene, not a replacement blank geometry:

```bash
curl -X POST http://127.0.0.1:8090/api/scenarios/clear
```

The viewer consumes this command and uses its existing **Elements** panel delete buttons to remove boundaries, exits, start areas, stages, zones, and obstacles.

Response:

```json
{
  "ok": true,
  "clear_scene": {
    "id": "1780000000000-abc12345",
    "status": "queued",
    "queued_at": 1780000000000,
    "updated_at": 1780000000000
  }
}
```

Check clear status:

```bash
curl http://127.0.0.1:8090/api/scenarios/clear/latest
```

Possible statuses:

```text
queued
accepted
completed
failed
rejected
```

When the command completes, the status includes a `result` object with counts from the Elements panel:

```json
{
  "deleted_count": 6,
  "empty": true,
  "counts": {
    "boundaries": 0,
    "exits": 0,
    "starting_areas": 0,
    "stages": 0,
    "zones": 0,
    "obstacles": 0
  }
}
```

After a clear command is queued or completed, the bridge discards its latest published scenario and latest captured UI-state so an old scenario is not accidentally re-imported.

## Run A Simulation

```bash
curl -X POST http://127.0.0.1:8090/api/simulations/run
```

The viewer consumes this command and clicks its own **Run Simulation** button. This means JuPedSim Web still performs its normal validation, quota checks, and simulation workflow.

After a bridge-triggered simulation reaches the **Simulation Completed** modal, the viewer helper automatically clicks **View Results**. The explicit `/api/results/view` command is still available as a manual recovery/action endpoint.

Response:

```json
{
  "ok": true,
  "simulation": {
    "id": "1780000000000-abc12345",
    "status": "queued",
    "queued_at": 1780000000000,
    "updated_at": 1780000000000
  }
}
```

Check run status:

```bash
curl http://127.0.0.1:8090/api/simulations/latest
```

Possible statuses:

```text
queued
accepted
running
completed
failed
rejected
```

If the status is `rejected`, usually the UI cannot run because the scenario is invalid, incomplete, or quota is unavailable.

## Open The Results View Manually

Bridge-triggered simulations open results automatically after completion. If the completed-run modal is visible and results did not open, or if another workflow completed outside the bridge, ask the bridge to press **View Results**:

```bash
curl -X POST http://127.0.0.1:8090/api/results/view
```

Check the command status:

```bash
curl http://127.0.0.1:8090/api/results/view/latest
```

Possible statuses:

```text
queued
accepted
completed
failed
rejected
```

If results mode is already open, the bridge reports `completed`. If the completed-run modal is not visible and results mode is not already open, it reports `rejected`.

## Read Latest Simulation Summary

```bash
curl http://127.0.0.1:8090/api/results/latest
```

Example result:

```json
{
  "ok": true,
  "results": {
    "simulation_id": "1780000000000-abc12345",
    "result": {
      "source": "simulation_progress_modal",
      "progress_percent": 100,
      "total_agents": 50,
      "agents_evacuated": 50,
      "evacuation_time_seconds": 64.52,
      "execution_time_seconds": 23.63
    }
  }
}
```

This summary is read from the viewer's visible **Simulation Completed** panel.

## Read SQLite Outputs

After a completed simulation, the viewer publishes the result files to the bridge. The bridge inspects SQLite outputs and exposes them as JSON.

List captured result files:

```bash
curl http://127.0.0.1:8090/api/results/latest/archive
```

List SQLite files only:

```bash
curl http://127.0.0.1:8090/api/results/latest/sqlite
```

Example SQLite response:

```json
{
  "ok": true,
  "archive_id": "1780000000000-abc12345",
  "simulation_id": "1780000000000-run12345",
  "sqlite_files": [
    {
      "sqlite_index": 0,
      "path": "simulation_seed_42.sqlite",
      "sqlite": {
        "tables": [
          {
            "name": "trajectory_data",
            "row_count": 21771,
            "columns": [
              {"name": "frame", "type": "INTEGER"},
              {"name": "id", "type": "INTEGER"},
              {"name": "pos_x", "type": "REAL"},
              {"name": "pos_y", "type": "REAL"}
            ]
          }
        ]
      }
    }
  ]
}
```

Read rows from a table:

```bash
curl "http://127.0.0.1:8090/api/results/latest/sqlite/0/tables/trajectory_data?limit=5"
```

The `0` is the `sqlite_index`. The table name is URL-encoded if needed.

Optional query parameters:

```text
limit   default 100, max 1000
offset  default 0
```

Read metadata:

```bash
curl "http://127.0.0.1:8090/api/results/latest/sqlite/0/tables/metadata?limit=20"
```

Typical SQLite tables:

```text
frame_data
geometry
metadata
trajectory_data
```

## Per-Agent Exit Times

The result archive also contains `agents.csv`, which includes one row per agent and an `evacuation_time` field.

Current bridge version can list/download the captured files:

```bash
curl http://127.0.0.1:8090/api/results/latest/archive
curl -o agents.csv http://127.0.0.1:8090/api/results/latest/files/1
```

Use the archive file list to find the index for `agents.csv`; do not assume it is always `1`.

The `agents.csv` columns are typically:

```text
agent_id,start_x,start_y,premovement_time,exit_x,exit_y,evacuation_time
```

`evacuation_time` is the per-agent exit time in seconds.

## Common Workflow

1. Check bridge is alive.

```bash
curl http://127.0.0.1:8090/api/health
```

2. Send or update a scenario.

```bash
curl -X POST http://127.0.0.1:8090/api/validate \
  -H "Content-Type: application/json" \
  -d @scenario.json
```

3. Optionally clear the viewer scene when starting fresh.

```bash
curl -X POST http://127.0.0.1:8090/api/scenarios/clear
curl http://127.0.0.1:8090/api/scenarios/clear/latest
```

4. Wait briefly for the viewer to load it.

5. Run the simulation.

```bash
curl -X POST http://127.0.0.1:8090/api/simulations/run
```

6. Poll until completed.

```bash
curl http://127.0.0.1:8090/api/simulations/latest
```

7. Read summary.

```bash
curl http://127.0.0.1:8090/api/results/latest
```

8. Open the viewer results mode when needed.

```bash
curl -X POST http://127.0.0.1:8090/api/results/view
```

9. Read SQLite tables.

```bash
curl http://127.0.0.1:8090/api/results/latest/sqlite
curl "http://127.0.0.1:8090/api/results/latest/sqlite/0/tables/trajectory_data?limit=100"
```

## Notes For LLM Clients

- Keep all requests local to `127.0.0.1:8090`.
- The viewer must be open at `http://localhost:8081/draw` for loading scenarios, running simulations, and capturing result files.
- Use `POST /api/scenarios/clear` for a true empty scene. Do not fake clearing by sending a replacement square unless that is intentionally what you want.
- Do not call the viewer's internal backend directly unless you intentionally know its API.
- Prefer `/api/results/latest` for high-level answers.
- Prefer `/api/results/latest/sqlite` and table endpoints for trajectory analysis.
- Prefer `agents.csv` for exact per-agent evacuation times.
- `trajectory_data` is sampled by frame. The current metadata `fps` tells you the time resolution; with `fps = 10`, each frame is `0.1s`.
- Bridge storage is in memory. Restarting the bridge clears the latest scenario command/result archive until the viewer republishes or a new run completes.
