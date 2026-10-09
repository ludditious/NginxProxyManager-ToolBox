# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from dataclasses import dataclass

from .models import MasterInstance, SlaveInstance, User


@dataclass(frozen=True)
class ServerRef:
    key: str
    label: str
    kind: str  # source | target
    master: MasterInstance | None = None
    slave: SlaveInstance | None = None


def list_configured_servers(user: User) -> list[ServerRef]:
    out: list[ServerRef] = []
    if user.master:
        host = user.master.admin_host or user.master.api_url or "source"
        out.append(
            ServerRef(
                key="source",
                label=f"Source ({host})",
                kind="source",
                master=user.master,
            )
        )
    for s in sorted(user.slaves, key=lambda x: x.sort_order):
        host = s.admin_host or s.api_url or s.name or str(s.id)
        out.append(
            ServerRef(
                key=f"target:{s.id}",
                label=f"Target: {s.name or host}",
                kind="target",
                slave=s,
            )
        )
    return out


def resolve_server_ref(user: User, key: str) -> ServerRef | None:
    k = (key or "").strip()
    for ref in list_configured_servers(user):
        if ref.key == k:
            return ref
    return None


def migrate_row(ref: ServerRef) -> MasterInstance | SlaveInstance | None:
    return ref.master or ref.slave
