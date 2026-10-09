# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
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
    ("access-lists", "/api/nginx/access-lists"),
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

_PROTECTED_USER_IDS = frozenset({1})

# NPM OpenAPI POST /api/nginx/certificates — certificate-object.json#/properties/meta
_CERT_META_CREATE_KEYS = frozenset(
    {
        "certificate",
        "certificate_key",
        "dns_challenge",
        "dns_provider",
        "dns_provider_credentials",
        "letsencrypt_agree",
        "letsencrypt_email",
        "propagation_seconds",
    }
)


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


def _export_section(export: dict[str, Any], key: str) -> Any | None:
    """Return export payload for key, or None if missing / failed on source."""
    raw = export.get(key)
    if raw is None:
        return None
    if isinstance(raw, dict) and "_error" in raw:
        return None
    return raw


def _delete_all(client: NpmClient, path: str) -> None:
    items = _as_list(client._get_json(path))
    for item in items:
        rid = item.get("id")
        if rid is None:
            continue
        if path.rstrip("/").endswith("/users") and int(rid) in _PROTECTED_USER_IDS:
            continue
        client._delete_json(f"{path.rstrip('/')}/{rid}")


def _sync_users(client: NpmClient, items: list[dict[str, Any]]) -> tuple[int, int]:
    """Upsert users by email. NPM forbids deleting the primary admin (id 1)."""
    existing = _as_list(client._get_json("/api/users"))
    by_email: dict[str, dict[str, Any]] = {}
    for row in existing:
        email = str(row.get("email") or "").strip().lower()
        if email:
            by_email[email] = row
    updated = 0
    created = 0
    for item in items:
        payload = _clean_payload(deepcopy(item))
        email = str(payload.get("email") or "").strip().lower()
        if not email:
            continue
        payload.pop("password", None)
        payload.pop("secret", None)
        match = by_email.get(email)
        if match and match.get("id") is not None:
            rid = int(match["id"])
            if rid in _PROTECTED_USER_IDS:
                continue
            try:
                client._put_json(f"/api/users/{rid}", payload)
                updated += 1
            except NpmError:
                continue
        else:
            try:
                client._post_json("/api/users", payload)
                created += 1
            except NpmError:
                continue
    return updated, created


def _certificate_meta_for_create(meta: Any) -> dict[str, Any]:
    if not isinstance(meta, dict):
        return {}
    out: dict[str, Any] = {}
    for key, val in meta.items():
        if key not in _CERT_META_CREATE_KEYS or val is None:
            continue
        if key in ("certificate", "certificate_key") and not str(val).strip():
            continue
        out[key] = val
    return out


def _normalize_domain_names(raw: Any) -> list[str]:
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
                return _normalize_domain_names(parsed)
        return [part.strip() for part in text.split(",") if part.strip()]
    if isinstance(raw, list):
        out: list[str] = []
        for entry in raw:
            text = str(entry).strip()
            if text:
                out.append(text)
        return out
    return []


def _npm_provider_from_cert_export(item: dict[str, Any]) -> str | None:
    """Map export row to NPM ssl_provider: letsencrypt | other (OpenAPI pattern)."""
    raw = item.get("provider")
    if isinstance(raw, str) and raw.strip():
        provider = raw.strip().lower()
        if provider in ("letsencrypt", "other"):
            return provider
    cert_type = str(item.get("type") or "").strip().lower()
    if cert_type in ("custom", "mkcert"):
        return "other"
    if cert_type in ("http", "dns"):
        return "letsencrypt"
    meta = item.get("meta")
    if isinstance(meta, dict) and meta.get("dns_challenge") is True:
        return "letsencrypt"
    if isinstance(meta, dict) and (
        meta.get("letsencrypt_email") or meta.get("letsencrypt_agree") is not None
    ):
        return "letsencrypt"
    if isinstance(meta, dict) and (
        meta.get("certificate") or meta.get("certificate_key")
    ):
        return "other"
    return None


def _certificate_meta_enrich_from_item(
    item: dict[str, Any], meta: dict[str, Any]
) -> dict[str, Any]:
    dns = item.get("dns_provider")
    if isinstance(dns, dict):
        name = dns.get("name") or dns.get("provider")
        if name and "dns_provider" not in meta:
            meta = {**meta, "dns_provider": str(name)}
        creds = dns.get("credentials")
        if creds is not None and "dns_provider_credentials" not in meta:
            if isinstance(creds, str):
                meta = {**meta, "dns_provider_credentials": creds}
            else:
                meta = {**meta, "dns_provider_credentials": json.dumps(creds)}
    cert_type = str(item.get("type") or "").strip().lower()
    if cert_type == "dns" and "dns_challenge" not in meta:
        meta = {**meta, "dns_challenge": True}
    elif cert_type == "http" and "dns_challenge" not in meta:
        meta = {**meta, "dns_challenge": False}
    return meta


def _npm_openapi_certificate_payload(item: dict[str, Any]) -> dict[str, Any]:
    """
    POST /api/nginx/certificates (OpenAPI): only provider, nice_name, domain_names, meta.
    See backend/schema/paths/nginx/certificates/post.json
    """
    provider = _npm_provider_from_cert_export(item)
    if not provider:
        label = item.get("nice_name") or item.get("name") or item.get("id") or "?"
        raise NpmError(
            f"Certificate {label}: cannot map to NPM provider (letsencrypt or other)."
        )
    payload: dict[str, Any] = {"provider": provider}
    nice_name = item.get("nice_name") or item.get("name")
    if nice_name:
        payload["nice_name"] = str(nice_name).strip()
    domains = _normalize_domain_names(item.get("domain_names"))
    if domains:
        payload["domain_names"] = domains
    meta = _certificate_meta_for_create(item.get("meta"))
    meta = _certificate_meta_enrich_from_item(item, meta)
    if meta:
        payload["meta"] = meta
    return payload


def _create_certificates(client: NpmClient, items: list[dict[str, Any]]) -> None:
    for item in items:
        payload = _npm_openapi_certificate_payload(item)
        client._post_json("/api/nginx/certificates", payload)


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
        if key == "users":
            continue
        path = path_by_key.get(key)
        if not path:
            continue
        if _export_section(export, key) is None:
            continue
        _delete_all(target, path)
        lines.append(f"Cleared target {key}")

    for key in _CREATE_ORDER:
        path = path_by_key.get(key)
        if not path:
            continue
        section = _export_section(export, key)
        if section is None:
            continue
        items = _as_list(section)
        if not items:
            continue
        if key == "users":
            upd, new = _sync_users(target, items)
            lines.append(f"Synced users on target ({upd} updated, {new} created)")
            continue
        if key == "certificates":
            _create_certificates(target, items)
        else:
            _create_all(target, path, items)
        lines.append(f"Applied {len(items)} {key} to target")

    if "settings" in export:
        _sync_settings(target, export["settings"])
        lines.append("Updated settings on target")

    return lines
