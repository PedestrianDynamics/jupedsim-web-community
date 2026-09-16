# JuPedSim Web HTTP Bridge Setup

This guide is for humans or LLM agents that need to operate the local JuPedSim Web bridge from a fresh session.

## Local Services

Use these local endpoints:

```text
Viewer: http://localhost:8081/draw
Bridge: http://127.0.0.1:8090
```

The viewer must be open in a browser for UI actions such as loading scenarios, running simulations, opening results, and capturing result files.

## Files

Important files (paths relative to the repository root):

```text
docker/llm-bridge/bridge_server.py
docker/llm-bridge/bridge-button-v14.js
docker/llm-bridge/LLM_BRIDGE_USAGE.md
docker/llm-bridge/CONFIG_JSON_TEMPLATE.md
docker/llm-bridge/config.template.json
docker/llm-bridge/geometry.template.wkt
docker/llm-bridge/nobuild/docker-compose.yml
```

The bridge API guide is `LLM_BRIDGE_USAGE.md`. The scenario JSON guide is `CONFIG_JSON_TEMPLATE.md`.

## Start Everything (no rebuild)

The recommended path starts the viewer, the bridge, and a proxy that injects the
**Bridge** button — all from stock images, no custom viewer build. From the
repository root:

```bash
docker compose -f docker/llm-bridge/nobuild/docker-compose.yml up
```

Open:

```text
http://localhost:8081/draw
```

The viewer shows a **Bridge** button after **Analytics**. Confirm its port is
`8090`. See `docker/llm-bridge/nobuild/README.md` for how the injection works and
what to verify.

Check the bridge:

```bash
curl http://127.0.0.1:8090/api/health
```

### Run The Bridge Manually (alternative)

If you already run the viewer another way, start just the stdlib bridge server
yourself. On Windows:

```powershell
py docker\llm-bridge\bridge_server.py --host 127.0.0.1 --port 8090
```

On Linux/macOS:

```bash
python3 docker/llm-bridge/bridge_server.py --host 127.0.0.1 --port 8090
```

## Send The Template Scenario

Use multipart upload for the two project files:

```bash
curl -X POST http://127.0.0.1:8090/api/validate \
  -F "config=@docker/llm-bridge/config.template.json;type=application/json" \
  -F "geometry=@docker/llm-bridge/geometry.template.wkt;type=text/plain"
```

The viewer polls the bridge and loads the scenario automatically.

## Standard LLM Workflow

1. Check bridge health.

```bash
curl http://127.0.0.1:8090/api/health
```

2. Read what is currently in the UI.

```bash
curl http://127.0.0.1:8090/api/ui-state/latest
```

3. Clear the viewer scene when starting from an empty canvas.

```bash
curl -X POST http://127.0.0.1:8090/api/scenarios/clear
curl http://127.0.0.1:8090/api/scenarios/clear/latest
```

This uses the viewer's existing **Elements** panel delete buttons. It does not load a replacement geometry.

4. Validate and publish a scenario.

```bash
curl -X POST http://127.0.0.1:8090/api/validate \
  -H "Content-Type: application/json" \
  -d @scenario.payload.json
```

5. Run a simulation.

```bash
curl -X POST http://127.0.0.1:8090/api/simulations/run
```

When a bridge-triggered simulation completes, the viewer opens **View Results** automatically.

6. Poll status.

```bash
curl http://127.0.0.1:8090/api/simulations/latest
```

7. Read summary results.

```bash
curl http://127.0.0.1:8090/api/results/latest
```

8. Open the results view in the UI.

```bash
curl -X POST http://127.0.0.1:8090/api/results/view
```

9. Read SQLite outputs.

```bash
curl http://127.0.0.1:8090/api/results/latest/sqlite
curl "http://127.0.0.1:8090/api/results/latest/sqlite/0/tables/trajectory_data?limit=100"
```

## Geometry And Config

The bridge does not send DXF or IFC layers. It sends:

```json
{
  "config": {},
  "geometry_wkt": "POLYGON((...))"
}
```

`geometry_wkt` is the walkable area. Scenario elements live in `config`:

```text
exits
distributions
checkpoints
zones
obstacles
journeys_v2
```

For new scenarios, use `journeys_v2` plus each start area's `journey_weights`. Keep legacy `journeys` and `transitions` empty unless converting an older scenario.

## Optional Codex Skill

A project-local Codex skill draft lives at:

```text
docker/llm-bridge/skills/jupedsim-web-bridge/SKILL.md
```

It is not installed globally. To use it in another Codex environment, copy the `jupedsim-web-bridge` folder into that environment's skills directory, then invoke `$jupedsim-web-bridge`.

## Troubleshooting

- If the **Bridge** button is missing, the proxy is probably not injecting the script (or the page needs a hard reload). Confirm with `curl -s http://localhost:8081/draw | grep __bridge`.
- If bridge calls succeed but the UI does not change, confirm the **Bridge** button port is set to `8090`.
- If simulation status becomes `rejected`, inspect the UI and `/api/validate` errors; the scenario may be incomplete or quota may be unavailable.
- If no result files appear, keep the viewer open until the simulation-completed modal appears.
- The bridge stores state in memory. Restarting it clears latest scenarios, commands, and result archives until the viewer republishes or a new simulation completes.
- Keep the bridge bound to `127.0.0.1`; do not expose it publicly.
