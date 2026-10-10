# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

DOCKER_SOCKET_URL = "unix:///var/run/docker.sock"


def get_docker_client():
    import docker

    sock = Path("/var/run/docker.sock")
    if sock.is_socket():
        return docker.DockerClient(base_url=DOCKER_SOCKET_URL)
    return docker.from_env()
