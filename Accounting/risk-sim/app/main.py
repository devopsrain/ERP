"""
Risk assessment API: correlated Monte Carlo price simulation.

Endpoints:
  GET  /                      self-contained HTML dashboard (also at /dashboard)
  GET  /healthz              liveness probe
  GET  /readyz                readiness probe (runs a tiny simulation)
  GET  /api/v1/version        build/version info
  POST /api/v1/simulate       run a correlated-price risk simulation
  GET  /api/v1/correlations         list dates with a daily correlation snapshot
  GET  /api/v1/correlations/latest  newest snapshot (written by app.daily_correlation)
  GET  /api/v1/correlations/{date}  snapshot for a specific YYYY-MM-DD
  GET  /api/v1/screener             list dates with a momentum-screener snapshot
  GET  /api/v1/screener/latest      newest screener snapshot (app.momentum_screener)
  GET  /api/v1/screener/hits        hit-days index (per-date candidate/doubler counts)
  GET  /api/v1/screener/{date}      screener snapshot for a specific YYYY-MM-DD

Run locally:
  uvicorn app.main:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path

import numpy as np
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import ValidationError

from app.models import SimulationRequest, SimulationResponse, AssetResult
from app.risk_engine import Asset, run_risk_assessment

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("risk-sim")

APP_VERSION = "1.0.0"

# Where the correlation-job compose service drops its daily snapshots
# (shared named volume, mounted read-only here). Missing dir/files are a
# normal state before the first job run — endpoints return empty/404, not 500.
CORRELATION_OUTPUT_DIR = Path(os.getenv("CORRELATION_OUTPUT_DIR", "/data/output"))
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

# Self-contained dashboard page (inline CSS+JS, no CDNs — works offline).
# Read once at import; the file ships inside the image next to this module.
DASHBOARD_HTML_PATH = Path(__file__).parent / "static" / "dashboard.html"
DASHBOARD_HTML = DASHBOARD_HTML_PATH.read_text(encoding="utf-8")

app = FastAPI(
    title="Correlated Price Risk Simulator",
    description="Monte Carlo VaR / CVaR / margin-call risk assessment for correlated positions.",
    version=APP_VERSION,
)


@app.get("/", include_in_schema=False, response_class=HTMLResponse)
@app.get("/dashboard", include_in_schema=False, response_class=HTMLResponse)
def dashboard():
    """Correlation dashboard: heatmap + per-ticker stat tiles, all inline."""
    return HTMLResponse(DASHBOARD_HTML)


@app.get("/healthz")
def healthz():
    """Liveness probe: process is up."""
    return {"status": "ok"}


@app.get("/readyz")
def readyz():
    """Readiness probe: confirms the numeric engine actually runs."""
    try:
        asset = Asset(
            ticker="SELFTEST", initial_price=100.0, annual_volatility=0.2,
            annual_drift=0.0, position_units=1.0, margin_pct=None,
        )
        run_risk_assessment(
            assets=[asset],
            correlation_matrix=[[1.0]],
            num_simulations=200,
            horizon_days=5,
            confidence_level=0.95,
            random_seed=1,
        )
        return {"status": "ready"}
    except Exception as exc:  # noqa: BLE001
        logger.exception("Readiness self-test failed")
        raise HTTPException(status_code=503, detail=f"engine self-test failed: {exc}") from exc


@app.get("/api/v1/version")
def version():
    return {"version": APP_VERSION}


def _read_snapshot_file(path: Path, kind: str = "correlation") -> dict:
    if not path.is_file():
        raise HTTPException(status_code=404, detail=f"no {kind} snapshot named {path.name}")
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        # unreadable file must never 500; the jobs write atomically, so this
        # is either transient or a manually mangled file
        raise HTTPException(status_code=404, detail=f"{kind} snapshot unreadable: {exc}") from exc


def _read_correlation_file(path: Path) -> dict:
    return _read_snapshot_file(path, "correlation")


@app.get("/api/v1/correlations")
def list_correlations():
    """Dates that have a daily correlation snapshot on disk."""
    corr_dir = CORRELATION_OUTPUT_DIR / "correlations"
    if not corr_dir.is_dir():
        return {"dates": []}
    return {"dates": sorted(p.stem for p in corr_dir.glob("*.json") if _DATE_RE.match(p.stem))}


@app.get("/api/v1/correlations/latest")
def correlations_latest():
    """Most recent snapshot (stable name written on every job run)."""
    return _read_correlation_file(CORRELATION_OUTPUT_DIR / "latest.json")


@app.get("/api/v1/correlations/{date}")
def correlations_by_date(date: str):
    """Snapshot for a specific day, e.g. /api/v1/correlations/2026-08-12."""
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=404, detail="date must look like YYYY-MM-DD")
    return _read_correlation_file(CORRELATION_OUTPUT_DIR / "correlations" / f"{date}.json")


@app.get("/api/v1/screener")
def list_screener():
    """Dates that have a daily momentum-screener snapshot on disk."""
    screener_dir = CORRELATION_OUTPUT_DIR / "screener"
    if not screener_dir.is_dir():
        return {"dates": []}
    return {"dates": sorted(p.stem for p in screener_dir.glob("*.json") if _DATE_RE.match(p.stem))}


@app.get("/api/v1/screener/latest")
def screener_latest():
    """Most recent screener snapshot (stable name written on every job run)."""
    return _read_snapshot_file(CORRELATION_OUTPUT_DIR / "screener-latest.json", "screener")


@app.get("/api/v1/screener/hits")
def screener_hits():
    """Hit-days index (screener-hits.json), upserted on every snapshot write.
    Declared BEFORE the /{date} route so it isn't shadowed by it. Guarded:
    a missing or unreadable index is a normal pre-first-run state -> {"hits": []}."""
    path = CORRELATION_OUTPUT_DIR / "screener-hits.json"
    if not path.is_file():
        return {"hits": []}
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {"hits": []}
    hits = doc.get("hits") if isinstance(doc, dict) else None
    return {"hits": hits if isinstance(hits, list) else []}


@app.get("/api/v1/screener/{date}")
def screener_by_date(date: str):
    """Screener snapshot for a specific day, e.g. /api/v1/screener/2026-08-25."""
    if not _DATE_RE.match(date):
        raise HTTPException(status_code=404, detail="date must look like YYYY-MM-DD")
    return _read_snapshot_file(CORRELATION_OUTPUT_DIR / "screener" / f"{date}.json", "screener")


@app.post("/api/v1/simulate", response_model=SimulationResponse)
def simulate(req: SimulationRequest):
    start = time.perf_counter()
    try:
        assets = [
            Asset(
                ticker=a.ticker,
                initial_price=a.initial_price,
                annual_volatility=a.annual_volatility,
                annual_drift=a.annual_drift,
                position_units=a.position_units,
                margin_pct=a.margin_pct,
            )
            for a in req.assets
        ]
        result = run_risk_assessment(
            assets=assets,
            correlation_matrix=req.correlation_matrix,
            num_simulations=req.num_simulations,
            horizon_days=req.horizon_days,
            confidence_level=req.confidence_level,
            random_seed=req.random_seed,
        )
    except np.linalg.LinAlgError as exc:
        raise HTTPException(status_code=400, detail=f"correlation matrix error: {exc}") from exc
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.exception("simulation failed")
        raise HTTPException(status_code=500, detail=f"simulation failed: {exc}") from exc

    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "simulate: n_assets=%d sims=%d horizon=%d elapsed_ms=%.1f",
        len(req.assets), req.num_simulations, req.horizon_days, elapsed_ms,
    )

    return SimulationResponse(
        num_simulations=req.num_simulations,
        horizon_days=req.horizon_days,
        confidence_level=req.confidence_level,
        initial_portfolio_value=result["initial_portfolio_value"],
        mean_terminal_pnl=result["mean_terminal_pnl"],
        value_at_risk=result["value_at_risk"],
        conditional_value_at_risk=result["conditional_value_at_risk"],
        prob_of_loss=result["prob_of_loss"],
        portfolio_margin_call_probability=result["portfolio_margin_call_probability"],
        realized_correlation_matrix=result["realized_correlation_matrix"],
        per_asset=[AssetResult(**a) for a in result["per_asset"]],
    )
