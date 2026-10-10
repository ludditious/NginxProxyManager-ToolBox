# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

from sqlalchemy.orm import Session

from npmtbx.snapshot import (
    create_snapshot_zip,
    extract_volume_member,
    load_api_export_from_zip,
    zip_contains_member,
)
from npmtbx.sync_engine import apply_export_to_target

from .config import get_settings
from .docker_control import restart_container, stop_container
from .local_volume_paths import apply_mount_defaults, validate_full_backup_paths
from .models import LocalNpmBackup, MasterInstance, NpmBackup, SlaveInstance, User, utcnow
from .npm_bridge import (
    npm_client_from_local,
    npm_client_from_master,
    npm_client_from_slave,
    npm_dns_servers_for_user,
    npm_host_overrides_for_user,
)


def _require_local_npm(user: User) -> LocalNpmBackup:
    local = user.local_npm
    if not local or not local.enabled:
        raise ValueError("Local NPM backup is not configured.")
    return local


def backup_zip_path(name: str) -> Path:
    settings = get_settings()
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w.-]+", "-", name).strip("-") or "backup"
    return settings.backups_dir / f"{safe}.zip"


def create_npm_backup(
    db: Session,
    user: User,
    *,
    is_automated: bool = False,
    include_api: bool = True,
    include_volumes: bool = True,
    backup_kind: str = "snapshot",
) -> NpmBackup:
    local = _require_local_npm(user)
    when = datetime.now(timezone.utc)
    stamp = when.strftime("%Y-%m-%d-%H%M%S")
    container_label = (local.docker_container_id or "npm")[:12]

    if backup_kind == "full":
        if apply_mount_defaults(local):
            db.add(local)
            db.commit()
            db.refresh(local)
        validate_full_backup_paths(local.data_path, local.letsencrypt_path)
        include_api = False
        include_volumes = True
        api_export: dict = {}
        api_base = f"this-docker-host:{container_label}"
        name = f"full-local-{stamp}"
        display_url = f"This server ({local.docker_image or 'NPM'})"
    else:
        dns = npm_dns_servers_for_user(user)
        overrides = npm_host_overrides_for_user(user)
        client = npm_client_from_local(local, dns_servers=dns, host_overrides=overrides)
        api_export = client.export_configuration() if include_api else {}
        include_volumes = False
        api_base = client.base_url
        name = f"snap-local-{stamp}"
        display_url = f"local API ({client.base_url})"

    zip_path = backup_zip_path(name)
    manifest = create_snapshot_zip(
        dest_zip=zip_path,
        source_label=f"local-docker-host:{name}",
        api_base_url=api_base,
        api_export=api_export,
        data_path=local.data_path,
        letsencrypt_path=local.letsencrypt_path,
        docker_image=local.docker_image,
        docker_container_id=local.docker_container_id,
        include_volumes=include_volumes,
        include_docker_inspect=backup_kind == "full",
    )

    if backup_kind == "full":
        volumes = manifest.get("volume_files") or {}
        if not volumes.get("data"):
            if zip_path.is_file():
                zip_path.unlink()
            raise ValueError(
                "Backup failed: no NPM /data was archived. "
                f"Path {local.data_path!r} is empty or not NPM's data folder (expect database.sqlite). "
                "Bind-mount the same host directory NPM uses for /data (e.g. -v …/npm/data:/npm-data), "
                "or Save with a detected NPM container so ToolBox can read /data via Docker."
            )
        min_bytes = 4096
        zip_size = zip_path.stat().st_size
        if zip_size < min_bytes:
            zip_path.unlink(missing_ok=True)
            raise ValueError(
                f"Backup ZIP was too small ({zip_size} bytes)—no real NPM data was captured. "
                "Fix mounts or link the NPM container, then try again."
            )

    row = NpmBackup(
        user_id=user.id,
        name=name,
        snapshot_id=str(manifest.get("snapshot_id") or ""),
        api_url=display_url,
        file_name=zip_path.name,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
        is_automated=is_automated,
        backup_kind=backup_kind,
        size_bytes=zip_path.stat().st_size if zip_path.is_file() else 0,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def delete_npm_backup(db: Session, user: User, backup_id: int) -> None:
    row = db.get(NpmBackup, backup_id)
    if not row or row.user_id != user.id:
        raise ValueError("Backup not found.")
    path = get_settings().backups_dir / row.file_name
    if path.is_file():
        path.unlink()
    db.delete(row)
    db.commit()


def backup_file_path(row: NpmBackup) -> Path:
    return get_settings().backups_dir / row.file_name


def _npm_client_for_restore(
    user: User,
    inst: MasterInstance | SlaveInstance | LocalNpmBackup,
):
    dns = npm_dns_servers_for_user(user)
    overrides = npm_host_overrides_for_user(user)
    if isinstance(inst, SlaveInstance):
        return npm_client_from_slave(inst, dns_servers=dns, host_overrides=overrides)
    if isinstance(inst, LocalNpmBackup):
        return npm_client_from_local(inst, dns_servers=dns, host_overrides=overrides)
    return npm_client_from_master(inst, dns_servers=dns, host_overrides=overrides)


def restore_npm_snapshot(
    db: Session,
    user: User,
    backup_id: int,
    *,
    target: MasterInstance | SlaveInstance | LocalNpmBackup | None = None,
) -> list[str]:
    """Restore NPM to snapshot state: volume archives when present, else API export onto live NPM."""
    row = db.get(NpmBackup, backup_id)
    if not row or row.user_id != user.id:
        raise ValueError("Backup not found.")
    if target is not None:
        inst = target
    else:
        inst = _require_local_npm(user)
    zip_path = backup_file_path(row)
    if not zip_path.is_file():
        raise ValueError("Backup file is missing on disk.")

    has_data = zip_contains_member(zip_path, "volumes/data.tar.gz")
    has_le = zip_contains_member(zip_path, "volumes/letsencrypt.tar.gz")
    lines: list[str] = []

    if has_data or has_le:
        data_path = (inst.data_path or "").strip()
        le_path = (inst.letsencrypt_path or "").strip()
        if has_data and not data_path:
            raise ValueError("Backup includes /data but local data path is not configured.")
        if has_le and not le_path:
            raise ValueError("Backup includes certificates but local certificates path is not configured.")
        container_id = (inst.docker_container_id or "").strip()
        if container_id:
            try:
                stop_container(container_id)
                lines.append(f"Stopped NPM container {container_id[:12]}")
            except Exception as e:
                lines.append(f"Warning: could not stop container before restore ({e})")
        if has_data and data_path:
            extract_volume_member(zip_path, "volumes/data.tar.gz", Path(data_path), clear_dest=True)
            lines.append(f"Restored NPM /data to {data_path}")
        if has_le and le_path:
            extract_volume_member(zip_path, "volumes/letsencrypt.tar.gz", Path(le_path), clear_dest=True)
            lines.append(f"Restored /etc/letsencrypt to {le_path}")
        if container_id:
            try:
                lines.append(restart_container(container_id))
            except Exception as e:
                lines.append(f"Warning: volumes restored but container restart failed ({e})")
        lines.insert(0, "Restored NPM on this server from backup")
        return lines

    if row.backup_kind == "full":
        raise ValueError("This full backup has no volume data to restore.")

    export = load_api_export_from_zip(zip_path)
    api_keys = [k for k in export if k != "api_base_url" and not (isinstance(export.get(k), dict) and "_error" in export[k])]
    if not api_keys:
        raise ValueError("Backup has no volume archives and no API export.")
    if isinstance(inst, LocalNpmBackup):
        client = npm_client_from_local(
            inst,
            dns_servers=npm_dns_servers_for_user(user),
            host_overrides=npm_host_overrides_for_user(user),
        )
    else:
        client = _npm_client_for_restore(user, inst)
    apply_lines = apply_export_to_target(
        export,
        client,
        source_data_path=(inst.data_path or "").strip(),
        source_letsencrypt_path=(inst.letsencrypt_path or "").strip(),
        source_docker_container_id=(inst.docker_container_id or "").strip(),
        source_client=None,
    )
    lines.append("Configuration restore from snapshot API export")
    lines.extend(apply_lines)
    return lines


def restore_npm_volumes(
    db: Session,
    user: User,
    backup_id: int,
    *,
    target: MasterInstance | SlaveInstance | LocalNpmBackup | None = None,
) -> list[str]:
    return restore_npm_snapshot(db, user, backup_id, target=target)


def purge_expired_backups(
    db: Session,
    user: User,
    retention_days: int,
    *,
    backup_kind: str | None = None,
) -> int:
    if retention_days < 1:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    q = db.query(NpmBackup).filter(
        NpmBackup.user_id == user.id,
        NpmBackup.is_automated.is_(True),
        NpmBackup.created_at < cutoff,
    )
    if backup_kind:
        q = q.filter(NpmBackup.backup_kind == backup_kind)
    rows = q.all()
    count = 0
    for row in rows:
        try:
            delete_npm_backup(db, user, row.id)
            count += 1
        except ValueError:
            continue
    return count


def purge_expired_automated_backups(db: Session, user: User, retention_days: int) -> int:
    if retention_days < 1:
        return 0
    return purge_expired_backups(db, user, retention_days)
