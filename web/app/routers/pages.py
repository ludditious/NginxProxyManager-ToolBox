# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from ..auth_constants import SECURITY_QUESTIONS
from ..backup_retention import BACKUP_RETENTION_OPTIONS, BACKUPS_PER_PAGE, retention_days_from_form
from ..config import get_settings
from ..crypto import encrypt
from ..database import get_db
from ..deps import get_current_user
from ..docker_discover import discover_npm_containers, single_high_confidence
from ..mount_diagnostics import diagnose_local_npm_mounts
from ..models import (
    DAY_KEYS,
    BackupRunLog,
    MasterInstance,
    NpmBackup,
    RemotePullSource,
    RemoteToolBox,
    SlaveInstance,
    ToolBoxBackup,
    User,
)
from ..npm_backup_service import (
    backup_file_path,
    delete_npm_backup,
    restore_npm_snapshot,
)
from ..restore_guard import require_restore_confirmation
from ..ui_context import ROLE_BACKUP_ENDPOINT, template_nav_extras
from ..npm_address import build_api_url_from_form, parse_api_url, validate_host
from npmtbx.dns_resolve import (
    host_override_map_from_rows,
    host_override_rows_from_form,
    host_override_rows_from_storage,
    parse_dns_server_list,
    serialize_host_override_rows,
)

from ..npm_bridge import (
    apply_candidate_to_local,
    apply_candidate_to_slave,
    npm_dns_servers_for_user,
    npm_host_overrides_for_user,
    store_secret,
    test_npm_connection,
)
from ..migrate_service import run_migrate_pull, run_migrate_push, save_migrate_credentials
from ..npm_sync_service import sync_source_to_target
from ..remote_toolbox import pull_latest_from_source, push_backup_to_remote
from ..server_registry import list_configured_servers, resolve_server_ref
from ..schedule_ui import DR_PUSH_INTERVAL_CHOICES, INTERVAL_CHOICES, minutes_from_form
from ..security import hash_password, verify_password
from ..email_notify import send_test_email
from ..services import (
    ensure_user_defaults,
    recovery_answers_for_display,
    run_user_backup,
    save_recovery_answers,
    user_has_recovery,
)
from ..toolbox_backup_service import (
    create_toolbox_backup,
    delete_toolbox_backup,
    restore_toolbox_backup,
)
from ..update_checker import apply_update_session, check_for_update, session_update_available
from ..version import APP_NAME, read_bundled_revision, read_bundled_version

router = APIRouter()
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))


def _host_override_rows_for_ui(stored: str) -> list[dict[str, str]]:
    try:
        return host_override_rows_from_storage(stored)
    except ValueError:
        return [{"host": "", "target": ""}]


def _ctx(request: Request, user: User, **extra):
    settings = get_settings()
    return {
        "request": request,
        "user": user,
        "app_title": settings.app_title,
        "app_name": APP_NAME,
        "app_version": read_bundled_version(),
        "app_revision": read_bundled_revision(),
        "current_username": user.username,
        "update_available": session_update_available(request.session),
        **template_nav_extras(user),
        **extra,
    }


def _redirect_if_backup_endpoint(user: User) -> RedirectResponse | None:
    ui = user.ui_settings
    if ui and ui.toolbox_role == ROLE_BACKUP_ENDPOINT:
        return RedirectResponse("/dashboard", status_code=303)
    return None


def _paginated_backups(
    db: Session,
    user: User,
    *,
    is_automated: bool,
    page: int,
    backup_kind: str,
) -> tuple:
    page = max(1, page)
    base = db.query(NpmBackup).filter(
        NpmBackup.user_id == user.id,
        NpmBackup.is_automated.is_(is_automated),
        NpmBackup.backup_kind == backup_kind,
    )
    total = base.count()
    total_pages = max(1, (total + BACKUPS_PER_PAGE - 1) // BACKUPS_PER_PAGE)
    if page > total_pages:
        page = total_pages
    rows = (
        base.order_by(NpmBackup.created_at.desc())
        .offset((page - 1) * BACKUPS_PER_PAGE)
        .limit(BACKUPS_PER_PAGE)
        .all()
    )
    return rows, page, total_pages, total


@router.get("/dashboard", response_class=HTMLResponse)
def dashboard(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_user_defaults(db, user)
    from ..models import IngestEvent

    ui = user.ui_settings
    extra: dict = {}
    if ui and ui.toolbox_role == ROLE_BACKUP_ENDPOINT:
        extra["stored_backups"] = (
            db.query(NpmBackup)
            .filter(NpmBackup.user_id == user.id)
            .order_by(NpmBackup.created_at.desc())
            .limit(50)
            .all()
        )
        extra["ingest_events"] = (
            db.query(IngestEvent)
            .filter(IngestEvent.user_id == user.id)
            .order_by(IngestEvent.created_at.desc())
            .limit(15)
            .all()
        )
    else:
        extra["recent"] = (
            db.query(BackupRunLog)
            .filter(BackupRunLog.user_id == user.id)
            .order_by(BackupRunLog.started_at.desc())
            .limit(8)
            .all()
        )
    return templates.TemplateResponse(request, "dashboard.html", _ctx(request, user, **extra))


@router.post("/backup-now")
def backup_now(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    ensure_user_defaults(db, user)
    if blocked := _redirect_if_backup_endpoint(user):
        return blocked
    run_user_backup(db, user, trigger="manual", backup_kind="full")
    return RedirectResponse("/logs", status_code=303)


@router.get("/master", response_class=HTMLResponse)
def master_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    msg: str | None = None,
    err: str | None = None,
):
    ensure_user_defaults(db, user)
    if blocked := _redirect_if_backup_endpoint(user):
        return blocked
    master = user.master
    npm_host, port_preset, npm_port_custom = parse_api_url(master.api_url if master else "")
    if master and master.admin_host:
        npm_host = master.admin_host
    connect_host = (master.connect_host if master else "") or ""
    return templates.TemplateResponse(
        request,
        "master.html",
        _ctx(
            request,
            user,
            master=master,
            npm_host=npm_host,
            connect_host=connect_host,
            port_preset=port_preset,
            npm_port_custom=npm_port_custom,
            message=msg,
            error=err,
        ),
    )


@router.post("/master/save")
def master_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    npm_host: str = Form(""),
    connect_host: str = Form(""),
    port_preset: str = Form("80"),
    npm_port_custom: str = Form(""),
    identity: str = Form(""),
    npm_password: str = Form(""),
    public_endpoint: str = Form(""),
    link_group: str = Form("default"),
    verify_tls: str | None = Form(None),
    enabled: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    master = user.master
    assert master is not None
    try:
        master.admin_host = npm_host.strip()
        master.connect_host = connect_host.strip()
        master.api_url = build_api_url_from_form(
            npm_host, port_preset, npm_port_custom, connect_host=connect_host
        )
    except ValueError as e:
        return RedirectResponse(f"/master?err={quote(str(e))}", status_code=303)
    master.identity = identity.strip()
    enc, _ = store_secret(npm_password, master.password_enc)
    master.password_enc = enc
    master.public_endpoint = public_endpoint.strip()
    master.link_group = link_group.strip() or "default"
    master.verify_tls = verify_tls == "on"
    master.enabled = enabled != "off"
    db.commit()
    return RedirectResponse("/master?msg=Source%20saved", status_code=303)


@router.post("/master/test")
def master_test(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    npm_host: str = Form(""),
    connect_host: str = Form(""),
    port_preset: str = Form("80"),
    npm_port_custom: str = Form(""),
    identity: str = Form(""),
    npm_password: str = Form(""),
    verify_tls: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    master = user.master
    assert master is not None
    try:
        api_url = build_api_url_from_form(
            npm_host, port_preset, npm_port_custom, connect_host=connect_host
        )
    except ValueError as e:
        return RedirectResponse(f"/master?err={quote(str(e))}", status_code=303)
    admin_host: str | None = None
    try:
        if (npm_host or "").strip():
            admin_host = validate_host(npm_host)
    except ValueError:
        admin_host = None
    res = test_npm_connection(
        api_url=api_url,
        identity=identity,
        password_enc=master.password_enc,
        form_secret=npm_password or None,
        verify_tls=verify_tls == "on",
        dns_servers=npm_dns_servers_for_user(user),
        host_overrides=npm_host_overrides_for_user(user),
        admin_host=admin_host,
    )
    key = "msg" if res.ok else "err"
    return RedirectResponse(f"/master?{key}={quote(res.message)}", status_code=303)


@router.post("/slaves/test")
def slave_test(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    slave_id: str = Form(""),
    npm_host: str = Form(""),
    connect_host: str = Form(""),
    port_preset: str = Form("80"),
    npm_port_custom: str = Form(""),
    identity: str = Form(""),
    npm_password: str = Form(""),
    verify_tls: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    password_enc = ""
    sid = slave_id.strip()
    if sid:
        slave = db.get(SlaveInstance, int(sid))
        if not slave or slave.user_id != user.id:
            return RedirectResponse("/synchronize?err=Target%20not%20found", status_code=303)
        password_enc = slave.password_enc
    try:
        api_url = build_api_url_from_form(
            npm_host, port_preset, npm_port_custom, connect_host=connect_host
        )
    except ValueError as e:
        return RedirectResponse(f"/synchronize?err={quote(str(e))}", status_code=303)
    admin_host: str | None = None
    try:
        if (npm_host or "").strip():
            admin_host = validate_host(npm_host)
    except ValueError:
        admin_host = None
    res = test_npm_connection(
        api_url=api_url,
        identity=identity,
        password_enc=password_enc,
        form_secret=npm_password or None,
        verify_tls=verify_tls == "on",
        dns_servers=npm_dns_servers_for_user(user),
        host_overrides=npm_host_overrides_for_user(user),
        admin_host=admin_host,
    )
    key = "msg" if res.ok else "err"
    return RedirectResponse(f"/synchronize?{key}={quote(res.message)}", status_code=303)


@router.get("/slaves")
def slaves_page():
    return RedirectResponse("/synchronize", status_code=307)


@router.post("/slaves/save")
def slave_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    slave_id: str = Form(""),
    name: str = Form(""),
    npm_host: str = Form(""),
    connect_host: str = Form(""),
    port_preset: str = Form("80"),
    npm_port_custom: str = Form(""),
    identity: str = Form(""),
    npm_password: str = Form(""),
    data_path: str = Form(""),
    letsencrypt_path: str = Form(""),
    public_endpoint: str = Form(""),
    link_group: str = Form("default"),
    verify_tls: str | None = Form(None),
    enabled: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    sid = slave_id.strip()
    if sid:
        slave = db.get(SlaveInstance, int(sid))
        if not slave or slave.user_id != user.id:
            return RedirectResponse("/synchronize?err=Not%20found", status_code=303)
    else:
        slave = SlaveInstance(user_id=user.id, sort_order=len(user.slaves))
        db.add(slave)
    slave.name = name.strip()
    try:
        slave.admin_host = npm_host.strip()
        slave.connect_host = connect_host.strip()
        slave.api_url = build_api_url_from_form(
            npm_host, port_preset, npm_port_custom, connect_host=connect_host
        )
    except ValueError as e:
        return RedirectResponse(f"/synchronize?err={quote(str(e))}", status_code=303)
    slave.identity = identity.strip()
    enc, _ = store_secret(npm_password, slave.password_enc)
    slave.password_enc = enc
    slave.data_path = data_path.strip()
    slave.letsencrypt_path = letsencrypt_path.strip()
    slave.public_endpoint = public_endpoint.strip()
    slave.link_group = link_group.strip() or "default"
    slave.verify_tls = verify_tls == "on"
    slave.enabled = enabled != "off"
    db.commit()
    return RedirectResponse("/synchronize?msg=Target%20saved", status_code=303)


@router.post("/slaves/delete")
def slave_delete(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    slave_id: int = Form(...),
):
    slave = db.get(SlaveInstance, slave_id)
    if slave and slave.user_id == user.id:
        db.delete(slave)
        db.commit()
    return RedirectResponse("/synchronize", status_code=303)


@router.post("/slaves/restore")
def slave_restore(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    slave_id: int = Form(...),
    backup_id: int = Form(...),
    confirm_phrase: str = Form(""),
):
    slave = db.get(SlaveInstance, slave_id)
    if not slave or slave.user_id != user.id:
        return RedirectResponse("/synchronize?err=Target%20not%20found", status_code=303)
    try:
        require_restore_confirmation(confirm_phrase)
        lines = restore_npm_snapshot(db, user, backup_id, target=slave)
        msg = "; ".join(lines)
        return RedirectResponse(f"/synchronize?msg={quote(msg)}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/synchronize?err={quote(str(e))}", status_code=303)


@router.get("/remote")
def remote_redirect():
    return RedirectResponse("/dr-sync", status_code=307)


@router.get("/migrate")
def migrate_redirect():
    return RedirectResponse("/dr-sync", status_code=307)


def _slave_rows(user: User) -> list[dict]:
    rows = []
    for s in sorted(user.slaves, key=lambda x: x.sort_order):
        h, pr, cu = parse_api_url(s.api_url)
        rows.append(
            {
                "slave": s,
                "npm_host": s.admin_host or h,
                "connect_host": s.connect_host or "",
                "port_preset": pr,
                "npm_port_custom": cu,
            }
        )
    return rows


@router.get("/dr-sync", response_class=HTMLResponse)
def dr_sync_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    msg: str | None = None,
    err: str | None = None,
):
    ensure_user_defaults(db, user)
    if blocked := _redirect_if_backup_endpoint(user):
        return blocked
    settings = get_settings()
    ui = user.ui_settings
    return templates.TemplateResponse(
        request,
        "dr_sync.html",
        _ctx(
            request,
            user,
            servers=list_configured_servers(user),
            destinations=sorted(user.remote_destinations, key=lambda d: d.sort_order),
            ingest_secret=settings.ingest_secret,
            public_toolbox_url=(ui.public_toolbox_url if ui else ""),
            day_keys=DAY_KEYS,
            dr_interval_choices=DR_PUSH_INTERVAL_CHOICES,
            message=msg,
            error=err,
        ),
    )


@router.post("/remote/destination/save")
def remote_dest_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    dest_id: str = Form(""),
    name: str = Form(""),
    base_url: str = Form(""),
    ingest_token: str = Form(""),
    enabled: str | None = Form(None),
    push_after_backup: str | None = Form(None),
):
    sk = get_settings().secret_key
    if dest_id.strip():
        row = db.get(RemoteToolBox, int(dest_id))
        if not row or row.user_id != user.id:
            return RedirectResponse("/remote?err=Not%20found", status_code=303)
    else:
        row = RemoteToolBox(user_id=user.id, sort_order=len(user.remote_destinations))
        db.add(row)
    row.name = name.strip()
    row.base_url = base_url.strip()
    if ingest_token.strip():
        row.ingest_token_enc = encrypt(sk, ingest_token.strip())
    row.enabled = enabled != "off"
    row.push_after_backup = push_after_backup != "off"
    db.commit()
    return RedirectResponse("/remote?msg=Saved", status_code=303)


@router.post("/remote/source/save")
def remote_source_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    source_id: str = Form(""),
    name: str = Form(""),
    base_url: str = Form(""),
    ingest_token: str = Form(""),
    enabled: str | None = Form(None),
):
    sk = get_settings().secret_key
    if source_id.strip():
        row = db.get(RemotePullSource, int(source_id))
        if not row or row.user_id != user.id:
            return RedirectResponse("/remote?err=Not%20found", status_code=303)
    else:
        row = RemotePullSource(user_id=user.id, sort_order=len(user.remote_sources))
        db.add(row)
    row.name = name.strip()
    row.base_url = base_url.strip()
    if ingest_token.strip():
        row.ingest_token_enc = encrypt(sk, ingest_token.strip())
    row.enabled = enabled != "off"
    db.commit()
    return RedirectResponse("/remote?msg=Saved", status_code=303)


@router.post("/remote/pull")
def remote_pull(user: User = Depends(get_current_user), db: Session = Depends(get_db), source_id: int = Form(...)):
    source = db.get(RemotePullSource, source_id)
    if not source or source.user_id != user.id:
        return RedirectResponse("/remote?err=Source%20not%20found", status_code=303)
    try:
        pulled = pull_latest_from_source(source)
        from ..services import ingest_snapshot_file

        meta = pulled["meta"]
        content = pulled["response"].content
        ingest_snapshot_file(
            db,
            file_name=f"pull-{meta.get('name', 'remote')}.zip",
            file_bytes=content,
            name=str(meta.get("name") or "remote"),
            snapshot_id=str(meta.get("snapshot_id") or ""),
        )
        return RedirectResponse("/remote?msg=Pull%20completed", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/remote?err={quote(str(e))}", status_code=303)


@router.get("/schedule")
def schedule_redirect():
    return RedirectResponse("/synchronize", status_code=307)


@router.get("/synchronize", response_class=HTMLResponse)
def synchronize_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    msg: str | None = None,
    err: str | None = None,
):
    ensure_user_defaults(db, user)
    if blocked := _redirect_if_backup_endpoint(user):
        return blocked
    sync_sched = user.sync_schedule
    return templates.TemplateResponse(
        request,
        "synchronize.html",
        _ctx(
            request,
            user,
            sync_schedule=sync_sched,
            target_rows=_slave_rows(user),
            slave_rows=_slave_rows(user),
            day_keys=DAY_KEYS,
            interval_choices=INTERVAL_CHOICES,
            message=msg,
            error=err,
        ),
    )


@router.post("/synchronize/backup/save")
def synchronize_backup_save_legacy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    enabled: str | None = Form(None),
    interval_minutes: str = Form("1440"),
    days: list[str] = Form(default=[]),
):
    return backup_restore_schedule_save(user, db, enabled, interval_minutes, days)


@router.post("/synchronize/sync-schedule/save")
def synchronize_sync_schedule_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    sync_enabled: str | None = Form(None),
    interval_minutes: str = Form("1440"),
    sync_days: list[str] = Form(default=[]),
):
    ensure_user_defaults(db, user)
    sched = user.sync_schedule
    assert sched is not None
    sched.enabled = sync_enabled == "on"
    sched.interval_minutes = minutes_from_form(interval_minutes)
    sched.set_days({d.lower() for d in sync_days if d.lower() in DAY_KEYS})
    db.commit()
    return RedirectResponse("/synchronize?msg=Sync%20schedule%20saved", status_code=303)


@router.post("/synchronize/targets/save")
def synchronize_targets_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    sync_target: list[str] = Form(default=[]),
):
    ensure_user_defaults(db, user)
    enabled_ids = {int(x) for x in sync_target if str(x).isdigit()}
    for slave in user.slaves:
        slave.schedule_sync_enabled = slave.id in enabled_ids
    db.commit()
    return RedirectResponse("/synchronize?msg=Target%20sync%20selection%20saved", status_code=303)


@router.post("/synchronize/sync-now/{slave_id}")
def synchronize_sync_now(
    slave_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    ensure_user_defaults(db, user)
    try:
        lines = sync_source_to_target(db, user, slave_id)
        return RedirectResponse(f"/synchronize?msg={quote('; '.join(lines))}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/synchronize?err={quote(str(e))}", status_code=303)


@router.post("/schedule/save")
def schedule_save_legacy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    enabled: str | None = Form(None),
    interval_minutes: str = Form("1440"),
    days: list[str] = Form(default=[]),
):
    return synchronize_backup_save(user, db, enabled, interval_minutes, days)


@router.post("/migrate/credentials/save")
def migrate_credentials_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    server_key: str = Form(""),
    migrate_url: str = Form(""),
    migrate_token: str = Form(""),
):
    ensure_user_defaults(db, user)
    ref = resolve_server_ref(user, server_key)
    if not ref:
        return RedirectResponse("/dr-sync?err=Invalid%20server", status_code=303)
    try:
        save_migrate_credentials(
            ref,
            base_url=migrate_url,
            ingest_token=migrate_token or None,
            secret_key=get_settings().secret_key,
        )
        db.commit()
    except ValueError as e:
        return RedirectResponse(f"/dr-sync?err={quote(str(e))}", status_code=303)
    return RedirectResponse("/dr-sync?msg=Connection%20saved", status_code=303)


@router.post("/migrate/push")
def migrate_push(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    server_key: str = Form(""),
    snapshot_type: str = Form("npm"),
):
    ensure_user_defaults(db, user)
    try:
        msg = run_migrate_push(db, user, server_key, snapshot_type)
        return RedirectResponse(f"/dr-sync?msg={quote(msg)}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/dr-sync?err={quote(str(e))}", status_code=303)


@router.post("/migrate/pull")
def migrate_pull(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    server_key: str = Form(""),
    snapshot_type: str = Form("npm"),
):
    ensure_user_defaults(db, user)
    try:
        msg = run_migrate_pull(db, user, server_key, snapshot_type)
        return RedirectResponse(f"/dr-sync?msg={quote(msg)}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/dr-sync?err={quote(str(e))}", status_code=303)


@router.post("/dr-sync/destination/save")
def dr_sync_destination_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    dest_id: str = Form(""),
    name: str = Form(""),
    base_url: str = Form(""),
    ingest_token: str = Form(""),
    destination_kind: str = Form("dr_site"),
    verify_tls: str | None = Form(None),
    enabled: str | None = Form(None),
    push_after_backup: str | None = Form(None),
    schedule_enabled: str | None = Form(None),
    interval_minutes: str = Form("10080"),
    days: list[str] = Form(default=[]),
):
    sk = get_settings().secret_key
    if dest_id.strip():
        row = db.get(RemoteToolBox, int(dest_id))
        if not row or row.user_id != user.id:
            return RedirectResponse("/dr-sync?err=Not%20found", status_code=303)
    else:
        row = RemoteToolBox(user_id=user.id, sort_order=len(user.remote_destinations))
        db.add(row)
    row.name = name.strip()
    row.base_url = base_url.strip()
    if ingest_token.strip():
        row.ingest_token_enc = encrypt(sk, ingest_token.strip())
    row.destination_kind = destination_kind.strip() or "dr_site"
    row.verify_tls = verify_tls == "on"
    row.enabled = enabled != "off"
    row.push_after_backup = push_after_backup != "off"
    row.schedule_enabled = schedule_enabled == "on"
    row.interval_minutes = minutes_from_form(interval_minutes)
    row.set_days({d.lower() for d in days if d.lower() in DAY_KEYS})
    db.commit()
    return RedirectResponse("/dr-sync?msg=Destination%20saved", status_code=303)


@router.post("/dr-sync/destination/delete")
def dr_sync_destination_delete(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    dest_id: int = Form(...),
):
    row = db.get(RemoteToolBox, dest_id)
    if row and row.user_id == user.id:
        db.delete(row)
        db.commit()
    return RedirectResponse("/dr-sync?msg=Deleted", status_code=303)


@router.post("/dr-sync/push-now")
def dr_sync_push_now(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    dest_id: int = Form(...),
):
    row = db.get(RemoteToolBox, dest_id)
    if not row or row.user_id != user.id:
        return RedirectResponse("/dr-sync?err=Not%20found", status_code=303)
    log = run_user_backup(db, user, trigger="manual", backup_kind="full", push_remotes=[row])
    if log.exit_code != 0:
        return RedirectResponse(f"/dr-sync?err={quote(log.body[:200])}", status_code=303)
    return RedirectResponse("/dr-sync?msg=Pushed", status_code=303)


@router.get("/logs", response_class=HTMLResponse)
def logs_page(request: Request, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    rows = (
        db.query(BackupRunLog)
        .filter(BackupRunLog.user_id == user.id)
        .order_by(BackupRunLog.started_at.desc())
        .limit(40)
        .all()
    )
    return templates.TemplateResponse(request, "logs.html", _ctx(request, user, logs=rows))


@router.get("/backups")
def backups_redirect():
    return RedirectResponse("/snapshots", status_code=307)


@router.get("/snapshots", response_class=HTMLResponse)
def snapshots_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    manual_page: int = 1,
    auto_page: int = 1,
    msg: str | None = None,
    err: str | None = None,
):
    ensure_user_defaults(db, user)
    if blocked := _redirect_if_backup_endpoint(user):
        return blocked
    manual, mp, mpages, mtotal = _paginated_backups(
        db, user, is_automated=False, page=manual_page, backup_kind="snapshot"
    )
    auto, ap, apages, atotal = _paginated_backups(
        db, user, is_automated=True, page=auto_page, backup_kind="snapshot"
    )
    snap_sched = user.snapshot_schedule
    local = user.local_npm
    npm_host, port_preset, npm_port_custom = parse_api_url(local.api_url if local else "")
    if local and local.admin_host:
        npm_host = local.admin_host
    return templates.TemplateResponse(
        request,
        "snapshots.html",
        _ctx(
            request,
            user,
            local=local,
            npm_host=npm_host,
            connect_host=(local.connect_host if local else "") or "",
            port_preset=port_preset,
            npm_port_custom=npm_port_custom,
            identity=(local.identity if local else "") or "",
            verify_tls=local.verify_tls if local else False,
            enabled=local.enabled if local else True,
            snapshot_schedule=snap_sched,
            manual_backups=manual,
            manual_page=mp,
            manual_pages=mpages,
            manual_total=mtotal,
            auto_backups=auto,
            auto_page=ap,
            auto_pages=apages,
            auto_total=atotal,
            day_keys=DAY_KEYS,
            interval_choices=INTERVAL_CHOICES,
            backup_retention_options=BACKUP_RETENTION_OPTIONS,
            message=msg or err,
            message_class="notice notice-ok" if msg else ("notice notice-err" if err else ""),
        ),
    )


@router.post("/snapshots/schedule/save")
def snapshots_schedule_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    enabled: str | None = Form(None),
    interval_minutes: str = Form("1440"),
    retention_days: str = Form("30"),
    days: list[str] = Form(default=[]),
):
    ensure_user_defaults(db, user)
    sched = user.snapshot_schedule
    assert sched is not None
    sched.enabled = enabled == "on"
    sched.interval_minutes = minutes_from_form(interval_minutes)
    sched.retention_days = retention_days_from_form(retention_days)
    sched.set_days({d.lower() for d in days if d.lower() in DAY_KEYS})
    db.commit()
    return RedirectResponse("/snapshots?msg=Snapshot%20schedule%20saved", status_code=303)


@router.post("/snapshots/local/save")
def snapshots_local_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    npm_host: str = Form(""),
    connect_host: str = Form(""),
    port_preset: str = Form("81"),
    npm_port_custom: str = Form(""),
    identity: str = Form(""),
    npm_password: str = Form(""),
    verify_tls: str | None = Form(None),
    enabled: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    local = user.local_npm
    assert local is not None
    try:
        local.admin_host = npm_host.strip()
        local.connect_host = connect_host.strip()
        local.api_url = build_api_url_from_form(
            npm_host, port_preset, npm_port_custom, connect_host=connect_host
        )
    except ValueError as e:
        return RedirectResponse(f"/snapshots?err={quote(str(e))}", status_code=303)
    local.identity = identity.strip()
    enc, _ = store_secret(npm_password, local.password_enc)
    local.password_enc = enc
    local.verify_tls = verify_tls == "on"
    local.enabled = enabled != "off"
    db.commit()
    return RedirectResponse("/snapshots?msg=NPM%20API%20settings%20saved", status_code=303)


@router.post("/snapshots/local/test")
def snapshots_local_test(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    npm_host: str = Form(""),
    connect_host: str = Form(""),
    port_preset: str = Form("81"),
    npm_port_custom: str = Form(""),
    identity: str = Form(""),
    npm_password: str = Form(""),
    verify_tls: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    local = user.local_npm
    password_enc = local.password_enc if local else ""
    try:
        api_url = build_api_url_from_form(
            npm_host, port_preset, npm_port_custom, connect_host=connect_host
        )
    except ValueError as e:
        return RedirectResponse(f"/snapshots?err={quote(str(e))}", status_code=303)
    admin_host: str | None = npm_host.strip() or None
    res = test_npm_connection(
        api_url=api_url,
        identity=identity,
        password_enc=password_enc,
        form_secret=npm_password or None,
        verify_tls=verify_tls == "on",
        dns_servers=npm_dns_servers_for_user(user),
        host_overrides=npm_host_overrides_for_user(user),
        admin_host=admin_host,
    )
    key = "msg" if res.ok else "err"
    return RedirectResponse(f"/snapshots?{key}={quote(res.message)}", status_code=303)


@router.post("/snapshots/create")
def snapshots_create(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log = run_user_backup(db, user, trigger="manual", backup_kind="snapshot")
    if log.exit_code != 0:
        return RedirectResponse(f"/snapshots?err={quote(log.body or 'Snapshot failed')}", status_code=303)
    return RedirectResponse("/snapshots?msg=Snapshot%20created", status_code=303)


@router.post("/snapshots/upload")
async def snapshots_upload(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_file: UploadFile = File(...),
):
    from ..services import ingest_snapshot_file

    raw = await backup_file.read()
    if not raw:
        return RedirectResponse("/snapshots?err=Empty%20file", status_code=303)
    name = backup_file.filename or "upload.zip"
    ingest_snapshot_file(
        db,
        file_name=name,
        file_bytes=raw,
        name=name,
        snapshot_id="",
    )
    return RedirectResponse("/snapshots?msg=Uploaded", status_code=303)


@router.post("/snapshots/restore")
def snapshots_restore(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_id: int = Form(...),
    confirm_phrase: str = Form(""),
):
    try:
        require_restore_confirmation(confirm_phrase)
        lines = restore_npm_snapshot(db, user, backup_id)
        return RedirectResponse(f"/snapshots?msg={quote('; '.join(lines))}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/snapshots?err={quote(str(e))}", status_code=303)


@router.get("/backup-restore", response_class=HTMLResponse)
def backup_restore_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    manual_page: int = 1,
    auto_page: int = 1,
    msg: str | None = None,
    err: str | None = None,
):
    ensure_user_defaults(db, user)
    if blocked := _redirect_if_backup_endpoint(user):
        return blocked
    manual, mp, mpages, mtotal = _paginated_backups(
        db, user, is_automated=False, page=manual_page, backup_kind="full"
    )
    auto, ap, apages, atotal = _paginated_backups(
        db, user, is_automated=True, page=auto_page, backup_kind="full"
    )
    local = user.local_npm
    candidates, discover_err = discover_npm_containers(probe_api=False)
    auto = single_high_confidence(candidates)
    mount_diag = diagnose_local_npm_mounts(local, candidates) if local else None
    npm_settings = user.npm_backup_settings
    return templates.TemplateResponse(
        request,
        "backup_restore.html",
        _ctx(
            request,
            user,
            local=local,
            candidates=candidates,
            discover_err=discover_err,
            auto_candidate=auto,
            mount_diag=mount_diag,
            schedule=user.schedule,
            retention_days=npm_settings.retention_days if npm_settings else 30,
            manual_backups=manual,
            manual_page=mp,
            manual_pages=mpages,
            manual_total=mtotal,
            auto_backups=auto,
            auto_page=ap,
            auto_pages=apages,
            auto_total=atotal,
            day_keys=DAY_KEYS,
            interval_choices=INTERVAL_CHOICES,
            backup_retention_options=BACKUP_RETENTION_OPTIONS,
            message=msg or err,
            message_class="notice notice-ok" if msg else ("notice notice-err" if err else ""),
        ),
    )


@router.post("/backup-restore/schedule/save")
def backup_restore_schedule_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    enabled: str | None = Form(None),
    interval_minutes: str = Form("1440"),
    retention_days: str = Form("30"),
    days: list[str] = Form(default=[]),
):
    ensure_user_defaults(db, user)
    sched = user.schedule
    assert sched is not None
    sched.enabled = enabled == "on"
    sched.interval_minutes = minutes_from_form(interval_minutes)
    sched.set_days({d.lower() for d in days if d.lower() in DAY_KEYS})
    settings = user.npm_backup_settings
    assert settings is not None
    settings.retention_days = retention_days_from_form(retention_days)
    db.commit()
    return RedirectResponse("/backup-restore?msg=Backup%20schedule%20saved", status_code=303)


@router.post("/backup-restore/local/save")
def backup_restore_local_save(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    data_path: str = Form(""),
    letsencrypt_path: str = Form(""),
    use_detected: str | None = Form(None),
    candidate_id: str = Form(""),
):
    ensure_user_defaults(db, user)
    local = user.local_npm
    assert local is not None
    if use_detected == "on" and candidate_id.strip():
        candidates, _ = discover_npm_containers()
        want = candidate_id.strip()
        for c in candidates:
            cid = c.container_id
            if cid == want or cid.startswith(want) or want.startswith(cid[:12]):
                apply_candidate_to_local(local, c)
                break
    local.data_path = data_path.strip()
    local.letsencrypt_path = letsencrypt_path.strip()
    local.enabled = True
    db.commit()
    return RedirectResponse("/backup-restore?msg=Local%20NPM%20saved", status_code=303)


@router.post("/backup-restore/create")
def backup_restore_create(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    log = run_user_backup(db, user, trigger="manual", backup_kind="full")
    if log.exit_code != 0:
        return RedirectResponse(
            f"/backup-restore?err={quote(log.body or 'Full backup failed')}",
            status_code=303,
        )
    return RedirectResponse("/backup-restore?msg=Full%20backup%20created", status_code=303)


@router.post("/backup-restore/upload")
async def backup_restore_upload(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_file: UploadFile = File(...),
    restore_after: str | None = Form(None),
    confirm_phrase: str = Form(""),
):
    from ..services import ingest_snapshot_file

    raw = await backup_file.read()
    if not raw:
        return RedirectResponse("/backup-restore?err=Empty%20file", status_code=303)
    name = backup_file.filename or "upload.zip"
    row = ingest_snapshot_file(
        db,
        file_name=name,
        file_bytes=raw,
        name=name,
        snapshot_id="",
    )
    msg = f"Uploaded {row.name}"
    if restore_after == "on":
        try:
            require_restore_confirmation(confirm_phrase)
            lines = restore_npm_snapshot(db, user, row.id)
            msg = f"{msg}; {'; '.join(lines)}"
        except Exception as e:
            return RedirectResponse(f"/backup-restore?err={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/backup-restore?msg={quote(msg)}", status_code=303)


@router.post("/backup-restore/restore")
def backup_restore_restore(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_id: int = Form(...),
    confirm_phrase: str = Form(""),
):
    try:
        require_restore_confirmation(confirm_phrase)
        lines = restore_npm_snapshot(db, user, backup_id)
        return RedirectResponse(f"/backup-restore?msg={quote('; '.join(lines))}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/backup-restore?err={quote(str(e))}", status_code=303)


@router.post("/backups/create")
def backups_create_legacy(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    run_user_backup(db, user, trigger="manual", backup_kind="full")
    return RedirectResponse("/backup-restore?msg=Full%20backup%20created", status_code=303)


@router.post("/backups/upload/toolbox")
async def backups_upload_toolbox(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    config_file: UploadFile = File(...),
    apply_now: str | None = Form(None),
):
    import json

    from ..toolbox_backup_service import CONFIG_FORMAT, restore_toolbox_backup

    raw = await config_file.read()
    if not raw:
        return RedirectResponse("/backups?err=Empty%20file", status_code=303)
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return RedirectResponse("/backups?err=Invalid%20JSON", status_code=303)
    if doc.get("format") != CONFIG_FORMAT:
        return RedirectResponse("/backups?err=Unsupported%20ToolBox%20config%20format", status_code=303)
    row = create_toolbox_backup(db, user)
    row.payload_json = json.dumps(doc, ensure_ascii=False)
    db.commit()
    db.refresh(row)
    msg = f"Uploaded ToolBox config {row.name}"
    if apply_now != "off":
        try:
            restore_toolbox_backup(db, user, row.id)
            msg = f"{msg} and applied"
        except Exception as e:
            return RedirectResponse(f"/backups?err={quote(str(e))}", status_code=303)
    return RedirectResponse(f"/backups?msg={quote(msg)}", status_code=303)


@router.get("/backups/download/{backup_id}")
def backups_download(backup_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(NpmBackup, backup_id)
    if not row or row.user_id != user.id:
        return RedirectResponse("/backups?err=Not%20found", status_code=303)
    path = backup_file_path(row)
    return FileResponse(path, filename=row.file_name, media_type="application/zip")


@router.post("/backups/delete")
def backups_delete(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_id: int = Form(...),
):
    try:
        delete_npm_backup(db, user, backup_id)
    except ValueError as e:
        return RedirectResponse(f"/backups?err={quote(str(e))}", status_code=303)
    return RedirectResponse("/backups?msg=Deleted", status_code=303)


@router.post("/backups/restore-master")
def backups_restore_master_legacy(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_id: int = Form(...),
    confirm_phrase: str = Form(""),
):
    try:
        require_restore_confirmation(confirm_phrase)
        lines = restore_npm_snapshot(db, user, backup_id)
        return RedirectResponse(f"/backup-restore?msg={quote('; '.join(lines))}", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/backup-restore?err={quote(str(e))}", status_code=303)


@router.post("/backups/push")
def backups_push(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    backup_id: int = Form(...),
    dest_id: int = Form(...),
):
    row = db.get(NpmBackup, backup_id)
    dest = db.get(RemoteToolBox, dest_id)
    if not row or row.user_id != user.id or not dest or dest.user_id != user.id:
        return RedirectResponse("/backups?err=Invalid%20selection", status_code=303)
    try:
        push_backup_to_remote(dest, row, backup_file_path(row))
        return RedirectResponse("/backups?msg=Pushed", status_code=303)
    except Exception as e:
        return RedirectResponse(f"/backups?err={quote(str(e))}", status_code=303)


@router.post("/backups/toolbox/create")
def toolbox_backup_create(user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    create_toolbox_backup(db, user)
    return RedirectResponse("/backups?msg=ToolBox%20config%20saved", status_code=303)


@router.get("/backups/toolbox/{backup_id}/download")
def toolbox_download(backup_id: int, user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    row = db.get(ToolBoxBackup, backup_id)
    if not row or row.user_id != user.id:
        return RedirectResponse("/backups?err=Not%20found", status_code=303)
    from fastapi.responses import Response

    return Response(
        content=row.payload_json,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="{row.name}.json"'},
    )


@router.post("/backups/toolbox/{backup_id}/restore")
def toolbox_backup_restore(
    backup_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        restore_toolbox_backup(db, user, backup_id)
        return RedirectResponse("/backups?msg=ToolBox%20config%20restored", status_code=303)
    except ValueError as e:
        return RedirectResponse(f"/backups?err={quote(str(e))}", status_code=303)


@router.post("/backups/toolbox/{backup_id}/delete")
def toolbox_backup_delete(
    backup_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        delete_toolbox_backup(db, user, backup_id)
    except ValueError as e:
        return RedirectResponse(f"/backups?err={quote(str(e))}", status_code=303)
    return RedirectResponse("/backups?msg=Deleted", status_code=303)


@router.get("/settings", response_class=HTMLResponse)
def settings_page(
    request: Request,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    msg: str | None = None,
    err: str | None = None,
):
    ensure_user_defaults(db, user)
    sk = get_settings().secret_key
    settings = get_settings()
    ui = user.ui_settings
    dns_settings = user.npm_dns_settings
    smtp = user.smtp_settings
    prefs = user.notification_prefs
    return templates.TemplateResponse(
        request,
        "settings.html",
        _ctx(
            request,
            user,
            message=msg or err,
            message_class="notice notice-ok" if msg else ("notice notice-err" if err else "notice"),
            security_questions=SECURITY_QUESTIONS,
            recovery_answers=recovery_answers_for_display(user, sk),
            recovery_reenter=user_has_recovery(user) and not any(recovery_answers_for_display(user, sk)),
            recovery_configured=user_has_recovery(user),
            check_updates_on_login=user.check_updates_on_login,
            toolbox_role=ui.toolbox_role if ui else "primary",
            public_toolbox_url=ui.public_toolbox_url if ui else "",
            nav_show_source=ui.nav_show_source if ui else True,
            nav_show_snapshots=ui.nav_show_snapshots if ui else True,
            nav_show_synchronize=ui.nav_show_synchronize if ui else True,
            nav_show_backup_restore=ui.nav_show_backup_restore if ui else True,
            nav_show_dr_sync=ui.nav_show_dr_sync if ui else True,
            nav_show_logs=ui.nav_show_logs if ui else True,
            endpoint_retention_days=ui.endpoint_retention_days if ui else 30,
            dns_use_custom=dns_settings.use_custom_dns if dns_settings else False,
            dns_servers=dns_settings.dns_servers if dns_settings else "",
            host_overrides_enabled=dns_settings.use_host_overrides if dns_settings else False,
            host_override_rows=_host_override_rows_for_ui(
                dns_settings.host_overrides if dns_settings else ""
            ),
            backup_retention_options=BACKUP_RETENTION_OPTIONS,
            ingest_secret=settings.ingest_secret,
            smtp=smtp,
            prefs=prefs,
        ),
    )


@router.post("/settings/account/password")
def settings_password(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    current_password: str = Form(""),
    new_password: str = Form(""),
    confirm_password: str = Form(""),
):
    if not verify_password(current_password, user.password_hash):
        return RedirectResponse("/settings?err=Current%20password%20incorrect", status_code=303)
    if len(new_password) < 8 or new_password != confirm_password:
        return RedirectResponse("/settings?err=Password%20invalid", status_code=303)
    user.password_hash = hash_password(new_password)
    db.commit()
    return RedirectResponse("/settings?msg=Password%20updated", status_code=303)


@router.post("/settings/account/recovery")
def settings_recovery(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    answer_1: str = Form(""),
    answer_2: str = Form(""),
    answer_3: str = Form(""),
):
    try:
        save_recovery_answers(user, (answer_1, answer_2, answer_3), get_settings().secret_key)
        db.commit()
    except ValueError as e:
        return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
    return RedirectResponse("/settings?msg=Recovery%20saved", status_code=303)


@router.post("/settings/dns")
def settings_dns(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    use_custom_dns: str | None = Form(None),
    dns_servers: str = Form(""),
):
    ensure_user_defaults(db, user)
    row = user.npm_dns_settings
    assert row is not None
    row.use_custom_dns = use_custom_dns == "on"
    text = dns_servers.strip()
    if row.use_custom_dns:
        try:
            servers = parse_dns_server_list(text)
        except ValueError as e:
            return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
        if not servers:
            return RedirectResponse(
                "/settings?err=Enter%20at%20least%20one%20DNS%20server%20IP",
                status_code=303,
            )
        row.dns_servers = ", ".join(servers)
    elif text:
        row.dns_servers = text
    db.commit()
    return RedirectResponse("/settings?msg=DNS%20settings%20saved", status_code=303)


@router.post("/settings/host-overrides")
def settings_host_overrides(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    use_host_overrides: str | None = Form(None),
    override_host: list[str] = Form(default=[]),
    override_target: list[str] = Form(default=[]),
):
    ensure_user_defaults(db, user)
    row = user.npm_dns_settings
    assert row is not None
    row.use_host_overrides = use_host_overrides == "on"
    try:
        pairs = host_override_rows_from_form(override_host, override_target)
    except ValueError as e:
        return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
    if row.use_host_overrides:
        if not pairs:
            return RedirectResponse(
                "/settings?err=Enter%20host%20and%20target%20for%20at%20least%20one%20override",
                status_code=303,
            )
        try:
            host_override_map_from_rows(pairs)
        except ValueError as e:
            return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
        row.host_overrides = serialize_host_override_rows(pairs)
    elif pairs:
        row.host_overrides = serialize_host_override_rows(pairs)
    db.commit()
    return RedirectResponse("/settings?msg=Host%20override%20settings%20saved", status_code=303)


@router.post("/settings/ui")
def settings_ui(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    toolbox_role: str = Form("primary"),
    public_toolbox_url: str = Form(""),
    nav_show_source: str | None = Form(None),
    nav_show_snapshots: str | None = Form(None),
    nav_show_synchronize: str | None = Form(None),
    nav_show_backup_restore: str | None = Form(None),
    nav_show_dr_sync: str | None = Form(None),
    nav_show_logs: str | None = Form(None),
    endpoint_retention_days: str = Form("30"),
):
    ensure_user_defaults(db, user)
    ui = user.ui_settings
    assert ui is not None
    role = toolbox_role.strip() or "primary"
    if role not in ("primary", "backup_endpoint", "dr_site"):
        role = "primary"
    ui.toolbox_role = role
    ui.public_toolbox_url = public_toolbox_url.strip()
    ui.nav_show_source = nav_show_source == "on"
    ui.nav_show_snapshots = nav_show_snapshots == "on"
    ui.nav_show_synchronize = nav_show_synchronize == "on"
    ui.nav_show_backup_restore = nav_show_backup_restore == "on"
    ui.nav_show_dr_sync = nav_show_dr_sync == "on"
    ui.nav_show_logs = nav_show_logs == "on"
    ui.endpoint_retention_days = retention_days_from_form(endpoint_retention_days)
    db.commit()
    return RedirectResponse("/settings?msg=Role%20and%20navigation%20saved", status_code=303)


@router.post("/settings/smtp/test")
def settings_smtp_test(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    test_to: str = Form(""),
):
    ensure_user_defaults(db, user)
    to_addr = test_to.strip()
    if not to_addr:
        return RedirectResponse("/settings?err=Test%20recipient%20required", status_code=303)
    try:
        send_test_email(user, to_addr)
    except Exception as e:
        return RedirectResponse(f"/settings?err={quote(str(e))}", status_code=303)
    return RedirectResponse("/settings?msg=Test%20email%20sent", status_code=303)


@router.post("/settings/updates")
def settings_updates(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    check_updates_on_login: str | None = Form(None),
):
    user.check_updates_on_login = check_updates_on_login == "on"
    db.commit()
    return RedirectResponse("/settings?msg=Update%20preference%20saved", status_code=303)


@router.post("/settings/smtp")
def settings_smtp(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    smtp_enabled: str | None = Form(None),
    smtp_host: str = Form(""),
    smtp_port: str = Form("587"),
    smtp_use_tls: str | None = Form(None),
    smtp_username: str = Form(""),
    smtp_password: str = Form(""),
    smtp_from: str = Form(""),
    smtp_to: str = Form(""),
):
    sk = get_settings().secret_key
    ensure_user_defaults(db, user)
    smtp = user.smtp_settings
    assert smtp is not None
    smtp.enabled = smtp_enabled == "on"
    smtp.host = smtp_host.strip()
    smtp.port = int(smtp_port or "587")
    smtp.use_tls = smtp_use_tls != "off"
    if smtp_username.strip():
        smtp.username_enc = encrypt(sk, smtp_username.strip())
    if smtp_password.strip():
        smtp.password_enc = encrypt(sk, smtp_password.strip())
    smtp.from_address = smtp_from.strip()
    smtp.to_addresses = smtp_to.strip()
    db.commit()
    return RedirectResponse("/settings?msg=SMTP%20saved", status_code=303)


@router.post("/settings/notifications")
def settings_notifications(
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
    on_backup_success: str | None = Form(None),
    on_backup_failure: str | None = Form(None),
    on_push_failure: str | None = Form(None),
    on_pull_failure: str | None = Form(None),
):
    ensure_user_defaults(db, user)
    prefs = user.notification_prefs
    assert prefs is not None
    prefs.on_backup_success = on_backup_success != "off"
    prefs.on_backup_failure = on_backup_failure != "off"
    prefs.on_push_failure = on_push_failure != "off"
    prefs.on_pull_failure = on_pull_failure != "off"
    db.commit()
    return RedirectResponse("/settings?msg=Notifications%20saved", status_code=303)


@router.get("/about", response_class=HTMLResponse)
def about_page(request: Request, user: User = Depends(get_current_user)):
    return templates.TemplateResponse(request, "about.html", _ctx(request, user))


@router.get("/update", response_class=HTMLResponse)
def update_page(request: Request, user: User = Depends(get_current_user)):
    status = check_for_update()
    apply_update_session(request.session, status)
    return templates.TemplateResponse(
        request,
        "update.html",
        _ctx(
            request,
            user,
            installed_version=status.installed_version,
            remote_version=status.remote_version,
            pull_command=status.pull_command,
            release_notes=status.release_notes,
            update_check_error=status.error,
        ),
    )
