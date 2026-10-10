# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

DOCKER_SOCKET_URL = "unix:///var/run/docker.sock"


def docker_socket_path() -> Path:
    return Path("/var/run/docker.sock")


def get_docker_client():
    """Prefer the mounted socket path (works with :ro bind and explicit DOCKER_HOST)."""
    import docker

    sock = docker_socket_path()
    if sock.is_socket():
        return docker.DockerClient(base_url=DOCKER_SOCKET_URL)
    return docker.from_env()


def ping_docker() -> tuple[bool, str]:
    sock = docker_socket_path()
    if not sock.exists():
        return False, "/var/run/docker.sock not present (not mounted into ToolBox)"
    if not sock.is_socket():
        return False, "/var/run/docker.sock exists but is not a socket"
    try:
        client = get_docker_client()
        client.ping()
        client.containers.list(limit=1)
        return True, "connected"
    except Exception as exc:
        text = str(exc).strip() or exc.__class__.__name__
        if "Permission denied" in text or "permission denied" in text.lower():
            return (
                False,
                f"{text} — ToolBox must run as root or a user in the host docker group "
                "(official image runs as root)",
            )
        return False, text
