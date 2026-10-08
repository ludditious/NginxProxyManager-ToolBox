# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

import requests
from npmtbx.client import NpmClient, NpmError

from .config import get_settings
from .crypto import decrypt, encrypt
from .models import MasterInstance, SlaveInstance


@dataclass(frozen=True)
class TestResult:
    ok: bool
    title: str
    message: str


def _is_dns_failure(exc: BaseException) -> bool:
    text = str(exc).lower()
    if "failed to resolve" in text or "no address associated with hostname" in text:
        return True
    if "nameresolutionerror" in text or "getaddrinfo failed" in text:
        return True
    cause = getattr(exc, "__cause__", None)
    if cause is not None and cause is not exc:
        return _is_dns_failure(cause)
    return False


def _connection_error_message(api_url: str, exc: Exception, *, verify_tls: bool) -> str:
    parsed = urlparse(NpmClient.normalize_api_url(api_url))
    host = parsed.hostname or ""
    lines: list[str] = []

    if _is_dns_failure(exc):
        lines.append(f"The ToolBox container could not resolve the hostname “{host}”.")
        lines.append(
            "Your PC may know that name (DNS, Pi-hole, or hosts file) but the container often does not."
        )
        lines.append(
            "Use the same IP address you use for NPM on the LAN (for example 192.168.x.x) in the host field, "
            "or set CUSTOM_DNS on the container to your LAN DNS resolver."
        )
        lines.append(
            "If this name is your public proxy site (not the admin UI), use the admin host/IP instead — "
            "the API path /api/tokens must be reachable on the host and port you select."
        )
        return " ".join(lines)

    lines.append(str(exc).strip())
    if isinstance(exc, requests.exceptions.SSLError) or "SSL" in str(exc):
        lines.append(
            "SSL/TLS failed for the host and port you selected. "
            "Check NPM host and port match the admin UI you use in a browser."
        )
        if parsed.scheme == "https" and verify_tls:
            lines.append('Turn off “Verify TLS certificate” if the cert does not match this host.')
        elif parsed.scheme == "https":
            lines.append("Port 443 uses HTTPS; port 80 uses HTTP. Pick the port where NPM admin actually listens.")
    return " ".join(lines)


def resolve_secret(password_enc: str, form_secret: str | None) -> tuple[str | None, str | None]:
    sk = get_settings().secret_key
    if form_secret and form_secret.strip():
        return form_secret.strip(), None
    if password_enc:
        plain = decrypt(sk, password_enc)
        if plain:
            return plain, None
        return None, "Stored password could not be decrypted."
    return None, "Password is required."


def store_secret(form_secret: str | None, existing_enc: str) -> tuple[str, str]:
    """Returns (password_enc, status: unchanged|updated|missing)."""
    sk = get_settings().secret_key
    if form_secret and form_secret.strip():
        return encrypt(sk, form_secret.strip()), "updated"
    if existing_enc:
        return existing_enc, "unchanged"
    return "", "missing"


def npm_client_from_master(master: MasterInstance, *, secret: str | None = None) -> NpmClient:
    pw, err = resolve_secret(master.password_enc, secret)
    if err or not pw:
        raise ValueError(err or "Master password missing.")
    if not master.api_url.strip():
        raise ValueError("Master API URL is not configured.")
    client = NpmClient(
        master.api_url,
        identity=master.identity,
        secret=pw,
        verify_tls=master.verify_tls,
    )
    client.login()
    return client


def test_npm_connection(
    *,
    api_url: str,
    identity: str,
    password_enc: str,
    form_secret: str | None,
    verify_tls: bool,
) -> TestResult:
    pw, err = resolve_secret(password_enc, form_secret)
    if err or not pw:
        return TestResult(False, "Connection test", err or "Password missing.")
    url = NpmClient.normalize_api_url(api_url)
    if not url:
        return TestResult(False, "Connection test", "API URL is required.")
    try:
        client = NpmClient(url, identity=identity, secret=pw, verify_tls=verify_tls)
        client.login()
        client.export_configuration()
    except NpmError as e:
        return TestResult(False, "Connection test", str(e))
    except (requests.exceptions.SSLError, requests.exceptions.ConnectionError) as e:
        return TestResult(
            False,
            "Connection test",
            _connection_error_message(url, e, verify_tls=verify_tls),
        )
    except Exception as e:
        return TestResult(
            False,
            "Connection test",
            _connection_error_message(url, e, verify_tls=verify_tls),
        )
    return TestResult(True, "Connection test", f"Authenticated to {url}")


def apply_candidate_to_master(master: MasterInstance, candidate) -> None:
    from .npm_address import PORT_PRESET_CUSTOM, build_api_url_from_form

    master.docker_container_id = candidate.container_id
    master.docker_image = candidate.image
    if candidate.data_path:
        master.data_path = candidate.data_path
    if candidate.letsencrypt_path:
        master.letsencrypt_path = candidate.letsencrypt_path
    if candidate.admin_port and not master.api_url.strip():
        port = (candidate.admin_port or "").strip()
        host = "127.0.0.1"
        if port in ("80", "443"):
            master.api_url = build_api_url_from_form(host, port, "")
        elif port.isdigit():
            master.api_url = build_api_url_from_form(host, PORT_PRESET_CUSTOM, port)


def apply_candidate_to_slave(slave: SlaveInstance, candidate) -> None:
    slave.docker_container_id = candidate.container_id
    slave.docker_image = candidate.image
    if candidate.data_path:
        slave.data_path = candidate.data_path
    if candidate.letsencrypt_path:
        slave.letsencrypt_path = candidate.letsencrypt_path
    if candidate.admin_port and not slave.api_url.strip():
        from .npm_address import PORT_PRESET_CUSTOM, build_api_url_from_form

        port = (candidate.admin_port or "").strip()
        host = "127.0.0.1"
        if port in ("80", "443"):
            slave.api_url = build_api_url_from_form(host, port, "")
        elif port.isdigit():
            slave.api_url = build_api_url_from_form(host, PORT_PRESET_CUSTOM, port)
