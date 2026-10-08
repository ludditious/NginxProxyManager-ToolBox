# Web app (Docker)

Build from repository root:

```bash
docker compose -f web/docker-compose.yml up -d --build
```

Container name: **`NginxProxyManager-ToolBox`**

## Optional mounts

| Mount | Purpose |
|-------|---------|
| `/var/run/docker.sock:ro` | Discover NPM containers |
| Host NPM `data` and `letsencrypt` paths `:ro` | Volume layer backup (set master paths to in-container mount points) |

## Health

`GET /health`

## Environment

| Variable | Purpose |
|----------|---------|
| `SECRET_KEY` | Session signing (auto-generated on first run) |
| `CRON_SECRET` | Internal cron tick auth |
| `INGEST_SECRET` | Remote snapshot ingest auth |
| `PORT` / host publish mapping | Web UI port (your choice on the host) |
| `CUSTOM_DNS` | Comma-separated DNS servers (e.g. LAN resolver) so NPM hostnames resolve inside the container |

*Revised: 2026-10-08*
