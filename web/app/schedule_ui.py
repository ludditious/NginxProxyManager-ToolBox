# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

INTERVAL_CHOICES = (
    (60, "Every hour"),
    (360, "Every 6 hours"),
    (720, "Every 12 hours"),
    (1440, "Every 24 hours"),
    (10080, "Weekly"),
    (20160, "Bi-weekly"),
    (43200, "Monthly (~30 days)"),
)

DR_PUSH_INTERVAL_CHOICES = (
    (1440, "Daily"),
    (10080, "Weekly"),
    (20160, "Bi-weekly"),
    (43200, "Monthly (~30 days)"),
)


def minutes_from_form(value: str | None) -> int:
    raw = (value or "").strip()
    for minutes, _label in (*INTERVAL_CHOICES, *DR_PUSH_INTERVAL_CHOICES):
        if str(minutes) == raw:
            return minutes
    return 1440


def parts_from_minutes(minutes: int) -> tuple[int, str]:
    for m, label in INTERVAL_CHOICES:
        if m == minutes:
            return m, label
    return minutes, f"Every {minutes} minutes"
