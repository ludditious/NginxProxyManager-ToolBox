# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ContainerMount:
    kind: str  # bind | volume
    source: str
    destination: str


def get_container_mount(container_id: str, destination: str) -> ContainerMount | None:
    from .docker_client import get_docker_client

    dest = destination.rstrip("/") or "/"
    container = get_docker_client().containers.get(container_id.strip())
    for raw in container.attrs.get("Mounts") or []:
        if not isinstance(raw, dict):
            continue
        d = str(raw.get("Destination") or "").rstrip("/")
        if d != dest:
            continue
        kind = str(raw.get("Type") or "bind")
        if kind == "bind":
            src = str(raw.get("Source") or "")
        else:
            src = str(raw.get("Name") or raw.get("Source") or "")
        if src:
            return ContainerMount(kind=kind, source=src, destination=dest)
    return None


def _volume_map(mount: ContainerMount) -> dict:
    if mount.kind == "volume":
        return {mount.source: {"bind": "/target", "mode": "rw"}}
    return {mount.source: {"bind": "/target", "mode": "rw"}}


def restore_tar_gz_to_mount(mount: ContainerMount, host_tar_path: str) -> None:
    """Replace NPM volume contents via helper container; tar must be on a Docker-host path."""
    from .docker_client import get_docker_client

    tar_path = Path(host_tar_path)
    name = tar_path.name
    host_dir = str(tar_path.parent)
    script = (
        "find /target -mindepth 1 -maxdepth 1 -exec rm -rf {} + 2>/dev/null; "
        f"tar -xzf /backup/{name} -C /target"
    )
    volumes = _volume_map(mount)
    volumes[host_dir] = {"bind": "/backup", "mode": "ro"}
    client = get_docker_client()
    client.containers.run(
        "alpine:3.20",
        command=["sh", "-c", script],
        volumes=volumes,
        remove=True,
        detach=False,
    )


def extract_zip_member_to_tar_gz(zip_path: Path, member: str, dest_tar_gz: Path) -> None:
    import zipfile

    dest_tar_gz.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path, "r") as zf:
        dest_tar_gz.write_bytes(zf.read(member))


def restore_npm_from_backup_zip(
    container_id: str,
    zip_path: Path,
    *,
    restore_data: bool,
    restore_letsencrypt: bool,
    host_path_resolver,
    staging_dir: Path | None = None,
) -> list[str]:
    from .docker_client import get_docker_client

    lines: list[str] = []
    cid = container_id.strip()
    client = get_docker_client()
    npm = client.containers.get(cid)
    was_running = npm.status == "running"
    if was_running:
        npm.stop(timeout=120)
        lines.append(f"Stopped NPM container {cid[:12]}")
    try:
        stage_root = staging_dir or Path("/data/backups")
        stage_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="npmtbx-docker-restore-", dir=str(stage_root)) as td:
            tmp = Path(td)
            if restore_data:
                mount = get_container_mount(cid, "/data")
                if not mount:
                    raise ValueError("NPM container has no /data mount.")
                data_tar = tmp / "data.tar.gz"
                extract_zip_member_to_tar_gz(zip_path, "volumes/data.tar.gz", data_tar)
                host_tar = host_path_resolver(data_tar)
                restore_tar_gz_to_mount(mount, host_tar)
                lines.append(f"Restored NPM /data via Docker ({mount.kind} {mount.source})")
            if restore_letsencrypt:
                mount = get_container_mount(cid, "/etc/letsencrypt")
                if not mount:
                    raise ValueError("NPM container has no /etc/letsencrypt mount.")
                le_tar = tmp / "le.tar.gz"
                extract_zip_member_to_tar_gz(zip_path, "volumes/letsencrypt.tar.gz", le_tar)
                host_tar = host_path_resolver(le_tar)
                restore_tar_gz_to_mount(mount, host_tar)
                lines.append(f"Restored certificates via Docker ({mount.kind} {mount.source})")
    finally:
        npm.start()
        lines.append(f"Started NPM container {cid[:12]}")
    return lines
