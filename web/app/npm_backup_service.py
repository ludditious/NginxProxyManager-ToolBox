# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy.orm import Session

from npmtbx.snapshot import create_snapshot_zip, extract_volume_member

from .config import get_settings
from .models import MasterInstance, NpmBackup, User, utcnow
from .npm_bridge import npm_client_from_master, npm_dns_servers_for_user


def _host_label(url: str) -> str:
    u = (url or "").strip()
    if not u.startswith("http"):
        u = "http://" + u
    host = urlparse(u).hostname or "npm"
    return re.sub(r"[^\w.-]+", "-", host).strip("-") or "npm"


def backup_display_name(api_url: str, when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"{_host_label(api_url)}-{when.strftime('%Y-%m-%d-%H%M%S')}"


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
) -> NpmBackup:
    master = user.master
    if not master or not master.enabled:
        raise ValueError("Master NPM instance is disabled or not configured.")
    client = npm_client_from_master(master, dns_servers=npm_dns_servers_for_user(user))
    api_export = client.export_configuration() if include_api else {}
    name = backup_display_name(master.api_url)
    zip_path = backup_zip_path(name)
    manifest = create_snapshot_zip(
        dest_zip=zip_path,
        source_label=name,
        api_base_url=client.base_url,
        api_export=api_export,
        data_path=master.data_path,
        letsencrypt_path=master.letsencrypt_path,
        docker_image=master.docker_image,
        docker_container_id=master.docker_container_id,
        include_volumes=include_volumes,
    )
    row = NpmBackup(
        user_id=user.id,
        name=name,
        snapshot_id=str(manifest.get("snapshot_id") or ""),
        api_url=client.base_url,
        file_name=zip_path.name,
        manifest_json=json.dumps(manifest, ensure_ascii=False),
        is_automated=is_automated,
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


def restore_npm_volumes(db: Session, user: User, backup_id: int, *, target: MasterInstance | None = None) -> list[str]:
    row = db.get(NpmBackup, backup_id)
    if not row or row.user_id != user.id:
        raise ValueError("Backup not found.")
    inst = target or user.master
    if not inst:
        raise ValueError("No target instance configured.")
    zip_path = backup_file_path(row)
    if not zip_path.is_file():
        raise ValueError("Backup file is missing on disk.")
    lines: list[str] = []
    if inst.data_path.strip():
        extract_volume_member(zip_path, "volumes/data.tar.gz", Path(inst.data_path), clear_dest=True)
        lines.append(f"Restored /data volume to {inst.data_path}")
    if inst.letsencrypt_path.strip():
        extract_volume_member(zip_path, "volumes/letsencrypt.tar.gz", Path(inst.letsencrypt_path), clear_dest=True)
        lines.append(f"Restored /etc/letsencrypt volume to {inst.letsencrypt_path}")
    if not lines:
        lines.append("No volume paths configured; nothing restored.")
    return lines


def purge_expired_automated_backups(db: Session, user: User, retention_days: int) -> int:
    if retention_days < 1:
        return 0
    cutoff = utcnow() - timedelta(days=retention_days)
    rows = (
        db.query(NpmBackup)
        .filter(
            NpmBackup.user_id == user.id,
            NpmBackup.is_automated.is_(True),
            NpmBackup.created_at < cutoff,
        )
        .all()
    )
    count = 0
    for row in rows:
        try:
            delete_npm_backup(db, user, row.id)
            count += 1
        except ValueError:
            continue
    return count
