# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json

from fastapi import APIRouter, Body, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..database import get_db
from ..models import NpmBackup
from ..npm_backup_service import backup_file_path
from ..services import ensure_single_user, ingest_snapshot_file, run_cron_tick
from ..toolbox_backup_service import CONFIG_FORMAT, build_toolbox_document, restore_toolbox_backup
from ..models import ToolBoxBackup

router = APIRouter()


def _require_ingest(authorization: str | None) -> None:
    settings = get_settings()
    expected = f"Bearer {settings.ingest_secret}"
    if authorization != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


@router.post("/internal/cron/tick")
def cron_tick(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    settings = get_settings()
    expected = f"Bearer {settings.cron_secret}"
    if authorization != expected:
        raise HTTPException(status_code=403, detail="Forbidden")
    result = run_cron_tick(db)
    return {"ok": True, **result}


@router.post("/internal/ingest/snapshot")
async def ingest_snapshot(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
    file: UploadFile = File(...),
    name: str = Form(""),
    snapshot_id: str = Form(""),
    source_label: str = Form(""),
):
    _require_ingest(authorization)
    raw = await file.read()
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")
    row = ingest_snapshot_file(
        db,
        file_name=file.filename or "ingest.zip",
        file_bytes=raw,
        name=name or file.filename or "ingest",
        snapshot_id=snapshot_id,
        source_label=source_label.strip(),
    )
    return {"ok": True, "backup_id": row.id, "name": row.name}


@router.get("/internal/ingest/latest")
def ingest_latest(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    _require_ingest(authorization)
    user = ensure_single_user(db)
    row = (
        db.query(NpmBackup)
        .filter(NpmBackup.user_id == user.id)
        .order_by(NpmBackup.created_at.desc())
        .first()
    )
    if not row:
        raise HTTPException(status_code=404, detail="No backups")
    return {
        "backup_id": row.id,
        "name": row.name,
        "snapshot_id": row.snapshot_id,
        "download_url": f"/internal/ingest/download/{row.id}",
    }


@router.get("/internal/ingest/download/{backup_id}")
def ingest_download(
    backup_id: int,
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    _require_ingest(authorization)
    user = ensure_single_user(db)
    row = db.get(NpmBackup, backup_id)
    if not row or row.user_id != user.id:
        raise HTTPException(status_code=404, detail="Not found")
    path = backup_file_path(row)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing")
    return FileResponse(path, filename=row.file_name, media_type="application/zip")


@router.post("/internal/ingest/toolbox-config")
def ingest_toolbox_config(
    payload: dict = Body(...),
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    _require_ingest(authorization)
    if payload.get("format") != CONFIG_FORMAT:
        raise HTTPException(status_code=400, detail="Unsupported ToolBox config format.")
    user = ensure_single_user(db)
    row = ToolBoxBackup(
        user_id=user.id,
        name="ingest-toolbox-config",
        payload_json=json.dumps(payload, ensure_ascii=False),
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    restore_toolbox_backup(db, user, row.id)
    return {"ok": True}


@router.get("/internal/ingest/toolbox-config/latest")
def ingest_toolbox_latest(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
):
    _require_ingest(authorization)
    user = ensure_single_user(db)
    return build_toolbox_document(user)
