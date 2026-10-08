# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base

DAY_KEYS = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    username: Mapped[str] = mapped_column(String(128), unique=True, index=True, default="admin")
    password_hash: Mapped[str] = mapped_column(String(255))
    recovery_answer_1_hash: Mapped[str] = mapped_column(Text, default="")
    recovery_answer_2_hash: Mapped[str] = mapped_column(Text, default="")
    recovery_answer_3_hash: Mapped[str] = mapped_column(Text, default="")
    recovery_answer_1_enc: Mapped[str] = mapped_column(Text, default="")
    recovery_answer_2_enc: Mapped[str] = mapped_column(Text, default="")
    recovery_answer_3_enc: Mapped[str] = mapped_column(Text, default="")
    check_updates_on_login: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    master: Mapped[MasterInstance | None] = relationship(back_populates="user", uselist=False)
    slaves: Mapped[list[SlaveInstance]] = relationship(back_populates="user")
    schedule: Mapped[BackupSchedule | None] = relationship(back_populates="user", uselist=False)
    run_logs: Mapped[list[BackupRunLog]] = relationship(back_populates="user")
    npm_backups: Mapped[list[NpmBackup]] = relationship(back_populates="user")
    toolbox_backups: Mapped[list[ToolBoxBackup]] = relationship(back_populates="user")
    npm_backup_settings: Mapped[NpmBackupSettings | None] = relationship(
        back_populates="user", uselist=False
    )
    remote_destinations: Mapped[list[RemoteToolBox]] = relationship(back_populates="user")
    remote_sources: Mapped[list[RemotePullSource]] = relationship(back_populates="user")
    smtp_settings: Mapped[SmtpSettings | None] = relationship(back_populates="user", uselist=False)
    npm_dns_settings: Mapped[NpmDnsSettings | None] = relationship(
        back_populates="user", uselist=False
    )
    notification_prefs: Mapped[NotificationPrefs | None] = relationship(
        back_populates="user", uselist=False
    )


class MasterInstance(Base):
    __tablename__ = "master_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    api_url: Mapped[str] = mapped_column(String(512), default="")
    admin_host: Mapped[str] = mapped_column(String(512), default="")
    connect_host: Mapped[str] = mapped_column(String(512), default="")
    identity: Mapped[str] = mapped_column(String(256), default="admin@example.com")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    data_path: Mapped[str] = mapped_column(String(1024), default="")
    letsencrypt_path: Mapped[str] = mapped_column(String(1024), default="")
    docker_container_id: Mapped[str] = mapped_column(String(128), default="")
    docker_image: Mapped[str] = mapped_column(String(512), default="")
    public_endpoint: Mapped[str] = mapped_column(String(512), default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    link_group: Mapped[str] = mapped_column(String(128), default="default")

    user: Mapped[User] = relationship(back_populates="master")


class SlaveInstance(Base):
    __tablename__ = "slave_instances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    api_url: Mapped[str] = mapped_column(String(512), default="")
    admin_host: Mapped[str] = mapped_column(String(512), default="")
    connect_host: Mapped[str] = mapped_column(String(512), default="")
    identity: Mapped[str] = mapped_column(String(256), default="admin@example.com")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    data_path: Mapped[str] = mapped_column(String(1024), default="")
    letsencrypt_path: Mapped[str] = mapped_column(String(1024), default="")
    docker_container_id: Mapped[str] = mapped_column(String(128), default="")
    docker_image: Mapped[str] = mapped_column(String(512), default="")
    public_endpoint: Mapped[str] = mapped_column(String(512), default="")
    verify_tls: Mapped[bool] = mapped_column(Boolean, default=False)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    link_group: Mapped[str] = mapped_column(String(128), default="default")
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    auto_pull: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[User] = relationship(back_populates="slaves")


class RemoteToolBox(Base):
    """Push snapshot to another ToolBox (DR)."""

    __tablename__ = "remote_toolboxes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    base_url: Mapped[str] = mapped_column(String(512), default="")
    ingest_token_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    push_after_backup: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="remote_destinations")


class RemotePullSource(Base):
    """Pull snapshots from another ToolBox."""

    __tablename__ = "remote_pull_sources"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(128), default="")
    base_url: Mapped[str] = mapped_column(String(512), default="")
    ingest_token_enc: Mapped[str] = mapped_column(Text, default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="remote_sources")


class BackupSchedule(Base):
    __tablename__ = "backup_schedules"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    interval_minutes: Mapped[int] = mapped_column(Integer, default=1440)
    days_json: Mapped[str] = mapped_column(String(128), default='["mon","tue","wed","thu","fri","sat","sun"]')
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    user: Mapped[User] = relationship(back_populates="schedule")

    def get_days(self) -> set[str]:
        try:
            data = json.loads(self.days_json)
            if isinstance(data, list):
                return {str(d).lower() for d in data if str(d).lower() in DAY_KEYS}
        except json.JSONDecodeError:
            pass
        return set(DAY_KEYS)

    def set_days(self, days: set[str]) -> None:
        ordered = [d for d in DAY_KEYS if d in days]
        self.days_json = json.dumps(ordered)


class NpmDnsSettings(Base):
    """Optional LAN DNS resolvers for NPM hostname lookups from the ToolBox container."""

    __tablename__ = "npm_dns_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    use_custom_dns: Mapped[bool] = mapped_column(Boolean, default=False)
    dns_servers: Mapped[str] = mapped_column(String(512), default="")

    user: Mapped[User] = relationship(back_populates="npm_dns_settings")


class NpmBackupSettings(Base):
    __tablename__ = "npm_backup_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    retention_days: Mapped[int] = mapped_column(Integer, default=30)
    last_run_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    include_api: Mapped[bool] = mapped_column(Boolean, default=True)
    include_volumes: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="npm_backup_settings")


class NpmBackup(Base):
    __tablename__ = "npm_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    snapshot_id: Mapped[str] = mapped_column(String(64), default="")
    api_url: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    file_name: Mapped[str] = mapped_column(String(512), default="")
    manifest_json: Mapped[str] = mapped_column(Text, default="")
    is_automated: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)

    user: Mapped[User] = relationship(back_populates="npm_backups")


class ToolBoxBackup(Base):
    __tablename__ = "toolbox_backups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    name: Mapped[str] = mapped_column(String(256), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    payload_json: Mapped[str] = mapped_column(Text)

    user: Mapped[User] = relationship(back_populates="toolbox_backups")


class SmtpSettings(Base):
    __tablename__ = "smtp_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    host: Mapped[str] = mapped_column(String(256), default="")
    port: Mapped[int] = mapped_column(Integer, default=587)
    use_tls: Mapped[bool] = mapped_column(Boolean, default=True)
    username_enc: Mapped[str] = mapped_column(Text, default="")
    password_enc: Mapped[str] = mapped_column(Text, default="")
    from_address: Mapped[str] = mapped_column(String(320), default="")
    to_addresses: Mapped[str] = mapped_column(String(1024), default="")

    user: Mapped[User] = relationship(back_populates="smtp_settings")


class NotificationPrefs(Base):
    __tablename__ = "notification_prefs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), unique=True)
    on_backup_success: Mapped[bool] = mapped_column(Boolean, default=True)
    on_backup_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    on_push_failure: Mapped[bool] = mapped_column(Boolean, default=True)
    on_pull_failure: Mapped[bool] = mapped_column(Boolean, default=True)

    user: Mapped[User] = relationship(back_populates="notification_prefs")


class CronTickLog(Base):
    __tablename__ = "cron_tick_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    users_due: Mapped[int] = mapped_column(Integer, default=0)
    backups_ran: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    detail: Mapped[str] = mapped_column(String(512), default="")


class BackupRunLog(Base):
    __tablename__ = "backup_run_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    trigger: Mapped[str] = mapped_column(String(32), default="manual")
    exit_code: Mapped[int] = mapped_column(Integer, default=0)
    body: Mapped[str] = mapped_column(Text, default="")

    user: Mapped[User] = relationship(back_populates="run_logs")
