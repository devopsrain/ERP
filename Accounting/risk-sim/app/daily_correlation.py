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

With positions configured the snapshot also gets `margin_timeseries`: total
margin at open / midday / close for every trading day from Jan 1 of the
current year through today, recomputed fresh each run from one batched
unadjusted-OHLC download (midday = (high+low)/2 proxy; today's midday is
refined with the real intraday total from `margin_account` when available).
Same guarantee: a failure here only logs and omits the section.

The SAME single YTD fetch also feeds `pnl_timeseries`: cumulative CFD account
P&L per completed trading day (today's partial bar is dropped). Per ticker
the basis is the configured entry_price when present, else the first
available close of the year (recorded per ticker in `basis_by_ticker`);
pnl(t) = sum qty x (close(t) - basis) over the tickers with a bar that day,
plus a per-day `daily_change`. Fees/overnight financing are NOT modeled.
Same never-fatal guarantee as the other CFD sections.

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
import random
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
logger = logging.getLogger("risk-sim.correlation")

# ---------------------------------------------------------------------------
# Hardened-container plumbing: writable caches + retries against Yahoo
#
# The job container runs read-only with a tmpfs /tmp (see docker-compose.yml),
# so $HOME (/home/appuser) is unwritable and yfinance's default tz cache under
# ~/.cache explodes with "Failed to create TzCache ... [Errno 17] File exists".
# Everything cache-shaped is therefore pointed at /tmp before yfinance runs.
#
# Yahoo also intermittently rate-limits/blocks, returning HTML instead of JSON
# ("Expecting value: line 1 column 1 (char 0)" / "YFTzMissingError possibly
# delisted"); every network call goes through with_retries() below.
# ---------------------------------------------------------------------------

_YF_TZ_CACHE_DIR = "/tmp/yfinance-tz"


def _redirect_unwritable_home() -> None:
    """If $HOME/.cache is unwritable (read-only container), point HOME and
    XDG_CACHE_HOME at /tmp so yfinance/requests/curl_cffi caches land on the
    tmpfs instead of failing. No-op on a normal writable home."""
    try:
        cache_dir = Path(os.path.expanduser("~")) / ".cache"
        cache_dir.mkdir(parents=True, exist_ok=True)
        probe = cache_dir / f".rw-probe-{os.getpid()}"
        probe.touch()
        probe.unlink()
    except OSError:
        os.environ["HOME"] = "/tmp"
        os.environ["XDG_CACHE_HOME"] = "/tmp/.cache"
        logger.info("home directory is unwritable; redirected HOME/XDG_CACHE_HOME to /tmp")


_redirect_unwritable_home()

_yf_cache_configured = False


def _import_yfinance():
    """Import yfinance lazily (offline tests import this module without it)
    and, once per process, relocate its tz cache to the writable /tmp tmpfs.
    The setter name varies across yfinance versions, so it is feature-detected
    and any failure is non-fatal (worst case yfinance falls back internally)."""
    global _yf_cache_configured
    import yfinance as yf

    if not _yf_cache_configured:
        try:
            Path(_YF_TZ_CACHE_DIR).mkdir(parents=True, exist_ok=True)
            for name in ("set_tz_cache_location", "set_cache_location"):
                setter = getattr(yf, name, None)
                if callable(setter):
                    setter(_YF_TZ_CACHE_DIR)
                    break
        except Exception:  # noqa: BLE001
            logger.warning("could not relocate yfinance tz cache to %s", _YF_TZ_CACHE_DIR,
                           exc_info=True)
        _yf_cache_configured = True
    return yf


RETRY_DELAYS_S = (2.0, 5.0, 12.0)  # base backoff schedule; jitter is added on top


def with_retries(call, *, what: str, attempts: int = 3, delays: tuple = RETRY_DELAYS_S,
                 sleep=time.sleep, rng=random.random):
    """Run `call()` up to `attempts` times, sleeping delays[i] (+ up to 50%
    jitter) after the i-th failure. Catches any Exception (Yahoo failures show
    up as JSON decode errors, HTTP errors or yfinance-internal exceptions
    depending on version) and re-raises the last one when attempts run out.
    `sleep` and `rng` are injectable so tests never actually wait."""
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return call()
        except Exception as exc:  # noqa: BLE001
            last_exc = exc
            if attempt >= attempts:
                break
            delay = delays[min(attempt - 1, len(delays) - 1)] * (1.0 + rng() * 0.5)
            logger.warning("%s failed (attempt %d/%d): %s — retrying in %.1fs",
                           what, attempt, attempts, exc, delay)
            sleep(delay)
    logger.error("%s failed after %d attempts: %s", what, attempts, last_exc)
    raise last_exc

TRADING_DAYS = 252
MIN_RETURN_OBSERVATIONS = 5   # per ticker; below this the estimate is noise
RUN_AT_UTC = "07:00"          # daily schedule for --loop mode

DEFAULT_CONFIG_PATH = os.getenv("CORRELATION_CONFIG", "/srv/config/tickers.json")
DEFAULT_OUTPUT_DIR = os.getenv("CORRELATION_OUTPUT_DIR", "/data/output")


def load_config(path: Path) -> dict:
    """Read tickers.json; tolerate missing optional keys, insist on >=2 tickers.

    "positions" maps ticker -> CFD quantity, in either of two forms (both
    accepted, mixed freely):

        "AAPL": 100                                    # plain quantity
        "AAPL": {"qty": 100, "entry_price": 185.5}     # object form

    entry_price is optional and only used by the P&L series (basis price);
    it is normalized out into a separate "entry_prices" map so every existing
    margin consumer keeps seeing plain ticker -> qty floats. Tickers without
    an entry (or with an unparsable one) default to qty 0 = excluded from the
    margin/P&L sections but still part of the correlation run. "margin_rate"
    defaults to 0.20.
    """
    with open(path) as f:
        cfg = json.load(f)
    tickers = [t.strip() for t in cfg.get("tickers", []) if isinstance(t, str) and t.strip()]
    if len(tickers) < 2:
        raise ValueError(f"config {path} must list at least 2 tickers")
    positions_cfg = cfg.get("positions") or {}
    positions, entry_prices = {}, {}
    for t in tickers:
        raw = positions_cfg.get(t, 0)
        entry_raw = None
        if isinstance(raw, dict):  # object form {"qty": ..., "entry_price": ...}
            entry_raw = raw.get("entry_price")
            raw = raw.get("qty", 0)
        try:
            positions[t] = float(raw or 0)
        except (TypeError, ValueError):
            positions[t] = 0.0
        if entry_raw is not None:
            try:
                entry_prices[t] = float(entry_raw)
            except (TypeError, ValueError):
                pass  # unparsable entry_price -> fall back to first-close basis
    return {
        "tickers": tickers,
        "lookback_days": int(cfg.get("lookback_days", 90)),
        "interval": str(cfg.get("interval", "1d")),
        "positions": positions,
        "entry_prices": entry_prices,
        "margin_rate": float(cfg.get("margin_rate", 0.20)),
    }


def fetch_close_history(tickers: list[str], lookback_days: int, interval: str = "1d") -> pd.DataFrame:
    """Adjusted-close history, one column per ticker. Tickers Yahoo knows
    nothing about simply come back missing/NaN — the caller records them as
    skipped rather than failing the run (unlike fetch_market_data.py).

    One batched yf.download call for ALL tickers (not per-ticker loops) to
    keep the request count minimal; retried via with_retries. Raises after
    the final retry — the caller maps that to skipped=yahoo_blocked_or_missing.
    """
    yf = _import_yfinance()  # lazy: lets offline tests import this module without yfinance

    end = datetime.now(timezone.utc)
    # buffer the calendar window since weekends/holidays eat into it
    start = end - timedelta(days=int(lookback_days * 1.6) + 10)

    raw = with_retries(
        lambda: yf.download(
            tickers,
            start=start.date().isoformat(),
            end=end.date().isoformat(),
            interval=interval,
            auto_adjust=True,
            progress=False,
            group_by="column",
        ),
        what=f"yf.download close history ({len(tickers)} tickers)",
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
    split/dividend-adjusted series used for the correlations.

    One batched call for all positioned tickers, retried; per-ticker columns
    are parsed out by the caller with the usual skip-on-missing semantics."""
    yf = _import_yfinance()  # lazy: offline tests import this module without yfinance

    raw = with_retries(
        lambda: yf.download(
            tickers, period="7d", interval="1d",
            auto_adjust=False, progress=False, group_by="ticker",
        ),
        what=f"yf.download daily OHLC ({len(tickers)} tickers)",
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
    yf = _import_yfinance()

    for interval in ("60m", "30m"):
        try:
            raw = with_retries(
                lambda interval=interval: yf.download(
                    tickers, period="2d", interval=interval,
                    auto_adjust=False, progress=False, group_by="column",
                ),
                what=f"yf.download intraday {interval} ({len(tickers)} tickers)",
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

    daily_fetch_failed = False
    try:
        daily = fetch_daily_ohlc(active)
    except Exception:  # noqa: BLE001
        logger.warning("daily OHLC fetch failed after retries; all margin tickers skipped",
                       exc_info=True)
        daily = pd.DataFrame()
        daily_fetch_failed = True
    intraday = None
    try:
        intraday = fetch_intraday_closes(active)
    except Exception:  # noqa: BLE001
        logger.warning("intraday fetch failed; midday falls back to (high+low)/2", exc_info=True)

    price_rows, skipped = [], {}
    for t in active:
        try:
            if daily.empty or t not in daily.columns.get_level_values(0):
                skipped[t] = ("yahoo_blocked_or_missing" if daily_fetch_failed
                              else "no daily OHLC data returned from Yahoo Finance")
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


# ---------------------------------------------------------------------------
# CFD margin: year-to-date time series (open / midday / close totals per day)
# ---------------------------------------------------------------------------

def fetch_ytd_ohlc(tickers: list[str]) -> pd.DataFrame:
    """Daily OHLC bars from Jan 1 of the current year through today, columns
    MultiIndex (ticker, field). Unadjusted prices for the same reason as
    fetch_daily_ohlc: margin is charged on traded prices. One batched call
    for all positioned tickers, retried via with_retries."""
    yf = _import_yfinance()  # lazy: offline tests import this module without yfinance

    today = datetime.now(timezone.utc).date()
    raw = with_retries(
        lambda: yf.download(
            tickers,
            start=today.replace(month=1, day=1).isoformat(),
            end=(today + timedelta(days=1)).isoformat(),  # yf `end` is exclusive
            interval="1d",
            auto_adjust=False, progress=False, group_by="ticker",
        ),
        what=f"yf.download YTD OHLC ({len(tickers)} tickers)",
    )
    if raw is None or raw.empty:
        return pd.DataFrame()
    if not isinstance(raw.columns, pd.MultiIndex):  # single ticker collapses flat
        raw.columns = pd.MultiIndex.from_product([tickers[:1], raw.columns])
    return raw


def compute_margin_timeseries(daily: pd.DataFrame, positions: dict, margin_rate: float) -> dict | None:
    """Pure math: per-trading-day total margin required at open / midday /
    close over a MultiIndex (ticker, field) daily-OHLC frame.

    midday is always the (high+low)/2 daily-bar proxy here (intraday history
    is not available that far back) — flagged once in the metadata as
    midday_source="hl_midpoint_proxy". Each day sums only the tickers that
    HAVE a complete bar that day; `n_tickers` records how many, so partial
    days (one exchange closed, late listing) stay visible instead of the
    total silently dipping. Returns None when no day has any usable bar.
    """
    if daily is None or daily.empty or not isinstance(daily.columns, pd.MultiIndex):
        return None
    fields = ("Open", "High", "Low", "Close")
    per_ticker: dict[str, tuple[float, pd.DataFrame]] = {}
    for t in daily.columns.get_level_values(0).unique():
        qty = float(positions.get(t, 0) or 0)
        if qty == 0:
            continue
        cols = daily[t]
        if not set(fields).issubset(cols.columns):
            continue
        bars = cols.dropna(subset=list(fields))
        if not bars.empty:
            per_ticker[t] = (qty, bars)
    if not per_ticker:
        return None

    dates = sorted({ts for _, bars in per_ticker.values() for ts in bars.index})
    points = []
    for ts in dates:
        tot_open = tot_mid = tot_close = 0.0
        n = 0
        for qty, bars in per_ticker.values():
            if ts not in bars.index:
                continue
            bar = bars.loc[ts]
            tot_open += float(bar["Open"]) * qty
            tot_mid += (float(bar["High"]) + float(bar["Low"])) / 2.0 * qty
            tot_close += float(bar["Close"]) * qty
            n += 1
        if n == 0:
            continue
        points.append({
            "date": ts.date().isoformat(),
            "open": round(tot_open * margin_rate, 2),
            "midday": round(tot_mid * margin_rate, 2),
            "close": round(tot_close * margin_rate, 2),
            "n_tickers": n,
        })
    if not points:
        return None
    return {"margin_rate": margin_rate, "midday_source": "hl_midpoint_proxy", "points": points}


def refine_today_midday(timeseries: dict, margin_account: dict | None) -> None:
    """Overwrite the last point's hl-proxy midday with the margin_account's
    real-intraday midday total — but only when both clearly describe the same
    session: the account totals contain at least one intraday row, cover the
    same number of tickers, and match the point's close-margin total exactly
    (both are computed from the same latest daily bars). The refined point is
    flagged midday_source="intraday" so the exception to the series-level
    proxy metadata stays visible."""
    if not margin_account or not margin_account.get("rows"):
        return
    if not any(r.get("midday_source") == "intraday" for r in margin_account["rows"]):
        return  # account midday is itself the proxy — nothing better to copy
    last = timeseries["points"][-1]
    totals = margin_account.get("totals", {})
    if len(margin_account["rows"]) != last["n_tickers"]:
        return
    try:
        if round(float(totals["margin_close"]), 2) != last["close"]:
            return
        last["midday"] = round(float(totals["margin_midday"]), 2)
    except (KeyError, TypeError, ValueError):
        return
    last["midday_source"] = "intraday"


def build_margin_timeseries(config: dict, margin_account: dict | None = None,
                            daily: pd.DataFrame | None = None) -> dict | None:
    """Compute the margin_timeseries section from YTD daily OHLC. Pass the
    already-fetched frame via `daily` (run_once fetches YTD once and feeds
    both this and the P&L series); when omitted, it is fetched here. Returns
    None (after a log warning) on any fetch/data failure — the snapshot is
    then simply written without it."""
    positions = config.get("positions", {})
    margin_rate = float(config.get("margin_rate", 0.20))
    active = [t for t in config["tickers"] if positions.get(t)]
    if not active:
        return None
    if daily is None:
        try:
            daily = fetch_ytd_ohlc(active)
        except Exception:  # noqa: BLE001
            logger.warning("YTD OHLC fetch failed after retries; margin_timeseries omitted",
                           exc_info=True)
            return None
    doc = compute_margin_timeseries(daily, positions, margin_rate)
    if doc is None:
        logger.warning("no usable YTD OHLC bars; margin_timeseries omitted")
        return None
    refine_today_midday(doc, margin_account)
    return doc


# ---------------------------------------------------------------------------
# CFD account P&L: year-to-date cumulative profit/loss per trading day
# ---------------------------------------------------------------------------

def compute_pnl_timeseries(daily: pd.DataFrame, positions: dict,
                           entry_prices: dict | None = None,
                           today: "datetime.date | None" = None) -> dict | None:
    """Pure math: cumulative CFD P&L per trading day over a MultiIndex
    (ticker, field) daily-OHLC frame (the same frame margin_timeseries uses).

    Per ticker the reference price is the configured entry_price when one
    exists, otherwise the FIRST available close of the year; which one was
    used (and its value) is recorded in `basis_by_ticker`. Per trading day t:
    pnl_i(t) = qty_i x (close_i(t) - ref_i), summed over the tickers that
    HAVE a close that day (`n_tickers` keeps partial days visible, exactly
    like margin_timeseries). `daily_change` is total_pnl(t) - total_pnl(t-1);
    the first point has no previous day, so its daily_change is None.

    Bars dated `today` (default: the current UTC date) are DROPPED so a
    partial live session never pollutes the series — the last point is the
    last completed trading day. Returns None when nothing usable remains.
    """
    if daily is None or daily.empty or not isinstance(daily.columns, pd.MultiIndex):
        return None
    entry_prices = entry_prices or {}
    if today is None:
        today = datetime.now(timezone.utc).date()

    per_ticker: dict[str, tuple[float, float, pd.Series]] = {}  # qty, ref, closes
    basis_by_ticker: dict[str, dict] = {}
    for t in daily.columns.get_level_values(0).unique():
        qty = float(positions.get(t, 0) or 0)
        if qty == 0:
            continue
        cols = daily[t]
        if "Close" not in cols.columns:
            continue
        closes = cols["Close"].dropna()
        closes = closes[[ts for ts in closes.index if ts.date() != today]]
        if closes.empty:
            continue
        if t in entry_prices:
            ref, basis = float(entry_prices[t]), "entry_price"
        else:
            ref, basis = float(closes.iloc[0]), "first_close"
        per_ticker[t] = (qty, ref, closes)
        basis_by_ticker[t] = {"basis": basis, "value": round(ref, 6)}
    if not per_ticker:
        return None

    dates = sorted({ts for _, _, closes in per_ticker.values() for ts in closes.index})
    points, prev_pnl = [], None
    for ts in dates:
        total, n = 0.0, 0
        for qty, ref, closes in per_ticker.values():
            if ts not in closes.index:
                continue  # missing bar: that ticker just doesn't contribute today
            total += qty * (float(closes.loc[ts]) - ref)
            n += 1
        if n == 0:
            continue
        pnl = round(total, 2)
        points.append({
            "date": ts.date().isoformat(),
            "pnl": pnl,
            "daily_change": None if prev_pnl is None else round(pnl - prev_pnl, 2),
            "n_tickers": n,
        })
        prev_pnl = pnl
    if not points:
        return None
    return {"basis_by_ticker": basis_by_ticker, "points": points}


def build_pnl_timeseries(config: dict, daily: pd.DataFrame | None = None) -> dict | None:
    """Compute the pnl_timeseries section from YTD daily OHLC. Pass the
    already-fetched frame via `daily` (run_once fetches YTD once for both the
    margin and P&L series); when omitted, it is fetched here. Returns None
    (after a log warning) on any fetch/data failure — the snapshot is then
    simply written without it."""
    positions = config.get("positions", {})
    active = [t for t in config["tickers"] if positions.get(t)]
    if not active:
        return None
    if daily is None:
        try:
            daily = fetch_ytd_ohlc(active)
        except Exception:  # noqa: BLE001
            logger.warning("YTD OHLC fetch failed after retries; pnl_timeseries omitted",
                           exc_info=True)
            return None
    doc = compute_pnl_timeseries(daily, positions, config.get("entry_prices") or {})
    if doc is None:
        logger.warning("no usable YTD close bars; pnl_timeseries omitted")
        return None
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
    history_fetch_failed = False
    try:
        closes = fetch_close_history(config["tickers"], config["lookback_days"], config["interval"])
    except Exception:  # noqa: BLE001
        logger.exception("close-history fetch failed after retries")
        closes = pd.DataFrame(columns=config["tickers"])
        history_fetch_failed = True

    absent_reason = ("yahoo_blocked_or_missing" if history_fetch_failed
                     else "no data returned from Yahoo Finance")
    absent = {
        t: absent_reason
        for t in config["tickers"]
        if t not in closes.columns or closes[t].dropna().empty
    }
    stats, corr, thin = compute_stats(closes.drop(columns=list(absent), errors="ignore"))
    skipped = {**absent, **thin}

    if len(stats) < 2:
        if not stats:
            logger.error(
                "every ticker failed — Yahoo appears to be blocking or rate-limiting this "
                "server's IP. Try `docker compose build --no-cache correlation-job` to pick "
                "up a newer yfinance, or wait and retry at the next scheduled run."
            )
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

    # YTD sections (margin time series + account P&L): same best-effort
    # contract as margin_account — recomputed fresh from Jan 1 every run,
    # omitted (never fatal) on failure. ONE batched YTD OHLC fetch feeds both.
    ytd = None
    active = [t for t in config["tickers"] if config["positions"].get(t)]
    if active:
        try:
            ytd = fetch_ytd_ohlc(active)
        except Exception:  # noqa: BLE001
            logger.warning("YTD OHLC fetch failed after retries; "
                           "margin_timeseries and pnl_timeseries omitted", exc_info=True)

    margin_ts = None
    if ytd is not None:
        try:
            margin_ts = build_margin_timeseries(config, margin, daily=ytd)
        except Exception:  # noqa: BLE001
            logger.exception("margin_timeseries computation failed; writing snapshot without it")
    if margin_ts is not None:
        doc["margin_timeseries"] = margin_ts
        pts = margin_ts["points"]
        logger.info("margin_timeseries: %d point(s), %s .. %s",
                    len(pts), pts[0]["date"], pts[-1]["date"])

    pnl_ts = None
    if ytd is not None:
        try:
            pnl_ts = build_pnl_timeseries(config, daily=ytd)
        except Exception:  # noqa: BLE001
            logger.exception("pnl_timeseries computation failed; writing snapshot without it")
    if pnl_ts is not None:
        doc["pnl_timeseries"] = pnl_ts
        pts = pnl_ts["points"]
        logger.info("pnl_timeseries: %d point(s), %s .. %s, latest pnl=%.2f",
                    len(pts), pts[0]["date"], pts[-1]["date"], pts[-1]["pnl"])

    targets = write_outputs(output_dir, doc)
    logger.info(
        "wrote %s (%d tickers ok, %d skipped%s)",
        " + ".join(str(t) for t in targets),
        len(stats), len(skipped), f": {skipped}" if skipped else "",
    )

    # Momentum screener: separate outputs (screener/<date>.json +
    # screener-latest.json), config-gated via screener.enabled and guarded —
    # a screener failure must never fail the correlation run. NOTE: this
    # fetch is much heavier than the correlation one (~500-ticker universe
    # x ~400 days of bars — the doubler windows + 52w highs need them).
    try:
        from app.momentum_screener import run_daily_screen
        screen = run_daily_screen(config_path, output_dir)
        if screen is not None:
            logger.info("momentum screener: scanned=%d passed=%d skipped=%d",
                        screen["scanned"], screen["passed_filters"], screen["skipped"])
    except Exception:  # noqa: BLE001
        logger.exception("momentum screener failed; correlation outputs are unaffected")

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
