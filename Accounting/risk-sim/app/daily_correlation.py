"""
Daily cross-stock correlation job.

Loads the ticker list from config/tickers.json, pulls adjusted-close history
from Yahoo Finance (yfinance), computes daily log returns over the lookback
window and writes two files under the output directory:

  correlations/YYYY-MM-DD.json   dated snapshot (kept forever = history)
  latest.json                    same content under a stable name

Each file contains the correlation matrix, per-ticker annualized volatility /
drift / last price, run metadata (skipped tickers + reasons), and a
`simulate_payload` section shaped as a ready-to-POST body for
POST /api/v1/simulate — the API serves these files via /api/v1/correlations.

If the config declares CFD positions ("positions" + "margin_rate" in
tickers.json), the snapshot also gets a `margin_account` section: per-ticker
open / midday / close prices for the latest trading day plus the margin
required (price x quantity x margin_rate) at each checkpoint, with totals and
the peak. Midday comes from intraday bars when Yahoo has them, otherwise the
(high+low)/2 daily-bar proxy (flagged per row via `midday_source`). A margin
failure never fails the correlation run — the section is just left out.

Fetch/compute math mirrors data/fetch_market_data.py (the laptop-side
payload builder), but failures are handled per ticker: bad symbols, empty
data or too-few observations are skipped and recorded in metadata instead of
aborting. The run only exits nonzero if fewer than two tickers succeed.

Run modes:
  one-off:   python -m app.daily_correlation
  scheduled: python -m app.daily_correlation --loop   # run now, then daily at 07:00 UTC

Paths (overridable via env or CLI flags):
  CORRELATION_CONFIG      default /srv/config/tickers.json
  CORRELATION_OUTPUT_DIR  default /data/output
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("risk-sim.correlation")

TRADING_DAYS = 252
MIN_RETURN_OBSERVATIONS = 5   # per ticker; below this the estimate is noise
RUN_AT_UTC = "07:00"          # daily schedule for --loop mode

DEFAULT_CONFIG_PATH = os.getenv("CORRELATION_CONFIG", "/srv/config/tickers.json")
DEFAULT_OUTPUT_DIR = os.getenv("CORRELATION_OUTPUT_DIR", "/data/output")


def load_config(path: Path) -> dict:
    """Read tickers.json; tolerate missing optional keys, insist on >=2 tickers.

    "positions" maps ticker -> CFD quantity; tickers without an entry (or with
    an unparsable one) default to 0 = excluded from the margin section but
    still part of the correlation run. "margin_rate" defaults to 0.20.
    """
    with open(path) as f:
        cfg = json.load(f)
    tickers = [t.strip() for t in cfg.get("tickers", []) if isinstance(t, str) and t.strip()]
    if len(tickers) < 2:
        raise ValueError(f"config {path} must list at least 2 tickers")
    positions_cfg = cfg.get("positions") or {}
    positions = {}
    for t in tickers:
        try:
            positions[t] = float(positions_cfg.get(t, 0) or 0)
        except (TypeError, ValueError):
            positions[t] = 0.0
    return {
        "tickers": tickers,
        "lookback_days": int(cfg.get("lookback_days", 90)),
        "interval": str(cfg.get("interval", "1d")),
        "positions": positions,
        "margin_rate": float(cfg.get("margin_rate", 0.20)),
    }


def fetch_close_history(tickers: list[str], lookback_days: int, interval: str = "1d") -> pd.DataFrame:
    """Adjusted-close history, one column per ticker. Tickers Yahoo knows
    nothing about simply come back missing/NaN — the caller records them as
    skipped rather than failing the run (unlike fetch_market_data.py)."""
    import yfinance as yf  # lazy: lets offline tests import this module without yfinance

    end = datetime.now(timezone.utc)
    # buffer the calendar window since weekends/holidays eat into it
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)

    raw = yf.download(
        tickers,
        start=start.date().isoformat(),
        end=end.date().isoformat(),
        interval=interval,
        auto_adjust=True,
        progress=False,
        group_by="column",
    )
    if raw is None or raw.empty:
        return pd.DataFrame(columns=tickers)

    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"]
    else:
        # single-ticker response collapses to a flat frame
        closes = raw[["Close"]]
        closes.columns = tickers[:1]

    return closes.dropna(how="all").tail(lookback_days + 1)


def compute_stats(
    closes: pd.DataFrame, min_observations: int = MIN_RETURN_OBSERVATIONS
) -> tuple[dict, pd.DataFrame, dict]:
    """Daily log returns -> (per-ticker stats, correlation matrix, skipped).

    Tickers with too few return observations or zero volatility (would be
    rejected by /api/v1/simulate anyway) are dropped and reported in `skipped`.
    """
    skipped: dict[str, str] = {}
    usable = []
    for t in closes.columns:
        n_returns = int(closes[t].dropna().shape[0]) - 1
        if n_returns < min_observations:
            skipped[t] = f"only {max(n_returns, 0)} return observations (need >= {min_observations})"
        else:
            usable.append(t)

    if not usable:
        return {}, pd.DataFrame(), skipped

    closes = closes[usable]
    log_returns = np.log(closes / closes.shift(1)).dropna(how="all")
    ann_vol = log_returns.std() * np.sqrt(TRADING_DAYS)
    ann_drift = log_returns.mean() * TRADING_DAYS
    last_price = closes.ffill().iloc[-1]

    for t in list(usable):
        if not np.isfinite(ann_vol[t]) or ann_vol[t] <= 0:
            skipped[t] = "zero/undefined volatility over the window"
            usable.remove(t)
    if not usable:
        return {}, pd.DataFrame(), skipped

    corr = log_returns[usable].corr()
    # pairs with no overlapping observations leave NaNs; neutralize them so the
    # matrix stays valid for the simulator, keep the diagonal exact
    corr = corr.fillna(0.0)
    for t in corr.index:
        corr.loc[t, t] = 1.0

    stats = {
        t: {
            "last_price": round(float(last_price[t]), 6),
            "annual_volatility": round(float(ann_vol[t]), 6),
            "annual_drift": round(float(ann_drift[t]), 6),
            "return_observations": int(log_returns[t].dropna().shape[0]),
        }
        for t in usable
    }
    return stats, corr, skipped


# ---------------------------------------------------------------------------
# CFD margin account: open / midday / close of the latest trading day
# ---------------------------------------------------------------------------

def fetch_daily_ohlc(tickers: list[str]) -> pd.DataFrame:
    """Last few daily OHLC bars, columns MultiIndex (ticker, field).
    Unadjusted prices on purpose: margin is charged on traded prices, not the
    split/dividend-adjusted series used for the correlations."""
    import yfinance as yf  # lazy: offline tests import this module without yfinance

    raw = yf.download(
        tickers, period="7d", interval="1d",
        auto_adjust=False, progress=False, group_by="ticker",
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    if not isinstance(raw.columns, pd.MultiIndex):  # single ticker collapses flat
        raw.columns = pd.MultiIndex.from_product([tickers[:1], raw.columns])
    return raw


def fetch_intraday_closes(tickers: list[str]) -> pd.DataFrame | None:
    """Intraday close bars for the last two sessions, one column per ticker,
    trying 60m then 30m. Returns None when Yahoo has nothing usable — the
    caller then falls back to the (high+low)/2 midday proxy."""
    import yfinance as yf

    for interval in ("60m", "30m"):
        try:
            raw = yf.download(
                tickers, period="2d", interval=interval,
                auto_adjust=False, progress=False, group_by="column",
            )
        except Exception:  # noqa: BLE001
            continue
        if raw is None or raw.empty:
            continue
        if isinstance(raw.columns, pd.MultiIndex):
            closes = raw["Close"]
        else:
            closes = raw[["Close"]]
            closes.columns = tickers[:1]
        closes = closes.dropna(how="all")
        if not closes.empty:
            return closes
    return None


def midday_from_intraday(closes: pd.Series, session_date) -> float | None:
    """Close of the intraday bar nearest the session midpoint on session_date.
    None when there are no bars for that date (caller falls back to proxy)."""
    s = closes.dropna()
    if s.empty:
        return None
    day_idx = [ts for ts in s.index if ts.date() == session_date]
    if not day_idx:
        return None
    midpoint = day_idx[0] + (day_idx[-1] - day_idx[0]) / 2
    nearest = min(day_idx, key=lambda ts: abs(ts - midpoint))
    return float(s[nearest])


def compute_margin_account(price_rows: list[dict], margin_rate: float) -> dict:
    """Pure margin math over per-ticker {open, midday, close} prices.

    Each input row: {ticker, quantity, open, midday, close, midday_source}.
    Zero-quantity tickers are OMITTED from rows entirely (they still appear in
    the correlation part of the snapshot). margin = price x quantity x rate;
    peak_margin = max of the three total-margin checkpoints. Prices are summed
    in their native quote currency — no FX conversion is applied to totals.
    """
    points = ("open", "midday", "close")
    totals = {f"position_value_{p}": 0.0 for p in points}
    totals.update({f"margin_{p}": 0.0 for p in points})
    rows = []
    for src in sorted(price_rows, key=lambda r: r["ticker"]):
        qty = float(src["quantity"])
        if qty == 0:
            continue
        row = {
            "ticker": src["ticker"],
            "quantity": qty,
            "open": round(float(src["open"]), 6),
            "midday": round(float(src["midday"]), 6),
            "close": round(float(src["close"]), 6),
            "midday_source": src["midday_source"],
        }
        for p in points:
            value = float(src[p]) * qty
            row[f"margin_{p}"] = round(value * margin_rate, 2)
            totals[f"position_value_{p}"] += value
            totals[f"margin_{p}"] += value * margin_rate
        rows.append(row)
    out_totals = {k: round(v, 2) for k, v in totals.items()}
    out_totals["peak_margin"] = (
        round(max(totals[f"margin_{p}"] for p in points), 2) if rows else 0.0
    )
    return {"margin_rate": margin_rate, "rows": rows, "totals": out_totals}


def build_margin_account(config: dict) -> dict | None:
    """Fetch latest-trading-day open/midday/close for every positioned ticker
    and compute the CFD margin section. Per-ticker failures land in `skipped`
    instead of aborting; returns None when no ticker has a nonzero position
    (the snapshot then simply has no margin_account section)."""
    positions = config.get("positions", {})
    margin_rate = float(config.get("margin_rate", 0.20))
    active = [t for t in config["tickers"] if positions.get(t)]
    if not active:
        return None

    daily = fetch_daily_ohlc(active)
    intraday = None
    try:
        intraday = fetch_intraday_closes(active)
    except Exception:  # noqa: BLE001
        logger.warning("intraday fetch failed; midday falls back to (high+low)/2", exc_info=True)

    price_rows, skipped = [], {}
    for t in active:
        try:
            if daily.empty or t not in daily.columns.get_level_values(0):
                skipped[t] = "no daily OHLC data returned from Yahoo Finance"
                continue
            bars = daily[t].dropna(subset=["Open", "High", "Low", "Close"])
            if bars.empty:
                skipped[t] = "no complete daily OHLC bar"
                continue
            bar = bars.iloc[-1]
            session_date = bars.index[-1].date()

            midday = None
            if intraday is not None and t in intraday.columns:
                try:
                    midday = midday_from_intraday(intraday[t], session_date)
                except Exception:  # noqa: BLE001
                    midday = None
            if midday is not None:
                source = "intraday"
            else:  # no bars for that session (or intraday fetch failed entirely)
                midday = (float(bar["High"]) + float(bar["Low"])) / 2.0
                source = "hl_midpoint_proxy"

            price_rows.append({
                "ticker": t,
                "quantity": positions[t],
                "open": float(bar["Open"]),
                "midday": midday,
                "close": float(bar["Close"]),
                "midday_source": source,
            })
        except Exception as exc:  # noqa: BLE001
            skipped[t] = f"margin price lookup failed: {exc}"

    doc = compute_margin_account(price_rows, margin_rate)
    doc["skipped"] = skipped
    return doc


def build_document(run_date: str, config: dict, stats: dict, corr: pd.DataFrame, skipped: dict) -> dict:
    """Assemble the JSON snapshot, including a ready-to-POST /api/v1/simulate body."""
    tickers = list(corr.columns)
    matrix = corr.loc[tickers, tickers].round(6).values.tolist()
    assets = [
        {
            "ticker": t,
            "initial_price": stats[t]["last_price"],
            "annual_volatility": stats[t]["annual_volatility"],
            # 0.0 on purpose: historical drift is a noisy return forecast; the
            # measured value is in per_ticker if you want to override it
            "annual_drift": 0.0,
            "position_units": 1.0,   # placeholder — set to your actual position size
            "margin_pct": None,       # placeholder — set to your actual margin %, e.g. 0.20
        }
        for t in tickers
    ]
    return {
        "date": run_date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "config": {"lookback_days": config["lookback_days"], "interval": config["interval"]},
        "tickers": tickers,
        "skipped": skipped,
        "per_ticker": stats,
        "correlation_matrix": matrix,
        "simulate_payload": {
            "assets": assets,
            "correlation_matrix": matrix,
            "num_simulations": 20000,
            "horizon_days": 20,
            "confidence_level": 0.95,
        },
        "simulate_payload_notes": "annual_drift fixed at 0.0 (risk-neutral); "
                                  "edit position_units/margin_pct before POSTing.",
    }


def write_outputs(output_dir: Path, doc: dict) -> list[Path]:
    """Write correlations/<date>.json and latest.json atomically (tmp + rename)
    so API readers never see a half-written file."""
    corr_dir = output_dir / "correlations"
    corr_dir.mkdir(parents=True, exist_ok=True)
    targets = [corr_dir / f"{doc['date']}.json", output_dir / "latest.json"]
    payload = json.dumps(doc, indent=2)
    for target in targets:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(payload)
        os.replace(tmp, target)
    return targets


def run_once(config_path: Path, output_dir: Path) -> int:
    """One full fetch/compute/write cycle. Returns a process exit code:
    0 if >=2 tickers succeeded, 1 otherwise (nothing written on failure)."""
    config = load_config(config_path)
    logger.info(
        "fetching %d tickers, lookback=%d days, interval=%s",
        len(config["tickers"]), config["lookback_days"], config["interval"],
    )
    closes = fetch_close_history(config["tickers"], config["lookback_days"], config["interval"])

    absent = {
        t: "no data returned from Yahoo Finance"
        for t in config["tickers"]
        if t not in closes.columns or closes[t].dropna().empty
    }
    stats, corr, thin = compute_stats(closes.drop(columns=list(absent), errors="ignore"))
    skipped = {**absent, **thin}

    if len(stats) < 2:
        logger.error(
            "only %d ticker(s) produced usable data (need >= 2); skipped=%s",
            len(stats), skipped,
        )
        return 1

    run_date = datetime.now(timezone.utc).date().isoformat()
    doc = build_document(run_date, config, stats, corr, skipped)

    # CFD margin section: best-effort — a failure here must never lose the
    # correlation snapshot, so it is guarded and simply omitted on error.
    try:
        margin = build_margin_account(config)
    except Exception:  # noqa: BLE001
        logger.exception("margin_account computation failed; writing snapshot without it")
        margin = None
    if margin is not None:
        doc["margin_account"] = margin
        logger.info(
            "margin_account: %d position(s), peak_margin=%.2f%s",
            len(margin["rows"]), margin["totals"]["peak_margin"],
            f", skipped={margin['skipped']}" if margin["skipped"] else "",
        )

    targets = write_outputs(output_dir, doc)
    logger.info(
        "wrote %s (%d tickers ok, %d skipped%s)",
        " + ".join(str(t) for t in targets),
        len(stats), len(skipped), f": {skipped}" if skipped else "",
    )
    return 0


def seconds_until_next_run(now: datetime, run_at: str = RUN_AT_UTC) -> float:
    """Seconds from `now` (tz-aware, UTC) to the next daily HH:MM UTC."""
    hour, minute = (int(part) for part in run_at.split(":"))
    nxt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if nxt <= now:
        nxt += timedelta(days=1)
    return (nxt - now).total_seconds()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--loop", action="store_true",
                        help=f"run immediately, then daily at {RUN_AT_UTC} UTC (compose service mode)")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to tickers.json")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="snapshot output directory")
    args = parser.parse_args()

    config_path, output_dir = Path(args.config), Path(args.output_dir)

    if not args.loop:
        sys.exit(run_once(config_path, output_dir))

    # Scheduler loop: a failed run (Yahoo outage, bad config edit) logs and
    # waits for the next slot instead of crash-looping against Yahoo.
    while True:
        try:
            run_once(config_path, output_dir)
        except Exception:  # noqa: BLE001
            logger.exception("correlation run failed; retrying at next scheduled run")
        delay = seconds_until_next_run(datetime.now(timezone.utc))
        logger.info("sleeping %.0f s until next run at %s UTC", delay, RUN_AT_UTC)
        time.sleep(delay)


if __name__ == "__main__":
    main()
