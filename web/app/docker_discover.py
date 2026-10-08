# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from npmtbx.client import NpmClient

DOCKER_DISCOVER_HELP = (
    "Auto-detect needs the Docker socket mounted into this container "
    "(for example /var/run/docker.sock read-only). If you did not add that, ignore this message: "
    "fill in the NPM admin address and login below. That is enough for a configuration backup."
)


def _friendly_docker_error(exc: BaseException) -> str:
    text = str(exc)
    if isinstance(exc, FileNotFoundError) or "No such file" in text or "Connection aborted" in text:
        return DOCKER_DISCOVER_HELP
    return f"Cannot connect to Docker: {exc}"


@dataclass(frozen=True)
class NpmCandidate:
    container_id: str
    name: str
    image: str
    confidence: str
    data_path: str
    letsencrypt_path: str
    suggested_api_url: str
    admin_port: str
    notes: str


def _mount_dest_map(mounts: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for m in mounts:
        dest = getattr(m, "Destination", None) or (m.get("Destination") if isinstance(m, dict) else None)
        src = getattr(m, "Source", None) or (m.get("Source") if isinstance(m, dict) else None)
        if dest and src:
            out[str(dest).rstrip("/")] = str(src)
    return out


def _published_port(ports: dict | None) -> str:
    if not ports:
        return ""
    binding = None
    for container_port in ("80/tcp", "81/tcp", "443/tcp"):
        binding = ports.get(container_port)
        if binding:
            break
    if not binding:
        for key, val in ports.items():
            if key.startswith(("80/", "81/", "443/")) and val:
                binding = val
                break
    if not binding:
        return ""
    if isinstance(binding, list) and binding:
        host_port = binding[0].get("HostPort") if isinstance(binding[0], dict) else getattr(binding[0], "HostPort", "")
        return str(host_port or "")
    return ""


def discover_npm_containers(*, probe_api: bool = True) -> tuple[list[NpmCandidate], str | None]:
    try:
        import docker
    except ImportError:
        return [], "Docker Python module is not installed."

    try:
        client = docker.from_env()
    except Exception as e:
        return [], _friendly_docker_error(e)

    candidates: list[NpmCandidate] = []
    try:
        containers = client.containers.list(all=True)
    except Exception as e:
        return [], f"Cannot list containers: {e}"

    for c in containers:
        try:
            attrs = c.attrs or {}
            image = (attrs.get("Config") or {}).get("Image") or c.image.tags[0] if c.image.tags else ""
            mounts = _mount_dest_map((attrs.get("Mounts") or []))
            data_path = mounts.get("/data", "")
            le_path = mounts.get("/etc/letsencrypt", "")
            ports = attrs.get("NetworkSettings", {}).get("Ports") or {}
            admin_port = _published_port(ports)
            name = (c.name or "").strip()
            score = 0
            notes: list[str] = []
            if data_path and le_path:
                score += 3
                notes.append("Has /data and /etc/letsencrypt mounts")
            elif data_path:
                score += 1
                notes.append("Has /data mount")
            if admin_port:
                score += 1
                notes.append(f"Admin port published: {admin_port}")
            if "nginx-proxy-manager" in (image or "").lower():
                score += 1
                notes.append("Image name suggests NPM")

            suggested = ""
            if admin_port:
                suggested = f"http://127.0.0.1:{admin_port}"
            elif name:
                suggested = f"http://{name}:80"

            if probe_api and suggested:
                try:
                    probe = NpmClient(suggested, identity="x", secret="x", verify_tls=False, timeout=8)
                    if probe.probe_api():
                        score += 3
                        notes.append("API fingerprint matched NPM")
                except Exception:
                    pass

            if score < 2:
                continue

            if score >= 7:
                confidence = "high"
            elif score >= 4:
                confidence = "medium"
            else:
                confidence = "low"

            candidates.append(
                NpmCandidate(
                    container_id=c.id[:12],
                    name=name,
                    image=image,
                    confidence=confidence,
                    data_path=data_path,
                    letsencrypt_path=le_path,
                    suggested_api_url=suggested,
                    admin_port=admin_port,
                    notes="; ".join(notes),
                )
            )
        except Exception:
            continue

    candidates.sort(key=lambda x: {"high": 0, "medium": 1, "low": 2}[x.confidence])
    return candidates, None


def single_high_confidence(candidates: list[NpmCandidate]) -> NpmCandidate | None:
    high = [c for c in candidates if c.confidence == "high"]
    if len(high) == 1:
        return high[0]
    return None
