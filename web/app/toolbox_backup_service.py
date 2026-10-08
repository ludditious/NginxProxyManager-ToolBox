# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from .config import clear_settings_cache, get_settings
from .crypto import decrypt
from .models import (
    BackupSchedule,
    MasterInstance,
    RemotePullSource,
    RemoteToolBox,
    SlaveInstance,
    ToolBoxBackup,
    User,
)
from .services import ensure_user_defaults

CONFIG_FORMAT = "nginxproxymanager-toolbox-config"
CONFIG_VERSION = 1


def toolbox_backup_display_name(when: datetime | None = None) -> str:
    when = when or datetime.now(timezone.utc)
    return f"toolbox-{when.strftime('%Y-%m-%d-%H%M%S')}"


def _export_master(row: MasterInstance | None) -> dict[str, Any]:
    if not row:
        return {}
    return {
        "api_url": row.api_url,
        "identity": row.identity,
        "data_path": row.data_path,
        "letsencrypt_path": row.letsencrypt_path,
        "docker_container_id": row.docker_container_id,
        "docker_image": row.docker_image,
        "public_endpoint": row.public_endpoint,
        "verify_tls": row.verify_tls,
        "enabled": row.enabled,
        "link_group": row.link_group,
    }


def build_toolbox_document(user: User) -> dict[str, Any]:
    slaves = sorted(user.slaves, key=lambda s: s.sort_order)
    return {
        "format": CONFIG_FORMAT,
        "version": CONFIG_VERSION,
        "master": _export_master(user.master),
        "slaves": [
            {
                "name": s.name,
                "api_url": s.api_url,
                "identity": s.identity,
                "data_path": s.data_path,
                "letsencrypt_path": s.letsencrypt_path,
                "docker_container_id": s.docker_container_id,
                "docker_image": s.docker_image,
                "public_endpoint": s.public_endpoint,
                "verify_tls": s.verify_tls,
                "enabled": s.enabled,
                "link_group": s.link_group,
                "sort_order": s.sort_order,
                "auto_pull": s.auto_pull,
            }
            for s in slaves
        ],
        "remote_destinations": [
            {
                "name": r.name,
                "base_url": r.base_url,
                "enabled": r.enabled,
                "push_after_backup": r.push_after_backup,
                "sort_order": r.sort_order,
            }
            for r in sorted(user.remote_destinations, key=lambda r: r.sort_order)
        ],
        "remote_sources": [
            {
                "name": r.name,
                "base_url": r.base_url,
                "enabled": r.enabled,
                "sort_order": r.sort_order,
            }
            for r in sorted(user.remote_sources, key=lambda r: r.sort_order)
        ],
        "schedule": {
            "enabled": user.schedule.enabled if user.schedule else False,
            "interval_minutes": user.schedule.interval_minutes if user.schedule else 1440,
            "days_json": user.schedule.days_json if user.schedule else "[]",
        },
        "dns_settings": {
            "use_custom_dns": bool(user.npm_dns_settings and user.npm_dns_settings.use_custom_dns),
            "dns_servers": user.npm_dns_settings.dns_servers if user.npm_dns_settings else "",
            "use_host_overrides": bool(
                user.npm_dns_settings and user.npm_dns_settings.use_host_overrides
            ),
            "host_overrides": user.npm_dns_settings.host_overrides if user.npm_dns_settings else "",
        },
    }


def create_toolbox_backup(db: Session, user: User) -> ToolBoxBackup:
    doc = build_toolbox_document(user)
    row = ToolBoxBackup(
        user_id=user.id,
        name=toolbox_backup_display_name(),
        payload_json=json.dumps(doc, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def restore_toolbox_backup(db: Session, user: User, backup_id: int) -> None:
    row = db.get(ToolBoxBackup, backup_id)
    if not row or row.user_id != user.id:
        raise ValueError("ToolBox backup not found.")
    doc = json.loads(row.payload_json)
    if doc.get("format") != CONFIG_FORMAT:
        raise ValueError("Unsupported ToolBox backup format.")
    ensure_user_defaults(db, user)
    master = user.master
    if master and doc.get("master"):
        m = doc["master"]
        for key, val in m.items():
            if key != "password_enc" and hasattr(master, key):
                setattr(master, key, val)
    for s in list(user.slaves):
        db.delete(s)
    for s in doc.get("slaves") or []:
        db.add(
            SlaveInstance(
                user_id=user.id,
                name=s.get("name", ""),
                api_url=s.get("api_url", ""),
                identity=s.get("identity", ""),
                data_path=s.get("data_path", ""),
                letsencrypt_path=s.get("letsencrypt_path", ""),
                docker_container_id=s.get("docker_container_id", ""),
                docker_image=s.get("docker_image", ""),
                public_endpoint=s.get("public_endpoint", ""),
                verify_tls=bool(s.get("verify_tls")),
                enabled=bool(s.get("enabled", True)),
                link_group=s.get("link_group", "default"),
                sort_order=int(s.get("sort_order") or 0),
                auto_pull=bool(s.get("auto_pull")),
            )
        )
    sched = user.schedule
    if sched and doc.get("schedule"):
        sd = doc["schedule"]
        sched.enabled = bool(sd.get("enabled"))
        sched.interval_minutes = int(sd.get("interval_minutes") or 1440)
        sched.days_json = sd.get("days_json") or sched.days_json
    dns = user.npm_dns_settings
    if dns and doc.get("dns_settings"):
        dd = doc["dns_settings"]
        dns.use_custom_dns = bool(dd.get("use_custom_dns"))
        dns.dns_servers = str(dd.get("dns_servers") or "")
        dns.use_host_overrides = bool(dd.get("use_host_overrides"))
        dns.host_overrides = str(dd.get("host_overrides") or "")
    db.commit()
    clear_settings_cache()


def delete_toolbox_backup(db: Session, user: User, backup_id: int) -> None:
    row = db.get(ToolBoxBackup, backup_id)
    if not row or row.user_id != user.id:
        raise ValueError("ToolBox backup not found.")
    db.delete(row)
    db.commit()
