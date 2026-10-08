# Nginx Proxy Manager ToolBox

**Nginx Proxy Manager ToolBox** backs up NPM configuration (REST API JSON), optional `/data` and `/etc/letsencrypt` volume archives, and replicates snapshots to linked slaves or remote ToolBox instances for disaster recovery.

| | |
|--|--|
| **GitHub** | [ludditious/NginxProxyManager-ToolBox](https://github.com/ludditious/NginxProxyManager-ToolBox) |
| **Container image** | `ghcr.io/ludditious/nginxproxymanager-toolbox:latest` |

---

## Docker web app

| Area | What you can do |
|------|------------------|
| **Dashboard** | Run **Backup now**; see recent runs |
| **Master** | NPM API URL, credentials, volume paths; optional **Docker discover** when the socket is mounted |
| **Slaves** | Linked NPM instances; restore snapshot volumes to slave paths |
| **Remote DR** | Push snapshots to another ToolBox; pull from a remote ToolBox ingest API |
| **Backup** | Manual and automated snapshots; **download** ZIP; restore master volumes |
| **Schedule** | Cron-style backup schedule (UTC) |
| **Settings** | Account, automated retention, SMTP notifications, update check on login |
| **Update** | Nav link when a newer GHCR image is published (same flow as AdGuard Home ToolBox) |

Default ToolBox login is documented on the sign-in page until you change the password (`admin` / `password`).

---

## Quick start (Docker)

Publish ports on the host however you prefer (example uses `8080`):

```bash
docker pull ghcr.io/ludditious/nginxproxymanager-toolbox:latest

docker run -d --name NginxProxyManager-ToolBox \
  -p 8080:8080 \
  -v npm-toolbox-data:/data \
  ghcr.io/ludditious/nginxproxymanager-toolbox:latest
```

For **Docker discover** and **volume backup**, mount the Docker socket and NPM host paths read-only, and set master volume paths to those mount points inside the ToolBox container. See [web/README.md](web/README.md).

---

## Remote DR

Each ToolBox exposes an **ingest token** (Settings / Remote DR). Another ToolBox pushes with `Authorization: Bearer <token>` to `/internal/ingest/snapshot`. Pull uses `/internal/ingest/latest` and `/internal/ingest/download/{id}`.

---

*Revised: 2026-10-08*
