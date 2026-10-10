# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .backup_retention import AUTO_BACKUP_INTERVAL_MINUTES
from .models import BackupSchedule, NpmBackupSettings, RemoteToolBox, SnapshotSchedule, SyncSchedule, User, utcnow


def _weekday_key(dt: datetime) -> str:
    return ("mon", "tue", "wed", "thu", "fri", "sat", "sun")[dt.weekday()]


def user_due_for_scheduled_backup(db: Session, user: User, now: datetime | None = None) -> bool:
    now = now or utcnow()
    sched = user.schedule
    if not sched or not sched.enabled:
        return False
    if _weekday_key(now.astimezone(timezone.utc)) not in sched.get_days():
        return False
    interval = max(1, int(sched.interval_minutes or AUTO_BACKUP_INTERVAL_MINUTES))
    last = sched.last_run_at
    if last and (now - last).total_seconds() < interval * 60:
        return False
    return True


def user_due_for_sync_schedule(db: Session, user: User, now: datetime | None = None) -> bool:
    now = now or utcnow()
    sched: SyncSchedule | None = user.sync_schedule
    if not sched or not sched.enabled:
        return False
    if _weekday_key(now.astimezone(timezone.utc)) not in sched.get_days():
        return False
    interval = max(1, int(sched.interval_minutes or 1440))
    last = sched.last_run_at
    if last and (now - last).total_seconds() < interval * 60:
        return False
    return any(s.schedule_sync_enabled and s.enabled for s in user.slaves)


def users_due_for_auto_backup(db: Session, now: datetime | None = None) -> list[User]:
    """Legacy daily auto-backup hook (unused; schedules use BackupSchedule / SnapshotSchedule)."""
    return []


def users_due_for_schedule(db: Session, now: datetime | None = None) -> list[User]:
    return [u for u in db.query(User).all() if user_due_for_scheduled_backup(db, u, now)]


def _schedule_due(sched, now: datetime) -> bool:
    if not sched or not sched.enabled:
        return False
    if _weekday_key(now.astimezone(timezone.utc)) not in sched.get_days():
        return False
    interval = max(1, int(sched.interval_minutes or 1440))
    last = sched.last_run_at
    if last and (now - last).total_seconds() < interval * 60:
        return False
    return True


def user_due_for_snapshot_schedule(db: Session, user: User, now: datetime | None = None) -> bool:
    now = now or utcnow()
    sched: SnapshotSchedule | None = user.snapshot_schedule
    return _schedule_due(sched, now)


def remote_due_for_scheduled_push(remote: RemoteToolBox, now: datetime | None = None) -> bool:
    now = now or utcnow()
    if not remote.enabled or not remote.schedule_enabled:
        return False
    if _weekday_key(now.astimezone(timezone.utc)) not in remote.get_days():
        return False
    interval = max(1, int(remote.interval_minutes or 10080))
    last = remote.last_push_at
    if last and (now - last).total_seconds() < interval * 60:
        return False
    return True
