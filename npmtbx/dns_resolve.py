# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import ipaddress
import socket


def is_literal_ip(host: str) -> bool:
    h = (host or "").strip()
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    try:
        ipaddress.ip_address(h)
        return True
    except ValueError:
        return False


def parse_dns_server_list(text: str | None) -> list[str]:
    out: list[str] = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ipaddress.ip_address(part)
        except ValueError as e:
            raise ValueError(f"Invalid DNS server IP: {part}") from e
        out.append(part)
    return out


def resolve_hostname(hostname: str, *, dns_servers: list[str] | None) -> str:
    """Return IPv4 string for hostname. IPs pass through. Uses custom resolvers when provided."""
    host = (hostname or "").strip()
    if not host:
        raise ValueError("Hostname is empty.")
    if is_literal_ip(host):
        return host
    if dns_servers:
        try:
            import dns.resolver
        except ImportError as e:
            raise RuntimeError("dnspython is required for custom DNS.") from e
        resolver = dns.resolver.Resolver(configure=False)
        resolver.nameservers = list(dns_servers)
        answers = resolver.resolve(host, "A")
        for rdata in answers:
            return str(rdata)
        raise OSError(f"No A record for {host} from DNS {dns_servers}")
    return socket.gethostbyname(host)
