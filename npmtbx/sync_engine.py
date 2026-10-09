# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .client import NpmClient, NpmError

_READONLY_KEYS = frozenset(
    {"id", "created_on", "modified_on", "meta", "owner", "owner_id", "is_deleted"}
)

# Delete dependents first; create foundations first.
_LIST_RESOURCES: tuple[tuple[str, str], ...] = (
    ("proxy-hosts", "/api/nginx/proxy-hosts"),
    ("redirection-hosts", "/api/nginx/redirection-hosts"),
    ("dead-hosts", "/api/nginx/dead-hosts"),
    ("streams", "/api/nginx/streams"),
    ("access-lists", "/api/access-lists"),
    ("certificates", "/api/nginx/certificates"),
    ("users", "/api/users"),
)

_CREATE_ORDER = (
    "access-lists",
    "certificates",
    "users",
    "proxy-hosts",
    "redirection-hosts",
    "dead-hosts",
    "streams",
)

_DELETE_ORDER = tuple(reversed(_CREATE_ORDER))


def _clean_payload(obj: Any) -> Any:
    if isinstance(obj, dict):
        out = {}
        for k, v in obj.items():
            if k in _READONLY_KEYS:
                continue
            out[k] = _clean_payload(v)
        return out
    if isinstance(obj, list):
        return [_clean_payload(x) for x in obj]
    return obj


def _as_list(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        if "_error" in data:
            raise NpmError(str(data["_error"]))
        if "id" in data:
            return [data]
    return []


def _delete_all(client: NpmClient, path: str) -> None:
    items = _as_list(client._get_json(path))
    for item in items:
        rid = item.get("id")
        if rid is not None:
            client._delete_json(f"{path.rstrip('/')}/{rid}")


def _create_all(client: NpmClient, path: str, items: list[dict[str, Any]]) -> None:
    for item in items:
        payload = _clean_payload(deepcopy(item))
        if payload:
            client._post_json(path, payload)


def _sync_settings(client: NpmClient, settings: Any) -> None:
    if isinstance(settings, dict) and "_error" in settings:
        raise NpmError(str(settings["_error"]))
    if not isinstance(settings, dict):
        return
    payload = _clean_payload(deepcopy(settings))
    if payload:
        client._put_json("/api/settings", payload)


def apply_export_to_target(export: dict[str, Any], target: NpmClient) -> list[str]:
    """Replace target NPM API objects with data from a source export dict."""
    lines: list[str] = []
    path_by_key = dict(_LIST_RESOURCES)

    for key in _DELETE_ORDER:
        path = path_by_key.get(key)
        if not path:
            continue
        data = export.get(key)
        if data is None:
            continue
        _delete_all(target, path)
        lines.append(f"Cleared target {key}")

    for key in _CREATE_ORDER:
        path = path_by_key.get(key)
        if not path:
            continue
        items = _as_list(export.get(key))
        if not items:
            continue
        _create_all(target, path, items)
        lines.append(f"Applied {len(items)} {key} to target")

    if "settings" in export:
        _sync_settings(target, export["settings"])
        lines.append("Updated settings on target")

    return lines
