# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path


@lru_cache(maxsize=1)
def get_settings() -> "Settings":
    return Settings()


def clear_settings_cache() -> None:
    get_settings.cache_clear()


class Settings:
    def __init__(self) -> None:
        self.secret_key = os.environ.get("SECRET_KEY", "change-me-in-production")
        self.database_url = os.environ.get(
            "DATABASE_URL",
            "sqlite:////data/npmtoolbox.db",
        )
        self.cron_secret = os.environ.get("CRON_SECRET", "change-cron-secret")
        self.ingest_secret = os.environ.get("INGEST_SECRET", "change-ingest-secret")
        self.allow_registration = os.environ.get("ALLOW_REGISTRATION", "true").lower() in (
            "1",
            "true",
            "yes",
        )
        self.app_title = os.environ.get("APP_TITLE", "Nginx Proxy Manager ToolBox")
        self.host = os.environ.get("HOST", "0.0.0.0")
        self.port = int(os.environ.get("PORT", "8080"))
        self.data_dir = Path(os.environ.get("DATA_DIR", "/data"))
        self.backups_dir = self.data_dir / "backups"
