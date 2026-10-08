# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .database import get_db
from .models import User


def get_current_user(request: Request, db: Session = Depends(get_db)) -> User:
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="Login required")
    user = db.get(User, int(user_id))
    if not user:
        request.session.clear()
        raise HTTPException(status_code=401, detail="Login required")
    return user
