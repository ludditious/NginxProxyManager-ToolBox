# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations


def sni_hostname_from_host_header(http_host: str) -> str:
    return (http_host or "").split(":", 1)[0].strip()


def friendly_auth_failure(status_code: int, body: str) -> str:
    text = (body or "").lower()
    if status_code in (401, 403):
        return (
            "NPM rejected the login (wrong email or password). "
            "Use the same identity and password as the NPM admin UI."
        )
    if status_code == 500 or "internal error" in text:
        return (
            "NPM responded with an internal error during login (HTTP 500). "
            "The ToolBox reached something on that host and port, but NPM did not handle /api/tokens correctly. "
            "Common causes: port 443 goes to a reverse proxy or public site instead of the NPM admin API; "
            "wrong HTTP vs HTTPS (try port 80 if admin is plain HTTP); Host header or TLS name does not match what NPM expects. "
            "Match the host, port, and http/https you use to open NPM in a browser. "
            "With Settings → Host overrides, put the NPM hostname on Master and the target IP you can reach from Docker."
        )
    if status_code == 404:
        return (
            "No NPM login API at /api/tokens on that host and port. "
            "You may be pointing at a proxied website, not the NPM admin service."
        )
    snippet = (body or "").strip().replace("\n", " ")[:120]
    return f"NPM login failed (HTTP {status_code}). {snippet}".strip()
