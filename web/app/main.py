# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from .config import get_settings
from .database import SessionLocal, init_db
from .routers import auth, internal, pages
from .scheduler import start_background_scheduler
from .services import ensure_single_user
from .version import APP_NAME, read_bundled_revision, read_bundled_version


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=APP_NAME, version=read_bundled_version())

    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=14 * 86400,
        same_site="lax",
        https_only=False,
    )

    app.include_router(auth.router)
    app.include_router(pages.router)
    app.include_router(internal.router)

    @app.exception_handler(401)
    async def login_required(request: Request, _exc: HTTPException):
        return RedirectResponse("/login", status_code=303)

    @app.get("/")
    async def root(request: Request):
        if request.session.get("user_id"):
            return RedirectResponse("/dashboard", status_code=307)
        return RedirectResponse("/login", status_code=307)

    @app.get("/health")
    async def health():
        return {
            "status": "ok",
            "service": "nginxproxymanager-toolbox",
            "name": APP_NAME,
            "revision": read_bundled_revision(),
            "version": read_bundled_version(),
        }

    static_dir = __import__("pathlib").Path(__file__).resolve().parent / "static"
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    @app.on_event("startup")
    def _startup() -> None:
        init_db()
        db = SessionLocal()
        try:
            ensure_single_user(db)
        finally:
            db.close()
        start_background_scheduler()

    return app


app = create_app()
