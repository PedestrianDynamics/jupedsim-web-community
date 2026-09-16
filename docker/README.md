# Run JuPedSim Web Locally (Docker)

The simulator ships as a single self-contained image: the frontend, both backends, and MongoDB in one container. No clone, no compose file, no `.env`, no OAuth setup.

## Quick start

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

## Update to a newer image

Containers are bound to the image they were started from, so an update replaces the container. The `jupedsim-data` volume is reused, so your scenarios are kept:

```bash
docker pull jupedsim/jupedsim-web:latest
docker stop --timeout 60 jupedsim && docker rm jupedsim
docker run -d \
  --name jupedsim \
  -p 8080:8080 \
  -v jupedsim-data:/data \
  --memory 4g \
  jupedsim/jupedsim-web:latest
```

Pin a release tag (for example `jupedsim/jupedsim-web:v1.13.0`) instead of `latest` if you want explicit upgrades and easy rollback. Add the same `-e MONGODB_URI=...` flag on re-run if you use an external MongoDB.

## Use your own MongoDB (optional)

Point `MONGODB_URI` at a managed/external MongoDB and the bundled `mongod` is not started:

```bash
docker run -d --name jupedsim -p 8080:8080 \
  -v jupedsim-data:/data --memory 4g \
  -e MONGODB_URI="mongodb+srv://user:pass@cluster.example.net/jupedsim-scenarios" \
  jupedsim/jupedsim-web:latest
```

A loopback URI (`localhost` / `127.0.0.1`) keeps the bundled database, so a stray local value can't disable it by accident. Put any credentials in the URI.

**Security note**: this image disables OAuth and serves every request as an anonymous "Local User". It's meant for local self-host. Do not expose port 8080 to the public internet.

**Licensing note**: the all-in-one image bundles MongoDB Community, which is distributed under the Server Side Public License (SSPL) v1. See the license text at https://www.mongodb.com/licensing/server-side-public-license and review its requirements for your intended use and deployment model.

