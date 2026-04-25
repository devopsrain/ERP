"""Forecasting & predictive analytics core.

Pure-stdlib implementations of industry-standard techniques:
    * Simple/Cumulative Moving Average
    * Ordinary Least Squares (OLS) linear regression / trend
    * Simple Exponential Smoothing (SES)
    * Holt's linear trend (double exponential smoothing)
    * Holt-Winters additive (triple exponential smoothing) -- seasonal
    * Naive seasonal & drift forecasts (Hyndman baselines)
    * Accuracy metrics: MAE, RMSE, MAPE
    * EVM (PMI standard): CPI, SPI, EAC, ETC, VAC, TCPI
    * Inventory: EOQ (Wilson), Reorder Point, Safety Stock
    * Direct cash-flow projection (13-week rolling)

The public entrypoint is :func:`forecast_series` which auto-selects the best
method by training on history minus the last ``holdout`` periods, scoring MAPE,
and refitting on the full series.
"""
from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, asdict, field
from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Sequence, Tuple, Dict, Any


# ---------------------------------------------------------------------------
# Accuracy metrics
# ---------------------------------------------------------------------------
def mae(actual: Sequence[float], predicted: Sequence[float]) -> float:
    n = min(len(actual), len(predicted))
    if n == 0:
        return 0.0
    return sum(abs(a - p) for a, p in zip(actual[:n], predicted[:n])) / n


def rmse(actual: Sequence[float], predicted: Sequence[float]) -> float:
    n = min(len(actual), len(predicted))
    if n == 0:
        return 0.0
    return math.sqrt(sum((a - p) ** 2 for a, p in zip(actual[:n], predicted[:n])) / n)


def mape(actual: Sequence[float], predicted: Sequence[float]) -> float:
    """Mean Absolute Percentage Error (%). Skips zero actuals."""
    pairs = [(a, p) for a, p in zip(actual, predicted) if a not in (0, 0.0)]
    if not pairs:
        return 0.0
    return 100.0 * sum(abs((a - p) / a) for a, p in pairs) / len(pairs)


# ---------------------------------------------------------------------------
# Simple methods
# ---------------------------------------------------------------------------
def moving_average(values: Sequence[float], window: int = 3, periods: int = 1) -> List[float]:
    if not values:
        return [0.0] * periods
    window = max(1, min(window, len(values)))
    series = list(values)
    out: List[float] = []
    for _ in range(periods):
        avg = sum(series[-window:]) / window
        out.append(avg)
        series.append(avg)
    return out


def linear_trend(values: Sequence[float], periods: int = 1) -> Tuple[List[float], float, float]:
    """OLS y = a + b*x. Returns (forecast, intercept, slope)."""
    n = len(values)
    if n < 2:
        v = float(values[0]) if values else 0.0
        return ([v] * periods, v, 0.0)
    xs = list(range(n))
    mean_x = sum(xs) / n
    mean_y = sum(values) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, values))
    den = sum((x - mean_x) ** 2 for x in xs) or 1e-9
    slope = num / den
    intercept = mean_y - slope * mean_x
    forecast = [intercept + slope * (n + h) for h in range(periods)]
    return forecast, intercept, slope


def naive_drift(values: Sequence[float], periods: int = 1) -> List[float]:
    if len(values) < 2:
        return [float(values[-1]) if values else 0.0] * periods
    drift = (values[-1] - values[0]) / (len(values) - 1)
    return [values[-1] + drift * (h + 1) for h in range(periods)]


def seasonal_naive(values: Sequence[float], season: int, periods: int) -> List[float]:
    if not values:
        return [0.0] * periods
    season = max(1, min(season, len(values)))
    return [values[-season + (h % season)] for h in range(periods)]


# ---------------------------------------------------------------------------
# Exponential smoothing family
# ---------------------------------------------------------------------------
def simple_exp_smoothing(values: Sequence[float], periods: int = 1, alpha: float = 0.3) -> List[float]:
    if not values:
        return [0.0] * periods
    level = float(values[0])
    for v in values[1:]:
        level = alpha * v + (1 - alpha) * level
    return [level] * periods


def holt_linear(values: Sequence[float], periods: int = 1,
                alpha: float = 0.3, beta: float = 0.1) -> List[float]:
    """Double exponential smoothing -- captures trend."""
    if len(values) < 2:
        return [float(values[-1]) if values else 0.0] * periods
    level = float(values[0])
    trend = float(values[1] - values[0])
    for v in values[1:]:
        prev_level = level
        level = alpha * v + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
    return [level + (h + 1) * trend for h in range(periods)]


def holt_winters_additive(values: Sequence[float], periods: int = 1, season: int = 12,
                          alpha: float = 0.3, beta: float = 0.1, gamma: float = 0.2) -> List[float]:
    """Triple exponential smoothing (additive seasonality)."""
    n = len(values)
    if n < 2 * season:
        # Not enough seasonal cycles; fall back to Holt
        return holt_linear(values, periods, alpha, beta)

    # initial level = mean of first season
    level = sum(values[:season]) / season
    # initial trend = avg per-period change between season 1 and season 2
    trend = sum((values[i + season] - values[i]) / season for i in range(season)) / season
    # initial seasonals
    seasonals = [values[i] - level for i in range(season)]

    for t, v in enumerate(values):
        s_idx = t % season
        prev_level = level
        level = alpha * (v - seasonals[s_idx]) + (1 - alpha) * (level + trend)
        trend = beta * (level - prev_level) + (1 - beta) * trend
        seasonals[s_idx] = gamma * (v - level) + (1 - gamma) * seasonals[s_idx]

    return [level + (h + 1) * trend + seasonals[(n + h) % season] for h in range(periods)]


# ---------------------------------------------------------------------------
# Auto-select forecaster
# ---------------------------------------------------------------------------
@dataclass
class ForecastResult:
    method: str
    forecast: List[float]
    lower: List[float]
    upper: List[float]
    history: List[float]
    mape: float
    rmse: float
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_METHODS = ("holt_winters", "holt", "ses", "linear", "moving_average", "drift", "seasonal_naive")


def _run(method: str, values: Sequence[float], periods: int, season: int) -> List[float]:
    if method == "holt_winters":
        return holt_winters_additive(values, periods, season=season)
    if method == "holt":
        return holt_linear(values, periods)
    if method == "ses":
        return simple_exp_smoothing(values, periods)
    if method == "linear":
        return linear_trend(values, periods)[0]
    if method == "moving_average":
        return moving_average(values, window=min(3, len(values)), periods=periods)
    if method == "drift":
        return naive_drift(values, periods)
    if method == "seasonal_naive":
        return seasonal_naive(values, season, periods)
    raise ValueError(f"Unknown method: {method}")


def forecast_series(values: Sequence[float], periods: int = 6, *,
                    season: int = 12, method: str = "auto",
                    confidence_z: float = 1.96) -> ForecastResult:
    """Forecast ``periods`` ahead.

    When ``method='auto'``, runs a holdout backtest (last min(season, n//4)
    periods) across all candidates and selects the lowest-MAPE method, then
    refits on the full series. Returns a :class:`ForecastResult` with 95%
    confidence bands derived from in-sample residual stdev.
    """
    values = [float(v) for v in values]
    n = len(values)
    if n == 0:
        return ForecastResult("none", [0.0] * periods, [0.0] * periods, [0.0] * periods,
                              [], 0.0, 0.0, "no history")

    if method == "auto":
        if n < 4:
            chosen = "moving_average"
        else:
            holdout = max(1, min(season, n // 4))
            train = values[:-holdout]
            test = values[-holdout:]
            scored: List[Tuple[str, float]] = []
            for m in _METHODS:
                if m == "holt_winters" and len(train) < 2 * season:
                    continue
                if m == "seasonal_naive" and len(train) < season:
                    continue
                try:
                    pred = _run(m, train, holdout, season)
                    scored.append((m, mape(test, pred)))
                except Exception:
                    continue
            chosen = min(scored, key=lambda x: x[1])[0] if scored else "linear"
    else:
        chosen = method

    forecast = _run(chosen, values, periods, season)

    # In-sample one-step residuals for confidence bands
    residuals: List[float] = []
    if n >= 3:
        for i in range(2, n):
            try:
                pred_i = _run(chosen, values[:i], 1, season)[0]
                residuals.append(values[i] - pred_i)
            except Exception:
                pass
    sigma = statistics.pstdev(residuals) if len(residuals) >= 2 else (
        statistics.pstdev(values) if n >= 2 else 0.0
    )
    lower = [f - confidence_z * sigma * math.sqrt(h + 1) for h, f in enumerate(forecast)]
    upper = [f + confidence_z * sigma * math.sqrt(h + 1) for h, f in enumerate(forecast)]

    # Backtest accuracy (last 1/4)
    backtest_pred: List[float] = []
    actual_tail: List[float] = []
    if n >= 4:
        h = max(1, n // 4)
        try:
            backtest_pred = _run(chosen, values[:-h], h, season)
            actual_tail = values[-h:]
        except Exception:
            pass

    return ForecastResult(
        method=chosen,
        forecast=[round(x, 2) for x in forecast],
        lower=[round(x, 2) for x in lower],
        upper=[round(x, 2) for x in upper],
        history=[round(x, 2) for x in values],
        mape=round(mape(actual_tail, backtest_pred), 2),
        rmse=round(rmse(actual_tail, backtest_pred), 2),
        notes=f"{n} historical points, {periods}-period horizon, season={season}",
    )


# ---------------------------------------------------------------------------
# Earned Value Management (PMI standard)
# ---------------------------------------------------------------------------
@dataclass
class EVMResult:
    pv: float        # Planned Value (BCWS)
    ev: float        # Earned Value (BCWP)
    ac: float        # Actual Cost (ACWP)
    bac: float       # Budget at Completion
    cv: float        # Cost Variance
    sv: float        # Schedule Variance
    cpi: float       # Cost Performance Index
    spi: float       # Schedule Performance Index
    eac: float       # Estimate at Completion
    etc: float       # Estimate to Complete
    vac: float       # Variance at Completion
    tcpi: float      # To-Complete Performance Index
    forecast_status: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def evm(bac: float, ev: float, ac: float, pv: float) -> EVMResult:
    """Compute PMI Earned Value metrics."""
    cv = ev - ac
    sv = ev - pv
    cpi = (ev / ac) if ac else 1.0
    spi = (ev / pv) if pv else 1.0
    eac = (bac / cpi) if cpi else bac
    etc = eac - ac
    vac = bac - eac
    remaining_work = bac - ev
    remaining_budget = bac - ac
    tcpi = (remaining_work / remaining_budget) if remaining_budget > 0 else 0.0

    if cpi >= 1 and spi >= 1:
        status = "on track"
    elif cpi < 0.9 or spi < 0.9:
        status = "at risk"
    else:
        status = "watch"

    return EVMResult(
        pv=round(pv, 2), ev=round(ev, 2), ac=round(ac, 2), bac=round(bac, 2),
        cv=round(cv, 2), sv=round(sv, 2),
        cpi=round(cpi, 3), spi=round(spi, 3),
        eac=round(eac, 2), etc=round(etc, 2), vac=round(vac, 2),
        tcpi=round(tcpi, 3),
        forecast_status=status,
    )


# ---------------------------------------------------------------------------
# Inventory & procurement (Wilson EOQ + safety stock)
# ---------------------------------------------------------------------------
@dataclass
class InventoryPolicy:
    eoq: float
    reorder_point: float
    safety_stock: float
    cycle_days: float
    annual_orders: float
    notes: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


_Z_SERVICE = {0.80: 0.84, 0.85: 1.04, 0.90: 1.28, 0.95: 1.65, 0.975: 1.96, 0.99: 2.33}


def inventory_policy(annual_demand: float, order_cost: float, holding_cost_per_unit: float,
                     daily_demand: float, lead_time_days: float,
                     demand_std: float = 0.0, service_level: float = 0.95) -> InventoryPolicy:
    if holding_cost_per_unit <= 0 or annual_demand <= 0:
        eoq = 0.0
    else:
        eoq = math.sqrt(2 * annual_demand * order_cost / holding_cost_per_unit)
    z = _Z_SERVICE.get(round(service_level, 3), 1.65)
    safety_stock = z * demand_std * math.sqrt(max(lead_time_days, 0))
    rop = daily_demand * lead_time_days + safety_stock
    annual_orders = (annual_demand / eoq) if eoq else 0.0
    cycle_days = (365.0 / annual_orders) if annual_orders else 0.0
    return InventoryPolicy(
        eoq=round(eoq, 2),
        reorder_point=round(rop, 2),
        safety_stock=round(safety_stock, 2),
        cycle_days=round(cycle_days, 1),
        annual_orders=round(annual_orders, 2),
        notes=f"Wilson EOQ, service level {int(service_level*100)}%",
    )


# ---------------------------------------------------------------------------
# Direct cash-flow projection (13-week rolling)
# ---------------------------------------------------------------------------
@dataclass
class CashFlowWeek:
    week: int
    week_start: str
    inflow: float
    outflow: float
    net: float
    closing_balance: float


def cash_flow_projection(opening_balance: float,
                         weekly_inflows: Sequence[float],
                         weekly_outflows: Sequence[float],
                         start: Optional[date] = None) -> List[CashFlowWeek]:
    """Treasurer-standard 13-week rolling forecast (direct method)."""
    start = start or date.today()
    n = max(len(weekly_inflows), len(weekly_outflows))
    weeks: List[CashFlowWeek] = []
    bal = float(opening_balance)
    for w in range(n):
        inflow = float(weekly_inflows[w]) if w < len(weekly_inflows) else 0.0
        outflow = float(weekly_outflows[w]) if w < len(weekly_outflows) else 0.0
        net = inflow - outflow
        bal += net
        weeks.append(CashFlowWeek(
            week=w + 1,
            week_start=(start + timedelta(weeks=w)).isoformat(),
            inflow=round(inflow, 2),
            outflow=round(outflow, 2),
            net=round(net, 2),
            closing_balance=round(bal, 2),
        ))
    return weeks


# ---------------------------------------------------------------------------
# Series helpers (DB rows -> aligned period series)
# ---------------------------------------------------------------------------
def bucketize_monthly(rows: Iterable[Tuple[Any, float]], months: int = 24,
                      end: Optional[date] = None) -> Tuple[List[str], List[float]]:
    """Aggregate ``(date, amount)`` rows into the last ``months`` calendar months.

    Returns parallel ``(labels_YYYY_MM, totals)`` lists ending at ``end`` (today).
    """
    end = end or date.today()
    end_month = date(end.year, end.month, 1)

    labels: List[str] = []
    cursor = end_month
    for _ in range(months):
        labels.append(cursor.strftime("%Y-%m"))
        # step back one month
        if cursor.month == 1:
            cursor = date(cursor.year - 1, 12, 1)
        else:
            cursor = date(cursor.year, cursor.month - 1, 1)
    labels.reverse()
    idx = {lab: i for i, lab in enumerate(labels)}
    totals = [0.0] * months

    for d, amt in rows:
        if d is None:
            continue
        if isinstance(d, datetime):
            d = d.date()
        elif isinstance(d, str):
            try:
                d = datetime.fromisoformat(d[:10]).date()
            except Exception:
                continue
        key = d.strftime("%Y-%m")
        if key in idx:
            totals[idx[key]] += float(amt or 0)
    return labels, totals


def bucketize_weekly(rows: Iterable[Tuple[Any, float]], weeks: int = 13,
                     end: Optional[date] = None) -> Tuple[List[str], List[float]]:
    end = end or date.today()
    # Snap to Monday of current week
    end_week = end - timedelta(days=end.weekday())
    labels = [(end_week - timedelta(weeks=weeks - 1 - i)).isoformat() for i in range(weeks)]
    idx = {lab: i for i, lab in enumerate(labels)}
    totals = [0.0] * weeks
    for d, amt in rows:
        if d is None:
            continue
        if isinstance(d, datetime):
            d = d.date()
        elif isinstance(d, str):
            try:
                d = datetime.fromisoformat(d[:10]).date()
            except Exception:
                continue
        wk = (d - timedelta(days=d.weekday())).isoformat()
        if wk in idx:
            totals[idx[wk]] += float(amt or 0)
    return labels, totals


def future_month_labels(start_after: str, periods: int) -> List[str]:
    y, m = (int(x) for x in start_after.split("-")[:2])
    out: List[str] = []
    for _ in range(periods):
        m += 1
        if m > 12:
            m = 1
            y += 1
        out.append(f"{y:04d}-{m:02d}")
    return out
