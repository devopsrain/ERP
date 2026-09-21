"""
Template render tests — Telegram module.

Renders every telegram/*.html template with route-accurate contexts, both
EMPTY and POPULATED (same stub harness as test_vat_templates.py). No database.
"""
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from telegram_data_store import TOPICS, TOPIC_LABELS  # noqa: E402

# autoescape=True mirrors the production engine (Starlette's Jinja2Templates
# default) — the escaping assertions below depend on it.
env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")),
                         autoescape=True)


def _base_ctx(path="/telegram/"):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(),
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
    )


CFG_EMPTY = dict(configured=False, token_hint="", secret_set=False, public_base_url="", webhook_url="")
CFG_FULL = dict(configured=True, token_hint="1234…wxyz", secret_set=True,
                public_base_url="https://ebms.devopsrain.com",
                webhook_url="https://ebms.devopsrain.com/telegram/webhook")
STATS_EMPTY = dict(links=0, subscriptions=0, outbox_pending=0, outbox_sent=0,
                   outbox_failed=0, updates=0, updates_24h=0)
STATS_FULL = dict(links=2, subscriptions=5, outbox_pending=1, outbox_sent=40,
                  outbox_failed=2, updates=120, updates_24h=9)
NOW = datetime(2026, 9, 12, 7, 30)
LINK = dict(id=1, chat_id=123456789, username="admin", company_id="default",
            telegram_username="abebe_tg", first_name="Abebe <script>", linked_at=NOW, is_active=True)
WEBHOOK_INFO = dict(url="https://ebms.devopsrain.com/telegram/webhook", pending_update_count=0,
                    last_error_message="", max_connections=40)


def _dashboard(full):
    return dict(cfg=CFG_FULL if full else CFG_EMPTY,
                webhook_info=WEBHOOK_INFO if full else None,
                stats=STATS_FULL if full else STATS_EMPTY,
                my_links=[LINK] if full else [],
                is_admin=full, topics=TOPICS, topic_labels=TOPIC_LABELS)


def _link(full):
    return dict(cfg=CFG_FULL if full else CFG_EMPTY,
                codes=[dict(code="ABC234", company_id="default", expires_at=NOW)] if full else [],
                links=[LINK] if full else [], bot_username="ebms_bot" if full else "",
                ttl_minutes=15)


def _subs(full):
    return dict(links=[dict(LINK, topics={"daily_digest", "approvals"})] if full else [],
                topics=TOPICS, topic_labels=TOPIC_LABELS)


def _log(full):
    updates = [dict(id=1, update_id=100, chat_id=123, text="/today", handled=True,
                    response="sent", received_at=NOW),
               dict(id=2, update_id=None, chat_id=None, text="", handled=False,
                    response="", received_at=NOW)] if full else []
    outbox = [dict(id=1, chat_id=123, text="<b>Digest</b> long text " * 20, parse_mode="HTML",
                   status="sent", attempts=1, last_error="", created_at=NOW, sent_at=NOW),
              dict(id=2, chat_id=123, text="x", parse_mode="HTML", status="failed",
                   attempts=5, last_error="chat not found", created_at=NOW, sent_at=None)] if full else []
    return dict(updates=updates, outbox=outbox, stats=STATS_FULL if full else STATS_EMPTY)


def _help(full):
    return dict(cfg=CFG_FULL if full else CFG_EMPTY, topics=TOPICS, topic_labels=TOPIC_LABELS,
                is_admin=full)


CASES = [
    ("telegram/dashboard.html",     lambda: _dashboard(False)),
    ("telegram/dashboard.html",     lambda: _dashboard(True)),
    ("telegram/dashboard.html",     lambda: dict(_dashboard(True), webhook_info={"error": "timeout"})),
    ("telegram/dashboard.html",     lambda: dict(_dashboard(True), webhook_info={"url": ""})),
    ("telegram/link.html",          lambda: _link(False)),
    ("telegram/link.html",          lambda: _link(True)),
    ("telegram/subscriptions.html", lambda: _subs(False)),
    ("telegram/subscriptions.html", lambda: _subs(True)),
    ("telegram/log.html",           lambda: _log(False)),
    ("telegram/log.html",           lambda: _log(True)),
    ("telegram/help.html",          lambda: _help(False)),
    ("telegram/help.html",          lambda: _help(True)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_telegram_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000


def test_dashboard_shows_not_configured():
    html = env.get_template("telegram/dashboard.html").render(**_base_ctx(), **_dashboard(False))
    assert "Not configured" in html and "TELEGRAM_BOT_TOKEN" in html
    assert "Set webhook" not in html                # admin buttons hidden when unconfigured


def test_dashboard_admin_buttons_and_escaping():
    html = env.get_template("telegram/dashboard.html").render(**_base_ctx(), **_dashboard(True))
    assert "Set webhook" in html and "Remove" in html
    assert "Abebe <script>" not in html              # autoescape of first_name
    assert "Abebe &lt;script&gt;" in html
    assert "https://ebms.devopsrain.com/telegram/webhook" in html


def test_help_documents_env_vars_and_funnel():
    html = env.get_template("telegram/help.html").render(**_base_ctx(), **_help(False))
    for needle in ("TELEGRAM_BOT_TOKEN", "TELEGRAM_WEBHOOK_SECRET", "TELEGRAM_PUBLIC_BASE_URL",
                   "/telegram/webhook", "Tailscale", "BotFather", "notify_topic"):
        assert needle in html, needle


def test_subscriptions_checkboxes_reflect_state():
    html = env.get_template("telegram/subscriptions.html").render(**_base_ctx(), **_subs(True))
    assert 'value="approvals" id="t_123456789_approvals" checked' in html
    assert 'value="payments" id="t_123456789_payments" >' in html or 'value="payments" id="t_123456789_payments">' in html
