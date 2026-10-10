# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path

DEFAULT_DATA_PATH = "/npm-data"
DEFAULT_LETSENCRYPT_PATH = "/npm-letsencrypt"


def resolve_toolbox_data_path(docker_host_path: str = "") -> str:
    if Path(DEFAULT_DATA_PATH).is_dir():
        return DEFAULT_DATA_PATH
    host = (docker_host_path or "").strip()
    if host and Path(host).is_dir():
        return host
    return DEFAULT_DATA_PATH


def resolve_toolbox_letsencrypt_path(docker_host_path: str = "") -> str:
    if Path(DEFAULT_LETSENCRYPT_PATH).is_dir():
        return DEFAULT_LETSENCRYPT_PATH
    host = (docker_host_path or "").strip()
    if host and Path(host).is_dir():
        return host
    return DEFAULT_LETSENCRYPT_PATH


def validate_full_backup_paths(data_path: str, letsencrypt_path: str) -> None:
    dp = (data_path or "").strip()
    if not dp:
        raise ValueError(
            "Set the NPM data folder path (inside this ToolBox container), then Save. "
            "Example: /npm-data — bind-mount the same host folder you use for NPM /data."
        )
    data = Path(dp)
    if not data.is_dir():
        raise ValueError(
            f"NPM data folder not found inside ToolBox: {dp!r}. "
            "Add a volume to the ToolBox container, e.g. -v /host/npm/data:/npm-data"
        )
    le = (letsencrypt_path or "").strip()
    if le and not Path(le).is_dir():
        raise ValueError(
            f"Certificates folder not found inside ToolBox: {le!r}. "
            "Add -v /host/npm/letsencrypt:/npm-letsencrypt or leave certificates path blank."
        )
