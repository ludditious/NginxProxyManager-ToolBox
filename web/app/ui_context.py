# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from typing import Any

from .models import ToolBoxUiSettings, User

ROLE_PRIMARY = "primary"
ROLE_BACKUP_ENDPOINT = "backup_endpoint"
ROLE_DR_SITE = "dr_site"


def ui_settings_for(user: User) -> ToolBoxUiSettings:
    row = user.ui_settings
    assert row is not None
    return row


def nav_items(user: User) -> list[dict[str, Any]]:
    ui = ui_settings_for(user)
    role = ui.toolbox_role or ROLE_PRIMARY
    items: list[dict[str, Any]] = [{"href": "/dashboard", "label": "Dashboard", "key": "dashboard"}]

    if role == ROLE_BACKUP_ENDPOINT:
        items.append({"href": "/logs", "label": "Logs", "key": "logs"})
        items.extend(
            [
                {"href": "/settings", "label": "Settings", "key": "settings"},
                {"href": "/about", "label": "About", "key": "about"},
            ]
        )
        return items

    flags = (
        ("source", ui.nav_show_source, "/master", "Source"),
        ("snapshots", ui.nav_show_snapshots, "/snapshots", "Snapshots"),
        ("synchronize", ui.nav_show_synchronize, "/synchronize", "Synchronize"),
        ("backup_restore", ui.nav_show_backup_restore, "/backup-restore", "Backup / Restore"),
        ("dr_sync", ui.nav_show_dr_sync, "/dr-sync", "DR Sync"),
    )
    for key, show, href, label in flags:
        if show:
            items.append({"href": href, "label": label, "key": key})
    if ui.nav_show_logs:
        items.append({"href": "/logs", "label": "Logs", "key": "logs"})
    items.extend(
        [
            {"href": "/settings", "label": "Settings", "key": "settings"},
            {"href": "/about", "label": "About", "key": "about"},
        ]
    )
    return items


def template_nav_extras(user: User) -> dict[str, Any]:
    ui = ui_settings_for(user)
    return {
        "nav_items": nav_items(user),
        "toolbox_role": ui.toolbox_role or ROLE_PRIMARY,
        "is_backup_endpoint": (ui.toolbox_role or ROLE_PRIMARY) == ROLE_BACKUP_ENDPOINT,
    }
