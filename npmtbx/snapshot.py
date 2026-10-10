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

NPM_DATA_MARKER = "database.sqlite"
# Empty directory tars are a few hundred bytes; real NPM /data is much larger.
MIN_VOLUME_TAR_GZ_BYTES = 4096


def _path_has_npm_data(source: Path) -> bool:
    if not source.is_dir():
        return False
    if (source / NPM_DATA_MARKER).is_file():
        return True
    return any(f.is_file() and f.stat().st_size > 0 for f in source.rglob("*"))


def _tar_gz_has_files(tar_gz: Path) -> bool:
    if not tar_gz.is_file() or tar_gz.stat().st_size < MIN_VOLUME_TAR_GZ_BYTES:
        return False
    try:
        with tarfile.open(tar_gz, "r:gz") as tar:
            return any(m.isfile() and m.size > 0 for m in tar.getmembers())
    except tarfile.TarError:
        return False


def _container_ids_to_try(primary: str, extras: list[str] | None) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for raw in [primary, *(extras or [])]:
        cid = (raw or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            out.append(cid)
    return out


def _archive_from_container(container_id: str, container_path: str, dest_tar_gz: Path) -> tuple[bool, str]:
    cid = container_id.strip()
    if not cid:
        return False, "no container id"
    try:
        import docker

        container = docker.from_env().containers.get(cid)
        stream, _ = container.get_archive(container_path)
        raw_tar = b"".join(stream)
        if not raw_tar:
            return False, f"empty archive from {container_path}"
        dest_tar_gz.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(delete=False, suffix=".tar") as tmp:
            tmp.write(raw_tar)
            tmp_path = Path(tmp.name)
        try:
            with tarfile.open(tmp_path, "r:") as src, tarfile.open(dest_tar_gz, "w:gz") as dest:
                for member in src.getmembers():
                    extracted = src.extractfile(member)
                    dest.addfile(member, extracted)
        finally:
            tmp_path.unlink(missing_ok=True)
        if _tar_gz_has_files(dest_tar_gz):
            return True, ""
        dest_tar_gz.unlink(missing_ok=True)
        return False, f"archive from {container_path} had no files"
    except Exception as exc:
        dest_tar_gz.unlink(missing_ok=True)
        return False, str(exc)


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
    docker_container_ids: list[str] | None = None,
    include_volumes: bool = True,
    include_docker_inspect: bool = False,
) -> dict[str, Any]:
    snapshot_id = uuid.uuid4().hex
    dest_zip.parent.mkdir(parents=True, exist_ok=True)
    volume_files: dict[str, str] = {}
    archive_notes: list[str] = []
    container_ids = _container_ids_to_try(docker_container_id, docker_container_ids)

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
            data_ok = False
            if data_src.exists() and _path_has_npm_data(data_src):
                _archive_directory(data_src, data_tar)
                data_ok = _tar_gz_has_files(data_tar)
            if not data_ok:
                for cid in container_ids:
                    ok, err = _archive_from_container(cid, "/data", data_tar)
                    if ok:
                        data_ok = True
                        break
                    archive_notes.append(f"container {cid[:12]} /data: {err}")
            if data_ok:
                volume_files["data"] = "volumes/data.tar.gz"
            else:
                data_tar.unlink(missing_ok=True)

            le_ok = False
            if le_src.exists() and any(
                f.is_file() and f.stat().st_size > 0 for f in le_src.rglob("*")
            ):
                _archive_directory(le_src, le_tar)
                le_ok = _tar_gz_has_files(le_tar)
            if not le_ok:
                for cid in container_ids:
                    ok, err = _archive_from_container(cid, "/etc/letsencrypt", le_tar)
                    if ok:
                        le_ok = True
                        break
                    archive_notes.append(f"container {cid[:12]} certs: {err}")
            if le_ok:
                volume_files["letsencrypt"] = "volumes/letsencrypt.tar.gz"
            else:
                le_tar.unlink(missing_ok=True)

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
        if archive_notes:
            manifest["archive_notes"] = archive_notes
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


def _tar_member_names(tar_gz: Path) -> list[str]:
    with tarfile.open(tar_gz, "r:gz") as tar:
        return [m.name for m in tar.getmembers()]


def _tar_has_npm_data_files(tar_gz: Path) -> bool:
    try:
        names = _tar_member_names(tar_gz)
    except tarfile.TarError:
        return False
    if any(NPM_DATA_MARKER in n.replace("\\", "/") for n in names):
        return True
    return False


def validate_volume_member_for_restore(
    zip_path: Path,
    member: str,
    *,
    require_npm_data: bool = False,
) -> None:
    if not zip_contains_member(zip_path, member):
        raise ValueError(f"Backup is missing {member}.")
    with zipfile.ZipFile(zip_path, "r") as zf:
        info = zf.getinfo(member)
        if info.file_size < MIN_VOLUME_TAR_GZ_BYTES:
            raise ValueError(
                f"Backup {member} is too small ({info.file_size} bytes) to restore safely."
            )
    with tempfile.TemporaryDirectory(prefix="npmtbx-validate-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extract(member, tmp_path)
        tar_path = tmp_path / member
        if require_npm_data and not _tar_has_npm_data_files(tar_path):
            raise ValueError(
                "Backup data archive does not contain database.sqlite — refusing restore "
                "so live NPM is not wiped."
            )


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
    require_npm_data: bool = False,
) -> None:
    validate_volume_member_for_restore(
        zip_path, member, require_npm_data=require_npm_data
    )
    dest_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="npmtbx-restore-") as tmp:
        tmp_path = Path(tmp)
        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extract(member, tmp_path)
        tar_path = tmp_path / member
        staging = tmp_path / "staging"
        staging.mkdir()
        with tarfile.open(tar_path, "r:gz") as tar:
            tar.extractall(staging)
        if require_npm_data and not _path_has_npm_data(staging):
            raise ValueError(
                "Extracted backup has no NPM data files — live folders were not modified."
            )
        if clear_dest and dest_dir.exists():
            shutil.rmtree(dest_dir)
        dest_dir.mkdir(parents=True, exist_ok=True)
        for item in staging.iterdir():
            target = dest_dir / item.name
            if item.is_dir():
                shutil.copytree(item, target, dirs_exist_ok=True)
            else:
                shutil.copy2(item, target)
