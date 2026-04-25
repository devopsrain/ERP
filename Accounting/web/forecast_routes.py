"""
Forecasting & Predictive Analytics Routes.

Industry-standard forecasting tools across all relevant ERP modules:

    /forecast/                  Index / overview
    /forecast/cashflow          13-week direct cash-flow projection
    /forecast/revenue           Income forecast (Holt-Winters / auto)
    /forecast/expenses          Expense forecast
    /forecast/procurement       Procurement spend + EOQ / reorder-point tools
    /forecast/projects          PMI Earned Value Management per project
    /forecast/payroll           Payroll cost projection
    /forecast/bookings          EMS booking-volume forecast
    /forecast/api/<area>        JSON endpoint for charts (params: periods, method)

The math lives in :mod:`forecasting`. Routes here just pull historical
series from existing tables and pass them through.
"""
from __future__ import annotations

from fastapi import APIRouter, Request, Depends
from fastapi.responses import JSONResponse, RedirectResponse
from deps import login_required, template_context, flash
from template_engine import templates
from db import get_conn

import forecasting as F
from forecasting import (
    forecast_series, evm, inventory_policy, cash_flow_projection,
    bucketize_monthly, bucketize_weekly, future_month_labels,
)

import logging
from datetime import date, datetime, timedelta

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/forecast", tags=["forecast"])


# ── Helpers ──────────────────────────────────────────────────────────────────
def _safe_query(sql: str, params: tuple) -> list:
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                return cur.fetchall() or []
    except Exception as e:
        logger.warning("forecast query failed: %s", e)
        return []


def _series_from_table(cid: str, table: str, date_col: str, amount_col: str,
                       extra_where: str = "", extra_params: tuple = (),
                       months: int = 24) -> tuple[list[str], list[float]]:
    """Pull (date, amount) rows and bucket them into monthly totals."""
    where = f"company_id = %s {extra_where}".strip()
    rows = _safe_query(
        f"SELECT {date_col}, {amount_col} FROM {table} WHERE {where}",
        (cid,) + extra_params,
    )
    return bucketize_monthly(rows, months=months)


def _periods(request: Request, default: int = 6, max_p: int = 24) -> int:
    try:
        p = int(request.query_params.get("periods", default))
    except (TypeError, ValueError):
        p = default
    return max(1, min(p, max_p))


def _method(request: Request) -> str:
    m = (request.query_params.get("method") or "auto").lower()
    return m if m in ("auto", "holt_winters", "holt", "ses", "linear",
                      "moving_average", "drift", "seasonal_naive") else "auto"


# ── Index ────────────────────────────────────────────────────────────────────
@router.get("/", name="forecast_index")
async def index(request: Request, user=Depends(login_required)):
    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Forecasting & Predictive Analytics",
    )
    return templates.TemplateResponse("forecast/index.html", ctx)


# ── Cash flow (13-week direct method) ────────────────────────────────────────
@router.get("/cashflow", name="forecast_cashflow")
async def cashflow_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")

    # Historical weekly inflow/outflow (last 13 weeks) drive the projection
    inflow_rows = _safe_query(
        "SELECT date, amount FROM income_records WHERE company_id=%s", (cid,),
    )
    outflow_rows = _safe_query(
        "SELECT date, amount FROM expense_records WHERE company_id=%s", (cid,),
    )
    in_labels, in_weekly = bucketize_weekly(inflow_rows, weeks=13)
    _, out_weekly = bucketize_weekly(outflow_rows, weeks=13)

    # Average of last 4 weeks projected forward 13 weeks
    avg_in = sum(in_weekly[-4:]) / 4 if any(in_weekly[-4:]) else (sum(in_weekly) / 13 if any(in_weekly) else 0)
    avg_out = sum(out_weekly[-4:]) / 4 if any(out_weekly[-4:]) else (sum(out_weekly) / 13 if any(out_weekly) else 0)

    # Opening balance: cumulative net to date (proxy if no GL view)
    opening = sum(in_weekly) - sum(out_weekly)

    weeks = cash_flow_projection(
        opening_balance=opening,
        weekly_inflows=[avg_in] * 13,
        weekly_outflows=[avg_out] * 13,
        start=date.today(),
    )
    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="13-Week Cash Flow Forecast",
        opening_balance=opening,
        avg_in=round(avg_in, 2),
        avg_out=round(avg_out, 2),
        weeks=weeks,
        history_labels=in_labels,
        history_in=in_weekly,
        history_out=out_weekly,
    )
    return templates.TemplateResponse("forecast/cashflow.html", ctx)


# ── Revenue ──────────────────────────────────────────────────────────────────
@router.get("/revenue", name="forecast_revenue")
async def revenue_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    periods = _periods(request, default=6)
    method = _method(request)

    labels, totals = _series_from_table(cid, "income_records", "date", "amount")
    result = forecast_series(totals, periods=periods, season=12, method=method)
    fut_labels = future_month_labels(labels[-1], periods) if labels else []

    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Revenue Forecast",
        area="revenue",
        history_labels=labels,
        future_labels=fut_labels,
        result=result.to_dict(),
        periods=periods,
        method=method,
    )
    return templates.TemplateResponse("forecast/series.html", ctx)


# ── Expenses ─────────────────────────────────────────────────────────────────
@router.get("/expenses", name="forecast_expenses")
async def expenses_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    periods = _periods(request, default=6)
    method = _method(request)

    labels, totals = _series_from_table(cid, "expense_records", "date", "amount")
    result = forecast_series(totals, periods=periods, season=12, method=method)
    fut_labels = future_month_labels(labels[-1], periods) if labels else []

    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Expense Forecast",
        area="expenses",
        history_labels=labels,
        future_labels=fut_labels,
        result=result.to_dict(),
        periods=periods,
        method=method,
    )
    return templates.TemplateResponse("forecast/series.html", ctx)


# ── Procurement (spend forecast + EOQ tool) ──────────────────────────────────
@router.get("/procurement", name="forecast_procurement")
async def procurement_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    periods = _periods(request, default=6)
    method = _method(request)

    labels, totals = _series_from_table(
        cid, "proc_purchase_orders", "created_at::date", "total_amount",
    )
    result = forecast_series(totals, periods=periods, season=12, method=method)
    fut_labels = future_month_labels(labels[-1], periods) if labels else []

    # EOQ defaults derived from history (rough heuristic)
    annual_demand = sum(totals[-12:]) if len(totals) >= 12 else sum(totals)
    policy = inventory_policy(
        annual_demand=annual_demand or 0,
        order_cost=50.0,
        holding_cost_per_unit=2.0,
        daily_demand=(annual_demand / 365.0) if annual_demand else 0.0,
        lead_time_days=14,
        demand_std=(F.statistics.pstdev(totals) / 30.0) if len(totals) >= 2 else 0.0,
        service_level=0.95,
    )

    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Procurement Spend Forecast",
        area="procurement",
        history_labels=labels,
        future_labels=fut_labels,
        result=result.to_dict(),
        policy=policy.to_dict(),
        periods=periods,
        method=method,
    )
    return templates.TemplateResponse("forecast/procurement.html", ctx)


@router.post("/procurement/eoq", name="forecast_eoq")
async def eoq_tool(request: Request, user=Depends(login_required)):
    form = await request.form()
    try:
        policy = inventory_policy(
            annual_demand=float(form.get("annual_demand", 0)),
            order_cost=float(form.get("order_cost", 0)),
            holding_cost_per_unit=float(form.get("holding_cost", 0)),
            daily_demand=float(form.get("daily_demand", 0)),
            lead_time_days=float(form.get("lead_time_days", 0)),
            demand_std=float(form.get("demand_std", 0)),
            service_level=float(form.get("service_level", 0.95)),
        )
        return JSONResponse({"ok": True, "policy": policy.to_dict()})
    except Exception as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=400)


# ── Projects (Earned Value Management) ───────────────────────────────────────
@router.get("/projects", name="forecast_projects")
async def projects_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    rows = _safe_query(
        """SELECT id, name, status, start_date, end_date, total_budget,
                  COALESCE(material_costs,0) + COALESCE(consultant_fees,0) + COALESCE(internal_labor,0) AS ac
             FROM pm_projects WHERE company_id=%s""", (cid,),
    )
    today = date.today()
    items = []
    for r in rows:
        pid, name, status, start, end, bac, ac = r
        bac = float(bac or 0)
        ac = float(ac or 0)

        # Schedule % complete (planned)
        if start and end and end > start:
            total_days = (end - start).days
            elapsed = max(0, min((today - start).days, total_days))
            sched_pct = elapsed / total_days
        else:
            sched_pct = 1.0 if status == "completed" else 0.0
        pv = bac * sched_pct

        # Physical % complete from task completion
        task_rows = _safe_query(
            "SELECT status, est_hours FROM pm_tasks WHERE project_id=%s", (pid,),
        )
        if task_rows:
            tot_h = sum(float(h or 0) for _, h in task_rows) or 1.0
            done_h = sum(float(h or 0) for s, h in task_rows if s == "completed")
            phys_pct = done_h / tot_h
        else:
            phys_pct = 1.0 if status == "completed" else sched_pct
        ev = bac * phys_pct

        m = evm(bac=bac, ev=ev, ac=ac, pv=pv).to_dict()
        m["id"] = pid
        m["name"] = name
        m["status"] = status
        m["pct_complete"] = round(phys_pct * 100, 1)
        items.append(m)

    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Project EVM Forecast",
        projects=items,
    )
    return templates.TemplateResponse("forecast/projects.html", ctx)


# ── Payroll ──────────────────────────────────────────────────────────────────
@router.get("/payroll", name="forecast_payroll")
async def payroll_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    periods = _periods(request, default=6)
    method = _method(request)

    rows = _safe_query(
        """SELECT pay_period_start, COALESCE(net_pay, gross_pay, 0)
             FROM hrm_payroll_runs WHERE company_id=%s""", (cid,),
    )
    labels, totals = bucketize_monthly(rows, months=24)
    result = forecast_series(totals, periods=periods, season=12, method=method)
    fut_labels = future_month_labels(labels[-1], periods) if labels else []

    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Payroll Cost Forecast",
        area="payroll",
        history_labels=labels,
        future_labels=fut_labels,
        result=result.to_dict(),
        periods=periods,
        method=method,
    )
    return templates.TemplateResponse("forecast/series.html", ctx)


# ── EMS bookings ─────────────────────────────────────────────────────────────
@router.get("/bookings", name="forecast_bookings")
async def bookings_view(request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    periods = _periods(request, default=6)
    method = _method(request)

    rows = _safe_query(
        """SELECT start_time::date, COALESCE(total_amount, 1)
             FROM ems_bookings WHERE company_id=%s""", (cid,),
    )
    labels, totals = bucketize_monthly(rows, months=24)
    result = forecast_series(totals, periods=periods, season=12, method=method)
    fut_labels = future_month_labels(labels[-1], periods) if labels else []

    ctx = template_context(request)
    ctx.update(
        active_module="forecast",
        title="Event Bookings Forecast",
        area="bookings",
        history_labels=labels,
        future_labels=fut_labels,
        result=result.to_dict(),
        periods=periods,
        method=method,
    )
    return templates.TemplateResponse("forecast/series.html", ctx)


# ── JSON API (for embedding in other dashboards) ─────────────────────────────
_AREA_QUERIES = {
    "revenue":     ("income_records", "date", "amount", ""),
    "expenses":    ("expense_records", "date", "amount", ""),
    "procurement": ("proc_purchase_orders", "created_at::date", "total_amount", ""),
    "payroll":     ("hrm_payroll_runs", "pay_period_start", "COALESCE(net_pay, gross_pay, 0)", ""),
    "bookings":    ("ems_bookings", "start_time::date", "COALESCE(total_amount, 1)", ""),
}


@router.get("/api/{area}", name="forecast_api")
async def api_forecast(area: str, request: Request, user=Depends(login_required)):
    cid = request.session.get("current_company_id", "default")
    if area not in _AREA_QUERIES:
        return JSONResponse({"error": "unknown area"}, status_code=404)
    table, date_col, amount_col, extra = _AREA_QUERIES[area]
    rows = _safe_query(
        f"SELECT {date_col}, {amount_col} FROM {table} WHERE company_id=%s {extra}",
        (cid,),
    )
    labels, totals = bucketize_monthly(rows, months=24)
    periods = _periods(request, default=6)
    method = _method(request)
    result = forecast_series(totals, periods=periods, season=12, method=method)
    fut_labels = future_month_labels(labels[-1], periods) if labels else []
    return JSONResponse({
        "area": area,
        "history": {"labels": labels, "values": totals},
        "future":  {"labels": fut_labels, **result.to_dict()},
    })
