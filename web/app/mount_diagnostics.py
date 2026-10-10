# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from npmtbx.snapshot import NPM_DATA_MARKER

from .docker_discover import NpmCandidate, _mount_dest_map
from .local_volume_paths import DEFAULT_DATA_PATH, DEFAULT_LETSENCRYPT_PATH


def _norm_host_path(path: str) -> str:
    return os.path.normpath((path or "").strip())


def toolbox_bind_source(mount_point: str) -> str | None:
    """Host source path for a bind mount visible inside this container (/proc/mounts)."""
    target = _norm_host_path(mount_point)
    if target == "/":
        return None
    best_src: str | None = None
    best_len = -1
    try:
        with open("/proc/mounts", encoding="utf-8", errors="replace") as mounts:
            for line in mounts:
                parts = line.split()
                if len(parts) < 2:
                    continue
                src = parts[0].replace("\\040", " ")
                dest = parts[1].replace("\\040", " ")
                dest_norm = _norm_host_path(dest)
                if target == dest_norm or target.startswith(dest_norm + os.sep):
                    if len(dest_norm) > best_len:
                        best_len = len(dest_norm)
                        best_src = src
    except OSError:
        return None
    if best_src and best_src.startswith("/"):
        return best_src
    return None


def _dir_size_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for name in files:
                fp = Path(root) / name
                try:
                    total += fp.stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def container_mount_host_paths(container_id: str) -> dict[str, str]:
    import docker

    container = docker.from_env().containers.get(container_id.strip())
    attrs = container.attrs or {}
    return _mount_dest_map(attrs.get("Mounts") or [])


@dataclass(frozen=True)
class MountDiagnostic:
    npm_container_name: str
    npm_container_id: str
    npm_data_host: str
    npm_letsencrypt_host: str
    toolbox_data_path: str
    toolbox_data_host: str
    toolbox_data_host_note: str
    toolbox_letsencrypt_host: str
    sqlite_present: bool
    toolbox_data_bytes: int
    host_data_paths_match: bool
    host_letsencrypt_paths_match: bool
    ready_for_disk_backup: bool
    docker_socket_available: bool
    can_try_docker_export: bool

    def report_lines(self) -> list[str]:
        lines = [
            f"NPM container: {self.npm_container_name or '(none linked)'} ({self.npm_container_id[:12] or '—'})",
            f"NPM /data on Docker host: {self.npm_data_host or '(unknown — link NPM container)'}",
            f"ToolBox path {self.toolbox_data_path!r} → host: {self.toolbox_data_host or self.toolbox_data_host_note}",
            f"{NPM_DATA_MARKER} visible in ToolBox: {'yes' if self.sqlite_present else 'NO'}",
            f"Size ToolBox sees under {self.toolbox_data_path}: {self._human_bytes(self.toolbox_data_bytes)}",
        ]
        if self.npm_data_host and self.toolbox_data_host:
            lines.append(
                "Host /data paths match: "
                + ("yes" if self.host_data_paths_match else "NO — backup would not read NPM data")
            )
        if not self.ready_for_disk_backup:
            if self.npm_data_host and not self.host_data_paths_match:
                lines.append(
                    f"Fix ToolBox run, e.g. -v {self.npm_data_host}:{self.toolbox_data_path}"
                )
            elif not self.sqlite_present:
                lines.append(
                    "ToolBox /npm-data is empty or wrong — bind the same folder NPM uses for /data."
                )
            if self.can_try_docker_export:
                lines.append(
                    "Fallback: ToolBox may read /data via Docker export (requires docker.sock)."
                )
            elif not self.docker_socket_available:
                lines.append("Docker socket not available — cannot export /data from NPM container.")
        return lines

    @staticmethod
    def _human_bytes(n: int) -> str:
        if n < 1024:
            return f"{n} bytes"
        if n < 1024 * 1024:
            return f"{n / 1024:.1f} KB"
        if n < 1024 * 1024 * 1024:
            return f"{n / (1024 * 1024):.1f} MB"
        return f"{n / (1024 * 1024 * 1024):.2f} GB"

    def assert_ready_for_full_backup(self) -> None:
        if self.ready_for_disk_backup:
            return
        if self.can_try_docker_export:
            return
        raise ValueError("Full backup blocked — mount check failed.\n" + "\n".join(self.report_lines()))

    def assert_ready_for_volume_restore(self) -> None:
        if not self.npm_data_host:
            raise ValueError(
                "Restore blocked: NPM container /data host path unknown. "
                "Link NPM on Backup / Restore and ensure docker.sock is mounted."
            )
        if not self.toolbox_data_host:
            raise ValueError(
                "Restore blocked: ToolBox /npm-data is not bind-mounted to the host. "
                + "\n".join(self.report_lines())
            )
        if not self.host_data_paths_match:
            raise ValueError(
                "Restore blocked: ToolBox would write to the wrong folder.\n"
                + "\n".join(self.report_lines())
            )


def _docker_available() -> bool:
    try:
        import docker

        docker.from_env().ping()
        return True
    except Exception:
        return False


def diagnose_local_npm_mounts(
    local,
    candidates: list[NpmCandidate] | None = None,
) -> MountDiagnostic:
    data_path = (local.data_path if local else "").strip() or DEFAULT_DATA_PATH
    le_path = (local.letsencrypt_path if local else "").strip() or DEFAULT_LETSENCRYPT_PATH
    cid = (local.docker_container_id if local else "").strip()
    cname = ""
    npm_data_host = ""
    npm_le_host = ""

    if cid:
        try:
            import docker

            container = docker.from_env().containers.get(cid)
            cname = (container.name or "").strip()
            mounts = container_mount_host_paths(cid)
            npm_data_host = mounts.get("/data", "")
            npm_le_host = mounts.get("/etc/letsencrypt", "")
        except Exception:
            pass

    if not npm_data_host and candidates:
        for c in candidates:
            if c.data_path and (not cid or c.container_id == cid):
                npm_data_host = c.data_path
                npm_le_host = c.letsencrypt_path or npm_le_host
                cname = cname or c.name
                if not cid:
                    cid = c.container_id
                break

    tb_data_host = toolbox_bind_source(data_path)
    tb_le_host = toolbox_bind_source(le_path)
    if tb_data_host:
        tb_data_note = "bind mount"
    else:
        tb_data_note = "not bind-mounted (empty dir inside ToolBox container)"
    sqlite = (Path(data_path) / NPM_DATA_MARKER).is_file()
    tb_bytes = _dir_size_bytes(Path(data_path))

    npm_norm = _norm_host_path(npm_data_host) if npm_data_host else ""
    tb_norm = _norm_host_path(tb_data_host) if tb_data_host else ""
    match_data = bool(npm_norm and tb_norm and npm_norm == tb_norm)

    le_norm_n = _norm_host_path(npm_le_host) if npm_le_host else ""
    le_norm_t = _norm_host_path(tb_le_host) if tb_le_host else ""
    match_le = bool(le_norm_n and le_norm_t and le_norm_n == le_norm_t) or (not le_norm_n and not le_norm_t)

    sock = _docker_available()
    ready = sqlite and match_data and tb_bytes > 4096
    export_ok = sock and bool(cid) and not ready

    return MountDiagnostic(
        npm_container_name=cname,
        npm_container_id=cid,
        npm_data_host=npm_data_host,
        npm_letsencrypt_host=npm_le_host,
        toolbox_data_path=data_path,
        toolbox_data_host=tb_data_host or "",
        toolbox_data_host_note=tb_data_note,
        toolbox_letsencrypt_host=tb_le_host or "",
        sqlite_present=sqlite,
        toolbox_data_bytes=tb_bytes,
        host_data_paths_match=match_data,
        host_letsencrypt_paths_match=match_le,
        ready_for_disk_backup=ready,
        docker_socket_available=sock,
        can_try_docker_export=export_ok,
    )
