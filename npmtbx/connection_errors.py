# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from urllib.parse import urlparse

import requests

from .client import NpmClient
from .dns_resolve import is_literal_ip


def friendly_connection_error(api_url: str, exc: Exception, *, verify_tls: bool) -> str:
    parsed = urlparse(NpmClient.normalize_api_url(api_url))
    host = parsed.hostname or ""
    text = str(exc).lower()
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    if "unrecognized_name" in text or "tlsv1_unrecognized_name" in text:
        return (
            "TLS failed because the certificate on that server does not match how you connected "
            f"(often HTTPS to an IP on port {port} while the cert is issued for a hostname). "
            "On Master use the NPM hostname; in Settings → Host overrides map that name to the target IP. "
            "The ToolBox sends that hostname for TLS and the Host header. "
            "If NPM admin is HTTP only, use port 80 instead of 443."
        )

    if is_literal_ip(host):
        lines = [
            f"Could not reach NPM at {host}:{port} from inside this ToolBox container.",
            "That is an IP address — this is reachability, not DNS.",
        ]
        if "connection refused" in text or "econnrefused" in text:
            lines.append("Nothing accepted a connection on that host and port.")
        elif "timed out" in text or "timeout" in text:
            lines.append("The connection timed out — check firewall and routing from Docker.")
        lines.append(
            "Try Connect via on Master, or Settings → Host overrides, so the container uses an address it can route to."
        )
        return " ".join(lines)

    if "failed to resolve" in text or "getaddrinfo failed" in text or "nameresolutionerror" in text:
        if not is_literal_ip(host):
            return (
                f"The ToolBox could not resolve “{host}”. "
                "Try Settings → DNS for a LAN resolver, or Settings → Host overrides to map the NPM hostname to a reachable IP."
            )

    if "connection refused" in text or "econnrefused" in text:
        return (
            f"Nothing on {parsed.scheme}://{host}:{port} accepted a connection from this container. "
            "Confirm host, port, and Connect via / host overrides match where NPM admin listens."
        )
    if "timed out" in text or "timeout" in text:
        return "Connection timed out from the container. Check firewall, routing, and that NPM is reachable from Docker."

    if isinstance(exc, requests.exceptions.SSLError) or "ssl" in text:
        lines = [
            "SSL/TLS failed for the host and port you selected.",
            "Use the same scheme and port as the NPM admin UI in your browser (HTTP on 80, HTTPS on 443).",
        ]
        if verify_tls:
            lines.append('Turn off “Verify TLS certificate” if the cert name does not match.')
        return " ".join(lines)

    return str(exc).strip() or "Connection to NPM failed."
