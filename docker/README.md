# Run JuPedSim Web Locally (Docker)

This folder provides a ready-to-run local deployment using prebuilt Docker images from Docker Hub.

## Prerequisites

- Docker Desktop (or Docker Engine + Compose plugin)
- Access to image namespace: `jupedsim/*`

## Start

```bash
cd docker
cp .env.example .env
docker compose --env-file .env -f docker-compose.yml pull
docker compose --env-file .env -f docker-compose.yml up -d
```

Open: http://localhost:8080

## Stop

```bash
docker compose --env-file .env -f docker-compose.yml down
```

## Update to a Specific Image Tag

Set `IMAGE_TAG` in `docker/.env`, then pull and restart:

```bash
docker compose --env-file .env -f docker-compose.yml pull
docker compose --env-file .env -f docker-compose.yml up -d
```

## Troubleshooting

- If image pulls fail with `manifest unknown`, verify `IMAGE_TAG` exists.
- If pushes/pulls fail with auth errors, run `docker login`.
