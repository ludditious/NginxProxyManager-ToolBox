# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

"""Shared size gates for full NPM volume backups (create + restore)."""

from __future__ import annotations

# Real NPM /data (database.sqlite + keys/nginx) is usually hundreds of KB–MB in a ZIP.
MIN_FULL_BACKUP_ZIP_BYTES = 65_536

# Fresh NPM SQLite is typically tens of KB; smaller is almost always empty/wrong.
MIN_NPM_SQLITE_BYTES = 16_384

# Compressed data.tar.gz smaller than this is not credible NPM /data.
MIN_VOLUME_TAR_GZ_BYTES = 8_192
