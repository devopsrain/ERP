"""
Pure-logic tests for the Commercial module (no database).

Covers pricing / discount / VAT totals, credit-limit check, the three-approver
state machine, commission, forecasting, fulfilment and numbering helpers.
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_WEB = Path(__file__).resolve().parents[1]
if str(_WEB) not in sys.path:
    sys.path.insert(0, str(_WEB))

import commercial_logic as L  # noqa: E402


# ── money helpers ─────────────────────────────────────────────────
def test_D_and_q2_coercion():
    assert L.D("") == 0 and L.D(None) == 0 and L.D("1,250.5") == Decimal("1250.5")
    assert L.q2("10.005") == Decimal("10.01")          # ROUND_HALF_UP
    assert L.q2(Decimal("2.345")) == Decimal("2.35")
    assert L.opt("") is None and L.opt(" ") is None and L.opt("x") == "x" and L.opt(0) == 0
    assert L.to_int("12.7") == 12 and L.to_int("", 5) == 5
    assert L.as_bool("on") and L.as_bool("1") and not L.as_bool("") and not L.as_bool(None)


def test_format_doc_no():
    assert L.format_doc_no("SO", 2026, 1) == "SO-2026-000001"
    assert L.format_doc_no("PFI", 2026, 123456) == "PFI-2026-123456"
    assert set(L.DOC_PREFIXES) >= {"proforma", "sales_order", "invoice", "delivery_instruction", "credit_note"}


# ── pricing & totals ──────────────────────────────────────────────
def test_unit_price_tiers_and_fallback():
    items = [{"item_code": "NYA-2.5", "min_qty": 0, "unit_price": "52"},
             {"item_code": "NYA-2.5", "min_qty": 1000, "unit_price": "48"},
             {"item_code": "NYA-4", "min_qty": 0, "unit_price": "80"}]
    assert L.unit_price_for("NYA-2.5", 100, items, 60) == Decimal("52.00")
    assert L.unit_price_for("NYA-2.5", 1000, items, 60) == Decimal("48.00")
    assert L.unit_price_for("NYA-6", 10, items, "95.5") == Decimal("95.50")


def test_line_total_and_normalize_lines():
    assert L.line_total(100, "52", 200) == Decimal("5000.00")
    assert L.line_total(1, 10, 50) == Decimal("0.00")          # never negative
    lines = L.normalize_lines([{"item_code": "A", "quantity": "10", "unit_price": "5"},
                               {"item_code": "", "description": "", "quantity": "3"},   # blank → dropped
                               {"item_code": "B", "quantity": "0", "unit_price": "5"}])  # zero qty → dropped
    assert len(lines) == 1 and lines[0]["line_total"] == Decimal("50.00") and lines[0]["quantity"] == Decimal("10.000")


def test_document_totals_with_vat_and_discounts():
    lines = [{"quantity": 100, "unit_price": "52", "discount": "200"}, {"quantity": 50, "unit_price": "80", "discount": 0}]
    t = L.document_totals(lines, L.VAT_RATE, order_discount="300")
    assert t["subtotal"] == Decimal("9200.00")
    assert t["discount_total"] == Decimal("500.00")
    assert t["net"] == Decimal("8700.00")
    assert t["vat_total"] == Decimal("1305.00")
    assert t["grand_total"] == Decimal("10005.00")
    empty = L.document_totals([])
    assert empty["grand_total"] == 0 and empty["vat_total"] == 0


def test_discount_rules_and_best_discount():
    rules = [{"name": "Gov 5%", "kind": "percent", "value": 5, "applies_to": "segment", "target": "government"},
             {"name": "Big order", "kind": "amount", "value": 1000, "applies_to": "order", "min_order_value": 50000},
             {"name": "Expired", "kind": "percent", "value": 50, "applies_to": "order", "valid_to": "2020-01-01"}]
    assert L.discount_applies(rules[0], segment="Government")
    assert not L.discount_applies(rules[0], segment="retail")
    assert not L.discount_applies(rules[1], order_value=1000)
    assert not L.discount_applies(rules[2], on=date(2026, 1, 1))
    best = L.best_discount(rules, 60000, segment="government", on=date(2026, 1, 1))
    assert best["name"] == "Gov 5%" and best["amount"] == Decimal("3000.00")
    best2 = L.best_discount(rules, 60000, segment="retail", on=date(2026, 1, 1))
    assert best2["name"] == "Big order" and best2["amount"] == Decimal("1000.00")
    assert L.best_discount(rules, 100, segment="retail") is None


# ── credit control ────────────────────────────────────────────────
def test_credit_check_levels():
    ok = L.credit_check(100000, 20000, 30000)
    assert ok["ok"] and ok["level"] == "ok" and ok["exceeds_by"] == 0
    block = L.credit_check(100000, 80000, 30000)
    assert not block["ok"] and block["level"] == "block" and block["exceeds_by"] == Decimal("10000.00")
    warn = L.credit_check(100000, 80000, 30000, allow_over=True)
    assert warn["ok"] and warn["level"] == "warn"
    cash = L.credit_check(0, 500000, 30000, credit_sale=False)
    assert cash["ok"] and cash["level"] == "ok"
    no_facility = L.credit_check(None, 0, 1)
    assert no_facility["level"] == "block"
    assert L.customer_balance_from("1000", "250", "50") == Decimal("700.00")


# ── three-approver state machine ─────────────────────────────────
def _steps(*decisions):
    return [{"seq": i + 1, "role_label": f"A{i + 1}", "approver": "manager", "decision": d} for i, d in enumerate(decisions)]


def test_normalize_approvers_always_three():
    assert len(L.normalize_approvers(None)) == 3
    a = L.normalize_approvers('[{"label": "Sales Mgr", "username_or_role": "abebe"}]')
    assert a[0] == {"label": "Sales Mgr", "username_or_role": "abebe"} and len(a) == 3
    b = L.normalize_approvers(["x", "y", "z", "w"])
    assert len(b) == 3 and b[2]["username_or_role"] == "z"


def test_approval_state_transitions():
    assert L.approval_state(_steps("", "", "")) == "pending_approval"
    assert L.approval_state(_steps("approved", "", "")) == "pending_approval"
    assert L.approval_state(_steps("approved", "approved", "")) == "pending_approval"
    assert L.approval_state(_steps("approved", "approved", "approved")) == "approved"
    assert L.approval_state(_steps("approved", "rejected", "")) == "rejected"
    assert L.next_approval_seq(_steps("approved", "", "")) == 2
    assert L.next_approval_seq(_steps("approved", "approved", "approved")) is None


def test_can_approve_rules():
    step_user = {"username_or_role": "abebe"}
    step_role = {"username_or_role": "manager"}
    assert L.can_approve(step_user, "abebe", "viewer")
    assert not L.can_approve(step_user, "kebede", "admin")
    assert L.can_approve(step_role, "kebede", "manager")
    assert L.can_approve(step_role, "kebede", "admin")
    assert not L.can_approve(step_role, "kebede", "operator")
    # requesters may not approve their own order
    assert not L.can_approve(step_role, "kebede", "admin", requested_by="kebede")
    assert L.can_approve(step_role, "kebede", "admin", requested_by="kebede", allow_self=True)
    assert not L.can_approve(step_role, "", "admin")


def test_so_transitions_and_derived_status():
    assert L.can_transition("draft", "pending_approval")
    assert not L.can_transition("draft", "approved")
    assert L.can_transition("approved", "in_production")
    assert not L.can_transition("closed", "draft")
    lines = [{"quantity": 100, "delivered_qty": 0, "invoiced_qty": 0}, {"quantity": 50, "delivered_qty": 0, "invoiced_qty": 0}]
    assert L.derive_so_status("approved", lines) == "approved"
    lines[0]["delivered_qty"] = 100
    assert L.derive_so_status("approved", lines) == "partially_delivered"
    lines[1]["delivered_qty"] = 50
    assert L.derive_so_status("approved", lines) == "delivered"
    lines[0]["invoiced_qty"], lines[1]["invoiced_qty"] = 100, 50
    assert L.derive_so_status("delivered", lines) == "invoiced"
    assert L.derive_so_status("draft", lines) == "draft"      # never auto-advance unapproved orders


# ── invoices & payments ───────────────────────────────────────────
def test_invoice_status_after_payment_and_due_dates():
    today = date(2026, 9, 24)
    assert L.invoice_status_after(1000, 0, date(2026, 10, 1), today) == "issued"
    assert L.invoice_status_after(1000, 400, date(2026, 10, 1), today) == "partially_paid"
    assert L.invoice_status_after(1000, 1000, date(2026, 10, 1), today) == "paid"
    assert L.invoice_status_after(1000, 400, date(2026, 9, 1), today) == "overdue"
    assert L.invoice_status_after(1000, 1000, date(2026, 9, 1), today) == "paid"
    assert L.invoice_status_after(1000, 0, None, today, current="cancelled") == "cancelled"
    assert L.due_date_for("2026-09-01", 30) == date(2026, 10, 1)
    assert L.due_date_for(None, 30) is None
    assert L.days_overdue(date(2026, 9, 20), today) == 4 and L.days_overdue(date(2026, 9, 30), today) == 0


# ── commissions ───────────────────────────────────────────────────
def test_commission():
    assert L.commission("10000", "2.5") == Decimal("250.00")
    assert L.commission(0, 5) == 0
    assert L.commission_base({"subtotal": "11500", "discount_total": "500"}) == Decimal("11000.00")


# ── forecasting ───────────────────────────────────────────────────
def test_moving_average_and_linear_trend():
    assert L.moving_average([10, 20, 30, 40], 3) == Decimal("30.00")
    assert L.moving_average([], 3) == 0
    assert L.linear_trend([10, 20, 30]) == Decimal("40.00")
    assert L.linear_trend([5]) == Decimal("5.00")
    assert L.linear_trend([50, 40, 30, 20, 10, 0]) == Decimal("0.00")   # never negative
    assert L.forecast_next([10, 20, 30], "linear") == Decimal("40.00")
    assert L.forecast_next([10, 20, 30], "moving_avg") == Decimal("20.00")


def test_month_helpers_and_build_forecast():
    assert L.month_sequence("2026-03", 4) == ["2025-12", "2026-01", "2026-02", "2026-03"]
    assert L.next_period("2026-12") == "2027-01"
    assert L.period_of(date(2026, 9, 24)) == "2026-09"
    hist = {"NYA-2.5": {"2026-04": 100, "2026-05": 120, "2026-06": 140, "2026-07": 160, "2026-08": 180, "2026-09": 200},
            "DEAD": {}}
    rows = L.build_forecast(hist, "2026-10", 6)
    assert len(rows) == 1 and rows[0]["key"] == "NYA-2.5"
    r = rows[0]
    assert r["ma3"] == Decimal("180.00") and r["ma6"] == Decimal("150.00") and r["linear"] == Decimal("220.00")
    assert r["forecast_qty"] == Decimal("183.33")


# ── KPIs ──────────────────────────────────────────────────────────
def test_fulfilment_on_time_and_marketing_kpis():
    f = L.fulfilment(100, 60, 30)
    assert f["backlog"] == Decimal("40.000") and f["delivered_pct"] == Decimal("60.00") and f["invoiced_pct"] == Decimal("30.00")
    assert L.fulfilment(0, 0)["delivered_pct"] == 0
    deliveries = [{"required_date": "2026-09-10", "delivered_at": "2026-09-09"},
                  {"required_date": "2026-09-10", "delivered_at": "2026-09-12"},
                  {"required_date": None, "delivered_at": "2026-09-12"}]
    assert L.on_time_pct(deliveries) == Decimal("50.00")
    assert L.campaign_roi(1000, 2500) == Decimal("150.00") and L.campaign_roi(0, 100) is None
    assert L.conversion_rate(40, 10) == Decimal("25.00") and L.conversion_rate(0, 0) == 0
    split = L.retention_split({"c1": ["2026-08", "2026-09"], "c2": ["2026-09"], "c3": ["2026-07"]}, "2026-09")
    assert split == {"period": "2026-09", "new": 1, "returning": 1, "active": 2}


def test_status_constant_sets_are_consistent():
    for s, nexts in L.SO_TRANSITIONS.items():
        assert s in L.SO_STATUSES
        assert set(nexts) <= set(L.SO_STATUSES)
    assert "telebirr" in L.PAYMENT_METHODS and "cbebirr" in L.PAYMENT_METHODS
