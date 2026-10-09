# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from sqlalchemy.orm import Session

from npmtbx.sync_engine import apply_export_to_target

from .models import SlaveInstance, User
from .npm_bridge import (
    npm_client_from_master,
    npm_client_from_slave,
    npm_dns_servers_for_user,
    npm_host_overrides_for_user,
)


def sync_source_to_target(db: Session, user: User, slave_id: int) -> list[str]:
    master = user.master
    if not master or not master.enabled:
        raise ValueError("Source NPM is disabled or not configured.")
    slave = db.get(SlaveInstance, slave_id)
    if not slave or slave.user_id != user.id:
        raise ValueError("Target not found.")
    if not slave.enabled:
        raise ValueError("Target is disabled.")
    dns = npm_dns_servers_for_user(user)
    overrides = npm_host_overrides_for_user(user)
    source = npm_client_from_master(master, dns_servers=dns, host_overrides=overrides)
    export = source.export_configuration()
    target = npm_client_from_slave(slave, dns_servers=dns, host_overrides=overrides)
    lines = apply_export_to_target(export, target)
    lines.insert(0, f"Synced Source → {slave.name or slave.id}")
    return lines


def sync_all_scheduled_targets(db: Session, user: User) -> list[str]:
    lines: list[str] = []
    for slave in sorted(user.slaves, key=lambda s: s.sort_order):
        if not slave.schedule_sync_enabled or not slave.enabled:
            continue
        try:
            lines.extend(sync_source_to_target(db, user, slave.id))
        except Exception as e:
            lines.append(f"Target {slave.name or slave.id} failed: {e}")
    return lines
