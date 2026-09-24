"""
Commercial module — pure business logic (no I/O, no database).

Everything here is deterministic and unit-tested in
``tests/test_commercial_logic.py``:

* money helpers (``D``, ``q2``) — Decimal, quantised to 2 dp, ROUND_HALF_UP
* pricing: price-list tiers, discount rules, line and document totals with VAT
* credit-limit check (block / warn / ok)
* the three-approver Sales Order state machine
* sales commission
* forecasting (3/6-month moving average, linear trend)
* order-fulfilment and on-time-delivery ratios
* gapless document number formatting
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP, InvalidOperation
from typing import Any, Dict, Iterable, List, Optional, Sequence

VAT_RATE = Decimal("0.15")
CURRENCY = "ETB"
PERIOD_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# ── document prefixes (gapless per company + year) ───────────────
DOC_PREFIXES = {
    "proforma": "PFI", "sales_order": "SO", "mo_request": "MOR", "delivery_instruction": "DI",
    "dispatch": "DN", "invoice": "INV", "receipt": "RCT", "return": "RN", "credit_note": "CN",
}

SO_STATUSES = ("draft", "pending_approval", "approved", "rejected", "in_production", "ready",
               "partially_delivered", "delivered", "invoiced", "closed", "cancelled")
PROFORMA_STATUSES = ("draft", "sent", "accepted", "expired", "converted")
INVOICE_STATUSES = ("issued", "partially_paid", "paid", "overdue", "cancelled")
DISPATCH_STATUSES = ("planned", "dispatched", "delivered", "accepted", "rejected")
RETURN_STATUSES = ("draft", "approved", "credited", "rejected")
LEAD_STATUSES = ("new", "contacted", "qualified", "won", "lost")
CAMPAIGN_STATUSES = ("planned", "active", "paused", "completed", "cancelled")
PAYMENT_METHODS = ("cash", "bank", "telebirr", "cbebirr", "mpesa", "cheque")
DISCOUNT_KINDS = ("percent", "amount")
DISCOUNT_APPLIES = ("customer", "segment", "product", "order")
FORECAST_METHODS = ("moving_avg", "linear", "manual")
SEGMENTS = ("contractor", "distributor", "government", "utility", "industrial", "retail", "export", "other")

# Legal status transitions for a sales order (manual actions). Automatic
# transitions (delivery / invoicing) are driven by quantities in the store.
SO_TRANSITIONS = {
    "draft": {"pending_approval", "cancelled"},
    "pending_approval": {"approved", "rejected", "cancelled"},
    "rejected": {"draft", "cancelled"},
    "approved": {"in_production", "ready", "partially_delivered", "delivered", "cancelled"},
    "in_production": {"ready", "partially_delivered", "delivered", "cancelled"},
    "ready": {"partially_delivered", "delivered", "cancelled"},
    "partially_delivered": {"delivered", "invoiced"},
    "delivered": {"invoiced", "closed"},
    "invoiced": {"closed"},
    "closed": set(),
    "cancelled": set(),
}


# ── money ─────────────────────────────────────────────────────────
def D(value: Any, default: str = "0") -> Decimal:
    """Coerce anything form-ish to Decimal; '' / None / garbage → default."""
    if isinstance(value, Decimal):
        return value
    if value in (None, ""):
        return Decimal(default)
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return Decimal(default)


def q2(value: Any) -> Decimal:
    return D(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def q3(value: Any) -> Decimal:
    return D(value).quantize(Decimal("0.001"), rounding=ROUND_HALF_UP)


def opt(value: Any):
    """'' → None for DATE / NUMERIC columns."""
    if value is None:
        return None
    if isinstance(value, str) and value.strip() == "":
        return None
    return value


def to_int(value: Any, default: int = 0) -> int:
    try:
        return int(D(value, str(default)))
    except (InvalidOperation, ValueError):
        return default


def as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "y")


def format_doc_no(prefix: str, year: int, n: int, pad: int = 6) -> str:
    """PFI-2026-000001 style number."""
    return f"{prefix}-{int(year)}-{int(n):0{pad}d}"


# ── pricing ───────────────────────────────────────────────────────
def unit_price_for(item_code: str, qty: Any, price_list_items: Iterable[dict], list_price: Any = 0) -> Decimal:
    """Pick the best (largest satisfied min_qty) tier for ``item_code``;
    fall back to the catalogue list price."""
    qty = D(qty)
    best = None
    for it in price_list_items or ():
        if (it.get("item_code") or "") != item_code:
            continue
        min_qty = D(it.get("min_qty") or 0)
        if qty >= min_qty and (best is None or min_qty > D(best.get("min_qty") or 0)):
            best = it
    if best is not None:
        return q2(best.get("unit_price"))
    return q2(list_price)


def discount_applies(rule: dict, *, customer_id: str = "", segment: str = "", item_code: str = "",
                     order_value: Any = 0, on: Optional[date] = None) -> bool:
    on = on or date.today()
    vf, vt = rule.get("valid_from"), rule.get("valid_to")
    if vf and _as_date(vf) and on < _as_date(vf):
        return False
    if vt and _as_date(vt) and on > _as_date(vt):
        return False
    if D(order_value) < D(rule.get("min_order_value") or 0):
        return False
    applies, target = rule.get("applies_to") or "order", (rule.get("target") or "").strip()
    if applies == "customer":
        return bool(target) and target == (customer_id or "")
    if applies == "segment":
        return bool(target) and target.lower() == (segment or "").lower()
    if applies == "product":
        return bool(target) and target == (item_code or "")
    return applies == "order"


def discount_amount(rule: dict, base: Any) -> Decimal:
    base = D(base)
    if (rule.get("kind") or "percent") == "percent":
        return q2(base * D(rule.get("value")) / Decimal(100))
    return q2(min(base, D(rule.get("value"))))


def best_discount(rules: Iterable[dict], base: Any, **ctx) -> Optional[dict]:
    """The single applicable rule giving the largest saving (discounts don't stack)."""
    best, best_amt = None, Decimal(0)
    for r in rules or ():
        if not discount_applies(r, order_value=base, **ctx):
            continue
        amt = discount_amount(r, base)
        if amt > best_amt:
            best, best_amt = r, amt
    if best is None:
        return None
    return {"rule": best, "amount": best_amt, "name": best.get("name") or ""}


def line_total(quantity: Any, unit_price: Any, discount: Any = 0) -> Decimal:
    """Net line value: qty × price − discount (absolute, never below zero)."""
    gross = D(quantity) * D(unit_price)
    net = gross - D(discount)
    return q2(net if net > 0 else 0)


def normalize_lines(lines: Iterable[dict]) -> List[dict]:
    """Drop blank rows, coerce numerics, compute line_total."""
    out = []
    for raw in lines or ():
        code = (raw.get("item_code") or "").strip()
        desc = (raw.get("description") or "").strip()
        qty = D(raw.get("quantity"))
        if not (code or desc) or qty <= 0:
            continue
        ln = dict(raw)
        ln.update(item_code=code, description=desc, quantity=q3(qty),
                  unit_price=q2(raw.get("unit_price")), discount=q2(raw.get("discount") or 0))
        ln["line_total"] = line_total(ln["quantity"], ln["unit_price"], ln["discount"])
        out.append(ln)
    return out


def document_totals(lines: Iterable[dict], vat_rate: Any = VAT_RATE, order_discount: Any = 0) -> Dict[str, Decimal]:
    """Subtotal (gross of line discounts) − discounts, VAT on the net, grand total."""
    gross = Decimal(0)
    line_disc = Decimal(0)
    for ln in lines or ():
        gross += D(ln.get("quantity")) * D(ln.get("unit_price"))
        line_disc += D(ln.get("discount") or 0)
    order_disc = D(order_discount)
    discount_total = q2(line_disc + order_disc)
    net = gross - line_disc - order_disc
    if net < 0:
        net = Decimal(0)
    vat_total = q2(net * D(vat_rate))
    return {"subtotal": q2(gross), "discount_total": discount_total, "net": q2(net),
            "vat_total": vat_total, "grand_total": q2(net + vat_total)}


# ── credit ────────────────────────────────────────────────────────
def credit_check(credit_limit: Any, balance: Any, new_amount: Any, *, allow_over: bool = False,
                 credit_sale: bool = True) -> dict:
    """Return {ok, level, exposure, exceeds_by, message}.

    level: 'ok' | 'warn' (over limit but allowed) | 'block'.
    A zero/None credit limit means "no credit facility": any credit sale is
    over the limit. Cash sales are always ok."""
    limit, bal, amt = D(credit_limit), D(balance), D(new_amount)
    exposure = q2(bal + amt)
    if not credit_sale:
        return {"ok": True, "level": "ok", "exposure": exposure, "exceeds_by": Decimal("0.00"),
                "message": "Cash sale — no credit exposure"}
    if exposure <= limit:
        return {"ok": True, "level": "ok", "exposure": exposure, "exceeds_by": Decimal("0.00"),
                "message": f"Within credit limit ({q2(limit - exposure):,} remaining)"}
    over = q2(exposure - limit)
    if allow_over:
        return {"ok": True, "level": "warn", "exposure": exposure, "exceeds_by": over,
                "message": f"Credit limit exceeded by {over:,} — manager override in effect"}
    return {"ok": False, "level": "block", "exposure": exposure, "exceeds_by": over,
            "message": f"Credit limit exceeded by {over:,} — order blocked"}


def customer_balance_from(invoiced: Any, paid: Any, credited: Any = 0) -> Decimal:
    return q2(D(invoiced) - D(paid) - D(credited))


# ── three-approver state machine ─────────────────────────────────
DEFAULT_APPROVERS = [
    {"label": "Sales Manager", "username_or_role": "manager"},
    {"label": "Finance Manager", "username_or_role": "manager"},
    {"label": "General Manager", "username_or_role": "admin"},
]


def normalize_approvers(raw: Any) -> List[dict]:
    """Settings may hold a JSON list, a list of dicts or nothing → exactly 3 entries."""
    items: List[dict] = []
    if isinstance(raw, str):
        import json
        try:
            raw = json.loads(raw) if raw.strip() else []
        except Exception:
            raw = []
    for it in (raw or []):
        if isinstance(it, dict):
            items.append({"label": (it.get("label") or "").strip() or f"Approver {len(items) + 1}",
                          "username_or_role": (it.get("username_or_role") or it.get("user") or "").strip()})
        elif isinstance(it, str) and it.strip():
            items.append({"label": f"Approver {len(items) + 1}", "username_or_role": it.strip()})
    items = items[:3]
    while len(items) < 3:
        items.append(dict(DEFAULT_APPROVERS[len(items)]))
    return items


def approval_state(approvals: Sequence[dict]) -> str:
    """'approved' when all three approved, 'rejected' on any rejection,
    otherwise 'pending_approval'."""
    rows = list(approvals or [])
    if any((r.get("decision") or "") == "rejected" for r in rows):
        return "rejected"
    decided = [r for r in rows if (r.get("decision") or "") == "approved"]
    if len(rows) >= 3 and len(decided) >= 3 and all((r.get("decision") == "approved") for r in rows[:3]):
        return "approved"
    return "pending_approval"


def next_approval_seq(approvals: Sequence[dict]) -> Optional[int]:
    """Approvals are sequential: the lowest seq still pending."""
    pending = sorted(int(r.get("seq") or 0) for r in approvals or [] if not (r.get("decision") or ""))
    return pending[0] if pending else None


def can_approve(step: dict, username: str, privilege_level: str = "viewer", *, requested_by: str = "",
                allow_self: bool = False) -> bool:
    """A step may be decided by the named user, or by anyone holding the named
    role (manager/admin/super_admin) when the entry is a role. Requesters may
    not approve their own order unless allowed."""
    if not username:
        return False
    if not allow_self and requested_by and requested_by == username:
        return False
    target = (step.get("username_or_role") or step.get("approver_role") or "").strip()
    if not target:
        return privilege_level in ("manager", "admin", "super_admin")
    if target == username:
        return True
    levels = {"viewer": 0, "data_entry": 1, "operator": 2, "manager": 3, "admin": 4, "super_admin": 5}
    if target in levels:
        return levels.get(privilege_level, 0) >= levels[target]
    return False


def can_transition(current: str, new: str) -> bool:
    return new in SO_TRANSITIONS.get(current or "draft", set())


def derive_so_status(current: str, lines: Sequence[dict]) -> str:
    """After a dispatch/invoice: work out delivered / partially_delivered / invoiced."""
    if current in ("draft", "pending_approval", "rejected", "cancelled", "closed"):
        return current
    ordered = sum((D(l.get("quantity")) for l in lines), Decimal(0))
    delivered = sum((D(l.get("delivered_qty")) for l in lines), Decimal(0))
    invoiced = sum((D(l.get("invoiced_qty")) for l in lines), Decimal(0))
    if ordered <= 0:
        return current
    if invoiced >= ordered:
        return "invoiced"
    if delivered >= ordered:
        return "delivered"
    if delivered > 0:
        return "partially_delivered"
    return current


# ── invoices / payments ──────────────────────────────────────────
def invoice_status_after(total: Any, paid: Any, due_date: Any = None, today: Optional[date] = None,
                         current: str = "issued") -> str:
    if current == "cancelled":
        return current
    total, paid = D(total), D(paid)
    if total > 0 and paid >= total:
        return "paid"
    today = today or date.today()
    dd = _as_date(due_date)
    if dd and dd < today:
        return "overdue"
    return "partially_paid" if paid > 0 else "issued"


def due_date_for(invoice_date: Any, credit_terms_days: Any) -> Optional[date]:
    d = _as_date(invoice_date)
    if d is None:
        return None
    return d + timedelta(days=max(0, to_int(credit_terms_days)))


def days_overdue(due_date: Any, today: Optional[date] = None) -> int:
    dd = _as_date(due_date)
    if not dd:
        return 0
    return max(0, ((today or date.today()) - dd).days)


# ── commissions ──────────────────────────────────────────────────
def commission(base_amount: Any, pct: Any) -> Decimal:
    return q2(D(base_amount) * D(pct) / Decimal(100))


def commission_base(invoice: dict) -> Decimal:
    """Commission is earned on the net (VAT-exclusive) invoice value."""
    net = D(invoice.get("subtotal")) - D(invoice.get("discount_total") or 0)
    return q2(net if net > 0 else 0)


# ── forecasting ──────────────────────────────────────────────────
def moving_average(values: Sequence[Any], window: int = 3) -> Decimal:
    vals = [D(v) for v in values][-window:]
    if not vals:
        return Decimal("0.00")
    return q2(sum(vals) / Decimal(len(vals)))


def linear_trend(values: Sequence[Any]) -> Decimal:
    """Least-squares fit over the series; returns the next point (>= 0)."""
    ys = [D(v) for v in values]
    n = len(ys)
    if n == 0:
        return Decimal("0.00")
    if n == 1:
        return q2(ys[0])
    xs = [Decimal(i) for i in range(n)]
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return q2(my)
    slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
    nxt = my + slope * (Decimal(n) - mx)
    return q2(nxt if nxt > 0 else 0)


def forecast_next(history: Sequence[Any], method: str = "moving_avg", window: int = 3) -> Decimal:
    if method == "linear":
        return linear_trend(history)
    if method == "moving_avg6":
        return moving_average(history, 6)
    return moving_average(history, window)


def month_sequence(end_period: str, months: int) -> List[str]:
    """['2026-04', ..., end_period] — the last ``months`` periods inclusive."""
    y, m = int(end_period[:4]), int(end_period[5:7])
    out = []
    for _ in range(months):
        out.append(f"{y:04d}-{m:02d}")
        m -= 1
        if m == 0:
            y, m = y - 1, 12
    return list(reversed(out))


def next_period(period: str) -> str:
    y, m = int(period[:4]), int(period[5:7])
    m += 1
    if m == 13:
        y, m = y + 1, 1
    return f"{y:04d}-{m:02d}"


def period_of(d: Any) -> str:
    dd = _as_date(d) or date.today()
    return f"{dd.year:04d}-{dd.month:02d}"


def build_forecast(history_by_key: Dict[str, Dict[str, Any]], target_period: str, months: int = 6) -> List[dict]:
    """history_by_key: {item_code: {period: qty}} → one forecast row per key
    using 3- and 6-month moving averages and a linear trend; ``forecast_qty``
    is the average of the three estimators (robust to a single spike)."""
    prev = month_sequence(target_period, months + 1)[:-1]   # the months before target
    rows = []
    for key, series in (history_by_key or {}).items():
        vals = [D(series.get(p, 0)) for p in prev]
        if not any(v > 0 for v in vals):
            continue
        ma3, ma6, lin = moving_average(vals, 3), moving_average(vals, 6), linear_trend(vals)
        qty = q2((ma3 + ma6 + lin) / Decimal(3))
        rows.append({"key": key, "period": target_period, "ma3": ma3, "ma6": ma6, "linear": lin,
                     "forecast_qty": qty, "history": dict(zip(prev, vals))})
    return rows


# ── fulfilment / KPIs ────────────────────────────────────────────
def fulfilment(ordered: Any, delivered: Any, invoiced: Any = 0) -> dict:
    o, d, i = D(ordered), D(delivered), D(invoiced)
    pct = (d / o * 100) if o > 0 else Decimal(0)
    inv_pct = (i / o * 100) if o > 0 else Decimal(0)
    return {"ordered": q3(o), "delivered": q3(d), "invoiced": q3(i), "backlog": q3(max(o - d, Decimal(0))),
            "delivered_pct": q2(min(pct, Decimal(100))), "invoiced_pct": q2(min(inv_pct, Decimal(100)))}


def on_time_pct(deliveries: Iterable[dict]) -> Decimal:
    """Share of deliveries whose delivered_at <= required_date."""
    total = on_time = 0
    for d in deliveries or ():
        req = _as_date(d.get("required_date"))
        done = _as_date(d.get("delivered_at"))
        if not req or not done:
            continue
        total += 1
        if done <= req:
            on_time += 1
    return q2(Decimal(on_time) / Decimal(total) * 100) if total else Decimal("0.00")


def campaign_roi(spent: Any, revenue: Any) -> Optional[Decimal]:
    s = D(spent)
    if s <= 0:
        return None
    return q2((D(revenue) - s) / s * 100)


def conversion_rate(leads: Any, conversions: Any) -> Decimal:
    l = D(leads)
    return q2(D(conversions) / l * 100) if l > 0 else Decimal("0.00")


def retention_split(customers_periods: Dict[str, Sequence[str]], period: str) -> dict:
    """customers_periods: {customer_id: [periods with an invoice]} → counts of
    new (first invoice in ``period``) vs returning customers for that period."""
    new = returning = 0
    for _cid, periods in (customers_periods or {}).items():
        ps = sorted(set(periods))
        if period not in ps:
            continue
        if ps[0] == period:
            new += 1
        else:
            returning += 1
    return {"period": period, "new": new, "returning": returning, "active": new + returning}


# ── misc ─────────────────────────────────────────────────────────
def _as_date(v: Any) -> Optional[date]:
    if v in (None, ""):
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


as_date = _as_date

__all__ = [n for n in dir() if not n.startswith("_") or n in ("_as_date",)]
