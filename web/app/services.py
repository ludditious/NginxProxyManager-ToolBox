# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import threading
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from .auth_constants import DEFAULT_PASSWORD, DEFAULT_USERNAME
from .backup_retention import DEFAULT_RETENTION_DAYS
from .cron_logic import users_due_for_auto_backup, users_due_for_schedule
from .email_notify import notify_backup_result, send_notification
from .models import (
    BackupRunLog,
    BackupSchedule,
    CronTickLog,
    MasterInstance,
    NotificationPrefs,
    NpmBackup,
    NpmBackupSettings,
    NpmDnsSettings,
    SmtpSettings,
    User,
    utcnow,
)
from .npm_backup_service import backup_file_path, create_npm_backup, purge_expired_automated_backups
from .remote_toolbox import push_backup_to_remote
from .security import hash_password, verify_password

_cron_tick_lock = threading.Lock()


def ensure_single_user(db: Session) -> User:
    user = db.query(User).order_by(User.id).first()
    if not user:
        user = User(
            email="admin@local",
            username=DEFAULT_USERNAME,
            password_hash=hash_password(DEFAULT_PASSWORD),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    else:
        changed = False
        if not (user.username or "").strip():
            user.username = DEFAULT_USERNAME
            changed = True
        if not user.password_hash or len(user.password_hash) < 20:
            user.password_hash = hash_password(DEFAULT_PASSWORD)
            changed = True
        if changed:
            db.commit()
            db.refresh(user)
    ensure_user_defaults(db, user)
    return user


def uses_default_password(user: User | None) -> bool:
    if not user or not user.password_hash:
        return False
    return verify_password(DEFAULT_PASSWORD, user.password_hash)


def user_has_recovery(user: User) -> bool:
    return bool(
        user.recovery_answer_1_hash
        and user.recovery_answer_2_hash
        and user.recovery_answer_3_hash
    )


def recovery_answers_for_display(user: User, secret_key: str) -> list[str]:
    from .crypto import decrypt

    enc = (
        user.recovery_answer_1_enc,
        user.recovery_answer_2_enc,
        user.recovery_answer_3_enc,
    )
    return [decrypt(secret_key, t) if t else "" for t in enc]


def save_recovery_answers(user: User, answers: tuple[str, str, str], secret_key: str) -> None:
    from .crypto import encrypt
    from .security import hash_recovery_answer

    a1, a2, a3 = (a.strip() for a in answers)
    if not all((a1, a2, a3)):
        raise ValueError("All three recovery answers are required.")
    user.recovery_answer_1_hash = hash_recovery_answer(a1)
    user.recovery_answer_2_hash = hash_recovery_answer(a2)
    user.recovery_answer_3_hash = hash_recovery_answer(a3)
    user.recovery_answer_1_enc = encrypt(secret_key, a1)
    user.recovery_answer_2_enc = encrypt(secret_key, a2)
    user.recovery_answer_3_enc = encrypt(secret_key, a3)


def ensure_user_defaults(db: Session, user: User) -> None:
    if not user.master:
        db.add(MasterInstance(user_id=user.id))
    if not user.schedule:
        db.add(BackupSchedule(user_id=user.id))
    if not user.npm_backup_settings:
        db.add(NpmBackupSettings(user_id=user.id))
    if not user.npm_dns_settings:
        db.add(NpmDnsSettings(user_id=user.id))
    if not user.smtp_settings:
        db.add(SmtpSettings(user_id=user.id))
    if not user.notification_prefs:
        db.add(NotificationPrefs(user_id=user.id))
    db.commit()
    db.refresh(user)


def run_user_backup(db: Session, user: User, *, trigger: str = "manual") -> BackupRunLog:
    log = BackupRunLog(user_id=user.id, trigger=trigger, started_at=utcnow())
    db.add(log)
    db.commit()
    settings = user.npm_backup_settings
    include_api = settings.include_api if settings else True
    include_volumes = settings.include_volumes if settings else True
    lines: list[str] = []
    exit_code = 0
    try:
        row = create_npm_backup(
            db,
            user,
            is_automated=trigger != "manual",
            include_api=include_api,
            include_volumes=include_volumes,
        )
        lines.append(f"Created backup {row.name} ({row.size_bytes} bytes)")
        if settings and settings.enabled:
            settings.last_run_at = utcnow()
            purge_expired_automated_backups(db, user, settings.retention_days)
        for remote in sorted(user.remote_destinations, key=lambda r: r.sort_order):
            if not remote.enabled or not remote.push_after_backup:
                continue
            try:
                push_backup_to_remote(remote, row, backup_file_path(row))
                lines.append(f"Pushed to remote ToolBox: {remote.name}")
            except Exception as e:
                exit_code = 1
                lines.append(f"Push failed ({remote.name}): {e}")
                prefs = user.notification_prefs
                if not prefs or prefs.on_push_failure:
                    send_notification(user, "NginxProxyManager-ToolBox: push failed", str(e))
        notify_backup_result(user, success=True, detail="\n".join(lines))
    except Exception as e:
        exit_code = 1
        lines.append(str(e))
        notify_backup_result(user, success=False, detail=str(e))
    log.finished_at = utcnow()
    log.exit_code = exit_code
    log.body = "\n".join(lines)
    db.commit()
    db.refresh(log)
    if user.schedule and trigger == "schedule":
        user.schedule.last_run_at = utcnow()
        db.commit()
    return log


def run_cron_tick(db: Session) -> dict:
    with _cron_tick_lock:
        now = utcnow()
        due_schedule = users_due_for_schedule(db, now)
        due_auto = users_due_for_auto_backup(db, now)
        users_due = {u.id: u for u in due_schedule + due_auto}
        errors = 0
        ran = 0
        for user in users_due.values():
            trigger = "schedule" if user in due_schedule else "auto"
            log = run_user_backup(db, user, trigger=trigger)
            ran += 1
            if log.exit_code != 0:
                errors += 1
        tick = CronTickLog(
            users_due=len(users_due),
            backups_ran=ran,
            errors=errors,
            detail=f"schedule={len(due_schedule)} auto={len(due_auto)}",
        )
        db.add(tick)
        db.commit()
        return {"users_due": len(users_due), "backups_ran": ran, "errors": errors}


def ingest_snapshot_file(
    db: Session,
    *,
    file_name: str,
    file_bytes,
    name: str,
    snapshot_id: str,
) -> NpmBackup:
    import io
    import zipfile

    from .config import get_settings

    user = ensure_single_user(db)
    settings = get_settings()
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.backups_dir / file_name
    dest.write_bytes(file_bytes)
    manifest_json = "{}"
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            if "manifest.json" in zf.namelist():
                manifest_json = zf.read("manifest.json").decode("utf-8")
    except Exception:
        pass
    row = NpmBackup(
        user_id=user.id,
        name=name or file_name,
        snapshot_id=snapshot_id or "",
        api_url="",
        file_name=dest.name,
        manifest_json=manifest_json,
        is_automated=False,
        size_bytes=len(file_bytes),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row
