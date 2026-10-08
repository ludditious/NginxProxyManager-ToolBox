# Copyright (C) 2026 https://ludditious.com/
# SPDX-License-Identifier: AGPL-3.0-or-later

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from .config import get_settings
from .crypto import decrypt
from .models import NotificationPrefs, SmtpSettings, User

logger = logging.getLogger(__name__)


def send_notification(user: User, subject: str, body: str) -> None:
    prefs = user.notification_prefs
    smtp = user.smtp_settings
    if not smtp or not smtp.enabled:
        return
    sk = get_settings().secret_key
    username = decrypt(sk, smtp.username_enc) if smtp.username_enc else ""
    password = decrypt(sk, smtp.password_enc) if smtp.password_enc else ""
    recipients = [a.strip() for a in (smtp.to_addresses or "").split(",") if a.strip()]
    if not recipients or not smtp.host.strip():
        return
    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = smtp.from_address or username or "npmtoolbox@local"
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    try:
        with smtplib.SMTP(smtp.host, smtp.port, timeout=30) as server:
            if smtp.use_tls:
                server.starttls()
            if username:
                server.login(username, password)
            server.send_message(msg)
    except Exception:
        logger.exception("SMTP notification failed")


def notify_backup_result(user: User, *, success: bool, detail: str) -> None:
    prefs = user.notification_prefs
    if prefs:
        if success and not prefs.on_backup_success:
            return
        if not success and not prefs.on_backup_failure:
            return
    subject = "NginxProxyManager-ToolBox: backup succeeded" if success else "NginxProxyManager-ToolBox: backup failed"
    send_notification(user, subject, detail)
