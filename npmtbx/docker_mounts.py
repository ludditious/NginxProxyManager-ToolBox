# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from typing import Any


def mount_source_for_destination(mounts: list[Any], destination: str) -> str:
    """Host bind path or Docker volume name/path for a container mount point."""
    dest = (destination or "").rstrip("/") or "/"
    for raw in mounts or []:
        if isinstance(raw, dict):
            d = str(raw.get("Destination") or "").rstrip("/")
            if d != dest:
                continue
            kind = str(raw.get("Type") or "bind")
            if kind == "bind":
                src = str(raw.get("Source") or "").strip()
            else:
                src = str(raw.get("Source") or raw.get("Name") or "").strip()
            if src:
                return src
            continue
        d = str(getattr(raw, "Destination", "") or "").rstrip("/")
        if d != dest:
            continue
        src = str(getattr(raw, "Source", "") or "").strip()
        if src:
            return src
        name = str(getattr(raw, "Name", "") or "").strip()
        if name:
            return name
    return ""


def mount_dest_map(mounts: list[Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for raw in mounts or []:
        if isinstance(raw, dict):
            dest = str(raw.get("Destination") or "").rstrip("/")
            if not dest:
                continue
            src = mount_source_for_destination([raw], dest)
            if src:
                out[dest] = src
            continue
        dest = str(getattr(raw, "Destination", "") or "").rstrip("/")
        if not dest:
            continue
        src = str(getattr(raw, "Source", "") or "").strip()
        if not src:
            src = str(getattr(raw, "Name", "") or "").strip()
        if src:
            out[dest] = src
    return out
