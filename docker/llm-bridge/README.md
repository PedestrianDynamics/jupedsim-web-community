# JuPedSim Web LLM/HTTP Bridge

Local tooling that lets a human **or an LLM agent** drive the local JuPedSim Web
viewer over a small HTTP API — load and validate scenarios, run simulations,
and read results back as JSON, all by reusing the viewer's existing UI actions.

> **Status:** experimental community tooling. It complements the public
> [`docker/`](../../docker/) local-deployment setup; it is not part of the
> hosted web app.

## What it does

The bridge exposes a JSON-first HTTP API on `127.0.0.1:8090` that maps onto the
viewer's normal controls. Through it you can:

1. Validate a scenario (`config` + `geometry_wkt`).
2. Publish a scenario into the open viewer.
3. Clear the current scene using the viewer's own delete controls.
4. Read the current UI scenario back as `config` + `geometry_wkt`.
5. Trigger the viewer's **Run Simulation** action.
6. Auto-open **View Results** when a bridge-triggered run completes.
7. Read the latest run summary and query the SQLite result outputs.

The viewer stays responsible for validation, simulation execution, and result
navigation — the bridge only relays intent and reads state back.

## Architecture

| Component | File | Role |
|---|---|---|
| Bridge server | [`bridge_server.py`](bridge_server.py) | Stdlib-only Python HTTP server (no third-party deps). Holds scenario/command/result state in memory and serves the API on `127.0.0.1:8090`. |
| Viewer helper | [`bridge-button-v14.js`](bridge-button-v14.js) | Injected into the running viewer page (via the `nobuild/` proxy or a custom image); adds a **Bridge** button that polls `:8090` and applies bridge commands through the existing UI. |

The bridge sends only `config` (exits, distributions, checkpoints, zones,
obstacles, `journeys_v2`) and `geometry_wkt` (the walkable area) — it does not
push DXF/IFC layers.

## Quick start

Fastest path — viewer, bridge, and button-injecting proxy from stock images, no
rebuild (see [`nobuild/README.md`](nobuild/README.md)):

```bash
docker compose -f docker/llm-bridge/nobuild/docker-compose.yml up
```

Then open the local viewer at `http://localhost:8081/draw` and confirm the
**Bridge** button points at port `8090`:

```bash
curl http://127.0.0.1:8090/api/health
```

To run just the bridge yourself (stdlib only, no install needed):

```bash
python3 bridge_server.py --host 127.0.0.1 --port 8090
```

Full instructions are in [`LLM_BRIDGE_SETUP.md`](LLM_BRIDGE_SETUP.md).

## Contents

```text
docker/llm-bridge/
├── README.md                 # this file
├── bridge_server.py          # the HTTP bridge (stdlib only)
├── bridge-button-v14.js      # viewer-side helper button
├── LLM_BRIDGE_SETUP.md       # first-time local setup
├── LLM_BRIDGE_USAGE.md       # full HTTP API reference
├── CONFIG_JSON_TEMPLATE.md   # scenario JSON guide
├── config.template.json      # scenario config template
├── geometry.template.wkt     # walkable-area geometry template
├── examples/                 # ready-to-send example scenarios
│   ├── scenario_room1.json
│   ├── scenario_counterflow.json
│   └── scenario_building.json
├── skills/                   # optional agent skill for driving the bridge
│   └── jupedsim-web-bridge/
└── nobuild/                  # no-rebuild docker compose bring-up
    ├── docker-compose.yml
    ├── proxy/default.conf
    └── README.md
```

- **HTTP API reference:** [`LLM_BRIDGE_USAGE.md`](LLM_BRIDGE_USAGE.md)
- **Scenario JSON format:** [`CONFIG_JSON_TEMPLATE.md`](CONFIG_JSON_TEMPLATE.md)
- **Agent skill (optional):** [`skills/jupedsim-web-bridge/`](skills/jupedsim-web-bridge/) — a portable skill definition for an LLM agent to operate the bridge.

## Testing

Automated (bridge-only, no Docker needed):

```bash
uv run --extra dev pytest tests/test_llm_bridge_origin.py -v
```

Against the running stack (`nobuild` compose up, viewer open at
`http://localhost:8081/draw`):

1. **Local-only guard.** The first request must return `403`, the second `202`:

   ```bash
   curl -i -X POST -H "Origin: https://evil.example" http://127.0.0.1:8090/api/scenarios/clear
   curl -i -X POST http://127.0.0.1:8090/api/scenarios/clear
   ```

   Click **Bridge** in the viewer and confirm it still reports connected; the
   page on `:8081` must still be allowed to talk to `:8090`.

2. **End-to-end run.** Freshly reload the viewer tab, then:

   ```bash
   cd examples/analysis && python bottleneck_flow.py --run --agents 100
   ```

   Expect a plot, a CSV and a steady-state flow close to the value in
   [`examples/analysis/README.md`](examples/analysis/README.md). Reload the tab
   and run again with `--agents 20`: the script must wait for the archive tagged
   with the new run and pass its agent-count check.

3. **Command recovery.** Queue a run, then in the browser DevTools set the tab
   offline for a few seconds so a status update to the bridge fails, and go
   back online. `GET /api/simulations/latest` must still reach `completed`, and
   a later `POST /api/simulations/run` must return `202`, not `409`.

## Security

The bridge is for **local use only**. Keep it bound to `127.0.0.1` and do not
expose it publicly — it issues commands to a viewer running on the same machine
and performs no authentication. Requests whose `Origin` or `Host` header is not
`localhost` / `127.0.0.1` are refused with `403`, so a web page from another
site cannot queue commands and DNS rebinding does not reach the bridge.
