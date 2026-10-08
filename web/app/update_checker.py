# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import requests

DOCKER_PULL_IMAGE = "ghcr.io/ludditious/nginxproxymanager-toolbox:latest"
DEFAULT_RAW_BASE = "https://raw.githubusercontent.com/ludditious/NginxProxyManager-ToolBox/main"
FETCH_TIMEOUT = 12


@dataclass(frozen=True)
class UpdateStatus:
    installed_version: str
    remote_version: str | None
    update_available: bool
    pull_command: str
    release_notes: str
    error: str | None = None


def _raw_base() -> str:
    return (os.environ.get("UPDATE_CHECK_RAW_BASE") or DEFAULT_RAW_BASE).rstrip("/")


def installed_version() -> str:
    for path in (Path("/app/version.txt"), Path(__file__).resolve().parents[2] / "version.txt"):
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "unknown"


def _version_sort_key(version: str) -> tuple[str, int]:
    text = (version or "").strip()
    if not text or text == "unknown":
        return ("", -1)
    date_part, _, tail = text.partition("-")
    run = int(tail) if tail.isdigit() else 0
    return (date_part, run)


def _remote_is_newer(installed: str, remote: str) -> bool:
    return _version_sort_key(remote) > _version_sort_key(installed)


def check_for_update() -> UpdateStatus:
    inst = installed_version()
    base = _raw_base()
    pull = f"docker pull {DOCKER_PULL_IMAGE}"
    try:
        resp = requests.get(
            f"{base}/version.txt",
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "NginxProxyManager-ToolBox-UpdateCheck/1.0"},
        )
        resp.raise_for_status()
        remote_ver = resp.text.strip()
        notes_resp = requests.get(
            f"{base}/current-release.txt",
            timeout=FETCH_TIMEOUT,
            headers={"User-Agent": "NginxProxyManager-ToolBox-UpdateCheck/1.0"},
        )
        notes = notes_resp.text.strip() if notes_resp.ok else ""
    except requests.RequestException as exc:
        return UpdateStatus(
            installed_version=inst,
            remote_version=None,
            update_available=False,
            pull_command=pull,
            release_notes="",
            error=str(exc),
        )
    update = bool(remote_ver) and _remote_is_newer(inst, remote_ver)
    return UpdateStatus(
        installed_version=inst,
        remote_version=remote_ver,
        update_available=update,
        pull_command=pull,
        release_notes=notes,
        error=None,
    )


def apply_update_session(session: dict, status: UpdateStatus) -> None:
    session["update_available"] = status.update_available
    session["update_installed_version"] = status.installed_version
    session["update_remote_version"] = status.remote_version or ""
    session["update_release_notes"] = status.release_notes
    session["update_pull_command"] = status.pull_command
    session["update_check_error"] = status.error or ""


def refresh_update_nav_session(session: dict) -> None:
    remote = (session.get("update_remote_version") or "").strip()
    if not remote:
        return
    inst = installed_version()
    session["update_available"] = _remote_is_newer(inst, remote)


def session_update_available(session: dict) -> bool:
    refresh_update_nav_session(session)
    return bool(session.get("update_available"))
