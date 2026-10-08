# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import ipaddress
import json
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


def _validate_override_hostname(text: str) -> str:
    raw = (text or "").strip()
    if not raw:
        raise ValueError("Host / domain name is required.")
    if "://" in raw or "/" in raw:
        raise ValueError(f"Enter only the hostname, not a URL: {raw}")
    if not re.match(r"^[a-zA-Z0-9.\-:]+$", raw):
        raise ValueError(f"Invalid hostname: {raw}")
    if is_literal_ip(raw):
        raise ValueError("Host / domain must be the NPM name, not an IP address.")
    return raw


def host_override_rows_from_form(
    hosts: list[str] | None, targets: list[str] | None
) -> list[tuple[str, str]]:
    hs = hosts or []
    ts = targets or []
    n = max(len(hs), len(ts))
    rows: list[tuple[str, str]] = []
    for i in range(n):
        h = (hs[i] if i < len(hs) else "").strip()
        t = (ts[i] if i < len(ts) else "").strip()
        if not h and not t:
            continue
        rows.append((_validate_override_hostname(h), _valid_connect_target(t)))
    return rows


def host_override_map_from_rows(rows: list[tuple[str, str]]) -> dict[str, str]:
    out: dict[str, str] = {}
    for host, target in rows:
        key = host.lower()
        if key in out and out[key] != target:
            raise ValueError(f"Duplicate host override for {host}.")
        out[key] = target
    return out


def host_override_rows_from_storage(text: str | None) -> list[dict[str, str]]:
    raw = (text or "").strip()
    if not raw:
        return [{"host": "", "target": ""}]
    if raw.startswith("["):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError("Stored host overrides are invalid.") from e
        if not isinstance(data, list):
            raise ValueError("Stored host overrides are invalid.")
        rows: list[dict[str, str]] = []
        for item in data:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "host": str(item.get("host") or "").strip(),
                    "target": str(item.get("target") or "").strip(),
                }
            )
        return rows or [{"host": "", "target": ""}]
    pairs = _parse_host_override_lines(raw)
    if not pairs:
        return [{"host": "", "target": ""}]
    return [{"host": h, "target": t} for h, t in pairs]


def _parse_host_override_lines(text: str) -> list[tuple[str, str]]:
    """Legacy line format: ``hostname target`` (space, tab, or =)."""
    rows: list[tuple[str, str]] = []
    for line in text.splitlines():
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
        host = _validate_override_hostname(name_part)
        target = _valid_connect_target(target_part)
        rows.append((host, target))
    return rows


def host_override_map_from_storage(text: str | None) -> dict[str, str]:
    raw = (text or "").strip()
    if not raw:
        return {}
    if raw.startswith("["):
        rows = host_override_rows_from_storage(raw)
    else:
        rows = [{"host": h, "target": t} for h, t in _parse_host_override_lines(raw)]
    pairs = [(r["host"], r["target"]) for r in rows if r.get("host") and r.get("target")]
    return host_override_map_from_rows(pairs)


def serialize_host_override_rows(rows: list[tuple[str, str]]) -> str:
    payload = [{"host": h, "target": t} for h, t in rows]
    return json.dumps(payload, ensure_ascii=False)


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
