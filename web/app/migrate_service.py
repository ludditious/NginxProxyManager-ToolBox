# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from pathlib import Path

import requests
from sqlalchemy.orm import Session

from .config import get_settings
from .crypto import decrypt, encrypt
from .models import User
from .npm_backup_service import backup_file_path, create_npm_backup
from .remote_toolbox import _normalize_base
from .server_registry import ServerRef, migrate_row, resolve_server_ref
from .toolbox_backup_service import build_toolbox_document, create_toolbox_backup, restore_toolbox_backup


def save_migrate_credentials(
    ref: ServerRef,
    *,
    base_url: str,
    ingest_token: str | None,
    secret_key: str,
) -> None:
    row = migrate_row(ref)
    if not row:
        raise ValueError("Invalid server.")
    row.migrate_toolbox_url = base_url.strip()
    if ingest_token and ingest_token.strip():
        row.migrate_ingest_token_enc = encrypt(secret_key, ingest_token.strip())


def _token_for_ref(ref: ServerRef) -> tuple[str, str]:
    row = migrate_row(ref)
    if not row:
        raise ValueError("Server not found.")
    url = (row.migrate_toolbox_url or "").strip()
    if not url:
        raise ValueError(f"{ref.label}: ToolBox URL is not set on Migrate.")
    sk = get_settings().secret_key
    token = decrypt(sk, row.migrate_ingest_token_enc)
    if not token:
        raise ValueError(f"{ref.label}: ingest token is not set on Migrate.")
    return _normalize_base(url), token


def push_npm_snapshot(db: Session, user: User, dest_ref: ServerRef) -> str:
    row = create_npm_backup(db, user, is_automated=False, backup_kind="full")
    base, token = _token_for_ref(dest_ref)
    path = backup_file_path(row)
    with path.open("rb") as fh:
        resp = requests.post(
            f"{base}/internal/ingest/snapshot",
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (row.file_name, fh, "application/zip")},
            data={"name": row.name, "snapshot_id": row.snapshot_id},
            timeout=600,
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Push failed ({resp.status_code}): {resp.text[:300]}")
    return f"Pushed NPM backup {row.name} to {dest_ref.label}"


def push_toolbox_config(db: Session, user: User, dest_ref: ServerRef) -> str:
    doc = build_toolbox_document(user)
    base, token = _token_for_ref(dest_ref)
    resp = requests.post(
        f"{base}/internal/ingest/toolbox-config",
        headers={"Authorization": f"Bearer {token}"},
        json=doc,
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Push failed ({resp.status_code}): {resp.text[:300]}")
    return f"Pushed ToolBox config to {dest_ref.label}"


def pull_npm_snapshot(db: Session, user: User, source_ref: ServerRef) -> str:
    from .services import ingest_snapshot_file

    base, token = _token_for_ref(source_ref)
    meta_resp = requests.get(
        f"{base}/internal/ingest/latest",
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if meta_resp.status_code >= 400:
        raise RuntimeError(f"Pull failed ({meta_resp.status_code}): {meta_resp.text[:300]}")
    meta = meta_resp.json()
    download_url = meta.get("download_url") or ""
    if download_url.startswith("/"):
        download_url = base + download_url
    dl = requests.get(
        download_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=600,
    )
    if dl.status_code >= 400:
        raise RuntimeError(f"Download failed ({dl.status_code})")
    ingest_snapshot_file(
        db,
        file_name=f"pull-{meta.get('name') or 'snapshot'}.zip".replace("/", "-"),
        file_bytes=dl.content,
        name=str(meta.get("name") or "remote-pull"),
        snapshot_id=str(meta.get("snapshot_id") or ""),
    )
    return f"Pulled NPM backup from {source_ref.label}"


def pull_toolbox_config(db: Session, user: User, source_ref: ServerRef) -> str:
    base, token = _token_for_ref(source_ref)
    resp = requests.get(
        f"{base}/internal/ingest/toolbox-config/latest",
        headers={"Authorization": f"Bearer {token}"},
        timeout=120,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Pull failed ({resp.status_code}): {resp.text[:300]}")
    doc = resp.json()
    backup = create_toolbox_backup(db, user)
    backup.payload_json = json.dumps(doc, ensure_ascii=False)
    db.commit()
    restore_toolbox_backup(db, user, backup.id)
    return f"Pulled and applied ToolBox config from {source_ref.label}"


def run_migrate_push(db: Session, user: User, server_key: str, snapshot_type: str) -> str:
    ref = resolve_server_ref(user, server_key)
    if not ref:
        raise ValueError("Select a configured server.")
    if snapshot_type == "toolbox":
        return push_toolbox_config(db, user, ref)
    return push_npm_snapshot(db, user, ref)


def run_migrate_pull(db: Session, user: User, server_key: str, snapshot_type: str) -> str:
    ref = resolve_server_ref(user, server_key)
    if not ref:
        raise ValueError("Select a configured server.")
    if snapshot_type == "toolbox":
        return pull_toolbox_config(db, user, ref)
    return pull_npm_snapshot(db, user, ref)
