# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from sqlalchemy import create_engine, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import get_settings


class Base(DeclarativeBase):
    pass


def _engine():
    settings = get_settings()
    url = settings.database_url
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args["check_same_thread"] = False
    return create_engine(url, connect_args=connect_args, pool_pre_ping=True)


engine = _engine()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _sqlite_add_column(conn, table: str, column: str, ddl: str) -> None:
    rows = conn.execute(text(f"PRAGMA table_info({table})")).fetchall()
    existing = {str(r[1]) for r in rows}
    if column not in existing:
        conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {column} {ddl}"))


def migrate_schema() -> None:
    url = str(engine.url)
    if not url.startswith("sqlite"):
        return
    patches = (
        ("master_instances", "admin_host", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("master_instances", "connect_host", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("slave_instances", "admin_host", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("slave_instances", "connect_host", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("npm_dns_settings", "use_host_overrides", "BOOLEAN NOT NULL DEFAULT 0"),
        ("npm_dns_settings", "host_overrides", "TEXT NOT NULL DEFAULT ''"),
        ("master_instances", "migrate_toolbox_url", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("master_instances", "migrate_ingest_token_enc", "TEXT NOT NULL DEFAULT ''"),
        ("slave_instances", "schedule_sync_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("slave_instances", "migrate_toolbox_url", "VARCHAR(512) NOT NULL DEFAULT ''"),
        ("slave_instances", "migrate_ingest_token_enc", "TEXT NOT NULL DEFAULT ''"),
        ("npm_backups", "backup_kind", "VARCHAR(16) NOT NULL DEFAULT 'snapshot'"),
        ("remote_toolboxes", "verify_tls", "BOOLEAN NOT NULL DEFAULT 0"),
        ("remote_toolboxes", "destination_kind", "VARCHAR(32) NOT NULL DEFAULT 'dr_site'"),
        ("remote_toolboxes", "schedule_enabled", "BOOLEAN NOT NULL DEFAULT 0"),
        ("remote_toolboxes", "interval_minutes", "INTEGER NOT NULL DEFAULT 10080"),
        ("remote_toolboxes", "days_json", "VARCHAR(128) NOT NULL DEFAULT '[\"sun\"]'"),
        ("remote_toolboxes", "last_push_at", "DATETIME"),
    )
    with engine.begin() as conn:
        for table, column, ddl in patches:
            try:
                _sqlite_add_column(conn, table, column, ddl)
            except Exception:
                continue


def init_db() -> None:
    from . import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    migrate_schema()
