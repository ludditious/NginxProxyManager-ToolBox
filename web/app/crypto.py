# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken


def _fernet(secret_key: str) -> Fernet:
    digest = hashlib.sha256(secret_key.encode("utf-8")).digest()
    key = base64.urlsafe_b64encode(digest)
    return Fernet(key)


def encrypt(secret_key: str, plain: str) -> str:
    if not plain:
        return ""
    return _fernet(secret_key).encrypt(plain.encode("utf-8")).decode("ascii")


def decrypt(secret_key: str, token: str) -> str:
    if not token:
        return ""
    try:
        return _fernet(secret_key).decrypt(token.encode("ascii")).decode("utf-8")
    except InvalidToken:
        return ""
