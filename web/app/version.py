# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import re
from pathlib import Path

APP_NAME = "Nginx Proxy Manager ToolBox"
APP_REVISION = "2026-10-08-1"

_VERSION_FILE_PATTERN = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})-(\d+)$")


def read_bundled_version() -> str:
    for path in (
        Path("/app/version.txt"),
        Path(__file__).resolve().parents[2] / "version.txt",
    ):
        if path.is_file():
            text = path.read_text(encoding="utf-8").strip()
            if text:
                return text
    return "unknown"


def revision_from_version(version: str) -> str:
    v = (version or "").strip()
    m = _VERSION_FILE_PATTERN.match(v)
    if m:
        return f"{m.group(1)}-{m.group(2)}-{m.group(3)}-{m.group(4)}"
    return v or "unknown"


def read_bundled_revision() -> str:
    rev = revision_from_version(read_bundled_version())
    if rev != "unknown":
        return rev
    return APP_REVISION
