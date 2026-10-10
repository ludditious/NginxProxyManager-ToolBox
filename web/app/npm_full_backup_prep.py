# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

from sqlalchemy.orm import Session

from .docker_discover import NpmCandidate, discover_npm_containers, single_high_confidence
from .models import LocalNpmBackup
from .npm_bridge import apply_candidate_to_local


def _container_ids_match(a: str, b: str) -> bool:
    a = (a or "").strip()
    b = (b or "").strip()
    if not a or not b:
        return False
    return a == b or a.startswith(b[:12]) or b.startswith(a[:12])


def _pick_candidate(candidates: list[NpmCandidate]) -> NpmCandidate | None:
    pick = single_high_confidence(candidates)
    if pick:
        return pick
    if len(candidates) == 1:
        return candidates[0]
    medium = [c for c in candidates if c.confidence in ("high", "medium")]
    if len(medium) == 1:
        return medium[0]
    return None


def mount_hint_from_candidates(candidates: list[NpmCandidate]) -> str:
    for c in candidates:
        if (c.data_path or "").strip():
            host = c.data_path.strip()
            return (
                f"NPM on this host uses {host!r} for /data. "
                f"Add to ToolBox: -v {host}:/npm-data (and the matching letsencrypt mount if you use certs)."
            )
    return (
        "Ensure ToolBox has -v /var/run/docker.sock:/var/run/docker.sock:ro "
        "and can see the NPM container on this host."
    )


def sync_local_npm_from_detect(db: Session, local: LocalNpmBackup, candidates: list[NpmCandidate]) -> bool:
    """Link or relink detected NPM container id in the database. Returns True if updated."""
    pick = _pick_candidate(candidates)
    if not pick:
        return False
    saved = (local.docker_container_id or "").strip()
    if saved and _container_ids_match(saved, pick.container_id):
        return False
    apply_candidate_to_local(local, pick)
    db.add(local)
    db.commit()
    db.refresh(local)
    return True


def prepare_local_for_full_backup(
    db: Session,
    local: LocalNpmBackup,
) -> tuple[list[NpmCandidate], str | None]:
    """Detect NPM on Docker; link container if needed. Returns candidates and discover error."""
    candidates, discover_err = discover_npm_containers(probe_api=False)
    sync_local_npm_from_detect(db, local, candidates)
    return candidates, discover_err


def container_ids_for_archive(local: LocalNpmBackup, candidates: list[NpmCandidate]) -> list[str]:
    ids: list[str] = []
    seen: set[str] = set()
    primary = (local.docker_container_id or "").strip()
    if primary:
        seen.add(primary)
        ids.append(primary)
    for c in candidates:
        cid = (c.container_id or "").strip()
        if cid and cid not in seen:
            seen.add(cid)
            ids.append(cid)
    return ids
