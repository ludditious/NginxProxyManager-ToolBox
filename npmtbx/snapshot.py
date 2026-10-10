# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
import os
import shutil
import tarfile
import tempfile
import uuid
import zipfile
from pathlib import Path
from typing import Any

from .backup_format import build_manifest


def _add_tree_to_tar(tar: tarfile.TarFile, source: Path, arcname: str) -> None:
    if not source.exists():
        return
    if source.is_file():
        tar.add(source, arcname=arcname)
        return
    for root, _dirs, files in os.walk(source):
        root_path = Path(root)
        for name in files:
            full = root_path / name
            rel = full.relative_to(source)
            tar.add(full, arcname=str(Path(arcname) / rel).replace("\\", "/"))


def _archive_directory(source: Path, dest_tar_gz: Path) -> None:
    dest_tar_gz.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(dest_tar_gz, "w:gz") as tar:
        if source.is_dir():
            tar.add(source, arcname=".")
        elif source.is_file():
            tar.add(source, arcname=source.name)


def create_snapshot_zip(
    *,
    dest_zip: Path,
    source_label: str,
    api_base_url: str,
    api_export: dict[str, Any],
    data_path: str,
    letsencrypt_path: str,
    docker_image: str = "",
    docker_container_id: str = "",
    include_volumes: bool = True,
    include_docker_inspect: bool = False,
) -> dict[str, Any]:
    snapshot_id = uuid.uuid4().hex
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    volume_files: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="npmtbx-snap-") as tmp:
        tmp_path = Path(tmp)
        api_dir = tmp_path / "api"
        api_dir.mkdir()
        for key, payload in api_export.items():
            if key == "api_base_url":
                continue
            (api_dir / f"{key}.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

        vol_dir = tmp_path / "volumes"
        if include_volumes:
            vol_dir.mkdir()
            data_src = Path(data_path).expanduser()
            le_src = Path(letsencrypt_path).expanduser()
            data_tar = vol_dir / "data.tar.gz"
            le_tar = vol_dir / "letsencrypt.tar.gz"
            if data_src.exists():
                _archive_directory(data_src, data_tar)
                volume_files["data"] = "volumes/data.tar.gz"
            if le_src.exists():
                _archive_directory(le_src, le_tar)
                volume_files["letsencrypt"] = "volumes/letsencrypt.tar.gz"

        if include_docker_inspect and docker_container_id.strip():
            docker_dir = tmp_path / "docker"
            docker_dir.mkdir(exist_ok=True)
            inspect_path = docker_dir / "container-inspect.json"
            try:
                import docker

                container = docker.from_env().containers.get(docker_container_id.strip())
                inspect_path.write_text(
                    json.dumps(container.attrs, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            except Exception as exc:
                inspect_path.write_text(
                    json.dumps({"_error": str(exc)}, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )

        manifest = build_manifest(
            snapshot_id=snapshot_id,
            source_label=source_label,
            api_base_url=api_base_url,
            docker_image=docker_image,
            docker_container_id=docker_container_id,
            npm_api_export=api_export,
            volume_files=volume_files,
        )
        (tmp_path / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        with zipfile.ZipFile(dest_zip, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in tmp_path.rglob("*"):
                if path.is_file():
                    zf.write(path, path.relative_to(tmp_path).as_posix())

    return manifest


def zip_contains_member(zip_path: Path, member: str) -> bool:
    with zipfile.ZipFile(zip_path, "r") as zf:
        return member in zf.namelist()


def load_api_export_from_zip(zip_path: Path) -> dict[str, Any]:
    """Rebuild export dict from snapshot api/*.json (same shape as NpmClient.export_configuration)."""
    out: dict[str, Any] = {}
    with zipfile.ZipFile(zip_path, "r") as zf:
        if "manifest.json" in zf.namelist():
            manifest = json.loads(zf.read("manifest.json").decode("utf-8"))
            if isinstance(manifest, dict) and manifest.get("api_base_url"):
                out["api_base_url"] = manifest["api_base_url"]
        for name in zf.namelist():
            if not name.startswith("api/") or not name.endswith(".json"):
                continue
            key = Path(name).stem
            payload = json.loads(zf.read(name).decode("utf-8"))
            out[key] = payload
    return out


def extract_volume_member(
    zip_path: Path,
    member: str,
    dest_dir: Path,
    *,
    clear_dest: bool = False,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="npmtbx-restore-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extract(member, tmp_path)
        tar_path = tmp_path / member
        if clear_dest and dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(dest_dir)
