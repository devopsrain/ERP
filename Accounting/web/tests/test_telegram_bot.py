"""
Pure tests for the Telegram bot module — no database, no network.

Covers: command parsing, link-code flow, HTML escaping of user data, digest
formatting, approval callback bridging, outbox notify API, the webhook secret
check (unit + HTTP level through a minimal FastAPI app).
"""
import sys
import types
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import telegram_bot as tb  # noqa: E402
from telegram_bot import (  # noqa: E402
    NOT_LINKED_TEXT, Reply, build_reply, esc, format_bids, format_digest,
    format_totals, parse_command, verify_secret,
)

TODAY = date(2026, 9, 12)


# ── Fake store ────────────────────────────────────────────────────

class FakeStore:
    """In-memory stand-in implementing the slice of TelegramDataStore the bot uses."""

    def __init__(self):
        self.links = {}          # chat_id -> link dict
        self.codes = {}          # code -> {username, company_id, used}
        self.subs = {}           # chat_id -> set(topics)
        self.outbox = []
        self.handled = []
        self.totals = {"income": 1500.5, "expenses": 400, "income_count": 3, "expense_count": 1}
        self.bids = []
        self.cpos = []
        self.stock = []
        self.payments = None
        self.roles = {"admin": ["admin"]}

    # links / codes
    def get_link_by_chat(self, chat_id):
        l = self.links.get(chat_id)
        return l if l and l.get("is_active", True) else None

    def consume_link_code(self, code):
        c = self.codes.get((code or "").upper())
        if not c or c["used"]:
            return None
        c["used"] = True
        return {"username": c["username"], "company_id": c["company_id"]}

    def link_chat(self, chat_id, username, company_id, telegram_username="", first_name=""):
        self.links[chat_id] = dict(chat_id=chat_id, username=username, company_id=company_id,
                                   telegram_username=telegram_username, first_name=first_name,
                                   is_active=True)
        return True

    def unlink_chat(self, chat_id, username=None):
        if chat_id in self.links:
            self.links[chat_id]["is_active"] = False
            return True
        return False

    def links_for_user(self, username):
        return [l for l in self.links.values() if l["username"] == username and l["is_active"]]

    def chats_for_topic(self, company_id, topic):
        return [l for l in self.links.values()
                if l["company_id"] == company_id and l["is_active"] and topic in self.subs.get(l["chat_id"], set())]

    def user_roles(self, username):
        return self.roles.get(username, ["viewer"])

    # subscriptions
    def get_subscriptions(self, chat_id):
        return sorted(self.subs.get(chat_id, set()))

    def subscribe(self, chat_id, topic):
        self.subs.setdefault(chat_id, set()).add(topic); return True

    def unsubscribe(self, chat_id, topic):
        self.subs.setdefault(chat_id, set()).discard(topic); return True

    # outbox / log
    def enqueue(self, chat_id, text, parse_mode="HTML"):
        self.outbox.append((chat_id, text, parse_mode)); return len(self.outbox)

    def mark_handled(self, update_id, response):
        self.handled.append((update_id, response))

    # business queries
    def income_expense_totals(self, company_id, start, end):
        return dict(self.totals, start=start, end=end)

    def bids_due(self, company_id, days=7, today=None, limit=10):
        return self.bids

    def recent_cpos(self, company_id, limit=5):
        return self.cpos

    def cpo_outstanding(self, company_id):
        return {"count": len(self.cpos), "total": sum(c.get("amount", 0) for c in self.cpos)}

    def employee_count(self, company_id):
        return 12

    def low_stock(self, company_id, limit=10):
        return self.stock

    def payments_today(self, company_id, today=None):
        return self.payments


def msg(text, chat_id=555, first_name="Abebe", username="abebe_tg", chat_type="private"):
    return {"update_id": 1, "message": {
        "message_id": 10, "text": text,
        "chat": {"id": chat_id, "type": chat_type},
        "from": {"id": chat_id, "first_name": first_name, "username": username},
    }}


@pytest.fixture
def store():
    return FakeStore()


@pytest.fixture
def linked(store):
    store.link_chat(555, "admin", "default", "abebe_tg", "Abebe")
    return store


# ── Command parsing ───────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("/start", ("start", [])),
    ("/Link ABC123", ("link", ["ABC123"])),
    ("/today@EbmsBot", ("today", [])),
    ("/subscribe@EbmsBot  approvals extra ", ("subscribe", ["approvals", "extra"])),
    ("hello", ("", [])),
    ("", ("", [])),
    (None, ("", [])),
    ("  /help  ", ("help", [])),
])
def test_parse_command(text, expected):
    assert parse_command(text) == expected


# ── Webhook secret ────────────────────────────────────────────────

def test_verify_secret(monkeypatch):
    monkeypatch.delenv("TELEGRAM_WEBHOOK_SECRET", raising=False)
    assert verify_secret("anything") is False          # no server secret => reject all
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "s3cret")
    assert verify_secret("s3cret") is True
    assert verify_secret("S3CRET") is False
    assert verify_secret("") is False
    assert verify_secret(None) is False


def test_webhook_url_and_config(monkeypatch):
    monkeypatch.setenv("TELEGRAM_PUBLIC_BASE_URL", "https://ebms.devopsrain.com/")
    assert tb.webhook_url() == "https://ebms.devopsrain.com/telegram/webhook"
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert tb.is_configured() is False
    assert tb.config_status()["configured"] is False
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "123456789:AAAbbbCCCdddEEEfffGGG")
    st = tb.config_status()
    assert st["configured"] is True
    assert "AAAbbbCCC" not in st["token_hint"]           # token never shown in full


def test_unconfigured_api_call_is_noop(monkeypatch):
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    res = tb.send_message(1, "x")
    assert res["ok"] is False and res.get("not_configured")


# ── /start, /help, unknown chats ──────────────────────────────────

def test_start_unlinked_prompts_to_link(store):
    r = build_reply(msg("/start"), store, TODAY)
    assert isinstance(r, Reply) and r.chat_id == 555
    assert "/link CODE" in r.text
    assert "Hello Abebe" in r.text


def test_user_name_is_html_escaped(store):
    r = build_reply(msg("/start", first_name="<script>alert(1)</script>"), store, TODAY)
    assert "<script>" not in r.text
    assert "&lt;script&gt;" in r.text


def test_linked_commands_require_link(store):
    for cmd in ("/today", "/month", "/bids", "/cpo", "/approvals", "/stock", "/payments",
                "/subscribe approvals", "/subscriptions", "/unlink"):
        r = build_reply(msg(cmd), store, TODAY)
        assert r.text == NOT_LINKED_TEXT, cmd


def test_unknown_command_is_escaped(linked):
    r = build_reply(msg("/<b>evil</b>"), linked, TODAY)
    assert "<b>evil" not in r.text and "&lt;b&gt;evil" in r.text


def test_plain_text_private_vs_group(store):
    assert "/help" in build_reply(msg("hi there"), store, TODAY).text
    assert build_reply(msg("hi there", chat_type="group"), store, TODAY) is None


def test_help_is_bilingual(store):
    r = build_reply(msg("/help"), store, TODAY)
    assert "ገቢ / Income" in r.text and "/today" in r.text


# ── Link-code flow ────────────────────────────────────────────────

def test_link_flow(store):
    store.codes["ABC234"] = {"username": "admin", "company_id": "acme", "used": False}
    r = build_reply(msg("/link abc234"), store, TODAY)          # case-insensitive
    assert "admin" in r.text and "acme" in r.text
    link = store.get_link_by_chat(555)
    assert link["username"] == "admin" and link["company_id"] == "acme"
    assert link["telegram_username"] == "abebe_tg" and link["first_name"] == "Abebe"
    assert "daily_digest" in store.get_subscriptions(555)      # auto-subscribed
    # code is single-use
    r2 = build_reply(msg("/link ABC234", chat_id=777), store, TODAY)
    assert "invalid or expired" in r2.text
    assert store.get_link_by_chat(777) is None


def test_link_without_code_and_bad_code(store):
    assert "Usage" in build_reply(msg("/link"), store, TODAY).text
    assert "invalid" in build_reply(msg("/link ZZZZZZ"), store, TODAY).text


def test_unlink(linked):
    r = build_reply(msg("/unlink"), linked, TODAY)
    assert "unlinked" in r.text
    assert linked.get_link_by_chat(555) is None
    assert build_reply(msg("/today"), linked, TODAY).text == NOT_LINKED_TEXT


def test_start_when_linked_shows_account(linked):
    r = build_reply(msg("/start"), linked, TODAY)
    assert "admin" in r.text and "/today" in r.text


# ── Query commands ────────────────────────────────────────────────

def test_today_and_month(linked):
    r = build_reply(msg("/today"), linked, TODAY)
    assert "1,500.50 ETB" in r.text and "400.00 ETB" in r.text and "1,100.50 ETB" in r.text
    assert "2026-09-12" in r.text and "ገቢ / Income" in r.text
    r = build_reply(msg("/month"), linked, TODAY)
    assert "2026-09-01" in r.text and "2026-09-30" in r.text


def test_bids_escape_and_days_left(linked):
    linked.bids = [
        {"title": "Road <b>works</b>", "reference_number": "T/22", "organization": "ERA & co",
         "deadline_date": date(2026, 9, 12), "days_left": 0},
        {"title": "Supply", "reference_number": "", "organization": "",
         "deadline_date": date(2026, 9, 15), "days_left": 3},
    ]
    r = build_reply(msg("/bids"), linked, TODAY)
    assert "Road &lt;b&gt;works&lt;/b&gt;" in r.text and "<b>works" not in r.text
    assert "ERA &amp; co" in r.text
    assert "today" in r.text and "in 3 d" in r.text
    assert format_bids([], 7).endswith("ምንም / none")


def test_cpo_stock_payments(linked):
    linked.cpos = [{"name": "Bank <x>", "amount": 2500, "date": "2026-09-01", "bid_name": "B1", "is_returned": "false"}]
    r = build_reply(msg("/cpo"), linked, TODAY)
    assert "Bank &lt;x&gt;" in r.text and "2,500.00 ETB" in r.text and "held" in r.text
    linked.stock = [{"name": "Cement", "current_stock": 3, "threshold": 10, "unit": "bags"}]
    r = build_reply(msg("/stock"), linked, TODAY)
    assert "Cement" in r.text and "<b>3</b>" in r.text and "min 10" in r.text
    r = build_reply(msg("/payments"), linked, TODAY)
    assert "not installed" in r.text
    linked.payments = {"money_in": 100, "money_out": 40, "count": 3}
    r = build_reply(msg("/payments"), linked, TODAY)
    assert "100.00 ETB" in r.text and "Transactions" in r.text


def test_subscriptions_commands(linked):
    assert "Usage" in build_reply(msg("/subscribe nope"), linked, TODAY).text
    assert "Subscribed" in build_reply(msg("/subscribe APPROVALS"), linked, TODAY).text
    assert linked.get_subscriptions(555) == ["approvals"]
    r = build_reply(msg("/subscriptions"), linked, TODAY)
    assert "🔔 <code>approvals</code>" in r.text and "🔕 <code>payments</code>" in r.text
    assert "Unsubscribed" in build_reply(msg("/unsubscribe approvals"), linked, TODAY).text
    assert linked.get_subscriptions(555) == []


# ── Approvals bridge (module optional, unknown signature) ─────────

@pytest.fixture
def fake_approvals(monkeypatch):
    calls = []
    mod = types.ModuleType("approval_data_store")

    def pending_for(company_id, username, roles):
        return [{"id": "req-1", "title": "PO <#7>", "amount": 1200, "requested_by": "kebede"},
                {"approval_id": "req-2", "subject": "Leave"}]

    def decide(request_id, actor, decision, comment=""):     # deliberately odd parameter names
        calls.append((request_id, actor, decision, comment))
        return {"success": True, "message": "Decision recorded"}

    mod.pending_for = pending_for
    mod.decide = decide
    monkeypatch.setitem(sys.modules, "approval_data_store", mod)
    return calls


def test_approvals_without_module(linked, monkeypatch):
    monkeypatch.setitem(sys.modules, "approval_data_store", None)     # import fails
    r = build_reply(msg("/approvals"), linked, TODAY)
    assert "not installed" in r.text and r.reply_markup is None


def test_approvals_list_and_buttons(linked, fake_approvals):
    r = build_reply(msg("/approvals"), linked, TODAY)
    assert "PO &lt;#7&gt;" in r.text and "1,200.00 ETB" in r.text and "kebede" in r.text
    kb = r.reply_markup["inline_keyboard"]
    assert kb[0][0]["callback_data"] == "apr|req-1|a"
    assert kb[0][1]["callback_data"] == "apr|req-1|r"
    assert kb[1][0]["callback_data"] == "apr|req-2|a"
    assert all(len(b["callback_data"].encode()) <= 64 for row in kb for b in row)


def test_callback_decides_via_flexible_signature(linked, fake_approvals):
    update = {"update_id": 9, "callback_query": {
        "id": "cb1", "data": "apr|req-1|r",
        "message": {"chat": {"id": 555, "type": "private"}},
        "from": {"id": 555, "first_name": "Abebe"}}}
    r = build_reply(update, linked, TODAY)
    assert fake_approvals == [("req-1", "admin", "reject", "via Telegram")]
    assert r.callback_query_id == "cb1" and "Rejected" in r.text and "req-1" in r.text


def test_callback_from_unlinked_chat(store, fake_approvals):
    update = {"update_id": 9, "callback_query": {
        "id": "cb1", "data": "apr|req-1|a", "message": {"chat": {"id": 1, "type": "private"}}}}
    r = build_reply(update, store, TODAY)
    assert r.text == NOT_LINKED_TEXT and fake_approvals == []


# ── Digest formatting ─────────────────────────────────────────────

def test_format_digest():
    text = format_digest("Acme & Sons", TODAY,
                         {"income": 1000, "expenses": 250},
                         [{"title": "Bid <A>", "deadline_date": date(2026, 9, 13), "days_left": 1}],
                         [{"id": 1}, {"id": 2}], employee_count=7)
    assert "Acme &amp; Sons" in text and "2026-09-12" in text
    assert "750.00 ETB" in text
    assert "Bid &lt;A&gt;" in text and "in 1 d" in text
    assert "2 pending" in text and "/approvals" in text
    assert "7" in text and "ትናንት / Yesterday" in text
    # no approvals module => the approvals line is omitted; no bids => 'none'
    text2 = format_digest("X", TODAY, {"income": 0, "expenses": 0}, [], None)
    assert "ማጽደቅ" not in text2 and "ምንም / none" in text2


def test_format_totals_handles_missing_keys():
    out = format_totals("T", {})
    assert "0.00 ETB" in out


def test_esc():
    assert esc('<a href="x">&</a>') == "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;"
    assert esc(None) == ""


# ── handle_update glue + notify API ───────────────────────────────

def test_handle_update_never_raises_and_logs(monkeypatch, linked):
    sent = []
    monkeypatch.setattr(tb, "store", linked)
    monkeypatch.setattr(tb, "send_message", lambda cid, text, pm="HTML", rm=None: sent.append((cid, text)) or {"ok": True})
    tb.handle_update(msg("/today"))
    assert sent and sent[0][0] == 555
    assert linked.handled[-1] == (1, "sent")

    # transport failure => queued in the outbox for the retry job
    monkeypatch.setattr(tb, "send_message", lambda *a, **k: {"ok": False, "description": "timeout", "transport_error": True})
    tb.handle_update(msg("/today"))
    assert linked.outbox and linked.handled[-1] == (1, "queued")

    # an exploding handler is swallowed
    monkeypatch.setattr(tb, "build_reply", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    tb.handle_update(msg("/today"))
    assert linked.handled[-1][1].startswith("exception")


def test_notify_user_and_topic(monkeypatch, linked):
    monkeypatch.setattr(tb, "store", linked)
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    assert tb.notify_user("admin", "hi") == 0                      # unconfigured => no-op
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    assert tb.notify_user("admin", "hi") == 1
    assert tb.notify_user("nobody", "hi") == 0
    assert tb.notify_topic("default", "approvals", "new") == 0    # not subscribed
    linked.subscribe(555, "approvals")
    assert tb.notify_topic("default", "approvals", "new") == 1
    assert tb.notify_topic("default", "bogus_topic", "new") == 0
    assert linked.outbox[-1] == (555, "new", "HTML")


# ── Webhook route: secret check + dedupe + always 200 ─────────────

@pytest.fixture
def webhook_client(monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    import telegram_routes

    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "1:x")
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "topsecret")
    seen, handled = set(), []

    def log_update(update_id, chat_id, text):
        if update_id in seen:
            return False
        seen.add(update_id); return True

    monkeypatch.setattr(telegram_routes.telegram_store, "log_update", log_update)
    monkeypatch.setattr(telegram_routes.telegram_bot, "handle_update", lambda u: handled.append(u))
    app = FastAPI()
    app.include_router(telegram_routes.router)
    return TestClient(app), handled


def test_webhook_rejects_bad_secret(webhook_client):
    client, handled = webhook_client
    assert client.post("/telegram/webhook", json=msg("/start")).status_code == 403
    r = client.post("/telegram/webhook", json=msg("/start"),
                    headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"})
    assert r.status_code == 403 and handled == []


def test_webhook_accepts_dedupes_and_always_ok(webhook_client):
    client, handled = webhook_client
    h = {"X-Telegram-Bot-Api-Secret-Token": "topsecret"}
    r = client.post("/telegram/webhook", json=msg("/start"), headers=h)
    assert r.status_code == 200 and r.json()["ok"] is True
    assert len(handled) == 1
    r = client.post("/telegram/webhook", json=msg("/start"), headers=h)      # same update_id
    assert r.status_code == 200 and r.json().get("duplicate") is True
    assert len(handled) == 1
    r = client.post("/telegram/webhook", content=b"not json", headers=dict(h, **{"Content-Type": "application/json"}))
    assert r.status_code == 200 and r.json()["ok"] is True


def test_webhook_unconfigured_is_noop(monkeypatch):
    from fastapi import FastAPI
    from starlette.testclient import TestClient
    import telegram_routes
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setenv("TELEGRAM_WEBHOOK_SECRET", "topsecret")
    app = FastAPI(); app.include_router(telegram_routes.router)
    r = TestClient(app).post("/telegram/webhook", json=msg("/start"),
                             headers={"X-Telegram-Bot-Api-Secret-Token": "topsecret"})
    assert r.status_code == 200 and r.json()["ok"] is True


def test_static_routes_registered_before_params():
    """/link/generate must win over /link/{chat_id}/… and /webhook must not be shadowed."""
    import telegram_routes
    paths = [r.path for r in telegram_routes.router.routes]
    assert paths.index("/telegram/link/generate") < paths.index("/telegram/link/{chat_id}/unlink")
    assert "/telegram/webhook" in paths
    assert "/telegram/set-webhook" in paths and "/telegram/remove-webhook" in paths
    names = {r.name for r in telegram_routes.router.routes}
    assert all(n.startswith("telegram_") for n in names)
