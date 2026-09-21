"""
Pure-logic tests for the Approval Engine — no database required.

Covers: amount-threshold matching, workflow selection, step resolution,
delegation windows, require_all / sequential step outcomes, and next-approver
computation (acting_principal), plus the jobs module's import safety.
"""
import sys
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import approval_data_store as ads  # noqa: E402
from approval_data_store import (  # noqa: E402
    ApprovalDataStore, acting_principal, active_delegators, amount_matches, build_principals,
    can_act_on, decorate_request, first_step_order, matching_principal, next_step_order,
    request_delegations, resolve_step_approvers, select_workflow, step_at, step_outcome,
    to_decimal, to_int, to_bool,
)


def _step(order, a_type="role", a_value="manager", require_all=False, sla=None):
    return {"step_order": order, "approver_type": a_type, "approver_value": a_value,
            "require_all": require_all, "sla_hours": sla, "name": ""}


def _wf(name, et="expense", lo=None, hi=None, active=True):
    return {"id": name, "name": name, "entity_type": et, "min_amount": lo, "max_amount": hi,
            "is_active": active, "steps": [_step(1)]}


def _req(status="pending", current_step=1, requested_by="carol"):
    return {"id": "r1", "status": status, "current_step": current_step, "requested_by": requested_by}


def _act(action, actor, step=1, on_behalf_of="", delegate_to=""):
    return {"action": action, "actor": actor, "step_order": step,
            "on_behalf_of": on_behalf_of, "delegate_to": delegate_to}


# ── conversions ───────────────────────────────────────────────────

def test_to_decimal_handles_blank_and_garbage():
    assert to_decimal("") is None and to_decimal(None) is None
    assert to_decimal("1,250.75") == Decimal("1250.75")
    assert to_decimal(10) == Decimal(10)
    assert to_decimal("abc") is None


def test_to_int_and_to_bool():
    assert to_int("") is None and to_int("24") == 24 and to_int("x") is None
    assert to_bool("on") and to_bool("1") and to_bool(True)
    assert not to_bool("") and not to_bool(None) and not to_bool("false")


# ── amount thresholds ─────────────────────────────────────────────

def test_amount_matches_open_bounds_and_inclusive_edges():
    assert amount_matches(100, None, None)
    assert amount_matches(100, 100, 100)
    assert amount_matches(0, None, 50)
    assert not amount_matches(51, None, 50)
    assert not amount_matches(99.99, 100, None)
    assert amount_matches("1,000", "500", "2000")


def test_amount_matches_treats_missing_amount_as_zero():
    assert amount_matches(None, None, 1000)      # leave requests have no amount
    assert not amount_matches(None, 1, None)
    assert amount_matches("", None, None)


# ── workflow selection ────────────────────────────────────────────

def test_select_workflow_none_when_no_entity_type_match():
    assert select_workflow([_wf("a", et="leave")], "expense", 10) is None
    assert select_workflow([], "expense", 10) is None


def test_select_workflow_skips_inactive_and_out_of_range():
    wfs = [_wf("inactive", active=False), _wf("big", lo=10000)]
    assert select_workflow(wfs, "expense", 500) is None


def test_select_workflow_prefers_most_specific_range():
    wfs = [_wf("any"), _wf("above1k", lo=1000), _wf("1k-5k", lo=1000, hi=5000), _wf("1k-2k", lo=1000, hi=2000)]
    assert select_workflow(wfs, "expense", 1500)["name"] == "1k-2k"
    assert select_workflow(wfs, "expense", 3000)["name"] == "1k-5k"
    assert select_workflow(wfs, "expense", 9000)["name"] == "above1k"
    assert select_workflow(wfs, "expense", 10)["name"] == "any"


def test_select_workflow_tie_keeps_input_order():
    wfs = [_wf("first", lo=0, hi=100), _wf("second", lo=0, hi=100)]
    assert select_workflow(wfs, "expense", 50)["name"] == "first"


# ── step ordering ─────────────────────────────────────────────────

def test_step_ordering_helpers():
    steps = [_step(3), _step(1), _step(2)]
    assert first_step_order(steps) == 1
    assert next_step_order(steps, 1) == 2
    assert next_step_order(steps, 2) == 3
    assert next_step_order(steps, 3) is None
    assert next_step_order(steps, None) == 1
    assert step_at(steps, 2)["step_order"] == 2
    assert step_at(steps, 9) is None and step_at(steps, None) is None
    assert first_step_order([]) is None


def test_step_ordering_tolerates_gaps():
    steps = [_step(10), _step(20)]
    assert next_step_order(steps, 10) == 20
    assert next_step_order(steps, 15) == 20


# ── delegation windows ────────────────────────────────────────────

def test_active_delegators_respects_date_window():
    today = date(2026, 9, 12)
    ds = [
        {"from_user": "alice", "to_user": "bob", "starts_on": date(2026, 9, 1), "ends_on": date(2026, 9, 30)},
        {"from_user": "past", "to_user": "bob", "starts_on": date(2026, 8, 1), "ends_on": date(2026, 8, 31)},
        {"from_user": "future", "to_user": "bob", "starts_on": "2026-10-01", "ends_on": None},
        {"from_user": "open", "to_user": "bob", "starts_on": None, "ends_on": None},
        {"from_user": "other", "to_user": "zed", "starts_on": None, "ends_on": None},
        {"from_user": "bob", "to_user": "bob", "starts_on": None, "ends_on": None},   # self — ignored
    ]
    assert active_delegators(ds, "bob", today) == ["alice", "open"]
    assert active_delegators(ds, "nobody", today) == []
    assert active_delegators(None, "bob", today) == []


def test_request_delegations_only_current_step():
    acts = [_act("delegate", "bob", 1, delegate_to="erin"), _act("delegate", "bob", 2, delegate_to="fay"),
            _act("approve", "x", 1)]
    d = request_delegations(acts, 2)
    assert d == [{"from_user": "bob", "to_user": "fay", "starts_on": None, "ends_on": None}]


def test_build_principals_includes_delegators_with_their_roles():
    ds = [{"from_user": "alice", "to_user": "bob", "starts_on": None, "ends_on": None}]
    ps = build_principals("bob", ["operator"], ds, {"alice": {"manager"}})
    assert ps == [("bob", {"operator"}), ("alice", {"manager"})]


# ── step resolution ───────────────────────────────────────────────

def test_matching_principal_role_user_manager():
    ps = [("bob", {"operator"}), ("alice", {"manager"})]
    assert matching_principal(_step(1, "role", "manager"), ps) == "alice"
    assert matching_principal(_step(1, "role", "admin"), ps) is None
    assert matching_principal(_step(1, "user", "bob"), ps) == "bob"
    assert matching_principal(_step(1, "user", ""), ps) is None
    # manager_of_requester: resolved manager wins
    assert matching_principal(_step(1, "manager_of_requester", ""), ps, manager="bob") == "bob"
    assert matching_principal(_step(1, "manager_of_requester", ""), ps, manager="zed") is None
    # no HRM manager -> fallback approver_value (user or role), then 'manager' role
    assert matching_principal(_step(1, "manager_of_requester", "bob"), ps) == "bob"
    assert matching_principal(_step(1, "manager_of_requester", "admin"), ps) is None
    assert matching_principal(_step(1, "manager_of_requester", ""), ps) == "alice"
    assert matching_principal(_step(1, "bogus", "x"), ps) is None


def test_resolve_step_approvers():
    by_role = {"admin": ["bob", "dan", "bob"], "manager": ["alice"]}
    assert resolve_step_approvers(_step(1, "role", "admin"), by_role) == ["bob", "dan"]
    assert resolve_step_approvers(_step(1, "role", "nobody"), by_role) == []
    assert resolve_step_approvers(_step(1, "user", "erin"), by_role) == ["erin"]
    assert resolve_step_approvers(_step(1, "manager_of_requester", ""), by_role, manager="gus") == ["gus"]
    assert resolve_step_approvers(_step(1, "manager_of_requester", "admin"), by_role) == ["bob", "dan"]
    assert resolve_step_approvers(_step(1, "manager_of_requester", "hal"), by_role) == ["hal"]
    assert resolve_step_approvers(_step(1, "manager_of_requester", ""), by_role) == ["alice"]


# ── step outcomes ─────────────────────────────────────────────────

def test_step_outcome_single_approval_completes_step():
    step = _step(1, "role", "admin")
    assert step_outcome(step, ["bob", "dan"], []) == "pending"
    assert step_outcome(step, ["bob", "dan"], [_act("approve", "bob")]) == "approved"
    assert step_outcome(step, ["bob", "dan"], [_act("comment", "bob")]) == "pending"


def test_step_outcome_reject_wins():
    step = _step(1, "role", "admin")
    assert step_outcome(step, ["bob"], [_act("approve", "bob"), _act("reject", "dan")]) == "rejected"


def test_step_outcome_require_all_needs_everyone():
    step = _step(1, "role", "admin", require_all=True)
    approvers = ["bob", "dan"]
    assert step_outcome(step, approvers, [_act("approve", "bob")]) == "pending"
    assert step_outcome(step, approvers, [_act("approve", "bob"), _act("approve", "dan")]) == "approved"
    # a delegate's approval is credited to the delegator
    assert step_outcome(step, approvers, [_act("approve", "bob"), _act("approve", "erin", on_behalf_of="dan")]) == "approved"
    # actions from other steps are ignored
    assert step_outcome(step, approvers, [_act("approve", "bob"), _act("approve", "dan", step=2)]) == "pending"
    # require_all with no resolvable approvers degrades to single approval
    assert step_outcome(step, [], [_act("approve", "bob")]) == "approved"


# ── next-approver computation ─────────────────────────────────────

def test_acting_principal_basic_role_match():
    steps = [_step(1, "role", "manager"), _step(2, "role", "admin")]
    assert acting_principal(_req(), steps, [], "alice", ["manager"]) == "alice"
    assert acting_principal(_req(), steps, [], "bob", ["admin"]) is None          # step 2 not yet active
    assert acting_principal(_req(current_step=2), steps, [], "bob", ["admin"]) == "bob"
    assert can_act_on(_req(current_step=2), steps, [], "alice", ["manager"]) is False


def test_acting_principal_blocks_non_pending_and_missing_step():
    steps = [_step(1)]
    assert acting_principal(_req(status="approved"), steps, [], "alice", ["manager"]) is None
    assert acting_principal(_req(current_step=5), steps, [], "alice", ["manager"]) is None
    assert acting_principal(None, steps, [], "alice", ["manager"]) is None
    assert acting_principal(_req(), steps, [], "", ["manager"]) is None


def test_acting_principal_blocks_self_approval(monkeypatch):
    steps = [_step(1, "role", "manager")]
    monkeypatch.setattr(ads, "ALLOW_SELF_APPROVAL", False)
    assert acting_principal(_req(requested_by="alice"), steps, [], "alice", ["manager"]) is None
    monkeypatch.setattr(ads, "ALLOW_SELF_APPROVAL", True)
    assert acting_principal(_req(requested_by="alice"), steps, [], "alice", ["manager"]) == "alice"


def test_acting_principal_excludes_users_who_already_approved():
    steps = [_step(1, "role", "admin", require_all=True)]
    acts = [_act("approve", "bob")]
    assert acting_principal(_req(), steps, acts, "bob", ["admin"]) is None
    assert acting_principal(_req(), steps, acts, "dan", ["admin"]) == "dan"


def test_acting_principal_via_standing_delegation():
    steps = [_step(1, "user", "alice")]
    ds = [{"from_user": "alice", "to_user": "bob", "starts_on": None, "ends_on": None}]
    assert acting_principal(_req(), steps, [], "bob", ["operator"]) is None
    assert acting_principal(_req(), steps, [], "bob", ["operator"], delegations=ds) == "alice"
    # delegator's role counts too
    role_steps = [_step(1, "role", "manager")]
    assert acting_principal(_req(), role_steps, [], "bob", ["operator"], delegations=ds,
                            roles_lookup={"alice": {"manager"}}) == "alice"
    # once the delegator has approved, the delegate can no longer act for them
    assert acting_principal(_req(), steps, [_act("approve", "alice")], "bob", [], delegations=ds) is None


def test_acting_principal_via_per_request_delegate_action():
    steps = [_step(1, "user", "alice"), _step(2, "user", "zed")]
    acts = [_act("delegate", "alice", 1, delegate_to="erin")]
    assert acting_principal(_req(), steps, acts, "erin", []) == "alice"
    assert acting_principal(_req(current_step=2), steps, acts, "erin", []) is None   # other step


def test_acting_principal_manager_of_requester():
    steps = [_step(1, "manager_of_requester", "")]
    assert acting_principal(_req(), steps, [], "gus", ["operator"], manager="gus") == "gus"
    assert acting_principal(_req(), steps, [], "alice", ["manager"], manager="gus") is None
    assert acting_principal(_req(), steps, [], "alice", ["manager"], manager=None) == "alice"


def test_sequential_two_step_flow_end_to_end():
    """Simulate the decide() control flow with the pure functions only."""
    steps = [_step(1, "role", "manager"), _step(2, "role", "admin", require_all=True)]
    by_role = {"manager": ["alice"], "admin": ["bob", "dan"]}
    req = _req()
    actions = []

    # step 1: alice approves -> step complete -> advance to 2
    p = acting_principal(req, steps, actions, "alice", ["manager"])
    assert p == "alice"
    actions.append(_act("approve", "alice", 1))
    assert step_outcome(steps[0], resolve_step_approvers(steps[0], by_role), actions) == "approved"
    req["current_step"] = next_step_order(steps, 1)
    assert req["current_step"] == 2

    # step 2: needs both admins
    assert acting_principal(req, steps, actions, "alice", ["manager"]) is None
    actions.append(_act("approve", "bob", 2))
    assert step_outcome(steps[1], resolve_step_approvers(steps[1], by_role), actions) == "pending"
    assert acting_principal(req, steps, actions, "bob", ["admin"]) is None       # already approved
    assert acting_principal(req, steps, actions, "dan", ["admin"]) == "dan"
    actions.append(_act("approve", "dan", 2))
    assert step_outcome(steps[1], resolve_step_approvers(steps[1], by_role), actions) == "approved"
    assert next_step_order(steps, 2) is None                                      # -> request approved


# ── decoration / form parsing (no DB) ─────────────────────────────

def test_decorate_request_never_raises_on_sparse_data():
    r = decorate_request({"id": "x", "status": "pending", "steps": [], "current_step": 1})
    assert r["amount_display"] == "—" and r["created_display"] == "" and r["age_hours"] == 0.0
    assert r["current_step_def"] is None and r["sla_breached"] is False
    assert decorate_request(None) is None


def test_decorate_request_parses_payload_and_sla():
    old = datetime.now() - timedelta(hours=30)
    r = decorate_request({"id": "x", "status": "pending", "current_step": 1, "amount": "1234.5",
                          "payload": '{"a": 1}', "created_at": old, "steps": [_step(1, sla=24)]})
    assert r["payload"] == {"a": 1}
    assert r["amount_display"] == "1,234.50"
    assert r["sla_breached"] is True
    assert r["age_days"] >= 1.2
    assert decorate_request({"status": "pending", "payload": "not json", "steps": []})["payload"] == {}


def test_clean_steps_drops_blank_rows_and_renumbers():
    raw = [
        {"step_order": 1, "approver_type": "role", "approver_value": ""},               # blank -> dropped
        {"step_order": 2, "approver_type": "user", "approver_value": " bob ", "sla_hours": "48", "require_all": "on"},
        {"step_order": 3, "approver_type": "manager_of_requester", "approver_value": ""},  # kept without value
        {"step_order": 4, "approver_type": "alien", "approver_value": "x"},              # unknown type -> dropped
        {"step_order": 5, "approver_type": "role", "approver_value": "admin", "sla_hours": ""},
    ]
    clean = ApprovalDataStore._clean_steps(raw)
    assert [s["step_order"] for s in clean] == [1, 2, 3]
    assert clean[0] == {"step_order": 1, "name": "", "approver_type": "user", "approver_value": "bob",
                        "require_all": True, "sla_hours": 48}
    assert clean[1]["approver_type"] == "manager_of_requester"
    assert clean[2]["sla_hours"] is None and clean[2]["require_all"] is False
    assert ApprovalDataStore._clean_steps([]) == []


# ── jobs module ───────────────────────────────────────────────────

def test_jobs_import_is_side_effect_free_and_registers_cron():
    import approval_jobs

    class FakeScheduler:
        def __init__(self):
            self.jobs = []

        def add_job(self, func, trigger=None, id=None, replace_existing=False, **kw):
            self.jobs.append((func, trigger, id, replace_existing))

    sched = FakeScheduler()
    approval_jobs.register_jobs(sched)
    assert len(sched.jobs) == 1
    func, trigger, job_id, replace = sched.jobs[0]
    assert func is approval_jobs.send_escalation_reminders
    assert job_id == approval_jobs.JOB_ID and replace is True
    fields = {f.name: str(f) for f in trigger.fields}
    assert fields["hour"] == "8" and fields["minute"] == "30"
