# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import bcrypt


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("ascii")


def verify_password(password: str, password_hash: str) -> bool:
    if not password_hash:
        return False
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("ascii"))
    except (ValueError, TypeError):
        return False


def normalize_recovery_answer(answer: str) -> str:
    return " ".join((answer or "").strip().lower().split())


def hash_recovery_answer(answer: str) -> str:
    return hash_password(normalize_recovery_answer(answer))


def verify_recovery_answer(answer: str, answer_hash: str) -> bool:
    if not answer_hash:
        return False
    return verify_password(normalize_recovery_answer(answer), answer_hash)
