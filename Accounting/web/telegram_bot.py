"""
Telegram Bot — Bot API client + command dispatcher.

Configuration (environment):
  TELEGRAM_BOT_TOKEN        token from @BotFather. Unset => module is a no-op.
  TELEGRAM_WEBHOOK_SECRET   random string; Telegram echoes it back in the
                            X-Telegram-Bot-Api-Secret-Token header of every
                            webhook call and telegram_routes rejects anything
                            that does not match.
  TELEGRAM_PUBLIC_BASE_URL  https://ebms.devopsrain.com — the webhook URL is
                            this + /telegram/webhook.

Layout:
  * HTTP layer      _api_call / send_message / set_webhook / ... (never raise)
  * Pure logic      parse_command, build_reply(update, store) -> Reply|None
  * Glue            handle_update(update) = build_reply + send (used by webhook)
  * Python API      notify_user(username, text), notify_topic(company_id, topic, text)

All user-supplied text that ends up in a reply goes through esc() because
replies are sent with parse_mode=HTML.
"""
from __future__ import annotations

import hmac
import html
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)

API_BASE = "https://api.telegram.org"
API_TIMEOUT = 10            # seconds — hard ceiling for any single Bot API call
WEBHOOK_PATH = "/telegram/webhook"
MAX_MESSAGE_LEN = 4096
CURRENCY = "ETB"

# The store is a module attribute so tests can swap in a fake.
try:
    from telegram_data_store import telegram_store as store, TOPICS, TOPIC_LABELS
except Exception:                                   # pragma: no cover — import-time safety
    store = None
    TOPICS = ("daily_digest", "bid_deadlines", "approvals", "payments",
              "security_alerts", "siem_critical")
    TOPIC_LABELS = {t: t for t in TOPICS}


# ── Configuration ─────────────────────────────────────────────────

def bot_token() -> str:
    return os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()


def webhook_secret() -> str:
    return os.environ.get("TELEGRAM_WEBHOOK_SECRET", "").strip()


def public_base_url() -> str:
    return os.environ.get("TELEGRAM_PUBLIC_BASE_URL", "").strip().rstrip("/")


def webhook_url() -> str:
    base = public_base_url()
    return f"{base}{WEBHOOK_PATH}" if base else ""


def is_configured() -> bool:
    return bool(bot_token())


def config_status() -> dict:
    """What the dashboard shows. Never leaks the token itself."""
    tok = bot_token()
    return {
        "configured": bool(tok),
        "token_hint": f"{tok[:4]}…{tok[-4:]}" if len(tok) > 12 else ("set" if tok else ""),
        "secret_set": bool(webhook_secret()),
        "public_base_url": public_base_url(),
        "webhook_url": webhook_url(),
    }


def verify_secret(header_value: Optional[str]) -> bool:
    """Constant-time check of X-Telegram-Bot-Api-Secret-Token.
    A missing server-side secret rejects everything — the webhook must not be
    reachable unauthenticated."""
    secret = webhook_secret()
    if not secret or not header_value:
        return False
    return hmac.compare_digest(secret, str(header_value))


# ── HTTP layer ────────────────────────────────────────────────────

def _http_post(url: str, payload: dict, timeout: int) -> dict:
    """POST JSON, return the parsed body. Raises on transport errors."""
    try:
        import requests
        resp = requests.post(url, json=payload, timeout=timeout)
        try:
            return resp.json()
        except ValueError:
            return {"ok": False, "description": f"HTTP {resp.status_code}: non-JSON reply"}
    except ImportError:
        import urllib.request
        import urllib.error
        req = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return json.loads(r.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            try:
                return json.loads(e.read().decode("utf-8"))
            except Exception:
                return {"ok": False, "description": f"HTTP {e.code}"}


def _api_call(method: str, payload: dict = None, timeout: int = API_TIMEOUT) -> dict:
    """Call a Bot API method. Always returns a dict with an 'ok' key; never raises.
    Transport failures carry 'transport_error': True so callers can decide to
    queue the message for retry."""
    token = bot_token()
    if not token:
        return {"ok": False, "description": "TELEGRAM_BOT_TOKEN not configured", "not_configured": True}
    url = f"{API_BASE}/bot{token}/{method}"
    try:
        res = _http_post(url, payload or {}, timeout)
        if not res.get("ok"):
            logger.warning("telegram %s failed: %s", method, res.get("description"))
        return res
    except Exception as e:
        logger.warning("telegram %s transport error: %s", method, e)
        return {"ok": False, "description": str(e)[:300], "transport_error": True}


def send_message(chat_id, text: str, parse_mode: str = "HTML", reply_markup: dict = None,
                 disable_web_page_preview: bool = True) -> dict:
    payload = {"chat_id": chat_id, "text": (text or "")[:MAX_MESSAGE_LEN],
               "disable_web_page_preview": disable_web_page_preview}
    if parse_mode:
        payload["parse_mode"] = parse_mode
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _api_call("sendMessage", payload)


def answer_callback_query(callback_query_id: str, text: str = None, show_alert: bool = False) -> dict:
    payload = {"callback_query_id": callback_query_id, "show_alert": show_alert}
    if text:
        payload["text"] = text[:200]
    return _api_call("answerCallbackQuery", payload)


def set_webhook() -> dict:
    url = webhook_url()
    if not url:
        return {"ok": False, "description": "TELEGRAM_PUBLIC_BASE_URL not configured"}
    if not url.lower().startswith("https://"):
        return {"ok": False, "description": "Webhook URL must be https:// (Telegram requirement)"}
    payload = {"url": url, "allowed_updates": ["message", "callback_query"],
               "drop_pending_updates": False}
    if webhook_secret():
        payload["secret_token"] = webhook_secret()
    return _api_call("setWebhook", payload)


def delete_webhook() -> dict:
    return _api_call("deleteWebhook", {"drop_pending_updates": False})


def get_webhook_info() -> dict:
    return _api_call("getWebhookInfo", {}, timeout=6)


# ── Formatting helpers (pure) ─────────────────────────────────────

def esc(value: Any) -> str:
    """HTML-escape anything user-supplied before it goes into a reply."""
    return html.escape("" if value is None else str(value), quote=True)


def fmt_money(value, currency: str = CURRENCY) -> str:
    try:
        return f"{float(value or 0):,.2f} {currency}"
    except (TypeError, ValueError):
        return f"0.00 {currency}"


def fmt_date(d) -> str:
    if isinstance(d, date):
        return d.isoformat()
    return esc(d or "-")


# Short bilingual (Amharic / English) labels for the key concepts.
L = {
    "income":     "ገቢ / Income",
    "expenses":   "ወጪ / Expenses",
    "net":        "የተጣራ / Net",
    "today":      "ዛሬ / Today",
    "month":      "ይህ ወር / This month",
    "yesterday":  "ትናንት / Yesterday",
    "bids":       "ጨረታ / Bids",
    "deadline":   "ቀነ-ገደብ / Deadline",
    "cpo":        "CPO",
    "stock":      "ክምችት / Stock",
    "approvals":  "ማጽደቅ / Approvals",
    "payments":   "ክፍያ / Payments",
    "help":       "እገዛ / Help",
    "linked":     "ተገናኝቷል / Linked",
    "approve":    "✅ አጽድቅ / Approve",
    "reject":     "❌ ውድቅ / Reject",
    "none":       "ምንም / none",
    "days":       "ቀናት / days",
    "employees":  "ሠራተኞች / Employees",
    "digest":     "ዕለታዊ ማጠቃለያ / Daily digest",
}

HELP_TEXT = (
    f"<b>EBMS Bot — {L['help']}</b>\n\n"
    "/today — " + L["income"] + " &amp; " + L["expenses"] + " (" + L["today"] + ")\n"
    "/month — " + L["month"] + "\n"
    "/bids — " + L["bids"] + ": " + L["deadline"] + " ≤ 7 " + L["days"] + "\n"
    "/cpo — " + L["cpo"] + " (recent)\n"
    "/approvals — " + L["approvals"] + "\n"
    "/stock — " + L["stock"] + " (low stock)\n"
    "/payments — " + L["payments"] + " (" + L["today"] + ")\n"
    "/subscribe &lt;topic&gt; · /unsubscribe &lt;topic&gt; · /subscriptions\n"
    "/unlink — disconnect this chat\n\n"
    "Topics: " + ", ".join(TOPICS)
)

NOT_LINKED_TEXT = (
    "👋 This chat is not linked to an EBMS account yet.\n\n"
    "1. Log in to EBMS and open <b>Telegram → Link</b>\n"
    "2. Generate a code\n"
    "3. Send it here as <code>/link CODE</code>"
)


@dataclass
class Reply:
    """What the dispatcher wants to send back. Pure data — no I/O."""
    chat_id: Any
    text: str
    reply_markup: Optional[dict] = None
    parse_mode: str = "HTML"
    callback_query_id: Optional[str] = None      # answer this callback too
    callback_text: Optional[str] = None
    extra: list = field(default_factory=list)    # additional Reply objects


# ── Parsing (pure) ────────────────────────────────────────────────

def parse_command(text: Optional[str]) -> tuple[str, list[str]]:
    """'/Link@EbmsBot ABC123 x' -> ('link', ['ABC123', 'x']). Non-commands -> ('', [])."""
    if not text:
        return "", []
    text = text.strip()
    if not text.startswith("/"):
        return "", []
    parts = text.split()
    cmd = parts[0][1:]
    if "@" in cmd:
        cmd = cmd.split("@", 1)[0]
    return cmd.lower(), parts[1:]


def _month_bounds(today: date) -> tuple[date, date]:
    start = today.replace(day=1)
    nxt = (start + timedelta(days=32)).replace(day=1)
    return start, nxt - timedelta(days=1)


# ── Optional module bridges (approvals / payments) ────────────────

def _approval_module():
    try:
        import importlib
        return importlib.import_module("approval_data_store")
    except Exception:
        return None


def _resolve_callable(mod, name: str) -> Optional[Callable]:
    """Find `name` on the module or on a store object it exposes."""
    if mod is None:
        return None
    for holder in (mod, getattr(mod, "approval_store", None), getattr(mod, "store", None)):
        fn = getattr(holder, name, None) if holder is not None else None
        if callable(fn):
            return fn
    return None


def _call_flexible(fn: Callable, candidates: dict):
    """Call fn filling parameters BY NAME from `candidates` (aliases allowed).
    Lets us talk to approval_data_store.decide() without knowing its exact
    signature. Returns fn's result, or raises TypeError when a required
    parameter cannot be satisfied."""
    sig = inspect.signature(fn)
    kwargs = {}
    for pname, p in sig.parameters.items():
        if pname == "self" or p.kind in (p.VAR_POSITIONAL, p.VAR_KEYWORD):
            continue
        if pname in candidates:
            kwargs[pname] = candidates[pname]
        elif p.default is inspect.Parameter.empty:
            raise TypeError(f"cannot satisfy parameter {pname!r} of {fn.__name__}")
    return fn(**kwargs)


def pending_approvals(company_id: str, username: str, roles: list) -> Optional[list]:
    """None => approvals module not available; [] => nothing pending."""
    fn = _resolve_callable(_approval_module(), "pending_for")
    if fn is None:
        return None
    try:
        res = fn(company_id, username, roles)
        return list(res or [])
    except Exception as e:
        logger.error("pending_for failed: %s", e)
        return []


def decide_approval(approval_id: str, decision: str, username: str, company_id: str,
                    roles: list, comment: str = "via Telegram") -> tuple[bool, str]:
    fn = _resolve_callable(_approval_module(), "decide")
    if fn is None:
        return False, "Approvals module not available"
    aliases = {
        "approval_id": approval_id, "request_id": approval_id, "item_id": approval_id,
        "id": approval_id, "record_id": approval_id,
        "decision": decision, "action": decision, "status": decision, "outcome": decision,
        "username": username, "actor": username, "decided_by": username, "user": username,
        "approver": username, "approver_username": username, "by": username,
        "comment": comment, "note": comment, "notes": comment, "reason": comment, "remarks": comment,
        "company_id": company_id, "roles": roles,
    }
    try:
        res = _call_flexible(fn, aliases)
    except Exception as e:
        logger.error("decide failed: %s", e)
        return False, "Could not record decision"
    if isinstance(res, dict):
        ok = bool(res.get("success", res.get("ok", True)))
        return ok, str(res.get("error") or res.get("message") or ("Recorded" if ok else "Rejected"))
    if isinstance(res, tuple) and len(res) >= 2:
        return bool(res[0]), str(res[1])
    return bool(res), "Recorded" if res else "Could not record decision"


def _approval_field(item: dict, *keys, default="-"):
    for k in keys:
        v = item.get(k)
        if v not in (None, ""):
            return v
    return default


def approvals_keyboard(items: list) -> Optional[dict]:
    rows = []
    for it in items[:5]:
        aid = str(_approval_field(it, "id", "approval_id", "request_id", default=""))
        if not aid:
            continue
        rows.append([
            {"text": L["approve"], "callback_data": f"apr|{aid[:50]}|a"},
            {"text": L["reject"],  "callback_data": f"apr|{aid[:50]}|r"},
        ])
    return {"inline_keyboard": rows} if rows else None


def format_approvals(items: Optional[list]) -> str:
    if items is None:
        return f"<b>{L['approvals']}</b>\nApprovals module is not installed."
    if not items:
        return f"<b>{L['approvals']}</b>\n{L['none']} — nothing waiting for you 🎉"
    lines = [f"<b>{L['approvals']}</b> ({len(items)} pending)"]
    for i, it in enumerate(items[:5], 1):
        title = esc(_approval_field(it, "title", "subject", "description", "entity_type", "type"))
        amount = _approval_field(it, "amount", "total", default=None)
        who = esc(_approval_field(it, "requested_by", "requester", "created_by", default=""))
        line = f"{i}. {title}"
        if amount is not None:
            line += f" — {fmt_money(amount)}"
        if who:
            line += f" (by {who})"
        lines.append(line)
    if len(items) > 5:
        lines.append(f"… and {len(items) - 5} more in EBMS")
    return "\n".join(lines)


# ── Message formatters (pure) ─────────────────────────────────────

def format_totals(title: str, totals: dict) -> str:
    inc, exp = float(totals.get("income", 0)), float(totals.get("expenses", 0))
    return (
        f"<b>{title}</b> ({fmt_date(totals.get('start'))} → {fmt_date(totals.get('end'))})\n"
        f"📈 {L['income']}: <b>{fmt_money(inc)}</b> ({int(totals.get('income_count', 0))})\n"
        f"📉 {L['expenses']}: <b>{fmt_money(exp)}</b> ({int(totals.get('expense_count', 0))})\n"
        f"➖ {L['net']}: <b>{fmt_money(inc - exp)}</b>"
    )


def format_bids(bids: list, days: int) -> str:
    head = f"<b>{L['bids']}</b> — {L['deadline']} ≤ {days} {L['days']}"
    if not bids:
        return f"{head}\n{L['none']}"
    lines = [head]
    for b in bids:
        left = b.get("days_left", 0)
        when = "today ⚠️" if left == 0 else f"in {left} d"
        ref = esc(b.get("reference_number") or "")
        lines.append(
            f"• <b>{esc(b.get('title') or '(untitled)')}</b>"
            + (f" [{ref}]" if ref else "")
            + f" — {fmt_date(b.get('deadline_date'))} ({when})"
            + (f"\n  {esc(b.get('organization'))}" if b.get("organization") else "")
        )
    return "\n".join(lines)


def format_cpos(cpos: list, outstanding: dict = None) -> str:
    lines = [f"<b>{L['cpo']}</b> — recent"]
    if outstanding:
        lines.append(f"Outstanding: {outstanding.get('count', 0)} · {fmt_money(outstanding.get('total', 0))}")
    if not cpos:
        lines.append(L["none"])
    for c in cpos:
        returned = str(c.get("is_returned", "")).lower() == "true"
        lines.append(
            f"• {esc(c.get('name') or '-')} — {fmt_money(c.get('amount'))} "
            f"({esc(c.get('date') or '-')}) {'↩️ returned' if returned else '⏳ held'}"
            + (f"\n  {esc(c.get('bid_name'))}" if c.get("bid_name") else "")
        )
    return "\n".join(lines)


def format_stock(items: list) -> str:
    head = f"<b>{L['stock']}</b> — low stock"
    if not items:
        return f"{head}\n{L['none']} — all items above minimum ✅"
    lines = [head]
    for it in items:
        lines.append(
            f"• {esc(it.get('name') or it.get('sku') or '-')}: "
            f"<b>{float(it.get('current_stock') or 0):g}</b> / min {float(it.get('threshold') or 0):g}"
            + (f" {esc(it.get('unit'))}" if it.get("unit") else "")
        )
    return "\n".join(lines)


def format_payments(summary: Optional[dict]) -> str:
    head = f"<b>{L['payments']}</b> — {L['today']}"
    if summary is None:
        return f"{head}\nPayments module is not installed."
    if not summary:
        return f"{head}\n{L['none']}"
    lines = [head]
    shown = False
    for key, label in (("money_in", "In"), ("total_in", "In"), ("inflow", "In"),
                       ("money_out", "Out"), ("total_out", "Out"), ("outflow", "Out"),
                       ("count", "Transactions"), ("pending", "Pending"), ("failed", "Failed")):
        if key in summary and summary[key] is not None:
            v = summary[key]
            lines.append(f"• {label}: <b>{fmt_money(v) if 'count' not in key and key not in ('pending', 'failed') else esc(v)}</b>")
            shown = True
    if not shown:
        for k, v in list(summary.items())[:8]:
            if isinstance(v, (int, float, str)):
                lines.append(f"• {esc(k)}: <b>{esc(v)}</b>")
    return "\n".join(lines)


def format_subscriptions(topics: list) -> str:
    lines = ["<b>Subscriptions</b>"]
    for t in TOPICS:
        lines.append(f"{'🔔' if t in topics else '🔕'} <code>{t}</code> — {esc(TOPIC_LABELS.get(t, t))}")
    lines.append("\nUse /subscribe &lt;topic&gt; or /unsubscribe &lt;topic&gt;")
    return "\n".join(lines)


def format_digest(company_name: str, day: date, totals: dict, bids: list,
                  approvals: Optional[list], employee_count: int = None) -> str:
    """Morning digest text (pure)."""
    inc, exp = float(totals.get("income", 0)), float(totals.get("expenses", 0))
    lines = [
        f"☀️ <b>{L['digest']}</b> — {esc(company_name)} · {day.isoformat()}",
        "",
        f"<b>{L['yesterday']}</b>",
        f"📈 {L['income']}: {fmt_money(inc)}",
        f"📉 {L['expenses']}: {fmt_money(exp)}",
        f"➖ {L['net']}: {fmt_money(inc - exp)}",
        "",
        f"<b>{L['bids']}</b> — {L['deadline']} ≤ 3 {L['days']}",
    ]
    if bids:
        for b in bids[:5]:
            left = b.get("days_left", 0)
            lines.append(f"• {esc(b.get('title') or '(untitled)')} — "
                         f"{fmt_date(b.get('deadline_date'))} ({'today ⚠️' if left == 0 else f'in {left} d'})")
    else:
        lines.append(L["none"])
    lines.append("")
    if approvals is None:
        pass
    elif approvals:
        lines.append(f"<b>{L['approvals']}</b>: {len(approvals)} pending — send /approvals")
    else:
        lines.append(f"<b>{L['approvals']}</b>: {L['none']}")
    if employee_count is not None:
        lines.append(f"👥 {L['employees']}: {employee_count}")
    return "\n".join(lines).rstrip()


# ── Dispatcher (pure: takes a store, returns Reply) ───────────────

def _from(msg: dict) -> dict:
    return msg.get("from") or {}


def _cmd_start(ctx) -> Reply:
    if ctx["link"]:
        return Reply(ctx["chat_id"],
                     f"✅ {L['linked']}: <b>{esc(ctx['link']['username'])}</b> "
                     f"({esc(ctx['link']['company_id'])})\n\n{HELP_TEXT}")
    return Reply(ctx["chat_id"], f"Hello {esc(ctx['first_name']) or 'there'}!\n\n{NOT_LINKED_TEXT}")


def _cmd_link(ctx) -> Reply:
    args = ctx["args"]
    if not args:
        return Reply(ctx["chat_id"], "Usage: <code>/link CODE</code> — get the code from EBMS → Telegram → Link.")
    info = ctx["store"].consume_link_code(args[0])
    if not info:
        return Reply(ctx["chat_id"], "❌ Code invalid or expired. Generate a new one in EBMS → Telegram → Link.")
    ok = ctx["store"].link_chat(ctx["chat_id"], info["username"], info.get("company_id") or "default",
                                ctx["tg_username"], ctx["first_name"])
    if not ok:
        return Reply(ctx["chat_id"], "❌ Could not save the link. Please try again.")
    ctx["store"].subscribe(ctx["chat_id"], "daily_digest")
    return Reply(ctx["chat_id"],
                 f"✅ {L['linked']}: this chat is now connected to <b>{esc(info['username'])}</b> "
                 f"({esc(info.get('company_id') or 'default')}).\n"
                 f"Subscribed to <code>daily_digest</code>. Send /help for commands.")


def _cmd_unlink(ctx) -> Reply:
    ctx["store"].unlink_chat(ctx["chat_id"])
    return Reply(ctx["chat_id"], "🔓 This chat has been unlinked from EBMS. Send /link CODE to connect again.")


def _cmd_help(ctx) -> Reply:
    return Reply(ctx["chat_id"], HELP_TEXT)


def _cmd_today(ctx) -> Reply:
    t = ctx["today"]
    return Reply(ctx["chat_id"], format_totals(L["today"], ctx["store"].income_expense_totals(ctx["company_id"], t, t)))


def _cmd_month(ctx) -> Reply:
    start, end = _month_bounds(ctx["today"])
    return Reply(ctx["chat_id"], format_totals(L["month"], ctx["store"].income_expense_totals(ctx["company_id"], start, end)))


def _cmd_bids(ctx) -> Reply:
    return Reply(ctx["chat_id"], format_bids(ctx["store"].bids_due(ctx["company_id"], 7, ctx["today"]), 7))


def _cmd_cpo(ctx) -> Reply:
    s = ctx["store"]
    return Reply(ctx["chat_id"], format_cpos(s.recent_cpos(ctx["company_id"], 5), s.cpo_outstanding(ctx["company_id"])))


def _cmd_approvals(ctx) -> Reply:
    items = pending_approvals(ctx["company_id"], ctx["username"], ctx["roles"])
    return Reply(ctx["chat_id"], format_approvals(items), reply_markup=approvals_keyboard(items or []))


def _cmd_stock(ctx) -> Reply:
    return Reply(ctx["chat_id"], format_stock(ctx["store"].low_stock(ctx["company_id"], 10)))


def _cmd_payments(ctx) -> Reply:
    return Reply(ctx["chat_id"], format_payments(ctx["store"].payments_today(ctx["company_id"], ctx["today"])))


def _cmd_subscribe(ctx) -> Reply:
    topic = (ctx["args"][0].lower() if ctx["args"] else "")
    if topic not in TOPICS:
        return Reply(ctx["chat_id"], "Usage: <code>/subscribe &lt;topic&gt;</code>\nTopics: "
                                     + ", ".join(f"<code>{t}</code>" for t in TOPICS))
    ctx["store"].subscribe(ctx["chat_id"], topic)
    return Reply(ctx["chat_id"], f"🔔 Subscribed to <code>{topic}</code>.")


def _cmd_unsubscribe(ctx) -> Reply:
    topic = (ctx["args"][0].lower() if ctx["args"] else "")
    if topic not in TOPICS:
        return Reply(ctx["chat_id"], "Usage: <code>/unsubscribe &lt;topic&gt;</code>")
    ctx["store"].unsubscribe(ctx["chat_id"], topic)
    return Reply(ctx["chat_id"], f"🔕 Unsubscribed from <code>{topic}</code>.")


def _cmd_subscriptions(ctx) -> Reply:
    return Reply(ctx["chat_id"], format_subscriptions(ctx["store"].get_subscriptions(ctx["chat_id"])))


# Commands usable before linking.
PUBLIC_COMMANDS = {"start": _cmd_start, "link": _cmd_link, "help": _cmd_help}
# Commands that require a linked chat.
LINKED_COMMANDS = {
    "unlink": _cmd_unlink, "today": _cmd_today, "month": _cmd_month, "bids": _cmd_bids,
    "cpo": _cmd_cpo, "approvals": _cmd_approvals, "stock": _cmd_stock, "payments": _cmd_payments,
    "subscribe": _cmd_subscribe, "unsubscribe": _cmd_unsubscribe, "subscriptions": _cmd_subscriptions,
}


def _context(chat_id, sender: dict, link: Optional[dict], args: list, st, today: date) -> dict:
    return {
        "chat_id": chat_id, "args": args, "store": st, "today": today, "link": link,
        "first_name": (sender.get("first_name") or "")[:64],
        "tg_username": sender.get("username") or "",
        "username": link["username"] if link else "",
        "company_id": (link.get("company_id") or "default") if link else "default",
        "roles": st.user_roles(link["username"]) if link else [],
    }


def build_reply(update: dict, st=None, today: date = None) -> Optional[Reply]:
    """Pure dispatcher: Telegram update -> Reply (or None when nothing to say)."""
    st = st or store
    today = today or date.today()
    if st is None:
        return None

    cb = update.get("callback_query")
    if cb:
        return _handle_callback(cb, st, today)

    msg = update.get("message") or update.get("edited_message")
    if not msg:
        return None
    chat = msg.get("chat") or {}
    chat_id = chat.get("id")
    if chat_id is None:
        return None
    text = msg.get("text") or ""
    cmd, args = parse_command(text)
    if not cmd:
        # Plain text in a private chat: nudge toward /help; stay silent in groups.
        if chat.get("type") == "private":
            return Reply(chat_id, f"Send /help to see what I can do. ({L['help']})")
        return None

    link = st.get_link_by_chat(chat_id)
    ctx = _context(chat_id, _from(msg), link, args, st, today)

    if cmd in PUBLIC_COMMANDS:
        return PUBLIC_COMMANDS[cmd](ctx)
    if cmd in LINKED_COMMANDS:
        if not link:
            return Reply(chat_id, NOT_LINKED_TEXT)
        return LINKED_COMMANDS[cmd](ctx)
    return Reply(chat_id, f"Unknown command <code>/{esc(cmd)[:32]}</code>. Send /help.")


def _handle_callback(cb: dict, st, today: date) -> Optional[Reply]:
    cb_id = cb.get("id")
    msg = cb.get("message") or {}
    chat_id = (msg.get("chat") or {}).get("id")
    data = cb.get("data") or ""
    if chat_id is None:
        return None
    link = st.get_link_by_chat(chat_id)
    if not link:
        return Reply(chat_id, NOT_LINKED_TEXT, callback_query_id=cb_id, callback_text="Not linked")
    parts = data.split("|")
    if len(parts) == 3 and parts[0] == "apr":
        _, aid, action = parts
        # approval_data_store.ACTIONS uses the imperative form ("approve"/"reject").
        decision = "approve" if action == "a" else "reject"
        roles = st.user_roles(link["username"])
        ok, message = decide_approval(aid, decision, link["username"], link.get("company_id") or "default", roles)
        icon = "✅" if ok else "⚠️"
        label = "Approved" if action == "a" else "Rejected"
        return Reply(chat_id,
                     f"{icon} {esc(label)} <code>{esc(aid)}</code> — {esc(message)}",
                     callback_query_id=cb_id, callback_text=message[:60])
    return Reply(chat_id, "Unknown action.", callback_query_id=cb_id, callback_text="Unknown action")


def _deliver(reply: Reply) -> str:
    """Send a Reply; on transport failure queue it in the outbox. Returns a
    short status string for the updates log."""
    if reply.callback_query_id:
        answer_callback_query(reply.callback_query_id, reply.callback_text)
    res = send_message(reply.chat_id, reply.text, reply.parse_mode, reply.reply_markup)
    status = "sent" if res.get("ok") else f"error: {res.get('description', '')[:120]}"
    if not res.get("ok") and res.get("transport_error") and store is not None:
        store.enqueue(reply.chat_id, reply.text, reply.parse_mode)
        status = "queued"
    for extra in reply.extra:
        _deliver(extra)
    return status


def handle_update(update: dict) -> None:
    """Webhook entry point: dispatch + send. Never raises."""
    update_id = update.get("update_id")
    try:
        reply = build_reply(update)
        status = _deliver(reply) if reply else "ignored"
    except Exception as e:
        logger.exception("handle_update failed: %s", e)
        status = f"exception: {str(e)[:120]}"
    if store is not None:
        try:
            store.mark_handled(update_id, status)
        except Exception:
            pass


# ── Python API for other modules ──────────────────────────────────

def notify_user(username: str, text: str, parse_mode: str = "HTML") -> int:
    """Queue `text` for every Telegram chat linked to an EBMS user.
    Returns the number of messages queued (0 when unconfigured / unlinked)."""
    if store is None or not is_configured() or not text:
        return 0
    n = 0
    for link in store.links_for_user(username):
        if store.enqueue(link["chat_id"], text, parse_mode):
            n += 1
    return n


def notify_topic(company_id: str, topic: str, text: str, parse_mode: str = "HTML") -> int:
    """Queue `text` for every chat in `company_id` subscribed to `topic`."""
    if store is None or not is_configured() or not text or topic not in TOPICS:
        return 0
    n = 0
    for link in store.chats_for_topic(company_id or "default", topic):
        if store.enqueue(link["chat_id"], text, parse_mode):
            n += 1
    return n
