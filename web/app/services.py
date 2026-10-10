# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import io
import json
import threading
import zipfile

from sqlalchemy.orm import Session

from .auth_constants import DEFAULT_PASSWORD, DEFAULT_USERNAME
from .backup_retention import DEFAULT_RETENTION_DAYS
from .cron_logic import (
    remote_due_for_scheduled_push,
    user_due_for_snapshot_schedule,
    user_due_for_sync_schedule,
    users_due_for_schedule,
)
from .email_notify import notify_backup_result, send_notification
from .models import (
    BackupRunLog,
    BackupSchedule,
    CronTickLog,
    IngestEvent,
    MasterInstance,
    NpmBackup,
    NpmBackupSettings,
    NpmDnsSettings,
    NotificationPrefs,
    RemoteToolBox,
    SmtpSettings,
    SyncSchedule,
    ToolBoxUiSettings,
    User,
    utcnow,
)
from .npm_backup_service import (
    backup_file_path,
    create_npm_backup,
    delete_npm_backup,
    purge_expired_backups,
)
from .remote_toolbox import push_backup_to_remote
from .security import hash_password, verify_password
from .ui_context import ROLE_BACKUP_ENDPOINT

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
    if not user.snapshot_schedule:
        from .models import SnapshotSchedule

        db.add(SnapshotSchedule(user_id=user.id))
    if not user.sync_schedule:
        db.add(SyncSchedule(user_id=user.id))
    if not user.npm_backup_settings:
        db.add(NpmBackupSettings(user_id=user.id))
    if not user.npm_dns_settings:
        db.add(NpmDnsSettings(user_id=user.id))
    if not user.smtp_settings:
        db.add(SmtpSettings(user_id=user.id))
    if not user.notification_prefs:
        db.add(NotificationPrefs(user_id=user.id))
    if not user.ui_settings:
        db.add(ToolBoxUiSettings(user_id=user.id))
    if not user.local_npm:
        from .models import LocalNpmBackup

        local = LocalNpmBackup(user_id=user.id)
        db.add(local)
        db.flush()
        _seed_local_npm_from_master(user, local)
    db.commit()
    db.refresh(user)


def _seed_local_npm_from_master(user: User, local) -> None:
    """One-time: move volume/docker hints off Source without copying remote Source URL."""
    master = user.master
    if not master:
        return
    if not (local.data_path or "").strip() and (master.data_path or "").strip():
        local.data_path = master.data_path
    if not (local.letsencrypt_path or "").strip() and (master.letsencrypt_path or "").strip():
        local.letsencrypt_path = master.letsencrypt_path
    if not (local.docker_container_id or "").strip() and (master.docker_container_id or "").strip():
        local.docker_container_id = master.docker_container_id
        local.docker_image = master.docker_image or ""


def _push_full_backup_to_remotes(
    db: Session,
    user: User,
    row: NpmBackup,
    lines: list[str],
    *,
    only_remotes: list[RemoteToolBox] | None = None,
) -> int:
    exit_code = 0
    remotes = only_remotes if only_remotes is not None else list(user.remote_destinations)
    for remote in sorted(remotes, key=lambda r: r.sort_order):
        if not remote.enabled or not remote.push_after_backup:
            continue
        try:
            push_backup_to_remote(remote, row, backup_file_path(row))
            remote.last_push_at = utcnow()
            lines.append(f"Pushed to remote ToolBox: {remote.name}")
        except Exception as e:
            exit_code = 1
            lines.append(f"Push failed ({remote.name}): {e}")
            prefs = user.notification_prefs
            if not prefs or prefs.on_push_failure:
                send_notification(user, "NginxProxyManager-ToolBox: push failed", str(e))
    db.commit()
    return exit_code


def run_user_backup(
    db: Session,
    user: User,
    *,
    trigger: str = "manual",
    backup_kind: str = "full",
    push_remotes: list[RemoteToolBox] | None = None,
) -> BackupRunLog:
    log = BackupRunLog(user_id=user.id, trigger=trigger, started_at=utcnow())
    db.add(log)
    db.commit()
    settings = user.npm_backup_settings
    include_api = True
    include_volumes = backup_kind == "full"
    if backup_kind == "snapshot" and settings:
        include_api = settings.include_api
        include_volumes = False
    if backup_kind == "full" and settings:
        include_volumes = settings.include_volumes

    lines: list[str] = []
    exit_code = 0
    try:
        row = create_npm_backup(
            db,
            user,
            is_automated=trigger != "manual",
            include_api=include_api,
            include_volumes=include_volumes,
            backup_kind=backup_kind,
        )
        lines.append(f"Created {backup_kind} backup {row.name} ({row.size_bytes} bytes)")
        if backup_kind == "full":
            if settings:
                purge_expired_backups(db, user, settings.retention_days, backup_kind="full")
            exit_code = max(
                exit_code,
                _push_full_backup_to_remotes(db, user, row, lines, only_remotes=push_remotes),
            )
        elif user.snapshot_schedule and trigger != "manual":
            purge_expired_backups(
                db,
                user,
                user.snapshot_schedule.retention_days,
                backup_kind="snapshot",
            )
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
    if backup_kind == "full" and user.schedule and trigger == "schedule":
        user.schedule.last_run_at = utcnow()
        db.commit()
    if backup_kind == "snapshot" and user.snapshot_schedule and trigger == "schedule":
        user.snapshot_schedule.last_run_at = utcnow()
        db.commit()
    return log


def purge_endpoint_retention(db: Session, user: User) -> int:
    from datetime import timedelta

    ui = user.ui_settings
    if not ui or ui.toolbox_role != ROLE_BACKUP_ENDPOINT:
        return 0
    days = max(1, int(ui.endpoint_retention_days or 30))
    cutoff = utcnow() - timedelta(days=days)
    rows = (
        db.query(NpmBackup)
        .filter(NpmBackup.user_id == user.id, NpmBackup.created_at < cutoff)
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


def run_cron_tick(db: Session) -> dict:
    with _cron_tick_lock:
        now = utcnow()
        due_full = users_due_for_schedule(db, now)
        due_snapshot = [u for u in db.query(User).all() if user_due_for_snapshot_schedule(db, u, now)]
        users_due = {u.id: u for u in due_full + due_snapshot}
        errors = 0
        ran = 0
        for user in users_due.values():
            if user in due_full:
                log = run_user_backup(db, user, trigger="schedule", backup_kind="full")
                ran += 1
                if log.exit_code != 0:
                    errors += 1
            if user in due_snapshot:
                log = run_user_backup(db, user, trigger="schedule", backup_kind="snapshot")
                ran += 1
                if log.exit_code != 0:
                    errors += 1
        sync_ran = 0
        for user in db.query(User).all():
            if not user_due_for_sync_schedule(db, user, now):
                continue
            from .npm_sync_service import sync_all_scheduled_targets

            try:
                sync_all_scheduled_targets(db, user)
                sync_ran += 1
            except Exception:
                errors += 1
            if user.sync_schedule:
                user.sync_schedule.last_run_at = utcnow()
        dr_pushes = 0
        for user in db.query(User).all():
            due_remotes = [
                r for r in user.remote_destinations if remote_due_for_scheduled_push(r, now)
            ]
            if not due_remotes:
                continue
            try:
                log = run_user_backup(
                    db,
                    user,
                    trigger="dr_schedule",
                    backup_kind="full",
                    push_remotes=due_remotes,
                )
                if log.exit_code == 0:
                    dr_pushes += len(due_remotes)
                else:
                    errors += 1
            except Exception:
                errors += 1
        for user in db.query(User).all():
            try:
                purge_endpoint_retention(db, user)
            except Exception:
                errors += 1
        db.commit()
        tick = CronTickLog(
            users_due=len(users_due),
            backups_ran=ran,
            errors=errors,
            detail=f"full={len(due_full)} snap={len(due_snapshot)} sync={sync_ran} dr_push={dr_pushes}",
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
    source_label: str = "",
) -> NpmBackup:
    from .config import get_settings

    user = ensure_single_user(db)
    settings = get_settings()
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    dest = settings.backups_dir / file_name
    dest.write_bytes(file_bytes)
    manifest_json = "{}"
    backup_kind = "full"
    try:
        with zipfile.ZipFile(io.BytesIO(file_bytes)) as zf:
            if "manifest.json" in zf.namelist():
                manifest_json = zf.read("manifest.json").decode("utf-8")
                manifest = json.loads(manifest_json)
                if manifest.get("volume_files"):
                    backup_kind = "full"
                else:
                    backup_kind = "snapshot"
    except Exception:
        pass
    row = NpmBackup(
        user_id=user.id,
        name=name or file_name,
        snapshot_id=snapshot_id or "",
        api_url=source_label or "",
        file_name=dest.name,
        manifest_json=manifest_json,
        is_automated=False,
        backup_kind=backup_kind,
        size_bytes=len(file_bytes),
    )
    db.add(row)
    ui = user.ui_settings
    db.add(
        IngestEvent(
            user_id=user.id,
            ok=True,
            source_label=source_label or name or file_name,
            file_name=dest.name,
            detail=f"Stored {len(file_bytes)} bytes",
        )
    )
    db.commit()
    db.refresh(row)
    if ui and ui.toolbox_role == ROLE_BACKUP_ENDPOINT:
        purge_endpoint_retention(db, user)
    return row
