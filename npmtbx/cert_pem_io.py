# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from pathlib import Path
from typing import Any  # container handle from docker SDK

from .client import CertificateUploadMaterial


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


def _read_pem_from_container(container: Any, path: str) -> str | None:
    try:
        result = container.exec_run(["cat", path])
    except Exception:
        return None
    if getattr(result, "exit_code", 1) != 0:
        return None
    raw = getattr(result, "output", b"") or b""
    text = raw.decode("utf-8", errors="replace").strip()
    return text if "BEGIN" in text else None


def pem_from_docker_container(container_id: str, cert_id: int) -> tuple[str, str] | None:
    """Read cert material from a running NPM container (requires Docker socket in ToolBox)."""
    cid = (container_id or "").strip()
    if not cid:
        return None
    try:
        from .docker_client import get_docker_client

        container = get_docker_client().containers.get(cid)
    except Exception:
        return None
    cert_num = int(cert_id)
    live = f"/etc/letsencrypt/live/npm-{cert_num}"
    pair = _pem_pair(
        _read_pem_from_container(container, f"{live}/fullchain.pem"),
        _read_pem_from_container(container, f"{live}/privkey.pem"),
    )
    if pair:
        return pair
    custom = f"/data/custom_ssl/npm-{cert_num}"
    pair = _pem_pair(
        _read_pem_from_container(container, f"{custom}/fullchain.pem"),
        _read_pem_from_container(container, f"{custom}/privkey.pem"),
    )
    if pair:
        return pair
    try:
        listed = container.exec_run(
            [
                "sh",
                "-c",
                f"ls -d /data/certificates/{cert_num}-* 2>/dev/null | head -n 1",
            ]
        )
        if listed.exit_code != 0:
            return None
        folder = (listed.output or b"").decode("utf-8", errors="replace").strip()
        if not folder:
            return None
        return _pem_pair(
            _read_pem_from_container(container, f"{folder}/fullchain.pem"),
            _read_pem_from_container(container, f"{folder}/privkey.pem")
            or _read_pem_from_container(container, f"{folder}/key.pem"),
        )
    except Exception:
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


def export_is_letsencrypt(item: dict[str, Any]) -> bool:
    if item.get("provider") == "letsencrypt":
        return True
    return str(item.get("type") or "").strip().lower() in ("http", "dns")


def _pair_as_material(pair: tuple[str, str]) -> CertificateUploadMaterial:
    return CertificateUploadMaterial(certificate_pem=pair[0], key_pem=pair[1])


def load_certificate_material(
    item: dict[str, Any],
    *,
    data_path: str,
    letsencrypt_path: str,
    docker_container_id: str = "",
    source_client: Any | None = None,
) -> tuple[CertificateUploadMaterial | None, str, str]:
    """
    Returns (material, method_label, source_download_error).
    """
    api_error = ""
    from_meta = pem_from_meta(item.get("meta"))
    if from_meta:
        return _pair_as_material(from_meta), "export meta", ""
    cid = item.get("id")
    if cid is None:
        return None, "", ""
    try:
        cert_id = int(cid)
    except (TypeError, ValueError):
        return None, "", ""
    if source_client is not None:
        from_api, err = source_client.download_certificate_materials(cert_id)
        if from_api:
            return from_api, "Source NPM download API", ""
        if err:
            api_error = err
    from_paths = pem_from_npm_data_paths(
        cert_id=cert_id,
        data_path=data_path,
        letsencrypt_path=letsencrypt_path,
    )
    if from_paths:
        return _pair_as_material(from_paths), "Source volume paths", api_error
    from_docker = pem_from_docker_container(docker_container_id, cert_id)
    if from_docker:
        return _pair_as_material(from_docker), "Source Docker container", api_error
    return None, "", api_error


def pem_lookup_hint(
    *,
    data_path: str,
    letsencrypt_path: str,
    docker_container_id: str,
) -> str:
    parts: list[str] = []
    if data_path.strip():
        parts.append(f"data path {data_path.strip()!r}")
    if letsencrypt_path.strip():
        parts.append(f"LE path {letsencrypt_path.strip()!r}")
    if docker_container_id.strip():
        parts.append(f"Docker container {docker_container_id.strip()!r}")
    if not parts:
        return (
            "configure Source data + Let's Encrypt paths, or use Docker detect on "
            "Source (ToolBox needs /var/run/docker.sock to read certs from the NPM container)"
        )
    return "checked " + ", ".join(parts)
