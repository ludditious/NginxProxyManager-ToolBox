# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

BACKUP_FORMAT = "nginxproxymanager-toolbox-backup"
BACKUP_VERSION = 1


def build_manifest(
    *,
    snapshot_id: str,
    source_label: str,
    api_base_url: str,
    docker_image: str = "",
    docker_container_id: str = "",
    npm_api_export: dict[str, Any] | None = None,
    volume_files: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "format": BACKUP_FORMAT,
        "version": BACKUP_VERSION,
        "snapshot_id": snapshot_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "source_label": source_label,
        "api_base_url": api_base_url,
        "docker_image": docker_image,
        "docker_container_id": docker_container_id,
        "api_export_keys": sorted((npm_api_export or {}).keys()),
        "volume_files": volume_files or {},
    }


def parse_manifest(raw: str | bytes) -> dict[str, Any]:
    data = json.loads(raw)
    if not isinstance(data, dict):
        raise ValueError("Invalid manifest JSON.")
    if data.get("format") != BACKUP_FORMAT:
        raise ValueError(f"Unsupported backup format: {data.get('format')!r}")
    version = int(data.get("version") or 0)
    if version != BACKUP_VERSION:
        raise ValueError(f"Unsupported backup version: {version}")
    return data
