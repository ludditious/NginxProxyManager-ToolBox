# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

import requests

from .config import get_settings
from .crypto import decrypt
from .models import NpmBackup, RemotePullSource, RemoteToolBox


def _normalize_base(url: str) -> str:
    raw = (url or "").strip().rstrip("/")
    if not raw.startswith("http"):
        raw = "http://" + raw
    return raw


def push_backup_to_remote(remote: RemoteToolBox, backup: NpmBackup, zip_path: Path) -> None:
    sk = get_settings().secret_key
    token = decrypt(sk, remote.ingest_token_enc)
    if not token:
        raise ValueError(f"Remote {remote.name}: ingest token missing.")
    base = _normalize_base(remote.base_url)
    url = f"{base}/internal/ingest/snapshot"
    with zip_path.open("rb") as fh:
        resp = requests.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            files={"file": (backup.file_name, fh, "application/zip")},
            data={
                "name": backup.name,
                "snapshot_id": backup.snapshot_id,
                "source_label": backup.api_url or backup.name,
            },
            timeout=600,
            verify=bool(remote.verify_tls),
        )
    if resp.status_code >= 400:
        raise RuntimeError(f"Push failed ({resp.status_code}): {resp.text[:500]}")


def pull_latest_from_source(source: RemotePullSource) -> dict:
    sk = get_settings().secret_key
    token = decrypt(sk, source.ingest_token_enc)
    if not token:
        raise ValueError(f"Source {source.name}: ingest token missing.")
    base = _normalize_base(source.base_url)
    meta_url = f"{base}/internal/ingest/latest"
    resp = requests.get(
        meta_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=60,
    )
    if resp.status_code >= 400:
        raise RuntimeError(f"Pull metadata failed ({resp.status_code}): {resp.text[:500]}")
    meta = resp.json()
    download_url = meta.get("download_url")
    if not download_url:
        raise RuntimeError("Remote did not return download_url.")
    if download_url.startswith("/"):
        download_url = base + download_url
    dl = requests.get(
        download_url,
        headers={"Authorization": f"Bearer {token}"},
        timeout=600,
        stream=True,
    )
    if dl.status_code >= 400:
        raise RuntimeError(f"Download failed ({dl.status_code}): {dl.text[:300]}")
    return {"meta": meta, "response": dl}
