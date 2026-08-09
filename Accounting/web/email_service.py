"""
Email notification service — Resend HTTP API (https://resend.com).

Plain HTTP POST to https://api.resend.com/emails; no SDK dependency.
Uses the `requests` library when available, falling back to stdlib urllib.

Environment variables:
    RESEND_API_KEY : Resend API key. When unset, every send is a logged no-op.
    EMAIL_FROM     : Sender, e.g. 'EBMS <noreply@devopsrain.com>'.
                     Defaults to 'EBMS <onboarding@resend.dev>' (Resend sandbox).
    ADMIN_EMAIL    : Recipient for notify_admin() / alert_on_critical().

All functions log success/failure and NEVER raise — email is best-effort
and must not break the calling code path.
"""

import json
import logging
import os
from typing import List, Union

logger = logging.getLogger(__name__)

RESEND_API_URL   = "https://api.resend.com/emails"
DEFAULT_FROM     = "EBMS <onboarding@resend.dev>"
TIMEOUT_SECONDS  = 10


def _post_resend(api_key: str, payload: dict) -> tuple:
    """POST payload to the Resend API. Returns (status_code, body_snippet)."""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type":  "application/json",
    }
    try:
        import requests
        resp = requests.post(RESEND_API_URL, headers=headers,
                             json=payload, timeout=TIMEOUT_SECONDS)
        return resp.status_code, (resp.text or "")[:500]
    except ImportError:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            RESEND_API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
                return r.status, r.read(500).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read(500).decode("utf-8", "replace")


def send_email(to: Union[str, List[str]], subject: str, html: str,
               category: str = "general") -> bool:
    """
    Send an email via Resend. Returns True on success, False otherwise.

    No-op (returns False) when RESEND_API_KEY is unset or `to` is empty.
    Never raises.
    """
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    if not api_key:
        logger.info("email_skipped (RESEND_API_KEY not set): category=%s to=%s subject=%r",
                    category, to, subject)
        return False

    recipients = [to] if isinstance(to, str) else [t for t in (to or []) if t]
    recipients = [r.strip() for r in recipients if r and r.strip()]
    if not recipients:
        logger.warning("email_skipped (no recipient): category=%s subject=%r",
                       category, subject)
        return False

    sender  = os.environ.get("EMAIL_FROM", "").strip() or DEFAULT_FROM
    payload = {"from": sender, "to": recipients, "subject": subject, "html": html}

    try:
        status, body = _post_resend(api_key, payload)
        if 200 <= status < 300:
            logger.info("email_sent: category=%s to=%s subject=%r",
                        category, recipients, subject)
            return True
        logger.error("email_failed: category=%s to=%s status=%s body=%s",
                     category, recipients, status, body)
        return False
    except Exception as e:
        logger.error("email_failed: category=%s to=%s error=%s",
                     category, recipients, e)
        return False


def notify_admin(subject: str, html: str) -> bool:
    """
    Email the administrator (ADMIN_EMAIL env var).
    No-op with a log line when ADMIN_EMAIL is unset. Never raises.
    """
    admin = os.environ.get("ADMIN_EMAIL", "").strip()
    if not admin:
        logger.info("admin_email_skipped (ADMIN_EMAIL not set): subject=%r", subject)
        return False
    return send_email(admin, subject, html, category="admin")


def alert_on_critical(rule: str, message: str) -> bool:
    """
    Notify the admin about a critical/high security alert (SIEM).
    Best-effort; never raises, so callers need no guard of their own.
    """
    try:
        subject = f"[EBMS ALERT] {rule}"
        html = (
            "<h3>EBMS security alert</h3>"
            f"<p><b>Rule:</b> {rule}</p>"
            f"<p><b>Message:</b> {message}</p>"
            "<p>Review the SIEM dashboard for details.</p>"
        )
        return notify_admin(subject, html)
    except Exception as e:               # defence in depth — must never raise
        logger.error("alert_on_critical failed: %s", e)
        return False
