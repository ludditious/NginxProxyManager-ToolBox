# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Build NPM POST bodies matching OpenAPI create schemas (no expanded GET fields)."""

from __future__ import annotations

import json
from typing import Any


def normalize_domain_names(raw: Any) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return []
        if text.startswith("["):
            try:
                parsed = json.loads(text)
            except json.JSONDecodeError:
                parsed = None
            if isinstance(parsed, list):
                return normalize_domain_names(parsed)
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(raw, list):
        out: list[str] = []
        for entry in raw:
            text = str(entry).strip()
            if text:
                out.append(text)
        return out
    return []

_PROXY_HOST_KEYS = frozenset(
    {
        "domain_names",
        "forward_scheme",
        "forward_host",
        "forward_port",
        "certificate_id",
        "ssl_forced",
        "hsts_enabled",
        "hsts_subdomains",
        "http2_support",
        "block_exploits",
        "caching_enabled",
        "allow_websocket_upgrade",
        "access_list_id",
        "advanced_config",
        "enabled",
        "meta",
        "locations",
    }
)

_REDIRECTION_HOST_KEYS = frozenset(
    {
        "domain_names",
        "forward_http_code",
        "forward_scheme",
        "forward_domain_name",
        "preserve_path",
        "certificate_id",
        "ssl_forced",
        "hsts_enabled",
        "hsts_subdomains",
        "http2_support",
        "block_exploits",
        "advanced_config",
        "meta",
    }
)

_DEAD_HOST_KEYS = frozenset(
    {
        "domain_names",
        "certificate_id",
        "ssl_forced",
        "hsts_enabled",
        "hsts_subdomains",
        "http2_support",
        "advanced_config",
        "meta",
    }
)

_STREAM_KEYS = frozenset(
    {
        "incoming_port",
        "forwarding_host",
        "forwarding_port",
        "tcp_forwarding",
        "udp_forwarding",
        "certificate_id",
        "meta",
    }
)

_ACCESS_LIST_KEYS = frozenset(
    {"name", "satisfy_any", "pass_auth", "items", "clients", "meta"}
)

_LOCATION_KEYS = frozenset(
    {
        "path",
        "forward_scheme",
        "forward_host",
        "forward_port",
        "forward_path",
        "advanced_config",
    }
)

_ACCESS_CLIENT_KEYS = frozenset({"address", "directive"})
_ACCESS_ITEM_KEYS = frozenset({"username", "password"})

_CREATE_BY_PATH: dict[str, frozenset[str]] = {
    "/api/nginx/proxy-hosts": _PROXY_HOST_KEYS,
    "/api/nginx/redirection-hosts": _REDIRECTION_HOST_KEYS,
    "/api/nginx/dead-hosts": _DEAD_HOST_KEYS,
    "/api/nginx/streams": _STREAM_KEYS,
    "/api/nginx/access-lists": _ACCESS_LIST_KEYS,
}


def _pick(item: dict[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    return {key: item[key] for key in allowed if key in item}


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _sanitize_locations(locations: Any) -> list[dict[str, Any]]:
    if not isinstance(locations, list):
        return []
    rows: list[dict[str, Any]] = []
    for loc in locations:
        if not isinstance(loc, dict):
            continue
        row = {k: v for k, v in loc.items() if k in _LOCATION_KEYS}
        if row.get("path"):
            if "forward_port" in row:
                row["forward_port"] = _as_int(row["forward_port"], 80)
            rows.append(row)
    return rows


def _sanitize_access_clients(clients: Any) -> list[dict[str, Any]]:
    if not isinstance(clients, list):
        return []
    rows: list[dict[str, Any]] = []
    for client in clients:
        if not isinstance(client, dict):
            continue
        row = {k: client[k] for k in _ACCESS_CLIENT_KEYS if k in client}
        if row.get("address"):
            rows.append(row)
    return rows


def _sanitize_access_items(items: Any) -> list[dict[str, Any]]:
    if not isinstance(items, list):
        return []
    rows: list[dict[str, Any]] = []
    for entry in items:
        if not isinstance(entry, dict):
            continue
        row = {k: entry[k] for k in _ACCESS_ITEM_KEYS if k in entry}
        if row.get("username"):
            rows.append(row)
    return rows


def build_create_payload(path: str, item: dict[str, Any]) -> dict[str, Any]:
    """Return a POST body allowed by NPM's create schema for this path."""
    normalized = path.rstrip("/")
    allowed = _CREATE_BY_PATH.get(normalized)
    if not allowed:
        return dict(item)
    payload = _pick(item, allowed)
    if "domain_names" in allowed and "domain_names" in payload:
        payload["domain_names"] = normalize_domain_names(payload.get("domain_names"))
    if normalized == "/api/nginx/proxy-hosts":
        if "forward_port" in payload:
            payload["forward_port"] = _as_int(payload["forward_port"], 80)
        if "locations" in payload:
            payload["locations"] = _sanitize_locations(payload.get("locations"))
        if "certificate_id" in payload:
            payload["certificate_id"] = _as_int(payload.get("certificate_id"), 0)
        if "access_list_id" in payload:
            payload["access_list_id"] = _as_int(payload.get("access_list_id"), 0)
    elif normalized == "/api/nginx/redirection-hosts":
        if "forward_http_code" in payload:
            payload["forward_http_code"] = _as_int(payload.get("forward_http_code"), 301)
        if "certificate_id" in payload:
            payload["certificate_id"] = _as_int(payload.get("certificate_id"), 0)
    elif normalized == "/api/nginx/dead-hosts":
        if "certificate_id" in payload:
            payload["certificate_id"] = _as_int(payload.get("certificate_id"), 0)
    elif normalized == "/api/nginx/streams":
        if "incoming_port" in payload:
            payload["incoming_port"] = _as_int(payload.get("incoming_port"), 1)
        if "forwarding_port" in payload:
            payload["forwarding_port"] = _as_int(payload.get("forwarding_port"), 80)
        if "certificate_id" in payload:
            payload["certificate_id"] = _as_int(payload.get("certificate_id"), 0)
    elif normalized == "/api/nginx/access-lists":
        payload["clients"] = _sanitize_access_clients(payload.get("clients"))
        payload["items"] = _sanitize_access_items(payload.get("items"))
    return payload


def omitted_export_fields(item: dict[str, Any], payload: dict[str, Any]) -> list[str]:
    return sorted(set(item.keys()) - set(payload.keys()))
