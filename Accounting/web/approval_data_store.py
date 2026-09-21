"""
Approval Engine Data Store — PostgreSQL backend.

Tables: approval_workflows, approval_steps, approval_requests,
        approval_actions, approval_delegations

Public Python API (for other modules — procurement, HRM leave, expenses, payments):

    from approval_data_store import approval_store

    rid = approval_store.submit(company_id, entity_type, entity_id, title, amount,
                                requested_by, payload=None, currency="ETB")
        -> request id, or None when no active workflow matches (caller auto-approves)

    approval_store.decide(request_id, actor, action, comment="",
                          roles=None, company_id=None, delegate_to=None)
        -> {"ok": bool, "status": "pending|approved|rejected", "error": str|None, ...}

    approval_store.pending_for(company_id, username, roles)  -> [request dicts I can act on]
    approval_store.status_of(entity_type, entity_id, company_id=None)
        -> {"status", "request_id", "current_step", ...} or None
    approval_store.can_act(request_or_id, username, roles) -> bool
    approval_store.cancel(request_id, actor, company_id=None) -> {"ok": bool, ...}

Everything above the ``ApprovalDataStore`` class is pure logic (no DB) so the
step-resolution / threshold-matching / next-approver rules are unit-testable.

Roles are the ``users.privilege_level`` values (viewer, data_entry, operator,
manager, admin, super_admin). ``manager_of_requester`` is resolved through the
HRM ``employees.manager`` column, falling back to the step's ``approver_value``
(a username or role) and finally to the ``manager`` role.
"""
from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from db import get_conn

logger = logging.getLogger(__name__)

ENTITY_TYPES = ("purchase_requisition", "leave", "expense", "payment", "generic")
APPROVER_TYPES = ("role", "user", "manager_of_requester")
STATUSES = ("pending", "approved", "rejected", "cancelled")
ACTIONS = ("approve", "reject", "comment", "delegate")

# ── Completion hooks ──────────────────────────────────────────────
# Other modules register ``fn(request: dict, status: str, actor: str, comment: str)``
# to sync their own entity when a request reaches a final status
# (approved / rejected). See approval_hooks.py for the built-in ones.
_ON_DECIDED: List["callable"] = []


def register_on_decided(fn) -> None:
    if callable(fn) and fn not in _ON_DECIDED:
        _ON_DECIDED.append(fn)


def _fire_decided(req: dict, status: str, actor: str, comment: str = "") -> None:
    for fn in list(_ON_DECIDED):
        try:
            fn(req, status, actor, comment)
        except Exception as exc:  # never let a consumer break the decision
            logger.error("approval on_decided hook %s failed: %s", getattr(fn, "__name__", fn), exc)
ROLE_CHOICES = ("data_entry", "operator", "manager", "admin", "super_admin")

# Requesters may not approve their own requests unless explicitly allowed.
ALLOW_SELF_APPROVAL = os.environ.get("APPROVAL_ALLOW_SELF_APPROVE", "0").strip().lower() in ("1", "true", "yes")
ESCALATION_DAYS = int(os.environ.get("APPROVAL_ESCALATION_DAYS", "3") or 3)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS approval_workflows (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    name          TEXT NOT NULL,
    entity_type   TEXT NOT NULL DEFAULT 'generic',   -- purchase_requisition|leave|expense|payment|generic
    description   TEXT NOT NULL DEFAULT '',
    min_amount    NUMERIC(18,2),
    max_amount    NUMERIC(18,2),
    currency      TEXT NOT NULL DEFAULT 'ETB',
    is_active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_by    TEXT NOT NULL DEFAULT '',
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approval_workflows_company ON approval_workflows(company_id, entity_type);

CREATE TABLE IF NOT EXISTS approval_steps (
    id             TEXT PRIMARY KEY,
    workflow_id    TEXT NOT NULL REFERENCES approval_workflows(id) ON DELETE CASCADE,
    step_order     INTEGER NOT NULL DEFAULT 1,
    name           TEXT NOT NULL DEFAULT '',
    approver_type  TEXT NOT NULL DEFAULT 'role',      -- role|user|manager_of_requester
    approver_value TEXT NOT NULL DEFAULT '',
    require_all    BOOLEAN NOT NULL DEFAULT FALSE,
    sla_hours      INTEGER,
    created_at     TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approval_steps_workflow ON approval_steps(workflow_id, step_order);

CREATE TABLE IF NOT EXISTS approval_requests (
    id            TEXT PRIMARY KEY,
    company_id    TEXT NOT NULL DEFAULT 'default',
    workflow_id   TEXT NOT NULL,
    entity_type   TEXT NOT NULL DEFAULT 'generic',
    entity_id     TEXT NOT NULL DEFAULT '',
    title         TEXT NOT NULL DEFAULT '',
    amount        NUMERIC(18,2),
    currency      TEXT NOT NULL DEFAULT 'ETB',
    requested_by  TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'pending',    -- pending|approved|rejected|cancelled
    current_step  INTEGER NOT NULL DEFAULT 1,
    payload       JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMP NOT NULL DEFAULT NOW(),
    decided_at    TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_approval_requests_company ON approval_requests(company_id, status);
CREATE INDEX IF NOT EXISTS idx_approval_requests_entity ON approval_requests(entity_type, entity_id);

CREATE TABLE IF NOT EXISTS approval_actions (
    id            TEXT PRIMARY KEY,
    request_id    TEXT NOT NULL REFERENCES approval_requests(id) ON DELETE CASCADE,
    step_order    INTEGER NOT NULL DEFAULT 1,
    actor         TEXT NOT NULL DEFAULT '',
    on_behalf_of  TEXT NOT NULL DEFAULT '',            -- delegator when a delegate acts
    action        TEXT NOT NULL DEFAULT 'comment',     -- approve|reject|comment|delegate
    comment       TEXT NOT NULL DEFAULT '',
    delegate_to   TEXT NOT NULL DEFAULT '',            -- target of a per-request delegate action
    created_at    TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approval_actions_request ON approval_actions(request_id, step_order);

CREATE TABLE IF NOT EXISTS approval_delegations (
    id          TEXT PRIMARY KEY,
    company_id  TEXT NOT NULL DEFAULT 'default',
    from_user   TEXT NOT NULL,
    to_user     TEXT NOT NULL,
    starts_on   DATE NOT NULL DEFAULT CURRENT_DATE,
    ends_on     DATE,
    note        TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMP NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_approval_delegations_company ON approval_delegations(company_id, to_user);
"""


# ═══════════════════════════════════════════════════════════════════
#  Pure logic — no database access. Unit-tested in tests/test_approval_store.py
# ═══════════════════════════════════════════════════════════════════

def _opt(value):
    """Empty form fields arrive as '' — Postgres rejects '' for DATE/NUMERIC/INT."""
    return value if value not in ("", None) else None


def to_decimal(value) -> Optional[Decimal]:
    """'' / None -> None; numbers and numeric strings -> Decimal; garbage -> None."""
    if value in ("", None):
        return None
    if isinstance(value, Decimal):
        return value
    try:
        return Decimal(str(value).replace(",", "").strip())
    except (InvalidOperation, ValueError):
        return None


def to_int(value) -> Optional[int]:
    if value in ("", None):
        return None
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def to_bool(value) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in ("1", "true", "yes", "on", "y")


def _as_date(value) -> Optional[date]:
    if value in ("", None):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def amount_matches(amount, min_amount, max_amount) -> bool:
    """True when amount falls inside [min_amount, max_amount]; open bounds when NULL.
    A missing amount is treated as 0 so amount-less entities (leave) still match
    workflows without a lower bound."""
    amt = to_decimal(amount)
    if amt is None:
        amt = Decimal(0)
    lo = to_decimal(min_amount)
    hi = to_decimal(max_amount)
    if lo is not None and amt < lo:
        return False
    if hi is not None and amt > hi:
        return False
    return True


def select_workflow(workflows: Iterable[dict], entity_type: str, amount) -> Optional[dict]:
    """Pick the active workflow for ``entity_type`` whose amount range covers ``amount``.

    When several match, the most specific wins: a fully bounded range beats a
    half-open one, which beats an unbounded one; among bounded ranges the
    narrowest wins; ties keep input order (callers pass oldest first).
    """
    entity_type = (entity_type or "generic").strip()
    candidates: List[Tuple[Tuple[int, Decimal], int, dict]] = []
    for idx, wf in enumerate(workflows or []):
        if not wf.get("is_active", True):
            continue
        if (wf.get("entity_type") or "generic") != entity_type:
            continue
        if not amount_matches(amount, wf.get("min_amount"), wf.get("max_amount")):
            continue
        lo = to_decimal(wf.get("min_amount"))
        hi = to_decimal(wf.get("max_amount"))
        if lo is not None and hi is not None:
            key = (0, hi - lo)
        elif lo is not None or hi is not None:
            key = (1, Decimal(0))
        else:
            key = (2, Decimal(0))
        candidates.append((key, idx, wf))
    if not candidates:
        return None
    candidates.sort(key=lambda c: (c[0][0], c[0][1], c[1]))
    return candidates[0][2]


def sorted_steps(steps: Iterable[dict]) -> List[dict]:
    return sorted(steps or [], key=lambda s: int(s.get("step_order") or 0))


def first_step_order(steps: Iterable[dict]) -> Optional[int]:
    ordered = sorted_steps(steps)
    return int(ordered[0]["step_order"]) if ordered else None


def next_step_order(steps: Iterable[dict], current_order) -> Optional[int]:
    """The smallest step_order strictly greater than current_order, or None when done."""
    cur = -1 if current_order is None else int(current_order)
    for s in sorted_steps(steps):
        if int(s.get("step_order") or 0) > cur:
            return int(s["step_order"])
    return None


def step_at(steps: Iterable[dict], step_order) -> Optional[dict]:
    if step_order is None:
        return None
    for s in steps or []:
        if int(s.get("step_order") or 0) == int(step_order):
            return s
    return None


def active_delegators(delegations: Iterable[dict], username: str, today: date = None) -> List[str]:
    """Users who have delegated their approvals to ``username`` and whose window covers today."""
    today = today or date.today()
    out: List[str] = []
    for d in delegations or []:
        if not d or d.get("to_user") != username:
            continue
        starts = _as_date(d.get("starts_on"))
        ends = _as_date(d.get("ends_on"))
        if starts and today < starts:
            continue
        if ends and today > ends:
            continue
        frm = d.get("from_user")
        if frm and frm not in out and frm != username:
            out.append(frm)
    return out


def request_delegations(actions: Iterable[dict], step_order) -> List[dict]:
    """Per-request 'delegate' actions at the current step, shaped like delegation rows."""
    out = []
    for a in actions or []:
        if a.get("action") != "delegate" or not a.get("delegate_to"):
            continue
        if step_order is not None and int(a.get("step_order") or 0) != int(step_order):
            continue
        out.append({"from_user": a.get("on_behalf_of") or a.get("actor"),
                    "to_user": a.get("delegate_to"), "starts_on": None, "ends_on": None})
    return out


def build_principals(username: str, roles: Iterable[str], delegations: Iterable[dict] = None,
                     roles_lookup: Dict[str, Iterable[str]] = None, today: date = None
                     ) -> List[Tuple[str, set]]:
    """[(username, roles_set)] — the acting user first, then every active delegator."""
    principals = [(username, set(r for r in (roles or ()) if r))]
    lookup = roles_lookup or {}
    for delegator in active_delegators(delegations, username, today):
        principals.append((delegator, set(lookup.get(delegator, ()) or ())))
    return principals


def step_matches_principal(step: dict, principal: Tuple[str, set], manager: str = None) -> bool:
    """Does this (username, roles) satisfy the step's approver definition?"""
    name, roles = principal
    if not name:
        return False
    a_type = (step.get("approver_type") or "role").strip()
    a_value = (step.get("approver_value") or "").strip()
    if a_type == "user":
        return bool(a_value) and name == a_value
    if a_type == "role":
        return bool(a_value) and a_value in roles
    if a_type == "manager_of_requester":
        if manager:
            return name == manager
        if a_value:                       # fallback: configured username or role
            return name == a_value or a_value in roles
        return "manager" in roles
    return False


def matching_principal(step: dict, principals: Sequence[Tuple[str, set]], manager: str = None) -> Optional[str]:
    for p in principals:
        if step_matches_principal(step, p, manager):
            return p[0]
    return None


def resolve_step_approvers(step: dict, users_by_role: Dict[str, Iterable[str]] = None,
                           manager: str = None) -> List[str]:
    """Usernames expected to act on a step (used for require_all and notifications)."""
    by_role = users_by_role or {}
    a_type = (step.get("approver_type") or "role").strip()
    a_value = (step.get("approver_value") or "").strip()
    if a_type == "user":
        return [a_value] if a_value else []
    if a_type == "role":
        return list(dict.fromkeys(by_role.get(a_value, []) or []))
    if a_type == "manager_of_requester":
        if manager:
            return [manager]
        if a_value:
            if a_value in by_role:
                return list(dict.fromkeys(by_role.get(a_value) or []))
            return [a_value]
        return list(dict.fromkeys(by_role.get("manager", []) or []))
    return []


def step_outcome(step: dict, approvers: Iterable[str], actions: Iterable[dict]) -> str:
    """'approved' | 'rejected' | 'pending' for one step given the recorded actions.

    require_all: every resolved approver must approve (a delegate's approval is
    credited to the delegator via on_behalf_of). Otherwise one approval suffices.
    Any rejection at the step rejects it.
    """
    order = int(step.get("step_order") or 0)
    approved = set()
    for a in actions or []:
        if int(a.get("step_order") or 0) != order:
            continue
        if a.get("action") == "reject":
            return "rejected"
        if a.get("action") == "approve":
            approved.add(a.get("on_behalf_of") or a.get("actor"))
    if not approved:
        return "pending"
    required = [u for u in (approvers or []) if u]
    if to_bool(step.get("require_all")) and required:
        return "approved" if set(required) <= approved else "pending"
    return "approved"


def acting_principal(request: dict, steps: Iterable[dict], actions: Iterable[dict],
                     username: str, roles: Iterable[str], delegations: Iterable[dict] = None,
                     roles_lookup: Dict[str, Iterable[str]] = None, manager: str = None,
                     today: date = None) -> Optional[str]:
    """Which principal (self or a delegator) lets ``username`` act on the request now.

    Returns the principal's username, or None when the user cannot act:
    request not pending, no current step, self-approval, already approved at
    this step, or no matching approver definition.
    """
    if not request or not username or request.get("status") != "pending":
        return None
    step = step_at(steps, request.get("current_step"))
    if not step:
        return None
    order = int(step.get("step_order") or 0)
    all_delegations = list(delegations or []) + request_delegations(actions, order)
    principals = build_principals(username, roles, all_delegations, roles_lookup, today)
    if not ALLOW_SELF_APPROVAL:
        principals = [p for p in principals if p[0] != request.get("requested_by")]
    already = {(a.get("on_behalf_of") or a.get("actor")) for a in (actions or [])
               if int(a.get("step_order") or 0) == order and a.get("action") == "approve"}
    principals = [p for p in principals if p[0] not in already]
    return matching_principal(step, principals, manager)


def can_act_on(request: dict, steps, actions, username, roles, **kw) -> bool:
    return acting_principal(request, steps, actions, username, roles, **kw) is not None


def _fmt_ts(value) -> str:
    if value in ("", None):
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    return str(value)[:16]


def _age_hours(created_at, now: datetime = None) -> float:
    if not isinstance(created_at, datetime):
        return 0.0
    now = now or datetime.now()
    try:
        return max(0.0, (now - created_at).total_seconds() / 3600.0)
    except Exception:
        return 0.0


def decorate_request(req: dict, now: datetime = None) -> dict:
    """Add display helpers used by templates; never raises."""
    if not req:
        return req
    req["created_display"] = _fmt_ts(req.get("created_at"))
    req["decided_display"] = _fmt_ts(req.get("decided_at"))
    req["age_hours"] = _age_hours(req.get("created_at"), now)
    req["age_days"] = round(req["age_hours"] / 24.0, 1)
    amt = to_decimal(req.get("amount"))
    req["amount_display"] = f"{amt:,.2f}" if amt is not None else "—"
    if not isinstance(req.get("payload"), dict):
        try:
            req["payload"] = json.loads(req["payload"]) if req.get("payload") else {}
        except Exception:
            req["payload"] = {}
    step = step_at(req.get("steps") or [], req.get("current_step"))
    req["current_step_def"] = step
    sla = to_int(step.get("sla_hours")) if step else None
    req["sla_breached"] = bool(sla and req["status"] == "pending" and req["age_hours"] > sla)
    return req


def ensure_schema():
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(_SCHEMA)
        logger.info("approval schema ready")
    except Exception as e:
        logger.error("approval schema init failed: %s", e)


# ═══════════════════════════════════════════════════════════════════
#  Data store
# ═══════════════════════════════════════════════════════════════════

class ApprovalDataStore:

    def ensure_schema(self):
        ensure_schema()

    # ── Users / roles helpers ─────────────────────────────────────
    def get_company_users(self, company_id: str) -> List[dict]:
        """Active users visible to a company. Users still parked in 'default'
        (new registrations) are included so single-tenant installs work."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """SELECT username, full_name, email, privilege_level
                           FROM users WHERE is_active = TRUE
                             AND (company_id = %s OR company_id = 'default')
                           ORDER BY username""",
                        (company_id,)
                    )
                    return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_company_users: %s", e); return []

    def _users_by_role(self, company_id: str, users: List[dict] = None) -> Dict[str, List[str]]:
        out: Dict[str, List[str]] = {}
        for u in (users if users is not None else self.get_company_users(company_id)):
            out.setdefault(u.get("privilege_level") or "viewer", []).append(u["username"])
        return out

    def _roles_lookup(self, company_id: str, users: List[dict] = None) -> Dict[str, set]:
        return {u["username"]: {u.get("privilege_level") or "viewer"}
                for u in (users if users is not None else self.get_company_users(company_id))}

    def roles_of(self, username: str, company_id: str = None) -> List[str]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT privilege_level FROM users WHERE username=%s", (username,))
                    row = cur.fetchone()
                    return [row["privilege_level"]] if row and row.get("privilege_level") else []
        except Exception as e:
            logger.error("roles_of: %s", e); return []

    def _emails_for(self, usernames: Iterable[str], company_id: str) -> List[str]:
        names = [u for u in dict.fromkeys(usernames or []) if u]
        if not names:
            return []
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT email FROM users WHERE username = ANY(%s) AND email <> ''", (names,))
                    return [r["email"] for r in cur.fetchall() if r.get("email")]
        except Exception as e:
            logger.error("_emails_for: %s", e); return []

    def manager_of(self, company_id: str, username: str) -> Optional[str]:
        """Resolve the requester's manager to a username via HRM employees.manager.
        Returns None when unknown (callers fall back to the step's approver_value / role)."""
        if not username:
            return None
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT full_name, email FROM users WHERE username=%s", (username,))
                    u = cur.fetchone() or {}
                    full_name = (u.get("full_name") or "").strip()
                    cur.execute(
                        """SELECT manager FROM employees
                           WHERE company_id=%s AND COALESCE(manager,'') <> ''
                             AND (employee_id=%s OR name=%s OR (name=%s AND %s <> ''))
                           ORDER BY updated_date DESC NULLS LAST LIMIT 1""",
                        (company_id, username, username, full_name, full_name)
                    )
                    row = cur.fetchone()
                    if not row or not row.get("manager"):
                        return None
                    mgr = str(row["manager"]).strip()
                    cur.execute(
                        "SELECT username FROM users WHERE username=%s OR full_name=%s OR email=%s LIMIT 1",
                        (mgr, mgr, mgr)
                    )
                    m = cur.fetchone()
                    return m["username"] if m else None
        except Exception as e:
            logger.warning("manager_of(%s): %s", username, e)
            return None

    # ── Workflows ─────────────────────────────────────────────────
    def _attach_steps(self, workflows: List[dict]) -> List[dict]:
        ids = [w["id"] for w in workflows]
        for w in workflows:
            w["steps"] = []
        if not ids:
            return workflows
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM approval_steps WHERE workflow_id = ANY(%s) ORDER BY step_order", (ids,))
                    by_wf: Dict[str, List[dict]] = {}
                    for s in cur.fetchall():
                        by_wf.setdefault(s["workflow_id"], []).append(dict(s))
            for w in workflows:
                w["steps"] = by_wf.get(w["id"], [])
        except Exception as e:
            logger.error("_attach_steps: %s", e)
        return workflows

    def get_workflows(self, company_id: str, entity_type: str = None, active_only: bool = False) -> List[dict]:
        try:
            sql = "SELECT * FROM approval_workflows WHERE company_id=%s"
            params: list = [company_id]
            if entity_type:
                sql += " AND entity_type=%s"; params.append(entity_type)
            if active_only:
                sql += " AND is_active = TRUE"
            sql += " ORDER BY entity_type, created_at"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = [dict(r) for r in cur.fetchall()]
            return self._attach_steps(rows)
        except Exception as e:
            logger.error("get_workflows: %s", e); return []

    def get_workflow(self, workflow_id: str, company_id: str) -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM approval_workflows WHERE id=%s AND company_id=%s",
                                (workflow_id, company_id))
                    row = cur.fetchone()
            if not row:
                return None
            return self._attach_steps([dict(row)])[0]
        except Exception as e:
            logger.error("get_workflow: %s", e); return None

    @staticmethod
    def _clean_steps(steps: Iterable[dict]) -> List[dict]:
        out = []
        for i, s in enumerate(steps or [], start=1):
            a_type = (s.get("approver_type") or "role").strip()
            a_value = (s.get("approver_value") or "").strip()
            if a_type not in APPROVER_TYPES:
                continue
            if a_type != "manager_of_requester" and not a_value:
                continue
            out.append({"step_order": to_int(s.get("step_order")) or i,
                        "name": (s.get("name") or "").strip(),
                        "approver_type": a_type, "approver_value": a_value,
                        "require_all": to_bool(s.get("require_all")),
                        "sla_hours": to_int(s.get("sla_hours"))})
        # renumber sequentially so gaps never break next_step_order
        for i, s in enumerate(sorted_steps(out), start=1):
            s["step_order"] = i
        return sorted_steps(out)

    def _insert_steps(self, cur, workflow_id: str, steps: List[dict]):
        for s in steps:
            cur.execute(
                """INSERT INTO approval_steps(id,workflow_id,step_order,name,approver_type,approver_value,require_all,sla_hours)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s)""",
                (str(uuid.uuid4()), workflow_id, s["step_order"], s["name"], s["approver_type"],
                 s["approver_value"], s["require_all"], s["sla_hours"])
            )

    def create_workflow(self, company_id: str, data: dict, steps: Iterable[dict]) -> Optional[dict]:
        try:
            wid = str(uuid.uuid4())
            clean = self._clean_steps(steps)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO approval_workflows(id,company_id,name,entity_type,description,min_amount,max_amount,
                           currency,is_active,created_by) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (wid, company_id, (data.get("name") or "").strip() or "Untitled workflow",
                         data.get("entity_type") if data.get("entity_type") in ENTITY_TYPES else "generic",
                         data.get("description") or "", to_decimal(data.get("min_amount")),
                         to_decimal(data.get("max_amount")), data.get("currency") or "ETB",
                         to_bool(data.get("is_active", True)), data.get("created_by") or "")
                    )
                    row = dict(cur.fetchone())
                    self._insert_steps(cur, wid, clean)
            row["steps"] = clean
            return row
        except Exception as e:
            logger.error("create_workflow: %s", e); return None

    def update_workflow(self, workflow_id: str, company_id: str, data: dict, steps: Iterable[dict]) -> bool:
        try:
            clean = self._clean_steps(steps)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE approval_workflows SET name=%s,entity_type=%s,description=%s,min_amount=%s,
                           max_amount=%s,currency=%s,updated_at=NOW() WHERE id=%s AND company_id=%s""",
                        ((data.get("name") or "").strip() or "Untitled workflow",
                         data.get("entity_type") if data.get("entity_type") in ENTITY_TYPES else "generic",
                         data.get("description") or "", to_decimal(data.get("min_amount")),
                         to_decimal(data.get("max_amount")), data.get("currency") or "ETB",
                         workflow_id, company_id)
                    )
                    if cur.rowcount == 0:
                        return False
                    cur.execute("DELETE FROM approval_steps WHERE workflow_id=%s", (workflow_id,))
                    self._insert_steps(cur, workflow_id, clean)
            return True
        except Exception as e:
            logger.error("update_workflow: %s", e); return False

    def toggle_workflow(self, workflow_id: str, company_id: str) -> Optional[bool]:
        """Flip is_active; returns the new state or None on failure."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE approval_workflows SET is_active = NOT is_active, updated_at=NOW()
                           WHERE id=%s AND company_id=%s RETURNING is_active""",
                        (workflow_id, company_id)
                    )
                    row = cur.fetchone()
                    return bool(row["is_active"]) if row else None
        except Exception as e:
            logger.error("toggle_workflow: %s", e); return None

    # ── Delegations ───────────────────────────────────────────────
    def get_delegations(self, company_id: str, username: str = None, to_user: str = None) -> List[dict]:
        try:
            sql = "SELECT * FROM approval_delegations WHERE company_id=%s"
            params: list = [company_id]
            if username:
                sql += " AND (from_user=%s OR to_user=%s)"; params += [username, username]
            if to_user:
                sql += " AND to_user=%s"; params.append(to_user)
            sql += " ORDER BY starts_on DESC, created_at DESC"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = [dict(r) for r in cur.fetchall()]
            today = date.today()
            for r in rows:
                s, e = _as_date(r.get("starts_on")), _as_date(r.get("ends_on"))
                r["is_current"] = (not s or s <= today) and (not e or e >= today)
            return rows
        except Exception as e:
            logger.error("get_delegations: %s", e); return []

    def create_delegation(self, company_id: str, data: dict) -> Optional[dict]:
        try:
            frm = (data.get("from_user") or "").strip()
            to = (data.get("to_user") or "").strip()
            if not frm or not to or frm == to:
                return None
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO approval_delegations(id,company_id,from_user,to_user,starts_on,ends_on,note)
                           VALUES(%s,%s,%s,%s,COALESCE(%s, CURRENT_DATE),%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), company_id, frm, to, _opt(data.get("starts_on")),
                         _opt(data.get("ends_on")), data.get("note") or "")
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("create_delegation: %s", e); return None

    def delete_delegation(self, delegation_id: str, company_id: str, username: str = None,
                          is_admin: bool = False) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    if is_admin or not username:
                        cur.execute("DELETE FROM approval_delegations WHERE id=%s AND company_id=%s",
                                    (delegation_id, company_id))
                    else:
                        cur.execute("DELETE FROM approval_delegations WHERE id=%s AND company_id=%s AND from_user=%s",
                                    (delegation_id, company_id, username))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_delegation: %s", e); return False

    # ── Requests ──────────────────────────────────────────────────
    def _add_action(self, request_id: str, step_order, actor: str, action: str, comment: str = "",
                    on_behalf_of: str = "", delegate_to: str = "") -> Optional[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO approval_actions(id,request_id,step_order,actor,on_behalf_of,action,comment,delegate_to)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                        (str(uuid.uuid4()), request_id, int(step_order or 0), actor or "", on_behalf_of or "",
                         action, comment or "", delegate_to or "")
                    )
                    return dict(cur.fetchone())
        except Exception as e:
            logger.error("_add_action: %s", e); return None

    def get_actions(self, request_id: str) -> List[dict]:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM approval_actions WHERE request_id=%s ORDER BY created_at, step_order",
                                (request_id,))
                    rows = [dict(r) for r in cur.fetchall()]
            for a in rows:
                a["created_display"] = _fmt_ts(a.get("created_at"))
            return rows
        except Exception as e:
            logger.error("get_actions: %s", e); return []

    def _actions_for(self, request_ids: List[str]) -> Dict[str, List[dict]]:
        out: Dict[str, List[dict]] = {rid: [] for rid in request_ids}
        if not request_ids:
            return out
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM approval_actions WHERE request_id = ANY(%s) ORDER BY created_at",
                                (request_ids,))
                    for a in cur.fetchall():
                        a = dict(a); a["created_display"] = _fmt_ts(a.get("created_at"))
                        out.setdefault(a["request_id"], []).append(a)
        except Exception as e:
            logger.error("_actions_for: %s", e)
        return out

    def _steps_for(self, workflow_ids: List[str]) -> Dict[str, List[dict]]:
        ids = list(dict.fromkeys(w for w in workflow_ids if w))
        out: Dict[str, List[dict]] = {w: [] for w in ids}
        if not ids:
            return out
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT * FROM approval_steps WHERE workflow_id = ANY(%s) ORDER BY step_order", (ids,))
                    for s in cur.fetchall():
                        out.setdefault(s["workflow_id"], []).append(dict(s))
        except Exception as e:
            logger.error("_steps_for: %s", e)
        return out

    def _set_status(self, request_id: str, status: str) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """UPDATE approval_requests SET status=%s, updated_at=NOW(),
                           decided_at = CASE WHEN %s IN ('approved','rejected','cancelled') THEN NOW() ELSE decided_at END
                           WHERE id=%s""",
                        (status, status, request_id)
                    )
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("_set_status: %s", e); return False

    def _set_current_step(self, request_id: str, step_order: int) -> bool:
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("UPDATE approval_requests SET current_step=%s, updated_at=NOW() WHERE id=%s",
                                (int(step_order), request_id))
                    return cur.rowcount > 0
        except Exception as e:
            logger.error("_set_current_step: %s", e); return False

    def get_request(self, request_id: str, company_id: str = None) -> Optional[dict]:
        """Request with workflow_name, steps, actions and display helpers."""
        try:
            sql = """SELECT r.*, w.name AS workflow_name FROM approval_requests r
                     LEFT JOIN approval_workflows w ON w.id = r.workflow_id WHERE r.id=%s"""
            params: list = [request_id]
            if company_id:
                sql += " AND r.company_id=%s"; params.append(company_id)
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
            if not row:
                return None
            req = dict(row)
            req["steps"] = self._steps_for([req["workflow_id"]]).get(req["workflow_id"], [])
            req["actions"] = self.get_actions(req["id"])
            return decorate_request(req)
        except Exception as e:
            logger.error("get_request: %s", e); return None

    def get_requests(self, company_id: str, status: str = None, entity_type: str = None,
                     requested_by: str = None, limit: int = 200, with_details: bool = False) -> List[dict]:
        try:
            sql = """SELECT r.*, w.name AS workflow_name FROM approval_requests r
                     LEFT JOIN approval_workflows w ON w.id = r.workflow_id WHERE r.company_id=%s"""
            params: list = [company_id]
            if status:
                sql += " AND r.status=%s"; params.append(status)
            if entity_type:
                sql += " AND r.entity_type=%s"; params.append(entity_type)
            if requested_by:
                sql += " AND r.requested_by=%s"; params.append(requested_by)
            sql += " ORDER BY r.created_at DESC LIMIT %s"; params.append(int(limit))
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = [dict(r) for r in cur.fetchall()]
            steps = self._steps_for([r["workflow_id"] for r in rows])
            actions = self._actions_for([r["id"] for r in rows]) if with_details else {}
            for r in rows:
                r["steps"] = steps.get(r["workflow_id"], [])
                r["actions"] = actions.get(r["id"], [])
                decorate_request(r)
            return rows
        except Exception as e:
            logger.error("get_requests: %s", e); return []

    def submit(self, company_id: str, entity_type: str, entity_id: str, title: str, amount,
               requested_by: str, payload: dict = None, currency: str = "ETB") -> Optional[str]:
        """Open an approval request. Returns the request id, or None when no
        active workflow (with at least one step) matches — callers then treat
        the entity as auto-approved."""
        try:
            entity_type = (entity_type or "generic").strip()
            workflows = self.get_workflows(company_id, entity_type=entity_type, active_only=True)
            wf = select_workflow([w for w in workflows if w.get("steps")], entity_type, amount)
            if not wf:
                logger.info("approval.submit: no workflow for %s/%s amount=%s — auto-approve", company_id, entity_type, amount)
                return None
            first = first_step_order(wf["steps"])
            rid = str(uuid.uuid4())
            import psycopg2.extras
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """INSERT INTO approval_requests(id,company_id,workflow_id,entity_type,entity_id,title,amount,
                           currency,requested_by,status,current_step,payload)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,'pending',%s,%s)""",
                        (rid, company_id, wf["id"], entity_type, str(entity_id or ""), title or "",
                         to_decimal(amount), currency or wf.get("currency") or "ETB", requested_by or "",
                         first, psycopg2.extras.Json(payload or {}))
                    )
            req = self.get_request(rid, company_id)
            if req:
                self._notify_step_approvers(req)
            return rid
        except Exception as e:
            logger.error("submit: %s", e); return None

    def _act_context(self, req: dict, actor: str, roles: Iterable[str]) -> dict:
        cid = req["company_id"]
        users = self.get_company_users(cid)
        step = step_at(req.get("steps") or [], req.get("current_step"))
        manager = None
        if step and (step.get("approver_type") == "manager_of_requester"):
            manager = self.manager_of(cid, req.get("requested_by"))
        return {
            "users": users,
            "users_by_role": self._users_by_role(cid, users),
            "roles_lookup": self._roles_lookup(cid, users),
            "delegations": self.get_delegations(cid, to_user=actor),
            "manager": manager,
            "step": step,
        }

    def decide(self, request_id: str, actor: str, action: str, comment: str = "",
               roles: Iterable[str] = None, company_id: str = None, delegate_to: str = None) -> dict:
        """Record approve / reject / comment / delegate and advance the request.
        ``roles`` defaults to the actor's privilege_level from the users table."""
        action = (action or "").strip().lower()
        if action not in ACTIONS:
            return {"ok": False, "error": "Invalid action", "status": None}
        req = self.get_request(request_id, company_id)
        if not req:
            return {"ok": False, "error": "Request not found", "status": None}
        if req["status"] != "pending":
            return {"ok": False, "error": f"Request is already {req['status']}", "status": req["status"]}
        cid = req["company_id"]
        role_list = list(roles) if roles is not None else self.roles_of(actor, cid)
        comment = (comment or "").strip()
        step_order = req.get("current_step")

        if action == "comment":
            if not comment:
                return {"ok": False, "error": "Comment cannot be empty", "status": "pending"}
            self._add_action(req["id"], step_order, actor, "comment", comment)
            return {"ok": True, "status": "pending"}

        ctx = self._act_context(req, actor, role_list)
        if not ctx["step"]:
            # Workflow lost its steps after submission — nothing can act; approve to unblock.
            self._set_status(req["id"], "approved")
            self._add_action(req["id"], step_order, "system", "approve", "Auto-approved: workflow has no steps")
            _fire_decided(req, "approved", "system", "Auto-approved: workflow has no steps")
            return {"ok": True, "status": "approved", "auto": True}
        principal = acting_principal(req, req["steps"], req["actions"], actor, role_list,
                                     ctx["delegations"], ctx["roles_lookup"], ctx["manager"])
        if principal is None:
            return {"ok": False, "error": "You are not an approver for the current step", "status": "pending"}
        on_behalf = "" if principal == actor else principal

        if action == "delegate":
            target = (delegate_to or "").strip()
            if not target or target == actor:
                return {"ok": False, "error": "Choose a user to delegate to", "status": "pending"}
            self._add_action(req["id"], step_order, actor, "delegate", comment, on_behalf, target)
            self._notify_users(cid, [target], f"[EBMS] Approval delegated to you: {req['title']}",
                               self._mail_body(req, f"{actor} delegated this approval to you." + (f" Note: {comment}" if comment else "")))
            return {"ok": True, "status": "pending", "delegated_to": target}

        self._add_action(req["id"], step_order, actor, action, comment, on_behalf)
        if action == "reject":
            self._set_status(req["id"], "rejected")
            self._notify_users(cid, [req["requested_by"]], f"[EBMS] Rejected: {req['title']}",
                               self._mail_body(req, f"Rejected by {actor}." + (f" Reason: {comment}" if comment else "")))
            _fire_decided(req, "rejected", actor, comment)
            return {"ok": True, "status": "rejected"}

        actions = self.get_actions(req["id"])
        approvers = resolve_step_approvers(ctx["step"], ctx["users_by_role"], ctx["manager"])
        if step_outcome(ctx["step"], approvers, actions) != "approved":
            return {"ok": True, "status": "pending", "step_complete": False, "current_step": step_order}
        nxt = next_step_order(req["steps"], step_order)
        if nxt is None:
            self._set_status(req["id"], "approved")
            self._notify_users(cid, [req["requested_by"]], f"[EBMS] Approved: {req['title']}",
                               self._mail_body(req, f"Final approval by {actor}."))
            _fire_decided(req, "approved", actor, comment)
            return {"ok": True, "status": "approved", "step_complete": True}
        self._set_current_step(req["id"], nxt)
        fresh = self.get_request(req["id"], cid)
        if fresh:
            self._notify_step_approvers(fresh)
        return {"ok": True, "status": "pending", "step_complete": True, "current_step": nxt}

    def cancel(self, request_id: str, actor: str, company_id: str = None, is_admin: bool = False) -> dict:
        req = self.get_request(request_id, company_id)
        if not req:
            return {"ok": False, "error": "Request not found"}
        if req["status"] != "pending":
            return {"ok": False, "error": f"Request is already {req['status']}"}
        if not is_admin and actor != req.get("requested_by"):
            return {"ok": False, "error": "Only the requester can cancel"}
        self._set_status(req["id"], "cancelled")
        self._add_action(req["id"], req.get("current_step"), actor, "comment", "Request cancelled")
        return {"ok": True, "status": "cancelled"}

    def pending_for(self, company_id: str, username: str, roles: Iterable[str]) -> List[dict]:
        """Pending requests the user (or their delegators) can act on right now."""
        try:
            reqs = self.get_requests(company_id, status="pending", limit=500, with_details=True)
            if not reqs or not username:
                return []
            users = self.get_company_users(company_id)
            roles_lookup = self._roles_lookup(company_id, users)
            delegations = self.get_delegations(company_id, to_user=username)
            role_list = list(roles or [])
            manager_cache: Dict[str, Optional[str]] = {}
            out = []
            for r in reqs:
                step = r.get("current_step_def")
                manager = None
                if step and step.get("approver_type") == "manager_of_requester":
                    rb = r.get("requested_by") or ""
                    if rb not in manager_cache:
                        manager_cache[rb] = self.manager_of(company_id, rb)
                    manager = manager_cache[rb]
                p = acting_principal(r, r["steps"], r["actions"], username, role_list,
                                     delegations, roles_lookup, manager)
                if p:
                    r["acting_as"] = p
                    out.append(r)
            return out
        except Exception as e:
            logger.error("pending_for: %s", e); return []

    def status_of(self, entity_type: str, entity_id: str, company_id: str = None) -> Optional[dict]:
        """Latest approval request for an entity, or None if it was never submitted."""
        try:
            sql = "SELECT * FROM approval_requests WHERE entity_type=%s AND entity_id=%s"
            params: list = [entity_type, str(entity_id)]
            if company_id:
                sql += " AND company_id=%s"; params.append(company_id)
            sql += " ORDER BY created_at DESC LIMIT 1"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    row = cur.fetchone()
            if not row:
                return None
            r = dict(row)
            return {"request_id": r["id"], "status": r["status"], "current_step": r["current_step"],
                    "entity_type": r["entity_type"], "entity_id": r["entity_id"], "title": r["title"],
                    "requested_by": r["requested_by"], "created_at": _fmt_ts(r.get("created_at")),
                    "decided_at": _fmt_ts(r.get("decided_at")), "company_id": r["company_id"]}
        except Exception as e:
            logger.error("status_of: %s", e); return None

    def can_act(self, request, username: str, roles: Iterable[str]) -> bool:
        """``request`` may be a request id or a dict from get_request()."""
        req = self.get_request(request) if isinstance(request, str) else request
        if not req or not username:
            return False
        if "steps" not in req or "actions" not in req:
            req = self.get_request(req["id"]) or req
        ctx = self._act_context(req, username, roles)
        return acting_principal(req, req.get("steps") or [], req.get("actions") or [], username,
                                list(roles or []), ctx["delegations"], ctx["roles_lookup"], ctx["manager"]) is not None

    def current_approvers(self, req: dict) -> List[str]:
        """Usernames expected to act on the request's current step."""
        step = step_at(req.get("steps") or [], req.get("current_step"))
        if not step or req.get("status") != "pending":
            return []
        cid = req["company_id"]
        manager = self.manager_of(cid, req.get("requested_by")) if step.get("approver_type") == "manager_of_requester" else None
        approvers = resolve_step_approvers(step, self._users_by_role(cid), manager)
        # add delegates of those approvers so reminders reach whoever can act
        for d in self.get_delegations(cid):
            if d.get("is_current") and d.get("from_user") in approvers and d.get("to_user") not in approvers:
                approvers.append(d["to_user"])
        return approvers

    def get_stats(self, company_id: str, username: str = None, roles: Iterable[str] = None) -> dict:
        stats = {"total": 0, "pending": 0, "approved": 0, "rejected": 0, "cancelled": 0,
                 "overdue": 0, "my_pending": 0, "my_requests_pending": 0, "workflows_active": 0}
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT status, COUNT(*) AS c FROM approval_requests WHERE company_id=%s GROUP BY status",
                                (company_id,))
                    for r in cur.fetchall():
                        stats[r["status"]] = int(r["c"])
                    cur.execute(
                        """SELECT COUNT(*) AS c FROM approval_requests WHERE company_id=%s AND status='pending'
                           AND created_at < NOW() - %s * INTERVAL '1 day'""",
                        (company_id, ESCALATION_DAYS))
                    stats["overdue"] = int(cur.fetchone()["c"])
                    cur.execute("SELECT COUNT(*) AS c FROM approval_workflows WHERE company_id=%s AND is_active",
                                (company_id,))
                    stats["workflows_active"] = int(cur.fetchone()["c"])
                    if username:
                        cur.execute(
                            "SELECT COUNT(*) AS c FROM approval_requests WHERE company_id=%s AND status='pending' AND requested_by=%s",
                            (company_id, username))
                        stats["my_requests_pending"] = int(cur.fetchone()["c"])
            stats["total"] = sum(stats[s] for s in STATUSES)
            if username:
                stats["my_pending"] = len(self.pending_for(company_id, username, roles or []))
        except Exception as e:
            logger.error("get_stats: %s", e)
        return stats

    def get_overdue(self, days: int = None, company_id: str = None) -> List[dict]:
        """Pending requests older than ``days`` (all companies when company_id is None)."""
        days = ESCALATION_DAYS if days is None else int(days)
        try:
            sql = """SELECT r.*, w.name AS workflow_name FROM approval_requests r
                     LEFT JOIN approval_workflows w ON w.id = r.workflow_id
                     WHERE r.status='pending' AND r.created_at < NOW() - %s * INTERVAL '1 day'"""
            params: list = [days]
            if company_id:
                sql += " AND r.company_id=%s"; params.append(company_id)
            sql += " ORDER BY r.company_id, r.created_at"
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql, tuple(params))
                    rows = [dict(r) for r in cur.fetchall()]
            steps = self._steps_for([r["workflow_id"] for r in rows])
            for r in rows:
                r["steps"] = steps.get(r["workflow_id"], [])
                r["actions"] = []
                decorate_request(r)
            return rows
        except Exception as e:
            logger.error("get_overdue: %s", e); return []

    # ── Notifications (best effort, never raise) ──────────────────
    @staticmethod
    def _mail_body(req: dict, line: str) -> str:
        amt = req.get("amount_display") or "—"
        return (f"<h3>EBMS approval</h3><p><b>{req.get('title','')}</b> "
                f"({req.get('entity_type','')} · {req.get('currency','')} {amt})</p>"
                f"<p>Requested by {req.get('requested_by','')}.</p><p>{line}</p>"
                f"<p>Open: /approvals/requests/{req.get('id','')}</p>")

    def _notify_users(self, company_id: str, usernames: Iterable[str], subject: str, html: str) -> None:
        try:
            emails = self._emails_for(usernames, company_id)
            if not emails:
                return
            from email_service import send_email
            send_email(emails, subject, html, category="approval")
        except Exception as e:
            logger.warning("approval notify failed: %s", e)

    def _notify_step_approvers(self, req: dict) -> None:
        try:
            approvers = self.current_approvers(req)
            step = req.get("current_step_def") or {}
            self._notify_users(req["company_id"], approvers,
                               f"[EBMS] Approval needed: {req.get('title','')}",
                               self._mail_body(req, f"Awaiting your decision at step {req.get('current_step')}"
                                                    f"{' — ' + step['name'] if step.get('name') else ''}."))
        except Exception as e:
            logger.warning("_notify_step_approvers: %s", e)


approval_store = ApprovalDataStore()
