# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import re
from urllib.parse import urlparse

PORT_PRESET_80 = "80"
PORT_PRESET_81 = "81"
PORT_PRESET_443 = "443"
PORT_PRESET_CUSTOM = "custom"

DEFAULT_PORT_PRESET = PORT_PRESET_80

_HOST_RE = re.compile(r"^[a-zA-Z0-9.\-:]+$")


def validate_host(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Enter the NPM host (IP address or hostname).")
    if "://" in raw or "/" in raw:
        raise ValueError("Enter only the IP or hostname, without http:// or https://.")
    if not _HOST_RE.match(raw):
        raise ValueError("Host contains invalid characters.")
    return raw


def resolve_port(preset: str, custom_port: str) -> int:
    preset = (preset or "").strip().lower()
    if preset == PORT_PRESET_80:
        return 80
    if preset == PORT_PRESET_81:
        return 81
    if preset == PORT_PRESET_443:
        return 443
    if preset == PORT_PRESET_CUSTOM:
        raw = (custom_port or "").strip()
        if not raw.isdigit():
            raise ValueError("Custom port must be a number between 1 and 65535.")
        port = int(raw)
        if port < 1 or port > 65535:
            raise ValueError("Custom port must be a number between 1 and 65535.")
        return port
    raise ValueError("Select a port (80, 81, 443, or Custom).")


def scheme_for_port(port: int) -> str:
    return "https" if port == 443 else "http"


def connection_target(admin_host: str, connect_host: str) -> str:
    reach = (connect_host or "").strip()
    if reach:
        return validate_host(reach)
    return validate_host(admin_host)


def build_api_url_from_form(
    host: str,
    port_preset: str,
    custom_port: str,
    *,
    connect_host: str = "",
) -> str:
    h = connection_target(host, connect_host)
    p = resolve_port(port_preset, custom_port)
    return f"{scheme_for_port(p)}://{h}:{p}"


def parse_api_url(url: str) -> tuple[str, str, str]:
    """Returns (host, port_preset, custom_port). custom_port set only when preset is custom."""
    raw = (url or "").strip()
    if not raw:
        return "", DEFAULT_PORT_PRESET, ""
    if "://" not in raw:
        raw = f"http://{raw}"
    parsed = urlparse(raw)
    host = (parsed.hostname or "").strip()
    port = parsed.port
    if port is None:
        port = 443 if parsed.scheme == "https" else 80
    if port == 80:
        return host, PORT_PRESET_80, ""
    if port == 81:
        return host, PORT_PRESET_81, ""
    if port == 443:
        return host, PORT_PRESET_443, ""
    return host, PORT_PRESET_CUSTOM, str(port)
