"""
Report mailer — Resend HTTP API with base64 attachments.

`email_service.send_email` has no attachment support, so scheduled reports
post to https://api.resend.com/emails directly. Same environment variables:

    RESEND_API_KEY : when unset every send is a logged no-op (returns False)
    EMAIL_FROM     : sender, defaults to the Resend sandbox address

Never raises — email is best-effort.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Iterable, List, Sequence, Tuple, Union

logger = logging.getLogger(__name__)

RESEND_API_URL = "https://api.resend.com/emails"
DEFAULT_FROM = "EBMS <onboarding@resend.dev>"
TIMEOUT_SECONDS = 30
MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024   # Resend hard limit ~40MB incl. overhead

Attachment = Tuple[str, bytes, str]       # (filename, content, mime)


def parse_recipients(value: Union[str, Iterable[str], None]) -> List[str]:
    """'a@x.com, b@y.com;c@z.com' → ['a@x.com','b@y.com','c@z.com'] (deduped, order kept)."""
    if not value:
        return []
    if isinstance(value, str):
        parts = value.replace(";", ",").replace("\n", ",").split(",")
    else:
        parts = list(value)
    out: List[str] = []
    for p in parts:
        p = (p or "").strip()
        if p and "@" in p and p not in out:
            out.append(p)
    return out


def _post(api_key: str, payload: dict) -> Tuple[int, str]:
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = json.dumps(payload).encode("utf-8")
    try:
        import requests
        resp = requests.post(RESEND_API_URL, headers=headers, data=body, timeout=TIMEOUT_SECONDS)
        return resp.status_code, (resp.text or "")[:500]
    except ImportError:
        import urllib.error
        import urllib.request
        req = urllib.request.Request(RESEND_API_URL, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as r:
                return r.status, r.read(500).decode("utf-8", "replace")
        except urllib.error.HTTPError as e:
            return e.code, e.read(500).decode("utf-8", "replace")


def send_report_email(to: Union[str, Sequence[str]], subject: str, html: str,
                      attachments: Sequence[Attachment] = ()) -> bool:
    """Send an email with optional attachments via Resend. Returns True on success."""
    api_key = os.environ.get("RESEND_API_KEY", "").strip()
    recipients = parse_recipients(to)
    if not api_key:
        logger.info("report_email_skipped (RESEND_API_KEY not set): to=%s subject=%r", recipients, subject)
        return False
    if not recipients:
        logger.warning("report_email_skipped (no recipient): subject=%r", subject)
        return False

    payload = {
        "from": os.environ.get("EMAIL_FROM", "").strip() or DEFAULT_FROM,
        "to": recipients,
        "subject": subject,
        "html": html,
    }
    atts = []
    for filename, content, _mime in attachments or ():
        if not content:
            continue
        if len(content) > MAX_ATTACHMENT_BYTES:
            logger.warning("report_email: attachment %s too large (%d bytes) — skipped", filename, len(content))
            continue
        atts.append({"filename": filename, "content": base64.b64encode(content).decode("ascii")})
    if atts:
        payload["attachments"] = atts

    try:
        status, body = _post(api_key, payload)
        if 200 <= status < 300:
            logger.info("report_email_sent: to=%s subject=%r attachments=%d", recipients, subject, len(atts))
            return True
        logger.error("report_email_failed: to=%s status=%s body=%s", recipients, status, body)
        return False
    except Exception as e:
        logger.error("report_email_failed: to=%s error=%s", recipients, e)
        return False
