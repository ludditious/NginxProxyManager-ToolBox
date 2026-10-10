# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

RESTORE_CONFIRM_PHRASE = "RESTORE"


def require_restore_confirmation(phrase: str) -> None:
    if (phrase or "").strip() != RESTORE_CONFIRM_PHRASE:
        raise ValueError(
            f'Type {RESTORE_CONFIRM_PHRASE!r} in the confirmation box to restore.'
        )
