# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import ipaddress
import re
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


def _valid_connect_target(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Connect address is empty.")
    if is_literal_ip(raw):
        return raw
    if "://" in raw or "/" in raw:
        raise ValueError(f"Connect address must be an IP or hostname, not a URL: {raw}")
    if not re.match(r"^[a-zA-Z0-9.\-:]+$", raw):
        raise ValueError(f"Invalid connect address: {raw}")
    return raw


def parse_host_override_map(text: str | None) -> dict[str, str]:
    """Parse lines: ``admin-host connect-target`` (space, tab, or =). Keys are lowercased."""
    out: dict[str, str] = {}
    for line in (text or "").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" in line:
            name_part, target_part = line.split("=", 1)
        else:
            parts = re.split(r"[\s\t]+", line, maxsplit=1)
            if len(parts) != 2:
                raise ValueError(f"Invalid host override line (need hostname and address): {line}")
            name_part, target_part = parts
        name = name_part.strip().lower()
        target = _valid_connect_target(target_part)
        if not name or "://" in name:
            raise ValueError(f"Invalid hostname in override: {parts[0]}")
        if is_literal_ip(name):
            raise ValueError(
                f"Override left side must be the NPM hostname, not an IP: {name_part.strip()}"
            )
        out[name] = target
    return out


def format_host_override_map(mapping: dict[str, str]) -> str:
    lines = [f"{host} {target}" for host, target in sorted(mapping.items())]
    return "\n".join(lines)


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
