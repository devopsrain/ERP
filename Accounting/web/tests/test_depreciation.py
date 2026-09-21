"""
Pure-math tests for the fixed-asset depreciation schedule. No database.
"""
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

from fixed_assets_data_store import (  # noqa: E402
    D, gain_loss, month_units, normalize_rate, previous_period, q2, schedule, shift_period,
)


def _total(rows):
    return sum((r["amount"] for r in rows), Decimal(0))


def _assert_invariants(rows, cost, salvage):
    """Shared checks: 2dp amounts, monotone accumulation, salvage floor, exact plug."""
    cost, salvage = D(cost), D(salvage)
    acc = Decimal(0)
    for r in rows:
        assert r["amount"] == q2(r["amount"])
        assert r["amount"] >= 0
        acc += r["amount"]
        assert r["accumulated"] == acc
        assert r["book_value"] == cost - acc
        assert r["book_value"] >= salvage
    assert rows[-1]["book_value"] == salvage
    assert _total(rows) == cost - salvage


# ── helpers ──────────────────────────────────────────────────────

def test_shift_period_wraps_years():
    assert shift_period("2026-01", -1) == "2025-12"
    assert shift_period("2026-12", 1) == "2027-01"
    assert shift_period("2026-06", 30) == "2028-12"


def test_previous_period():
    assert previous_period(date(2026, 1, 15)) == "2025-12"
    assert previous_period(date(2026, 9, 1)) == "2026-08"


def test_normalize_rate_accepts_percent_or_fraction():
    assert normalize_rate(25) == Decimal("0.25")
    assert normalize_rate("0.2") == Decimal("0.2")
    assert normalize_rate("") is None
    assert normalize_rate(0) is None


def test_month_units_prorates_first_month_and_sums_to_life():
    units = month_units(date(2026, 1, 16), 12)       # 16 of 31 days remaining
    assert units[0] == Decimal(16) / Decimal(31)
    assert len(units) == 13
    assert sum(units) == Decimal(12)
    assert month_units(date(2026, 3, 1), 12) == [Decimal(1)] * 12


def test_gain_loss():
    assert gain_loss("1200", "1000") == Decimal("200.00")
    assert gain_loss("", "1000") == Decimal("-1000.00")


# ── straight line ────────────────────────────────────────────────

def test_straight_line_full_months():
    rows = schedule(1200, 0, 12, "straight_line", "2026-01-01")
    assert len(rows) == 12
    assert rows[0]["period"] == "2026-01" and rows[-1]["period"] == "2026-12"
    assert all(r["amount"] == Decimal("100.00") for r in rows)
    _assert_invariants(rows, 1200, 0)


def test_straight_line_partial_first_month_spills_into_extra_period():
    rows = schedule(1200, 200, 12, "straight_line", date(2026, 1, 16))
    assert len(rows) == 13
    assert rows[0]["amount"] == q2(Decimal("83.333333") * 16 / 31)   # 43.01
    assert rows[1]["amount"] == Decimal("83.33")
    assert rows[-1]["period"] == "2027-01"
    assert rows[-1]["amount"] == Decimal("40.36")                    # plug: 1000 - 959.64
    _assert_invariants(rows, 1200, 200)


def test_straight_line_rounding_plug_reaches_salvage_exactly():
    rows = schedule("1000", "0", 36, "straight_line", "2026-01-01")   # 27.777… per month
    assert rows[0]["amount"] == Decimal("27.78")
    assert rows[-1]["amount"] == Decimal("1000.00") - Decimal("27.78") * 35
    _assert_invariants(rows, 1000, 0)


# ── declining balance ────────────────────────────────────────────

def test_declining_balance_starts_high_and_switches_to_straight_line():
    rows = schedule(10000, 1000, 48, "declining_balance", "2026-01-01", declining_rate=25)
    assert len(rows) == 48
    assert rows[0]["amount"] == Decimal("208.33")                     # 10000 * 25% / 12
    assert rows[1]["amount"] < rows[0]["amount"]
    # once SL over the remaining life exceeds DB, the charge flattens out
    tail = [r["amount"] for r in rows[-6:]]
    assert max(tail) - min(tail) <= Decimal("0.02")
    _assert_invariants(rows, 10000, 1000)


def test_declining_balance_default_rate_is_double_declining():
    rows = schedule(6000, 0, 60, "declining_balance", "2026-01-01")   # 5 years → 40%/yr
    assert rows[0]["amount"] == Decimal("200.00")                     # 6000 * 40% / 12
    _assert_invariants(rows, 6000, 0)


def test_declining_balance_never_below_salvage_with_high_rate():
    rows = schedule(1000, 900, 24, "declining_balance", "2026-01-01", declining_rate=90)
    _assert_invariants(rows, 1000, 900)
    assert all(r["book_value"] >= Decimal(900) for r in rows)


# ── sum of years' digits ─────────────────────────────────────────

def test_sum_of_years_whole_years():
    rows = schedule(6000, 0, 36, "sum_of_years", "2026-01-01")        # digits 3,2,1 → 3000/2000/1000
    assert len(rows) == 36
    assert _total(rows[:12]) == Decimal("3000.00")
    # 2000/12 = 166.666… rounds to 166.67 per month; the drift is at most half a
    # cent per period and is absorbed by the final plug (checked in invariants)
    assert abs(_total(rows[12:24]) - Decimal("2000.00")) <= Decimal("0.06")
    assert rows[0]["amount"] == Decimal("250.00")
    assert rows[12]["amount"] == Decimal("166.67")
    assert rows[24]["amount"] == Decimal("83.33")
    _assert_invariants(rows, 6000, 0)


def test_sum_of_years_fractional_life_and_partial_month():
    rows = schedule(4500, 0, 30, "sum_of_years", "2026-01-10")        # digits 2.5, 1.5, 0.5
    assert len(rows) == 31
    # first period is prorated (22/31 of a month); a full month inside year 1
    # carries the 2.5 digit, a full month inside the half-year 3 the 0.5 digit
    assert rows[0]["amount"] == q2(Decimal(4500) * Decimal("2.5") / Decimal("4.5") / 12 * 22 / 31)
    assert rows[1]["amount"] == Decimal("208.33")                     # 4500 * 2.5/4.5 / 12
    assert rows[25]["amount"] == Decimal("83.33")                     # 4500 * 0.5/4.5 / 6
    # the service-year boundary (12 month-units) falls inside row 12
    assert _total(rows[:12]) < Decimal("2500") < _total(rows[:13])
    _assert_invariants(rows, 4500, 0)


# ── units of production ──────────────────────────────────────────

def test_units_of_production_proportional_and_capped():
    rows = schedule(1000, 100, 0, "units_of_production", None, total_units=900,
                    units_by_period={"2026-02": 500, "2026-01": 100, "2026-03": 400})
    assert [r["period"] for r in rows] == ["2026-01", "2026-02", "2026-03"]
    assert [r["amount"] for r in rows] == [Decimal("100.00"), Decimal("500.00"), Decimal("300.00")]
    assert rows[-1]["units_used"] == Decimal(400)
    _assert_invariants(rows, 1000, 100)


def test_units_of_production_requires_units():
    assert schedule(1000, 0, 0, "units_of_production", None, total_units=900) == []
    assert schedule(1000, 0, 0, "units_of_production", None, total_units=0,
                    units_by_period={"2026-01": 10}) == []


# ── degenerate inputs ────────────────────────────────────────────

@pytest.mark.parametrize("cost,salvage,life,start", [
    (0, 0, 12, "2026-01-01"),        # nothing to depreciate
    (1000, 1000, 12, "2026-01-01"),  # cost == salvage
    (1000, 1200, 12, "2026-01-01"),  # salvage above cost
    (1000, 0, 0, "2026-01-01"),      # zero life
    (1000, 0, 12, None),             # no in-service date
    (1000, 0, 12, "not-a-date"),
])
def test_schedule_empty_for_degenerate_inputs(cost, salvage, life, start):
    assert schedule(cost, salvage, life, "straight_line", start) == []


def test_schedule_accepts_form_strings_and_unknown_method_falls_back_to_sl():
    rows = schedule("1,200.00", "", "12", "", "2026-02-01")
    assert len(rows) == 12 and rows[0]["amount"] == Decimal("100.00")
    _assert_invariants(rows, 1200, 0)
