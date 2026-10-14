# LLM bridge — no-rebuild bring-up (prototype)

One command, three **stock** images, nothing built. This replaces the
"build a custom viewer image with the button baked in" step from
`LLM_BRIDGE_SETUP.md`.

```bash
docker compose -f docker/llm-bridge/nobuild/docker-compose.yml up
```

Then open <http://localhost:8081/draw>. The **Bridge** button is already there;
it points at the bridge on `127.0.0.1:8090`.

## How it works

| Service | Image (stock) | Role |
|---------|---------------|------|
| `viewer` | `jupedsim/jupedsim-web:latest` | The published app, unmodified. |
| `proxy`  | `nginx:1.27-alpine` | Injects `bridge-button.js` into every HTML page via `sub_filter` and strips CSP. |
| `bridge` | `python:3.12-slim` | Runs `bridge_server.py` (stdlib only) by mounting the parent dir. |

No `Dockerfile`, no custom tag, no rebuild when the app updates — bump
`VIEWER_IMAGE` and restart.

## Why a proxy instead of a custom image

The button only needs two things the stock image doesn't give it:

1. a `<script>` tag on the page — added by nginx `sub_filter`;
2. permission to load that script and `fetch()` the bridge — the proxy drops
   `Content-Security-Policy` so both are allowed.

The bridge answers `Access-Control-Allow-Origin` for `http://localhost` and
`http://127.0.0.1` origins on any port, so the cross-origin call from `:8081`
to `:8090` works without further changes.

Verified end-to-end against `jupedsim/jupedsim-web:latest`: button injected,
scenario published through the bridge, and rendered in the viewer.

## Notes and troubleshooting

- Button injection relies on `sub_filter`, which cannot rewrite a gzipped body;
  the proxy sends `Accept-Encoding ""` upstream to prevent that. Confirm with
  `curl -s localhost:8081/draw | grep __bridge`.
- Targets the all-in-one image, which serves the SPA on `:8080`. The multi-service
  `docker/docker-compose.yml` stack exposes `frontend` on `:80` — adjust
  `proxy_pass` if you point the proxy at that instead.
- **Viewer stuck on "Loading…" with `502` on `/api/*`**: the all-in-one backend
  crashes on a full Docker disk (`OSError: [Errno 28] No space left on device`),
  which looks like a proxy bug but isn't. Check `docker compose logs viewer`; free
  space with `docker builder prune` / `docker image prune`, then
  `docker compose ... up -d --force-recreate viewer`.
- `docker compose -f docker/llm-bridge/nobuild/docker-compose.yml down -v` resets volumes.
