# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from copy import deepcopy
from typing import Any

from .cert_pem_io import export_is_letsencrypt, load_certificate_material, pem_lookup_hint
from .client import NpmClient, NpmError
from .npm_create_payloads import (
    build_create_payload,
    normalize_domain_names as _normalize_domain_names,
    omitted_export_fields,
)

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


def _cert_domain_key(item: dict[str, Any]) -> tuple[str, ...]:
    domains = _normalize_domain_names(item.get("domain_names"))
    if domains:
        return tuple(sorted(domains))
    label = str(item.get("nice_name") or item.get("name") or "").strip().lower()
    return (label,) if label else ()


def _meta_text(value: Any) -> str | None:
    if value is True or value is False or value is None:
        return None
    text = str(value).strip()
    return text if text else None


def _letsencrypt_create_allowed(meta: dict[str, Any]) -> tuple[bool, str]:
    """
    NPM POST for letsencrypt runs ACME immediately; DNS challenge needs credentials
    on disk — the API never exports dns_provider_credentials (often redacted to true).
    """
    if meta.get("dns_challenge"):
        if not _meta_text(meta.get("dns_provider")):
            return False, "DNS provider name missing (NPM API export)"
        creds = meta.get("dns_provider_credentials")
        if creds is True or not _meta_text(creds):
            return (
                False,
                "DNS credentials are not available from the NPM API — recreate this "
                "certificate on the target or copy /etc/letsencrypt via Snapshots",
            )
    if not _meta_text(meta.get("letsencrypt_email")):
        return False, "Let's Encrypt contact email missing from certificate export"
    return True, ""


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


def _remap_access_list_id(
    payload: dict[str, Any], access_list_id_map: dict[int, int]
) -> None:
    aid = payload.get("access_list_id")
    if aid in (None, "", 0, "0"):
        return
    try:
        old_id = int(aid)
    except (TypeError, ValueError):
        return
    if old_id <= 0:
        return
    new_id = access_list_id_map.get(old_id)
    payload["access_list_id"] = new_id if new_id is not None else 0


def _remap_certificate_id(
    payload: dict[str, Any], certificate_id_map: dict[int, int]
) -> None:
    cid = payload.get("certificate_id")
    if cid in (None, "", 0, "0"):
        return
    try:
        old_id = int(cid)
    except (TypeError, ValueError):
        return
    if old_id <= 0:
        return
    new_id = certificate_id_map.get(old_id)
    payload["certificate_id"] = new_id if new_id is not None else 0


def _custom_certificate_create_payload(item: dict[str, Any]) -> dict[str, Any]:
    domains = _normalize_domain_names(item.get("domain_names"))
    nice = item.get("nice_name") or item.get("name") or (domains[0] if domains else "Imported certificate")
    payload: dict[str, Any] = {"provider": "other", "nice_name": str(nice).strip()}
    if domains:
        payload["domain_names"] = domains
    return payload


def _sync_certificates(
    client: NpmClient,
    items: list[dict[str, Any]],
    lines: list[str],
    *,
    source_data_path: str = "",
    source_letsencrypt_path: str = "",
    source_docker_container_id: str = "",
    source_client: NpmClient | None = None,
) -> dict[int, int]:
    """Create or preserve certificates; map source cert id → target cert id."""
    target_by_domains: dict[tuple[str, ...], dict[str, Any]] = {}
    for row in _as_list(client._get_json("/api/nginx/certificates")):
        key = _cert_domain_key(row)
        if key:
            target_by_domains[key] = row

    id_map: dict[int, int] = {}
    created = 0
    skipped = 0
    copied = 0
    paths_hint = False
    for item in items:
        source_id = item.get("id")
        try:
            source_id_int = int(source_id) if source_id is not None else None
        except (TypeError, ValueError):
            source_id_int = None
        domain_key = _cert_domain_key(item)
        label = item.get("nice_name") or item.get("name") or ",".join(domain_key) or "?"

        material, pem_via, download_err = load_certificate_material(
            item,
            data_path=source_data_path,
            letsencrypt_path=source_letsencrypt_path,
            docker_container_id=source_docker_container_id,
            source_client=source_client,
        )
        if download_err:
            lines.append(f"Certificate {label}: Source GET download — {download_err}")
        if material:
            existing = target_by_domains.get(domain_key) if domain_key else None
            if existing and existing.get("id") is not None:
                client._delete_json(f"/api/nginx/certificates/{int(existing['id'])}")
            created_row = client._post_json(
                "/api/nginx/certificates",
                _custom_certificate_create_payload(item),
            )
            if isinstance(created_row, dict) and created_row.get("id") is not None:
                target_id = int(created_row["id"])
                client.upload_certificate_pem(
                    target_id,
                    certificate_pem=material.certificate_pem,
                    key_pem=material.key_pem,
                    intermediate_pem=material.intermediate_pem,
                )
                try:
                    refreshed = client._get_json(f"/api/nginx/certificates/{target_id}")
                    if isinstance(refreshed, dict):
                        target_by_domains[domain_key] = refreshed
                        created_row = refreshed
                except NpmError:
                    pass
                if source_id_int is not None:
                    id_map[source_id_int] = target_id
                copied += 1
                lines.append(f"Installed SSL for {label} ({pem_via})")
            continue

        payload = _npm_openapi_certificate_payload(item)
        meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}

        if payload.get("provider") == "letsencrypt":
            ok, reason = _letsencrypt_create_allowed(meta)
            if not ok:
                skipped += 1
                if (
                    not source_data_path.strip()
                    and not source_letsencrypt_path.strip()
                    and not source_docker_container_id.strip()
                ):
                    paths_hint = True
                hint = pem_lookup_hint(
                    data_path=source_data_path,
                    letsencrypt_path=source_letsencrypt_path,
                    docker_container_id=source_docker_container_id,
                )
                lines.append(
                    f"Skipped certificate {label}: {reason} "
                    f"(no PEM found; {hint})"
                )
                existing = target_by_domains.get(domain_key) if domain_key else None
                if existing and existing.get("id") is not None and source_id_int:
                    id_map[source_id_int] = int(existing["id"])
                continue

        existing = target_by_domains.get(domain_key) if domain_key else None
        if existing and existing.get("id") is not None:
            client._delete_json(f"/api/nginx/certificates/{int(existing['id'])}")

        created_row = client._post_json("/api/nginx/certificates", payload)
        created += 1
        if isinstance(created_row, dict) and created_row.get("id") is not None:
            target_by_domains[domain_key] = created_row
            if source_id_int is not None:
                id_map[source_id_int] = int(created_row["id"])

    lines.append(
        f"Certificates on target: {copied} copied from files, {created} requested via "
        f"Let's Encrypt API, {skipped} skipped (see log)"
    )
    if paths_hint:
        lines.append(
            "Tip: on Source, set NPM data + Let's Encrypt folder paths (host bind mounts), "
            "or use Docker detect and save — ToolBox needs docker.sock to read certs from "
            "the NPM container when paths are not mounted into ToolBox."
        )
    return id_map


def _sync_access_lists(
    client: NpmClient, items: list[dict[str, Any]]
) -> dict[int, int]:
    id_map: dict[int, int] = {}
    path = "/api/nginx/access-lists"
    for item in items:
        source_id = item.get("id")
        payload = build_create_payload(path, item)
        try:
            created = client._post_json(path, payload)
        except NpmError as exc:
            omitted = omitted_export_fields(item, payload)
            hint = f" omitted from POST: {', '.join(omitted)}" if omitted else ""
            label = payload.get("name") or source_id or "?"
            raise NpmError(f"Access list {label}: {exc}{hint}") from exc
        if source_id is not None and isinstance(created, dict) and created.get("id"):
            id_map[int(source_id)] = int(created["id"])
    return id_map


def _create_all(
    client: NpmClient,
    path: str,
    items: list[dict[str, Any]],
    *,
    certificate_id_map: dict[int, int] | None = None,
    access_list_id_map: dict[int, int] | None = None,
) -> None:
    for item in items:
        payload = build_create_payload(path, item)
        if not payload:
            continue
        if certificate_id_map:
            _remap_certificate_id(payload, certificate_id_map)
        if access_list_id_map:
            _remap_access_list_id(payload, access_list_id_map)
        try:
            client._post_json(path, payload)
        except NpmError as exc:
            omitted = omitted_export_fields(item, payload)
            hint_parts = [f"POST fields: {', '.join(sorted(payload.keys()))}"]
            if omitted:
                hint_parts.append(f"omitted export fields: {', '.join(omitted)}")
            domains = payload.get("domain_names")
            if domains:
                hint_parts.append(f"domains: {domains!r}")
            raise NpmError(f"{exc} ({'; '.join(hint_parts)})") from exc


def _sync_settings(client: NpmClient, settings: Any) -> None:
    if isinstance(settings, dict) and "_error" in settings:
        raise NpmError(str(settings["_error"]))
    if not isinstance(settings, dict):
        return
    payload = _clean_payload(deepcopy(settings))
    if payload:
        client._put_json("/api/settings", payload)


def apply_export_to_target(
    export: dict[str, Any],
    target: NpmClient,
    *,
    source_data_path: str = "",
    source_letsencrypt_path: str = "",
    source_docker_container_id: str = "",
    source_client: NpmClient | None = None,
) -> list[str]:
    """Replace target NPM API objects with data from a source export dict."""
    lines: list[str] = []
    path_by_key = dict(_LIST_RESOURCES)

    certificate_id_map: dict[int, int] = {}
    access_list_id_map: dict[int, int] = {}

    for key in _DELETE_ORDER:
        if key in ("users", "certificates"):
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
            certificate_id_map = _sync_certificates(
                target,
                items,
                lines,
                source_data_path=source_data_path,
                source_letsencrypt_path=source_letsencrypt_path,
                source_docker_container_id=source_docker_container_id,
                source_client=source_client,
            )
            continue
        if key == "access-lists":
            access_list_id_map = _sync_access_lists(target, items)
            lines.append(f"Applied {len(items)} access-lists to target")
            continue
        _create_all(
            target,
            path,
            items,
            certificate_id_map=certificate_id_map,
            access_list_id_map=access_list_id_map,
        )
        lines.append(f"Applied {len(items)} {key} to target")

    if "settings" in export:
        _sync_settings(target, export["settings"])
        lines.append("Updated settings on target")

    return lines
