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


def resolve_path_on_docker_host(path: Path) -> str:
    """Map a path inside ToolBox to the path the Docker daemon uses on the host."""
    p = path.resolve()
    for anchor in [p, *p.parents]:
        if str(anchor) == anchor.anchor:
            break
        src = toolbox_bind_source(str(anchor))
        if src:
            return str(Path(src) / p.relative_to(anchor))
    raise ValueError(
        f"Cannot resolve {p} on the Docker host — store backups on a mounted /data volume."
    )


def _is_host_folder_bind_source(src: str) -> bool:
    s = (src or "").strip()
    if not s.startswith("/"):
        return False
    if s.startswith(("/dev/", "/proc", "/sys", "/run/docker")):
        return False
    if "overlay" in s or s == "tmpfs" or s.endswith("shm"):
        return False
    return True


def toolbox_bind_source(mount_point: str) -> str | None:
    """Host folder bind-mounted at mount_point inside this container (/proc/mounts)."""
    target = _norm_host_path(mount_point)
    if target == "/":
        return None
    exact: str | None = None
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
                if not _is_host_folder_bind_source(src):
                    continue
                if dest_norm == target:
                    exact = src
                elif dest_norm != "/" and target.startswith(dest_norm + os.sep):
                    if len(dest_norm) > best_len:
                        best_len = len(dest_norm)
                        best_src = src
    except OSError:
        return None
    return exact or best_src


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
    ready_for_docker_backup: bool
    docker_socket_available: bool
    stale_container_link: bool = False

    @property
    def can_try_docker_export(self) -> bool:
        return self.ready_for_docker_backup

    def report_lines(self) -> list[str]:
        if self.npm_container_name:
            npm_line = f"{self.npm_container_name} ({self.npm_container_id[:12]})"
        elif self.npm_container_id:
            npm_line = f"stale/unknown id {self.npm_container_id[:12]} — use detect + Save to relink"
        else:
            npm_line = "none — detect NPM on this page and Save"
        lines = [
            f"NPM container: {npm_line}",
            f"NPM /data on Docker host: {self.npm_data_host or '(detect NPM container with docker.sock)'}",
            f"ToolBox path {self.toolbox_data_path!r} → host: {self.toolbox_data_host or self.toolbox_data_host_note}",
            f"{NPM_DATA_MARKER} visible in ToolBox: {'yes' if self.sqlite_present else 'NO'}",
            f"Size ToolBox sees under {self.toolbox_data_path}: {self._human_bytes(self.toolbox_data_bytes)}",
        ]
        if self.stale_container_link:
            lines.append("Container link was out of date (should auto-fix on page refresh).")
        if self.npm_data_host and self.toolbox_data_host:
            lines.append(
                "Host /data paths match: "
                + ("yes" if self.host_data_paths_match else "NO — backup would not read NPM data")
            )
        elif self.npm_data_host and not self.toolbox_data_host:
            lines.append(
                "Optional bind mount: not set (same-host backup/restore can still use Docker)."
            )
        if self.ready_for_docker_backup:
            lines.append(
                "Same-host mode: ToolBox can read/write NPM /data via docker.sock (no /npm-data bind required)."
            )
        elif not self.docker_socket_available:
            lines.append(
                "Add -v /var/run/docker.sock:/var/run/docker.sock:ro to ToolBox, or bind NPM /data into /npm-data."
            )
        elif not self.npm_data_host:
            lines.append("Link NPM container (auto-detect on this page) so ToolBox knows which container to use.")
        if self.ready_for_disk_backup:
            lines.append("Direct disk backup: /npm-data bind matches NPM (fastest).")
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
        if self.ready_for_disk_backup or self.ready_for_docker_backup:
            return
        raise ValueError("Full backup blocked.\n" + "\n".join(self.report_lines()))

    def assert_ready_for_volume_restore(self) -> None:
        if self.ready_for_disk_backup or self.ready_for_docker_backup:
            return
        raise ValueError("Restore blocked.\n" + "\n".join(self.report_lines()))


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
    saved_cid = (local.docker_container_id if local else "").strip()
    cid = saved_cid
    cname = ""
    npm_data_host = ""
    npm_le_host = ""
    stale_link = False

    def _id_matches(a: str, b: str) -> bool:
        a = (a or "").strip()
        b = (b or "").strip()
        if not a or not b:
            return False
        return a == b or a.startswith(b[:12]) or b.startswith(a[:12])

    if cid:
        try:
            import docker

            container = docker.from_env().containers.get(cid)
            cname = (container.name or "").strip().lstrip("/")
            mounts = container_mount_host_paths(cid)
            npm_data_host = mounts.get("/data", "")
            npm_le_host = mounts.get("/etc/letsencrypt", "")
        except Exception:
            pass

    pick: NpmCandidate | None = None
    if candidates:
        if cid:
            for c in candidates:
                if _id_matches(c.container_id, cid):
                    pick = c
                    break
        if not pick:
            for c in candidates:
                if c.data_path:
                    pick = c
                    break
    if pick:
        if pick.data_path:
            npm_data_host = npm_data_host or pick.data_path
        if pick.letsencrypt_path:
            npm_le_host = npm_le_host or pick.letsencrypt_path
        cname = cname or pick.name
        if saved_cid and not _id_matches(pick.container_id, saved_cid):
            stale_link = True
        cid = pick.container_id

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
    ready_disk = sqlite and match_data and tb_bytes > 4096
    ready_docker = sock and bool(cid) and bool(npm_data_host)

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
        ready_for_disk_backup=ready_disk,
        ready_for_docker_backup=ready_docker,
        docker_socket_available=sock,
        stale_container_link=stale_link,
    )
