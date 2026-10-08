# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging
import os
import threading
import time

from .database import SessionLocal
from .services import run_cron_tick

logger = logging.getLogger(__name__)

_tick_lock = threading.Lock()
_started = False


def _scheduler_loop() -> None:
    time.sleep(15)
    while True:
        try:
            with _tick_lock:
                db = SessionLocal()
                try:
                    run_cron_tick(db)
                finally:
                    db.close()
        except Exception:
            logger.exception("Scheduler tick failed")
        time.sleep(60)


def start_background_scheduler() -> None:
    global _started
    if _started:
        return
    enabled = os.environ.get("ENABLE_SCHEDULER", "true").lower()
    if enabled in ("0", "false", "no"):
        logger.info("Built-in scheduler disabled (ENABLE_SCHEDULER=%s)", enabled)
        return
    _started = True
    thread = threading.Thread(target=_scheduler_loop, daemon=True, name="npmtbx-scheduler")
    thread.start()
    logger.info("Built-in scheduler started (checks every 60 seconds)")
