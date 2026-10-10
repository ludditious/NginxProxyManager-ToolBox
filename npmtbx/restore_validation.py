# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import tarfile
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from .backup_format import parse_manifest
from .backup_limits import (
    MIN_FULL_BACKUP_ZIP_BYTES,
    MIN_NPM_SQLITE_BYTES,
    MIN_VOLUME_TAR_GZ_BYTES,
)
from .snapshot import NPM_DATA_MARKER, _path_has_npm_data, zip_contains_member

MIN_LETSENCRYPT_TAR_GZ_BYTES = 512
DATA_MEMBER = "volumes/data.tar.gz"
LE_MEMBER = "volumes/letsencrypt.tar.gz"


@dataclass(frozen=True)
class DestructiveRestoreApproval:
    """Proof that a backup was validated before any live NPM data may be replaced."""

    zip_path: str
    approved_members: frozenset[str]

    def assert_can_replace(self, zip_path: Path, member: str) -> None:
        resolved = str(zip_path.expanduser().resolve())
        if resolved != self.zip_path:
            raise ValueError("Restore refused: backup file does not match validated archive.")
        if member not in self.approved_members:
            raise ValueError(
                f"Restore refused: {member} was not approved for destructive replace."
            )


def _extract_member_to_staging(zip_path: Path, member: str, staging: Path) -> Path:
    with zipfile.ZipFile(zip_path, "r") as zf:
        if member not in zf.namelist():
            raise ValueError(f"Backup is missing {member}.")
        info = zf.getinfo(member)
        if info.file_size < MIN_VOLUME_TAR_GZ_BYTES:
            raise ValueError(
                f"Backup {member} is too small ({info.file_size} bytes) to restore safely."
            )
        zf.extract(member, staging)
    tar_path = staging / member
    out = staging / "unpacked"
    out.mkdir()
    with tarfile.open(tar_path, "r:gz") as tar:
        tar.extractall(out)
    return out


def _validate_data_member(zip_path: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="npmtbx-restore-validate-") as tmp:
        staging = Path(tmp)
        unpacked = _extract_member_to_staging(zip_path, DATA_MEMBER, staging)
        if not _path_has_npm_data(unpacked):
            raise ValueError(
                "Backup data archive has no NPM files — refusing destructive restore."
            )
        sqlite_paths = list(unpacked.rglob(NPM_DATA_MARKER))
        if not sqlite_paths:
            raise ValueError(
                f"Backup data archive does not contain {NPM_DATA_MARKER} — refusing restore."
            )
        largest = max(p.stat().st_size for p in sqlite_paths if p.is_file())
        if largest < MIN_NPM_SQLITE_BYTES:
            raise ValueError(
                f"Backup {NPM_DATA_MARKER} is too small ({largest} bytes) to be valid NPM data."
            )


def _validate_letsencrypt_member(zip_path: Path) -> None:
    if not zip_contains_member(zip_path, LE_MEMBER):
        raise ValueError(f"Backup is missing {LE_MEMBER}.")
    with zipfile.ZipFile(zip_path, "r") as zf:
        info = zf.getinfo(LE_MEMBER)
        if info.file_size < MIN_LETSENCRYPT_TAR_GZ_BYTES:
            raise ValueError(
                f"Backup certificate archive is too small ({info.file_size} bytes)."
            )
    with tempfile.TemporaryDirectory(prefix="npmtbx-le-validate-") as tmp:
        staging = Path(tmp)
        unpacked = _extract_member_to_staging(zip_path, LE_MEMBER, staging)
        files = [f for f in unpacked.rglob("*") if f.is_file() and f.stat().st_size > 0]
        if not files:
            raise ValueError(
                "Backup certificate archive is empty — refusing destructive restore."
            )


def _validate_zip_shell(zip_path: Path, record_size_bytes: int = 0) -> dict:
    if not zip_path.is_file():
        raise ValueError("Backup file is missing on disk.")
    size = zip_path.stat().st_size
    if size < MIN_FULL_BACKUP_ZIP_BYTES:
        raise ValueError(
            f"Backup ZIP is only {size} bytes (need at least {MIN_FULL_BACKUP_ZIP_BYTES // 1024} KB) — "
            "refusing destructive restore. A real NPM full backup is usually much larger."
        )
    if record_size_bytes > 0 and record_size_bytes < MIN_FULL_BACKUP_ZIP_BYTES:
        raise ValueError(
            f"Backup record size ({record_size_bytes} bytes) is too small for a valid full NPM backup."
        )
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"Backup ZIP is corrupt (bad file: {bad}).")
            if "manifest.json" not in zf.namelist():
                raise ValueError("Backup ZIP has no manifest.json.")
            manifest = parse_manifest(zf.read("manifest.json"))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Backup is not a valid ZIP: {e}") from e
    return manifest


def approve_destructive_volume_restore(
    zip_path: Path,
    *,
    restore_data: bool,
    restore_letsencrypt: bool,
    record_size_bytes: int = 0,
) -> DestructiveRestoreApproval:
    """
    Run every check before stop/wipe/replace. Raises ValueError if unsafe.
    Returns an approval token required for extract_volume_member(clear_dest=True).
    """
    if not restore_data and not restore_letsencrypt:
        raise ValueError("Nothing to restore.")
    manifest = _validate_zip_shell(zip_path, record_size_bytes)
    approved: set[str] = set()

    if restore_data:
        if not zip_contains_member(zip_path, DATA_MEMBER):
            raise ValueError("Backup has no NPM /data archive.")
        vol = manifest.get("volume_files") or {}
        if not vol.get("data"):
            raise ValueError("Backup manifest does not list validated /data volume.")
        _validate_data_member(zip_path)
        approved.add(DATA_MEMBER)

    if restore_letsencrypt:
        _validate_letsencrypt_member(zip_path)
        approved.add(LE_MEMBER)

    return DestructiveRestoreApproval(
        zip_path=str(zip_path.expanduser().resolve()),
        approved_members=frozenset(approved),
    )


def approve_api_configuration_restore(zip_path: Path) -> None:
    """Validate snapshot API backup before pushing config onto live NPM."""
    if not zip_path.is_file():
        raise ValueError("Backup file is missing on disk.")
    size = zip_path.stat().st_size
    if size < 1024:
        raise ValueError(f"Snapshot ZIP is too small ({size} bytes) to restore.")
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise ValueError(f"Snapshot ZIP is corrupt (bad file: {bad}).")
            api_files = [n for n in zf.namelist() if n.startswith("api/") and n.endswith(".json")]
            if not api_files:
                raise ValueError("Snapshot has no API configuration files.")
            if "manifest.json" in zf.namelist():
                parse_manifest(zf.read("manifest.json"))
    except zipfile.BadZipFile as e:
        raise ValueError(f"Snapshot is not a valid ZIP: {e}") from e
