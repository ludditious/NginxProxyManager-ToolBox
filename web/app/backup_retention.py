# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

BACKUP_RETENTION_OPTIONS: tuple[tuple[str, int, str], ...] = (
    ("1", 1, "1 Day"),
    ("7", 7, "1 Week"),
    ("14", 14, "2 Weeks"),
    ("30", 30, "30 Days"),
    ("60", 60, "60 Days"),
    ("90", 90, "90 Days"),
    ("120", 120, "120 Days"),
)

DEFAULT_RETENTION_DAYS = 30
BACKUPS_PER_PAGE = 10
AUTO_BACKUP_INTERVAL_MINUTES = 24 * 60


def retention_days_from_form(value: str | None) -> int:
    raw = (value or "").strip()
    for form_val, days, _label in BACKUP_RETENTION_OPTIONS:
        if raw == form_val:
            return days
    return DEFAULT_RETENTION_DAYS


def retention_label(days: int) -> str:
    for _form_val, opt_days, label in BACKUP_RETENTION_OPTIONS:
        if opt_days == days:
            return label
    return f"{days} Days"
