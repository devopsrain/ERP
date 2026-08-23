"""
Forecast service — extrapolate monthly finance and payroll inputs to
end-of-year (EOY) values using simple, robust statistical methods.

Provides two public entry points:
  - forecast_finance(company_id, year) — forecasts revenue, expense, net
    based on monthly GL entries (fin_gl_entries) merged with VAT records
    (vat_income / vat_expenses).
  - forecast_payroll(company_id, year) — forecasts gross, net, tax, pension
    based on monthly payroll_data, falling back to a flat projection from
    current employee salaries when no payroll runs exist for the year.

Each returns a dict with:
  - actuals:    list of 12 monthly values (zeros for months with no data)
  - forecast:   list of 12 monthly values (actuals for past months,
                projected for remaining months)
  - eoy_total:  projected sum for the fiscal year
  - method:     name of extrapolation method used
  - confidence: simple heuristic (0-1) based on number of observed months
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

from db import get_tenant_cursor

logger = logging.getLogger(__name__)

MONTHS = 12


# ── Core statistical helpers ──────────────────────────────────────

def _linear_regression(xs: List[float], ys: List[float]) -> Tuple[float, float]:
    """Return (slope, intercept) for ordinary least-squares y = slope*x + intercept."""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    if n == 1:
        return 0.0, ys[0]
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    den = sum((x - mean_x) ** 2 for x in xs)
    slope = num / den if den else 0.0
    intercept = mean_y - slope * mean_x
    return slope, intercept


def _extrapolate(monthly: List[float], observed_through: int) -> Tuple[List[float], str]:
    """
    Given 12 monthly values (actuals zero-padded for months not yet seen) and
    the number of observed months (1-12), return (forecast_12, method_name).
    Forecast keeps actual values for observed months and projects the rest.
    """
    observed_through = max(1, min(MONTHS, observed_through))
    actuals = monthly[:observed_through]

    if observed_through >= MONTHS:
        return list(monthly), "actual"

    non_zero = [v for v in actuals if v != 0]
    # If we have at least 3 months of data, use linear regression.
    if len(non_zero) >= 3:
        xs = list(range(1, observed_through + 1))
        slope, intercept = _linear_regression([float(x) for x in xs], list(actuals))
        projected = [slope * m + intercept for m in range(observed_through + 1, MONTHS + 1)]
        # Clip negative forecasts to zero for accounting sanity.
        projected = [max(0.0, p) for p in projected]
        return list(actuals) + projected, "linear_regression"

    # With sparse data, fall back to the running average.
    avg = (sum(actuals) / observed_through) if observed_through else 0.0
    projected = [avg] * (MONTHS - observed_through)
    return list(actuals) + projected, "monthly_average"


def _confidence(observed_months: int) -> float:
    """Heuristic confidence 0..1 based on how much of the year is observed."""
    if observed_months <= 0:
        return 0.0
    return round(min(1.0, observed_months / MONTHS), 2)


def _current_month_of_year(year: int) -> int:
    today = date.today()
    if today.year > year:
        return MONTHS
    if today.year < year:
        return 0
    return today.month


def merge_monthly(*series: List[float]) -> List[float]:
    """
    Element-wise sum of any number of 12-month value lists.

    Pure function (no DB access) so the merge logic is unit-testable.
    Short or malformed inputs are tolerated: missing months count as zero,
    non-numeric entries are skipped.
    """
    merged = [0.0] * MONTHS
    for s in series:
        if not s:
            continue
        for i, v in enumerate(s[:MONTHS]):
            try:
                merged[i] += float(v or 0)
            except (TypeError, ValueError):
                continue
    return merged


# ── Finance forecast ──────────────────────────────────────────────

def _fetch_vat_monthly(company_id: str, year: int, table: str, date_expr: str) -> List[float]:
    """
    Monthly SUM(gross_amount) for a VAT table ('vat_income' / 'vat_expenses'),
    grouped by month of `date_expr`, for active rows of the company/year.
    Missing table or any query failure yields all zeros.
    `table` and `date_expr` are internal constants — never user input.
    """
    values = [0.0] * MONTHS
    try:
        with get_tenant_cursor(company_id) as cur:
            cur.execute(
                f"""
                SELECT EXTRACT(MONTH FROM {date_expr})::int AS m,
                       COALESCE(SUM(gross_amount), 0) AS amt
                FROM {table}
                WHERE company_id = %s
                  AND is_active = TRUE
                  AND EXTRACT(YEAR FROM {date_expr}) = %s
                GROUP BY m
                """,
                (company_id, year),
            )
            rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("forecast_service: %s query failed for %s: %s", table, company_id, e)
        rows = []

    for r in rows:
        try:
            m = int(r.get("m") or 0)
            if 1 <= m <= MONTHS:
                values[m - 1] += float(r.get("amt") or 0)
        except Exception:
            continue
    return values


def _fetch_finance_monthly(company_id: str, year: int) -> Tuple[Dict[str, List[float]], List[str]]:
    """
    Return ({'revenue': [...12], 'expense': [...12], 'net': [...12]}, sources).

    Revenue = GL revenue + vat_income gross amounts (by income_date, falling
    back to contract_date). Expense = GL expenses + vat_expenses gross
    amounts (by expense_date). `sources` lists the tables that contributed
    non-zero data. Each query is independently guarded — a missing table
    simply contributes zeros.

    GL convention: credit entries on accounts starting with '4' are revenue,
    debit entries on accounts starting with '5' or '6' are expenses.
    Fallback: treat all credits as revenue, all debits as expenses.
    """
    revenue = [0.0] * MONTHS
    expense = [0.0] * MONTHS
    try:
        with get_tenant_cursor(company_id) as cur:
            cur.execute(
                """
                SELECT EXTRACT(MONTH FROM entry_date)::int AS m,
                       account_code,
                       entry_type,
                       COALESCE(SUM(amount), 0) AS amt
                FROM fin_gl_entries
                WHERE company_id = %s
                  AND EXTRACT(YEAR FROM entry_date) = %s
                GROUP BY m, account_code, entry_type
                """,
                (company_id, year),
            )
            rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("forecast_service: GL query failed for %s: %s", company_id, e)
        rows = []

    for r in rows:
        try:
            m = int(r.get("m") or 0)
            if m < 1 or m > MONTHS:
                continue
            code = str(r.get("account_code") or "").strip()
            etype = str(r.get("entry_type") or "").lower()
            amt = float(r.get("amt") or 0)
        except Exception:
            continue
        is_revenue = code.startswith("4") or (not code and etype == "credit")
        is_expense = code.startswith(("5", "6")) or (not code and etype == "debit")
        if is_revenue and etype == "credit":
            revenue[m - 1] += amt
        elif is_expense and etype == "debit":
            expense[m - 1] += amt

    vat_income = _fetch_vat_monthly(
        company_id, year, "vat_income", "COALESCE(income_date, contract_date)"
    )
    vat_expenses = _fetch_vat_monthly(company_id, year, "vat_expenses", "expense_date")

    sources: List[str] = []
    if any(revenue) or any(expense):
        sources.append("fin_gl_entries")
    if any(vat_income):
        sources.append("vat_income")
    if any(vat_expenses):
        sources.append("vat_expenses")

    revenue = merge_monthly(revenue, vat_income)
    expense = merge_monthly(expense, vat_expenses)
    net = [r - e for r, e in zip(revenue, expense)]
    return {"revenue": revenue, "expense": expense, "net": net}, sources


def forecast_finance(company_id: str, year: Optional[int] = None) -> Dict[str, Any]:
    """End-of-year forecast for finance revenue, expense, and net income."""
    year = int(year or date.today().year)
    monthly, sources = _fetch_finance_monthly(company_id, year)
    observed = _current_month_of_year(year)

    series: Dict[str, Any] = {}
    method_used = "actual"
    for key, values in monthly.items():
        forecast_vals, method = _extrapolate(values, observed)
        method_used = method
        series[key] = {
            "actuals": [round(v, 2) for v in values],
            "forecast": [round(v, 2) for v in forecast_vals],
            "ytd_actual": round(sum(values[:observed]), 2),
            "eoy_projected": round(sum(forecast_vals), 2),
        }

    return {
        "module": "finance",
        "company_id": company_id,
        "fiscal_year": year,
        "observed_months": observed,
        "method": method_used,
        "confidence": _confidence(observed),
        "series": series,
        "sources": sources,
    }


# ── Payroll forecast ──────────────────────────────────────────────

def _fetch_payroll_monthly(company_id: str, year: int) -> Dict[str, List[float]]:
    """
    Return monthly totals for gross, net, income_tax, pension.
    """
    gross = [0.0] * MONTHS
    net = [0.0] * MONTHS
    tax = [0.0] * MONTHS
    pension = [0.0] * MONTHS
    try:
        with get_tenant_cursor(company_id) as cur:
            cur.execute(
                """
                SELECT month,
                       COALESCE(SUM(gross_salary), 0)     AS gross,
                       COALESCE(SUM(net_salary), 0)       AS net,
                       COALESCE(SUM(income_tax), 0)       AS tax,
                       COALESCE(SUM(pension), 0)          AS pension
                FROM payroll_data
                WHERE year = %s AND company_id = %s
                GROUP BY month
                """,
                (year, company_id),
            )
            rows = cur.fetchall() or []
    except Exception as e:
        logger.warning("forecast_service: payroll query failed for %s: %s", company_id, e)
        rows = []

    for r in rows:
        try:
            m = int(r.get("month") or 0)
            if m < 1 or m > MONTHS:
                continue
            gross[m - 1] = float(r.get("gross") or 0)
            net[m - 1] = float(r.get("net") or 0)
            tax[m - 1] = float(r.get("tax") or 0)
            pension[m - 1] = float(r.get("pension") or 0)
        except Exception:
            continue

    return {"gross": gross, "net": net, "income_tax": tax, "pension": pension}


def _fetch_employee_salary_baseline(company_id: str) -> float:
    """
    SUM(basic_salary) of active employees for the company — used as a flat
    monthly gross baseline when no payroll runs exist for the year.
    Missing table or query failure yields 0.0.
    """
    try:
        with get_tenant_cursor(company_id) as cur:
            cur.execute(
                """
                SELECT COALESCE(SUM(basic_salary), 0) AS total
                FROM employees
                WHERE company_id = %s AND is_active = TRUE
                """,
                (company_id,),
            )
            row = cur.fetchone() or {}
            return float(row.get("total") or 0)
    except Exception as e:
        logger.warning("forecast_service: employee baseline query failed for %s: %s", company_id, e)
        return 0.0


def forecast_payroll(company_id: str, year: Optional[int] = None) -> Dict[str, Any]:
    """End-of-year forecast for total gross, net, tax, pension payroll outlay."""
    year = int(year or date.today().year)
    monthly = _fetch_payroll_monthly(company_id, year)
    sources = ["payroll_data"]

    # Fallback: no payroll runs recorded for the year — project a flat
    # monthly gross from the current active employees' basic salaries.
    baseline_used = False
    if not any(any(vals) for vals in monthly.values()):
        baseline = _fetch_employee_salary_baseline(company_id)
        if baseline > 0:
            monthly["gross"] = [baseline] * MONTHS
            baseline_used = True
            sources = ["employees"]

    # Observed months = last month with any non-zero gross, else current month.
    # For the salary baseline the whole year is a projection, not actuals.
    last_observed = 0
    if not baseline_used:
        for i, v in enumerate(monthly.get("gross", []), start=1):
            if v:
                last_observed = i
    observed = last_observed or _current_month_of_year(year)

    series: Dict[str, Any] = {}
    method_used = "actual"
    for key, values in monthly.items():
        forecast_vals, method = _extrapolate(values, observed)
        method_used = method
        series[key] = {
            "actuals": [round(v, 2) for v in values],
            "forecast": [round(v, 2) for v in forecast_vals],
            "ytd_actual": round(sum(values[:observed]), 2),
            "eoy_projected": round(sum(forecast_vals), 2),
        }

    if baseline_used:
        method_used = "current_salary_baseline"

    return {
        "module": "payroll",
        "company_id": company_id,
        "fiscal_year": year,
        "observed_months": observed,
        "method": method_used,
        "confidence": _confidence(observed),
        "series": series,
        "sources": sources,
    }
