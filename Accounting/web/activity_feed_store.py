"""
Management Overview — Activity Feed Store.

Aggregates the most recent changes across all business modules into one
normalized, chronological feed ("Bid 'X' added", "Letter REF-0031 uploaded",
"CPO for payee Y — ETB 12,000", ...).

Each item is a dict:
    {module, icon, title, detail, actor, ts (datetime), link}

Design notes:
 - Every source is fetched inside its own try/except: a missing table or
   column (partially-migrated deployment) must never break the feed — the
   source is simply skipped and logged at DEBUG level.
 - Timestamps come back as datetime, date, or ISO/plain text depending on
   the table (several legacy tables store TEXT). `_to_dt()` normalizes
   defensively; items with unparseable timestamps are skipped.
 - Letters live in a JSON file store (letter_data_store) whose records
   carry a company_id. Choice: a letter is included when its company_id
   matches the requested company OR is "default" (shared/company-agnostic
   letters are shown everywhere).
"""

import logging
from datetime import date, datetime, time
from typing import Optional

from db import fetchall

logger = logging.getLogger(__name__)

# Module metadata: key -> (label, bootstrap-icon, accent color)
MODULES = [
    {"key": "bids",        "label": "Bids",         "icon": "bi-megaphone",             "color": "#0d6efd"},
    {"key": "cpo",         "label": "CPO",          "icon": "bi-credit-card-2-front",   "color": "#6610f2"},
    {"key": "letters",     "label": "Letters",      "icon": "bi-envelope-paper",        "color": "#d63384"},
    {"key": "vat_income",  "label": "Income",       "icon": "bi-graph-up-arrow",        "color": "#198754"},
    {"key": "vat_expense", "label": "Expenses",     "icon": "bi-graph-down-arrow",      "color": "#dc3545"},
    {"key": "employees",   "label": "Employees",    "icon": "bi-person-badge",          "color": "#fd7e14"},
    {"key": "projects",    "label": "Projects",     "icon": "bi-kanban",                "color": "#20c997"},
    {"key": "contracts",   "label": "Contracts",    "icon": "bi-file-earmark-text",     "color": "#6f42c1"},
    {"key": "events",      "label": "Events",       "icon": "bi-calendar-event",        "color": "#0dcaf0"},
    {"key": "procurement", "label": "Procurement",  "icon": "bi-cart-check",            "color": "#795548"},
    {"key": "dividends",   "label": "Dividends",    "icon": "bi-cash-coin",             "color": "#607d8b"},
]

_MODULE_META = {m["key"]: m for m in MODULES}


# ── helpers ──────────────────────────────────────────────────────────────────

def _to_dt(value) -> Optional[datetime]:
    """Normalize datetime / date / ISO or common text formats to datetime."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, date):
        return datetime.combine(value, time.min)
    s = str(value).strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        pass
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d",
                "%d/%m/%Y", "%d-%m-%Y", "%m/%d/%Y"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _etb(amount) -> str:
    try:
        return f"ETB {float(amount):,.2f}"
    except (TypeError, ValueError):
        return "ETB 0.00"


def _item(module: str, title: str, detail: str = "", actor: str = "",
          ts=None, link: str = "#") -> Optional[dict]:
    dt = _to_dt(ts)
    if dt is None:
        return None
    meta = _MODULE_META.get(module, {})
    return {
        "module": module,
        "icon": meta.get("icon", "bi-dot"),
        "color": meta.get("color", "#6c757d"),
        "title": title,
        "detail": detail or "",
        "actor": actor or "",
        "ts": dt,
        "link": link,
    }


# ── sources ──────────────────────────────────────────────────────────────────

def _src_bids(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, title, organization, created_at FROM bid_records "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("bids", f"Bid “{r.get('title') or 'Untitled'}” added",
              detail=r.get("organization") or "",
              ts=r.get("created_at"), link=f"/bid/view/{r['id']}")
        for r in rows
    ]


def _src_cpo(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, name, amount, date, created_at FROM cpo_records "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("cpo",
              f"CPO for payee {r.get('name') or 'Unknown'} — {_etb(r.get('amount'))}",
              detail=f"CPO date {r.get('date')}" if r.get("date") else "",
              ts=r.get("created_at") or r.get("date"), link="/cpo/list")
        for r in rows
    ]


def _src_letters(cid: str, n: int) -> list[dict]:
    import letter_data_store
    out = []
    for ltr in letter_data_store.get_all_letters(cid)[: n * 2]:
        ltr_cid = ltr.get("company_id") or "default"
        if ltr_cid not in (cid, "default"):
            continue
        verb = "uploaded" if ltr.get("source") == "uploaded" else "composed"
        it = _item(
            "letters", f"Letter {ltr.get('ref_number', '?')} {verb}",
            detail=ltr.get("subject") or "",
            actor=ltr.get("created_by") or "",
            ts=ltr.get("created_at"),
            link=f"/letters/{ltr.get('letter_id')}")
        if it is not None:
            out.append(it)
        if len(out) >= n:
            break
    return out


def _src_vat_income(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT description, gross_amount, created_date, created_by FROM vat_income "
        "WHERE company_id = %s AND is_active ORDER BY created_date DESC NULLS LAST LIMIT %s",
        (cid, n))
    return [
        _item("vat_income", f"Income recorded — {_etb(r.get('gross_amount'))}",
              detail=r.get("description") or "", actor=r.get("created_by") or "",
              ts=r.get("created_date"), link="/vat/income")
        for r in rows
    ]


def _src_vat_expenses(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT description, gross_amount, created_date, created_by FROM vat_expenses "
        "WHERE company_id = %s AND is_active ORDER BY created_date DESC NULLS LAST LIMIT %s",
        (cid, n))
    return [
        _item("vat_expense", f"Expense recorded — {_etb(r.get('gross_amount'))}",
              detail=r.get("description") or "", actor=r.get("created_by") or "",
              ts=r.get("created_date"), link="/vat/expenses")
        for r in rows
    ]


def _src_employees(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT employee_id, name, created_date FROM employees "
        "WHERE company_id = %s ORDER BY created_date DESC NULLS LAST LIMIT %s", (cid, n))
    return [
        _item("employees", f"Employee {r.get('employee_id')} created",
              detail=r.get("name") or "",
              ts=r.get("created_date"), link="/payroll/employees")
        for r in rows
    ]


def _src_projects(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, name, created_by, created_at FROM pm_projects "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("projects", f"Project “{r.get('name') or 'Untitled'}” created",
              actor=r.get("created_by") or "",
              ts=r.get("created_at"), link=f"/project/{r['id']}")
        for r in rows
    ]


def _src_contracts(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, title, party_name, created_by, created_at FROM contracts "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("contracts", f"Contract “{r.get('title') or 'Untitled'}” added",
              detail=r.get("party_name") or "", actor=r.get("created_by") or "",
              ts=r.get("created_at"), link=f"/contract/{r['id']}")
        for r in rows
    ]


def _src_events(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, event_name, client_name, created_by, created_at, event_start "
        "FROM ems_bookings WHERE company_id = %s ORDER BY created_at DESC LIMIT %s",
        (cid, n))
    return [
        _item("events", f"Booking “{r.get('event_name') or 'Event'}” created",
              detail=r.get("client_name") or "", actor=r.get("created_by") or "",
              ts=r.get("created_at") or r.get("event_start"),
              link=f"/ems/bookings/{r['id']}")
        for r in rows
    ]


def _src_purchase_orders(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, title, total_amount, created_by, created_at FROM proc_purchase_orders "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("procurement",
              f"Purchase order “{r.get('title') or 'Untitled'}” — {_etb(r.get('total_amount'))}",
              actor=r.get("created_by") or "",
              ts=r.get("created_at"), link=f"/procurement/po/{r['id']}")
        for r in rows
    ]


def _src_proc_plans(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, title, fiscal_year, created_at FROM proc_plans "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("procurement",
              f"Procurement plan “{r.get('title') or 'Untitled'}” added",
              detail=f"Fiscal year {r.get('fiscal_year')}" if r.get("fiscal_year") else "",
              ts=r.get("created_at"), link="/procurement/plans")
        for r in rows
    ]


def _src_dividends(cid: str, n: int) -> list[dict]:
    rows = fetchall(
        "SELECT id, title, declared_date, created_at FROM sh_dividends "
        "WHERE company_id = %s ORDER BY created_at DESC LIMIT %s", (cid, n))
    return [
        _item("dividends", f"Dividend “{r.get('title') or 'Untitled'}” declared",
              detail=f"Declared {r.get('declared_date')}" if r.get("declared_date") else "",
              ts=r.get("created_at") or r.get("declared_date"),
              link=f"/stakeholder/dividends/{r['id']}")
        for r in rows
    ]


# Registry: module key -> list of source callables (procurement has two tables)
_SOURCES: dict[str, list] = {
    "bids":        [_src_bids],
    "cpo":         [_src_cpo],
    "letters":     [_src_letters],
    "vat_income":  [_src_vat_income],
    "vat_expense": [_src_vat_expenses],
    "employees":   [_src_employees],
    "projects":    [_src_projects],
    "contracts":   [_src_contracts],
    "events":      [_src_events],
    "procurement": [_src_purchase_orders, _src_proc_plans],
    "dividends":   [_src_dividends],
}


# ── public API ───────────────────────────────────────────────────────────────

def get_feed(company_id: str, limit: int = 60, module: str = None) -> list[dict]:
    """
    Return the latest activity items across all modules for a company,
    normalized and sorted newest-first, capped at `limit`.

    `module` restricts the feed to one module key (see MODULES).
    A failing source (missing table/column, bad data) is skipped silently.
    """
    limit = max(1, min(int(limit or 60), 500))
    keys = [module] if module in _SOURCES else list(_SOURCES)

    items: list[dict] = []
    for key in keys:
        for fetch in _SOURCES[key]:
            try:
                for it in fetch(company_id, limit):
                    if it is not None:          # None = unparseable timestamp
                        items.append(it)
            except Exception as e:
                logger.debug("activity feed source %s skipped: %s",
                             getattr(fetch, "__name__", key), e)

    items.sort(key=lambda i: i["ts"], reverse=True)
    return items[:limit]
