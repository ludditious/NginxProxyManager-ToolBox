# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations


def restart_container(container_id: str) -> str:
    cid = (container_id or "").strip()
    if not cid:
        return ""
    try:
        import docker
    except ImportError as e:
        raise RuntimeError("Docker SDK is not available in this ToolBox image.") from e
    try:
        container = docker.from_env().containers.get(cid)
    except Exception as e:
        raise RuntimeError(f"Cannot access Docker container {cid!r}: {e}") from e
    container.stop(timeout=60)
    container.start()
    return f"Restarted NPM container {cid[:12]}"


def stop_container(container_id: str) -> None:
    cid = (container_id or "").strip()
    if not cid:
        return
    import docker

    container = docker.from_env().containers.get(cid)
    container.stop(timeout=60)
