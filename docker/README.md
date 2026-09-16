# Run JuPedSim Web Locally (Docker)

Two ways to run the simulator on your own machine, depending on how much control you want.

## Quick start — one container (self-host)

The fastest path. Bundles the frontend, both backends, and MongoDB into a single image. No clone, no compose file, no `.env`, no OAuth setup.

```bash
docker run -d \
  --name jupedsim \
  -p 8080:8080 \
  -v jupedsim-data:/data \
  --memory 4g \
  jupedsim/jupedsim-web:latest
```

Open: http://localhost:8080

Stop cleanly (mongod needs ~60 s on a busy DB):

```bash
docker stop --timeout 60 jupedsim
```

The `/data` volume holds MongoDB, scenario uploads, and the backend's SQLite state, so your scenarios survive `docker restart` and `docker rm`. Allow ~60 s on first start.

**Security note**: this image disables OAuth and serves every request as an anonymous "Local User". It's meant for local self-host. Do not expose port 8080 to the public internet.

**Licensing note**: the all-in-one image bundles MongoDB Community, which is distributed under the Server Side Public License (SSPL) v1. See the license text at https://www.mongodb.com/licensing/server-side-public-license and review its requirements for your intended use and deployment model.

## Drive the viewer over HTTP (optional)

[`llm-bridge/`](llm-bridge/) adds a small local HTTP API so a script or an LLM agent can validate and publish scenarios, run simulations, and read results back. See [llm-bridge/README.md](llm-bridge/README.md).
