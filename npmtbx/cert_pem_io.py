# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path
from typing import Any


def _read_pem_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace").strip()
    if "BEGIN" in text:
        return text
    return None


def _pem_pair(chain: str | None, key: str | None) -> tuple[str, str] | None:
    if chain and key:
        return chain, key
    return None


def pem_from_meta(meta: Any) -> tuple[str, str] | None:
    if not isinstance(meta, dict):
        return None
    cert = meta.get("certificate")
    key = meta.get("certificate_key")
    if cert is True or key is True:
        return None
    if not cert or not key:
        return None
    cert_s = str(cert).strip()
    key_s = str(key).strip()
    if "BEGIN CERTIFICATE" in cert_s and "BEGIN" in key_s:
        return cert_s, key_s
    return None


def pem_from_npm_data_paths(
    *,
    cert_id: int,
    data_path: str,
    letsencrypt_path: str,
) -> tuple[str, str] | None:
    """Read issued cert material from NPM host bind mounts (same paths as backup volumes)."""
    cid = int(cert_id)

    le_root = Path(letsencrypt_path).expanduser() if letsencrypt_path.strip() else None
    if le_root and le_root.is_dir():
        live = le_root / "live" / f"npm-{cid}"
        pair = _pem_pair(
            _read_pem_file(live / "fullchain.pem"),
            _read_pem_file(live / "privkey.pem"),
        )
        if pair:
            return pair

    data_root = Path(data_path).expanduser() if data_path.strip() else None
    if data_root and data_root.is_dir():
        cert_dir = data_root / "certificates"
        if cert_dir.is_dir():
            prefix = f"{cid}-"
            for folder in cert_dir.iterdir():
                if not folder.is_dir() or not folder.name.startswith(prefix):
                    continue
                chain = _read_pem_file(folder / "fullchain.pem")
                key = _read_pem_file(folder / "privkey.pem") or _read_pem_file(
                    folder / "key.pem"
                )
                pair = _pem_pair(chain, key)
                if pair:
                    return pair

    return None


def load_certificate_pem(
    item: dict[str, Any],
    *,
    data_path: str,
    letsencrypt_path: str,
) -> tuple[str, str] | None:
    from_meta = pem_from_meta(item.get("meta"))
    if from_meta:
        return from_meta
    cid = item.get("id")
    if cid is None:
        return None
    try:
        cert_id = int(cid)
    except (TypeError, ValueError):
        return None
    return pem_from_npm_data_paths(
        cert_id=cert_id,
        data_path=data_path,
        letsencrypt_path=letsencrypt_path,
    )
