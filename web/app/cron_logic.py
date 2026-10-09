# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .backup_retention import AUTO_BACKUP_INTERVAL_MINUTES
from .models import BackupSchedule, NpmBackupSettings, SyncSchedule, User, utcnow


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
    now = now or utcnow()
    out: list[User] = []
    for user in db.query(User).all():
        settings = user.npm_backup_settings
        if not settings or not settings.enabled:
            continue
        last = settings.last_run_at
        if last and (now - last).total_seconds() < AUTO_BACKUP_INTERVAL_MINUTES * 60:
            continue
        out.append(user)
    return out


def users_due_for_schedule(db: Session, now: datetime | None = None) -> list[User]:
    return [u for u in db.query(User).all() if user_due_for_scheduled_backup(db, u, now)]
