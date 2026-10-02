"""
Daily momentum screener.

Scans a large static universe (config/universe.json, ~500 US large caps) for
short-term momentum candidates and writes, next to the correlation snapshots:

  screener/YYYY-MM-DD.json   dated snapshot (kept forever = history)
  screener-latest.json       same content under a stable name
  screener-hits.json         per-date hits index (one row per snapshot date:
                             candidate/doubler counts + top picks), upserted
                             on EVERY snapshot write — daily, --asof and
                             --backfill alike; rebuilt from the dated files
                             when missing; newest-first, capped at ~400 rows

Pipeline (deliberately staged so the expensive lookups stay tiny):

  1. ONE pass of batched yf.download daily bars for the whole universe,
     chunked into batches of <= 100 tickers, each batch retried via
     with_retries (shared with app.daily_correlation). The window is ~400
     calendar days (~275 trading days) — it used to be ~110, but the DOUBLER
     criterion needs ~270 calendar days of returns and the 52-week-high
     check needs 252 trading days, so the daily fetch got ~4x heavier
     (still one daily-bars call per <=100-ticker batch).
     The benchmark tickers SPY and QQQ (BENCHMARK_TICKERS) ride along in the
     same fetch; they are never scanned, counted or listed — only used for
     relative strength, the market regime and the report-card benchmark.
  2. Pure per-ticker metric math (compute_ticker_metrics): last close,
     2d/5d/20d/60d close-to-close returns over TRADING bars, RVOL (last
     volume / mean of the prior 20 days' volume) + class + 5d/20d volume
     trend, 20d average + median dollar volume + liquidity tier, MA20/MA50
     + % distance + a trend state, 20d/50d new-high flags, the 52-week high
     (distance, bars since), plus the doubler window returns (ret_90d /
     ret_270d by default — calendar windows mapped to bars).
  3. Price-based filters -> three BUCKETS (a ticker may sit in several):
       fresh_momentum       (= "candidates", kept for compatibility): HARD
                            gates min_return_2d and min_return_5d; min_price,
                            min_rvol, min_avg_dollar_vol, require_above_ma20/50
                            are OPTIONAL tightening knobs, OFF by default.
       established_momentum ret_20d >= est_min_return_20d (0.20) AND ret_60d
                            >= est_min_return_60d (0.30) AND price > MA20 >
                            MA50.
       long_term_winners    (= "doublers"): >= doubler_min_return (+100%) over
                            ANY doubler window (+ the optional price/$vol
                            knobs when enabled).
     Trading-day window lengths are derived from the calendar windows as
     round(window * 252/365) — 90d -> 62 bars, 270d -> 186 bars.
  4. Market cap via yf fast_info/info ONLY for the few tickers that survived
     step 3 (looked up ONCE per finalist across all buckets). Caps below
     min_market_cap are dropped; UNKNOWN caps are KEPT but flagged
     "cap_unknown": true — a missing Yahoo field must not hide an
     otherwise-valid candidate.
  5. new_52w_high: computed from the main window when a ticker has >= 252
     closes; fresh-momentum rows that still lack it fall back to a
     finalists-only period="1y" fetch (null when that fails; never fatal).
  6. Research funnel per row (enrich_row_v3, all pure functions): relative
     strength vs SPY/QQQ and vs theme peers, acceleration label, SETUP
     classification, risk flags + confirmations, an ABSOLUTE 0-100 score
     (fixed scaling, see score_row), a research-queue tier A-D, freshness
     (consecutive prior snapshots in the same bucket), an earnings catalyst
     window and — for moves > +200% only — a data-quality check (jump days,
     splits). Earnings dates and splits are the ONLY extra lookups: tiny,
     finalists-only, cached in the output dir, never fetched in replay mode.
  7. List level: benchmarks, market regime, research_queue (A-D, each ticker
     once), theme_breadth over the long-term winners, sector concentration,
     and (via the hits index) the activity block.

Config lives in the "screener" section of tickers.json (all keys optional,
see DEFAULT_CRITERIA / the doubler defaults). The daily correlation job
calls run_daily_screen() after its own outputs; any failure here only logs —
it NEVER fails the correlation run. Note this fetch is much heavier than the
correlation one (~500 tickers x ~400 days vs a handful x 90).

At the end of each daily screen, evaluate_past_signals() grades snapshots
from 5/10/20 trading days ago (nearest file within +-2 days): realized
return from each pick's recorded price to the latest close, summarized per
lookback for momentum candidates and doublers separately, written as
"report_card" into the snapshot (empty lookbacks omitted, failures only log).

HISTORICAL REPLAY (CLI, run manually — the scheduled path is unchanged):

  python -m app.momentum_screener --asof YYYY-MM-DD   # full screen on data <= that date
  python -m app.momentum_screener --backfill N [--force]  # last N trading days, ONE fetch

Both write screener/<date>.json ONLY (screener-latest.json is untouched),
mark the snapshot "backfilled": true, and carry an honest "note": market-cap
filtering uses CURRENT caps (free data has no historical caps — same
limitation as app.backtest). --backfill skips dates that already have files
unless --force.

An empty candidates list is a NORMAL outcome — the default screen is LOOSE
(return thresholds + market cap only; tighten via config) but +10% in 2 days
AND +30% in 5 days is still rare. This is a discovery screen, not a buy
signal: a +30% week or a +100% quarter can be accumulation, a short squeeze
or pure hype — always do second-stage analysis.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.daily_correlation import _import_yfinance, with_retries

logger = logging.getLogger("risk-sim.screener")

BATCH_SIZE = 100              # tickers per yf.download call
INTER_BATCH_PAUSE_S = 3       # courtesy pause between live Yahoo batches
HISTORY_CALENDAR_DAYS = 400   # ~275 trading days: doubler ret_270d needs ~187
                              # bars and the 52w-high check needs 252 (was 110
                              # before the doubler criterion — heavier fetch)
VOLUME_WINDOW = 20            # days for RVOL denominator + avg dollar volume
MIN_CLOSES = 51               # 50 bars for MA50 + the bar being screened
TRADING_DAYS_52W = 252        # bars needed to call a 52-week high from history

# HARD gates by default: min_return_2d, min_return_5d and min_market_cap.
# The rest are OPTIONAL tightening knobs shipped OFF (0 / false = gate off,
# informational only): the metrics are still computed, shown and scored —
# raise them in config to make them filter again.
DEFAULT_CRITERIA = {
    "min_market_cap": 1e9,
    "min_price": 0.0,             # 0 = off (was 10.0 when it gated by default)
    "min_return_2d": 0.10,
    "min_return_5d": 0.30,
    "min_rvol": 0.0,              # 0 = off (was 1.5)
    "min_avg_dollar_vol": 0.0,    # 0 = off (was 20e6)
    "require_above_ma20": False,  # false = off (was True)
    "require_above_ma50": False,  # false = off (was True)
    "est_min_return_20d": 0.20,   # established_momentum bucket: ret_20d >= this
    "est_min_return_60d": 0.30,   # ... AND ret_60d >= this (AND price > MA20 > MA50)
}
DEFAULT_DOUBLER_WINDOWS = [90, 270]   # calendar days; trading bars derived below
DEFAULT_DOUBLER_MIN_RETURN = 1.00     # +100% over any window = a DOUBLER

BENCHMARK_TICKERS = ("SPY", "QQQ")    # fetched with every universe, never scanned/listed
BENCHMARK_PRIMARY = "SPY"             # the one `rs` / report-card excess is measured against
BUCKET_NAMES = ("fresh_momentum", "established_momentum", "long_term_winners")

HITS_INDEX_NAME = "screener-hits.json"  # per-date hits index next to screener-latest.json
HITS_MAX_ENTRIES = 400                  # newest-first cap (~1.5 years of trading days)
HITS_TOP_TICKERS = 10                   # per bucket, per hits row (persistence basis)

REPORT_CARD_LOOKBACKS = (5, 10, 20)   # trading days back to grade
REPORT_CARD_TOLERANCE_DAYS = 2        # accept the nearest snapshot within +-2 days

CATALYST_CACHE_NAME = "catalyst-cache.json"   # {ticker: {"fetched": date, "dates": [iso...]}}
CATALYST_CACHE_TTL_DAYS = 7
CATALYST_WINDOW_DAYS = 10                     # earnings within -10..+10 days = catalyst
SPLITS_CACHE_NAME = "splits-cache.json"       # {ticker: {"fetched": date, "splits": [{date, ratio}]}}
SPLITS_CACHE_TTL_DAYS = 30
DATA_QUALITY_TRIGGER_RETURN = 2.0             # |ret_90d| or |ret_270d| above this -> check
DATA_QUALITY_JUMP_MOVE = 0.5                  # |daily move| above this = a jump day

DEFAULT_UNIVERSE_PATH = os.getenv("SCREENER_UNIVERSE", "")  # default: next to tickers.json


# ---------------------------------------------------------------------------
# Config / universe loading
# ---------------------------------------------------------------------------

def load_screener_config(raw_cfg: dict) -> dict:
    """Extract the "screener" section of tickers.json with full defaults.
    Every key is optional; unknown keys are ignored (the pre-v3
    "score_weights" min-max weights among them — the v3 score uses the fixed
    absolute scaling in SCORE_MAX / score_row, exposed in doc["analytics"])."""
    sc = raw_cfg.get("screener") or {}
    out = {"enabled": bool(sc.get("enabled", True))}
    for key, default in DEFAULT_CRITERIA.items():
        raw = sc.get(key, default)
        if isinstance(default, bool):
            out[key] = bool(raw)
        else:
            try:
                out[key] = float(raw)
            except (TypeError, ValueError):
                out[key] = float(default)

    # Doubler criterion: calendar windows (list) + the min return over any of
    # them. Unparsable entries fall back to the defaults, like everything else.
    windows: list[int] = []
    raw_windows = sc.get("doubler_windows_days", DEFAULT_DOUBLER_WINDOWS)
    if isinstance(raw_windows, (list, tuple)):
        for w in raw_windows:
            try:
                iw = int(w)
            except (TypeError, ValueError):
                continue
            if iw > 0:
                windows.append(iw)
    out["doubler_windows_days"] = windows or list(DEFAULT_DOUBLER_WINDOWS)
    try:
        out["doubler_min_return"] = float(sc.get("doubler_min_return",
                                                 DEFAULT_DOUBLER_MIN_RETURN))
    except (TypeError, ValueError):
        out["doubler_min_return"] = float(DEFAULT_DOUBLER_MIN_RETURN)
    return out


def load_universe(path: Path) -> dict:
    """Read universe.json -> {"name", "tickers"} (deduped, order preserved)."""
    with open(path) as f:
        data = json.load(f)
    seen, tickers = set(), []
    for t in data.get("tickers", []):
        if isinstance(t, str) and t.strip() and t.strip() not in seen:
            seen.add(t.strip())
            tickers.append(t.strip())
    return {"name": str(data.get("name", "universe")), "tickers": tickers}


# ---------------------------------------------------------------------------
# Fetching (all injectable for offline tests)
# ---------------------------------------------------------------------------

def _make_live_fetch(end_date: date | None = None, extra_days: int = 0):
    """Batch fetcher for ~HISTORY_CALENDAR_DAYS (+extra_days) of daily bars,
    ending at `end_date` (default: resolved to 'today', UTC, per call).
    --asof binds end_date to the replay date; --backfill extends the window
    back with extra_days so ONE fetch covers every replayed day. The
    _live_yahoo marker turns on the inter-batch courtesy pause."""
    def fetch(batch: list[str]) -> pd.DataFrame:
        yf = _import_yfinance()
        end = end_date or datetime.now(timezone.utc).date()
        start = end - timedelta(days=HISTORY_CALENDAR_DAYS + extra_days)
        return yf.download(
            batch,
            start=start.isoformat(),
            end=(end + timedelta(days=1)).isoformat(),  # yf `end` is exclusive
            interval="1d",
            auto_adjust=True,
            progress=False,
            group_by="column",
        )
    fetch._live_yahoo = True  # noqa: SLF001 — marker read by fetch_universe_history
    return fetch


_default_fetch = _make_live_fetch()


def _default_fetch_market_cap(ticker: str) -> float | None:
    """Market cap via fast_info, falling back to .info. None = Yahoo has no
    figure (the caller keeps the candidate and flags cap_unknown)."""
    yf = _import_yfinance()

    def call():
        tk = yf.Ticker(ticker)
        cap = None
        fi = getattr(tk, "fast_info", None)
        if fi is not None:
            for key in ("market_cap", "marketCap"):
                try:
                    cap = getattr(fi, key, None) or fi[key]
                except Exception:  # noqa: BLE001  # fast_info access varies by version
                    cap = None
                if cap:
                    break
        if not cap:
            cap = (tk.info or {}).get("marketCap")
        return float(cap) if cap else None

    return with_retries(call, what=f"market cap lookup {ticker}")


def _default_fetch_52w(tickers: list[str]) -> pd.DataFrame:
    """1 year of daily closes for the finalists only (second, tiny pass)."""
    yf = _import_yfinance()
    raw = with_retries(
        lambda: yf.download(tickers, period="1y", interval="1d",
                            auto_adjust=True, progress=False, group_by="column"),
        what=f"yf.download 1y closes ({len(tickers)} finalists)",
    )
    return _split_close_volume(raw, tickers)[0]


def _split_close_volume(raw: pd.DataFrame | None, batch: list[str]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """(closes, volumes) frames, one column per ticker, from a yf.download
    group_by="column" result. Single-ticker responses collapse flat."""
    if raw is None or raw.empty:
        empty = pd.DataFrame(columns=batch)
        return empty, empty.copy()
    if isinstance(raw.columns, pd.MultiIndex):
        closes = raw["Close"] if "Close" in raw.columns.get_level_values(0) else pd.DataFrame(columns=batch)
        volumes = raw["Volume"] if "Volume" in raw.columns.get_level_values(0) else pd.DataFrame(columns=batch)
    else:
        closes = raw[["Close"]] if "Close" in raw.columns else pd.DataFrame(columns=batch[:1])
        volumes = raw[["Volume"]] if "Volume" in raw.columns else pd.DataFrame(columns=batch[:1])
        closes.columns = batch[: len(closes.columns)]
        volumes.columns = batch[: len(volumes.columns)]
    return closes, volumes


def with_benchmarks(tickers: list[str]) -> list[str]:
    """The universe tickers plus BENCHMARK_TICKERS (appended, deduped, order
    kept) — what every universe fetch actually asks for."""
    out = list(dict.fromkeys(list(tickers) + list(BENCHMARK_TICKERS)))
    return out


def fetch_universe_history(tickers: list[str], fetch=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily close + volume history for the whole universe, chunked into
    batches of <= BATCH_SIZE, each batch retried independently. A batch that
    still fails after retries is logged and its tickers simply come back as
    missing columns (they land in the `skipped` count). Callers pass
    with_benchmarks(universe) so SPY/QQQ always ride along."""
    fetch = fetch or _default_fetch
    close_parts, volume_parts = [], []
    for i in range(0, len(tickers), BATCH_SIZE):
        if i and getattr(fetch, "_live_yahoo", False):
            # Rate-limit courtesy: pause between real Yahoo batches so a full
            # universe scan doesn't land as back-to-back bursts.
            import time as _t
            _t.sleep(INTER_BATCH_PAUSE_S)
        batch = tickers[i:i + BATCH_SIZE]
        try:
            raw = with_retries(lambda b=batch: fetch(b),
                               what=f"screener yf.download batch {i // BATCH_SIZE + 1} ({len(batch)} tickers)")
        except Exception:  # noqa: BLE001
            logger.warning("screener batch %d (%d tickers) failed after retries; skipping it",
                           i // BATCH_SIZE + 1, len(batch), exc_info=True)
            continue
        closes, volumes = _split_close_volume(raw, batch)
        if not closes.empty:
            close_parts.append(closes)
            volume_parts.append(volumes)
    if not close_parts:
        empty = pd.DataFrame()
        return empty, empty.copy()
    return (pd.concat(close_parts, axis=1).sort_index(),
            pd.concat(volume_parts, axis=1).sort_index())


# ---------------------------------------------------------------------------
# Pure metric math
# ---------------------------------------------------------------------------

STANDARD_BAR_WINDOWS = {"ret_5d": 5, "ret_20d": 20, "ret_60d": 60}   # TRADING bars
RVOL_TREND_RECENT_BARS = 5           # rvol_trend = mean vol last 5 / mean vol prior 20
RVOL_TREND_PRIOR_BARS = 20
RVOL_TREND_RISING = 1.2
RVOL_TREND_FALLING = 0.8
RVOL_CLASSES = ((0.7, "weak"), (1.2, "normal"), (2.0, "confirmed"), (3.0, "high"))  # else "exceptional"
LIQUIDITY_TIERS = ((500e6, "A"), (100e6, "B"), (20e6, "C"))                        # else "D"
TREND_STATES = ("strong_uptrend", "uptrend", "mixed", "downtrend", "unknown")


def bar_return(closes: pd.Series, bars: int) -> float | None:
    """close[-1] / close[-(bars+1)] - 1 over `bars` TRADING bars; None when the
    series is too short (or the base close is not positive)."""
    c = closes.dropna()
    if bars <= 0 or len(c) < bars + 1:
        return None
    base = float(c.iloc[-(bars + 1)])
    if base <= 0:
        return None
    return round(float(c.iloc[-1]) / base - 1.0, 4)


def classify_trend(price: float | None, ma20: float | None, ma50: float | None) -> dict:
    """Trend state from the close vs its 20/50-bar simple moving averages.
    Exhaustive rules (so every row gets exactly one state):

      strong_uptrend : price > MA20  AND MA20 > MA50        (stacked: price over both, fast over slow)
      uptrend        : price > MA20  AND price > MA50, MA20 <= MA50 (above both, averages not yet stacked)
      mixed          : above exactly ONE of the two averages
                       (price > MA50 and price <= MA20  — the classic pullback-into-MA20 —
                        or price > MA20 and price <= MA50 — a bounce still under the slow MA)
      downtrend      : price <= MA20 AND price <= MA50      (under both)
      unknown        : any input missing
    """
    if price is None or ma20 is None or ma50 is None or ma20 <= 0 or ma50 <= 0:
        return {"state": "unknown", "above_ma20": None, "above_ma50": None,
                "ma20_gt_ma50": None, "dist_ma20": None, "dist_ma50": None}
    above20, above50, stacked = price > ma20, price > ma50, ma20 > ma50
    if above20 and stacked:
        state = "strong_uptrend"
    elif above20 and above50:
        state = "uptrend"
    elif above20 or above50:
        state = "mixed"
    else:
        state = "downtrend"
    return {"state": state, "above_ma20": bool(above20), "above_ma50": bool(above50),
            "ma20_gt_ma50": bool(stacked),
            "dist_ma20": round(price / ma20 - 1.0, 4), "dist_ma50": round(price / ma50 - 1.0, 4)}


def classify_rvol(rvol: float | None) -> str:
    """weak < 0.7 <= normal < 1.2 <= confirmed < 2.0 <= high < 3.0 <= exceptional."""
    if rvol is None:
        return "unknown"
    for upper, label in RVOL_CLASSES:
        if rvol < upper:
            return label
    return "exceptional"


def rvol_trend_ratio(volumes: pd.Series) -> float | None:
    """mean(volume last 5 bars) / mean(volume of the 20 bars before those).
    > RVOL_TREND_RISING rising, < RVOL_TREND_FALLING falling. None when fewer
    than 25 volume bars exist or the prior mean is not positive."""
    v = volumes.dropna()
    need = RVOL_TREND_RECENT_BARS + RVOL_TREND_PRIOR_BARS
    if len(v) < need:
        return None
    recent = float(v.iloc[-RVOL_TREND_RECENT_BARS:].mean())
    prior = float(v.iloc[-need:-RVOL_TREND_RECENT_BARS].mean())
    if prior <= 0:
        return None
    return round(recent / prior, 2)


def rvol_trend_label(ratio: float | None) -> str:
    if ratio is None:
        return "unknown"
    if ratio > RVOL_TREND_RISING:
        return "rising"
    if ratio < RVOL_TREND_FALLING:
        return "falling"
    return "flat"


def liquidity_tier(dollar_vol: float | None) -> str:
    """A > 500M, B 100-500M, C 20-100M, D < 20M (daily dollar volume; the
    row's tier uses the 20d MEDIAN so one spike day cannot lift it)."""
    if dollar_vol is None:
        return "D"
    for floor, tier in LIQUIDITY_TIERS:
        if dollar_vol > floor:
            return tier
    return "D"


def high_52w_stats(closes: pd.Series) -> dict:
    """52-week-high block from the main window: needs >= 252 closes, else
    every field is None. pct_from_52w_high = price / high - 1 (<= 0);
    days_since_52w_high = bars since the max close (0 = today is the high)."""
    c = closes.dropna()
    if len(c) < TRADING_DAYS_52W:
        return {"high_52w": None, "pct_from_52w_high": None,
                "days_since_52w_high": None, "new_52w_high": None}
    window = c.iloc[-TRADING_DAYS_52W:]
    high = float(window.max())
    price = float(window.iloc[-1])
    pos = int(window.values.argmax())
    return {"high_52w": round(high, 4),
            "pct_from_52w_high": round(price / high - 1.0, 4) if high > 0 else None,
            "days_since_52w_high": int(len(window) - 1 - pos),
            "new_52w_high": bool(price >= high)}


def compute_ticker_metrics(closes: pd.Series, volumes: pd.Series) -> dict | None:
    """Per-ticker momentum metrics from aligned daily close/volume series.
    Returns None when there is not enough history (< MIN_CLOSES closes or
    missing volume data) — the ticker is then counted as skipped.

    Definitions (all close-to-close, on the fetched auto-adjusted bars):
      ret_2d  = close[-1] / close[-3] - 1         (2 trading days back)
      ret_5d  = close[-1] / close[-6] - 1
      ret_20d / ret_60d = the same over 20 / 60 TRADING bars (None when the
                series is shorter than 61 bars — MIN_CLOSES only guarantees 51)
      rvol    = volume[-1] / mean(volume of the PRIOR 20 days)  (excludes today
                so a huge spike doesn't dilute its own denominator)
      rvol_class / rvol_trend (+ rvol_trend_label): see classify_rvol /
                rvol_trend_ratio
      avg_dollar_vol = mean(close*volume over the LAST 20 days, incl. today)
      median_dollar_vol_20d = median of the same 20 values; liquidity_tier
                is derived from the MEDIAN
      dist_maN = close[-1] / mean(last N closes) - 1;  ma20 / ma50 raw too
      trend   = classify_trend(price, ma20, ma50)
      new_Nd_high = close[-1] >= max(last N closes)
      high_52w / pct_from_52w_high / days_since_52w_high / new_52w_high:
                from the last 252 closes, None when fewer exist
    """
    c = closes.dropna()
    if len(c) < MIN_CLOSES:
        return None
    v = volumes.reindex(c.index).dropna()
    if len(v) < VOLUME_WINDOW + 1 or v.index[-1] != c.index[-1]:
        return None

    price = float(c.iloc[-1])
    ret_2d = price / float(c.iloc[-3]) - 1.0
    ret_5d = price / float(c.iloc[-6]) - 1.0

    prior_avg_vol = float(v.iloc[-(VOLUME_WINDOW + 1):-1].mean())
    if prior_avg_vol <= 0:
        return None
    rvol = float(v.iloc[-1]) / prior_avg_vol
    rv_trend = rvol_trend_ratio(v)

    dollar = (c.reindex(v.index) * v).iloc[-VOLUME_WINDOW:]
    avg_dollar_vol = float(dollar.mean())
    median_dollar_vol = float(dollar.median())

    ma20 = float(c.iloc[-20:].mean())
    ma50 = float(c.iloc[-50:].mean())
    out = {
        "price": round(price, 4),
        "ret_2d": round(ret_2d, 4),
        "ret_5d": round(ret_5d, 4),
        "ret_20d": bar_return(c, STANDARD_BAR_WINDOWS["ret_20d"]),
        "ret_60d": bar_return(c, STANDARD_BAR_WINDOWS["ret_60d"]),
        "rvol": round(rvol, 2),
        "rvol_class": classify_rvol(round(rvol, 2)),
        "rvol_trend": rv_trend,
        "rvol_trend_label": rvol_trend_label(rv_trend),
        "avg_dollar_vol": round(avg_dollar_vol, 0),
        "median_dollar_vol_20d": round(median_dollar_vol, 0),
        "liquidity_tier": liquidity_tier(median_dollar_vol),
        "ma20": round(ma20, 4),
        "ma50": round(ma50, 4),
        "dist_ma20": round(price / ma20 - 1.0, 4),
        "dist_ma50": round(price / ma50 - 1.0, 4),
        "trend": classify_trend(price, ma20, ma50),
        "new_20d_high": bool(price >= float(c.iloc[-20:].max())),
        "new_50d_high": bool(price >= float(c.iloc[-50:].max())),
    }
    out.update(high_52w_stats(c))
    return out


def trading_days_for_window(calendar_days: int) -> int:
    """Calendar window -> trading-bar count: round(window * 252/365).
    90 -> 62 bars, 270 -> 186 (the brief's '~63 / ~189' quarter-year rough
    cuts; the formula is authoritative and documented in the RUNBOOK)."""
    return max(1, round(calendar_days * 252 / 365))


def compute_window_returns(closes: pd.Series, windows: list[int]) -> dict:
    """Close/close return per doubler window: ret_<W>d = close[-1] /
    close[-(n+1)] - 1 over n = trading_days_for_window(W) TRADING days.
    None (not 0) when the ticker has too little history for a window."""
    c = closes.dropna()
    out: dict[str, float | None] = {}
    for w in windows:
        n = trading_days_for_window(w)
        key = f"ret_{w}d"
        if len(c) >= n + 1:
            out[key] = round(float(c.iloc[-1]) / float(c.iloc[-(n + 1)]) - 1.0, 4)
        else:
            out[key] = None
    return out


def benchmark_window_bars() -> dict[str, int]:
    """The standard return keys -> trading bars used for benchmarks / RS:
    5/20/60 bars plus the calendar-derived 90d and 270d windows."""
    return {**STANDARD_BAR_WINDOWS,
            "ret_90d": trading_days_for_window(90),
            "ret_270d": trading_days_for_window(270)}


def benchmark_returns(closes: pd.DataFrame, ticker: str,
                      windows: dict[str, int] | None = None) -> dict[str, float | None]:
    """{"ret_5d", "ret_20d", "ret_60d", "ret_90d", "ret_270d"} for one
    benchmark column of the closes frame (or the `windows` {key: bars} given).
    Every entry is None when the column is missing or too short."""
    windows = windows or benchmark_window_bars()
    cols = getattr(closes, "columns", [])
    if closes is None or ticker not in cols:
        return {k: None for k in windows}
    series = closes[ticker]
    return {k: bar_return(series, n) for k, n in windows.items()}


def passes_doubler_gates(m: dict, criteria: dict) -> bool:
    """Doubler pre-gates: only price + average dollar volume, and each only
    when configured > 0 (0 = gate off — the shipped default, so the default
    doubler hard gate is just the +100% window return + the market-cap
    check). A stock up 100% in a quarter usually FAILS the short-term
    momentum gates — that is the point of the separate list."""
    if criteria["min_price"] > 0 and m["price"] < criteria["min_price"]:
        return False
    if criteria["min_avg_dollar_vol"] > 0 and m["avg_dollar_vol"] < criteria["min_avg_dollar_vol"]:
        return False
    return True


def passes_established_gates(m: dict, criteria: dict) -> bool:
    """established_momentum bucket: ret_20d >= est_min_return_20d AND ret_60d
    >= est_min_return_60d AND price > MA20 AND MA20 > MA50 (i.e. the
    strong_uptrend trend state). Missing returns never pass. run_screen
    additionally applies passes_doubler_gates (the optional price / $vol
    knobs) and the market-cap check to this bucket."""
    r20, r60 = m.get("ret_20d"), m.get("ret_60d")
    if r20 is None or r60 is None:
        return False
    if r20 < criteria["est_min_return_20d"] or r60 < criteria["est_min_return_60d"]:
        return False
    ma20, ma50, price = m.get("ma20"), m.get("ma50"), m.get("price")
    if ma20 is None or ma50 is None or price is None:
        return False
    return bool(price > ma20 and ma20 > ma50)


def doubler_window_hits(window_rets: dict, windows: list[int], min_return: float) -> list[str]:
    """Labels of the windows whose return clears min_return, e.g. ["90d"].
    A None return (insufficient history) never counts as a hit."""
    hits = []
    for w in windows:
        r = window_rets.get(f"ret_{w}d")
        if r is not None and r >= min_return:
            hits.append(f"{w}d")
    return hits


def new_52w_high_from_history(closes: pd.Series) -> bool | None:
    """52-week-high flag from the main fetched window (needs >= 252 closes;
    None when the history is too short to know)."""
    c = closes.dropna()
    if len(c) < TRADING_DAYS_52W:
        return None
    return bool(float(c.iloc[-1]) >= float(c.iloc[-TRADING_DAYS_52W:].max()))


# ---------------------------------------------------------------------------
# Second-stage analytics on the finalist rows
#   1. pace test        — long-term winners only: is the 90d/270d move
#                         accelerating, steady, decelerating or pulling back?
#   2. sector / theme   — for the crowding check and theme-relative strength
#   3. sector concentration — how much of a list is one correlated theme
#   4. persistence      — how many distinct tickers top the recent hit-days
# All pure functions; the sector lookup is injectable like the other fetchers.
# (The pre-v3 "quality" heuristic is gone — score_row below replaces it.)
# ---------------------------------------------------------------------------

PACE_ACCEL_RATIO = 1.15    # recent pace / prior pace above this = accelerating
PACE_DECEL_RATIO = 0.85    # below this = decelerating; between = steady
PACE_LABELS = ("pulling_back", "decelerating", "steady", "accelerating")

CONCENTRATION_WARN_SHARE = 0.60   # >= this share of the list in one theme = crowded
CROWDED_FLAG_MIN_ROWS = 3         # a bucket smaller than this never flags crowded_theme

# yfinance sector/industry -> broad THEME used for the crowding check. Matched
# case-insensitively on substrings, first hit wins; fall back to the sector.
THEME_RULES = (
    ("semiconductor", "AI hardware supply chain"),
    ("computer hardware", "AI hardware supply chain"),
    ("electronic components", "AI hardware supply chain"),
    ("data storage", "AI hardware supply chain"),
    ("storage", "AI hardware supply chain"),
    ("servers", "AI hardware supply chain"),
    ("ai cloud", "AI hardware supply chain"),
    ("information technology services", "AI hardware supply chain"),
    ("software - infrastructure", "Software & security"),
    ("cybersecurity", "Software & security"),
    ("security", "Software & security"),
    ("software", "Software & security"),
    ("biotech", "Biotech & pharma"),
    ("drug manufacturers", "Biotech & pharma"),
    ("pharma", "Biotech & pharma"),
    ("bank", "Financials"),
    ("capital markets", "Financials"),
    ("insurance", "Financials"),
    ("oil", "Energy"),
    ("gas", "Energy"),
    ("uranium", "Energy"),
    ("utilities", "Utilities & power"),
    ("gold", "Metals & mining"),
    ("silver", "Metals & mining"),
    ("copper", "Metals & mining"),
    ("aerospace", "Aerospace & defense"),
    ("defense", "Aerospace & defense"),
)
SECTORS_FILE_NAME = "sectors.json"          # static overrides next to tickers.json
SECTOR_CACHE_NAME = "sectors-cache.json"    # live lookups remembered in the output dir


def pace_analysis(ret_short: float | None, ret_long: float | None,
                  bars_short: int, bars_long: int) -> dict | None:
    """Compare the per-bar pace of the recent (short) window with the implied
    pace of the earlier part of the long window.

    prior_factor = (1+ret_long) / (1+ret_short)  is the return over the
    bars_long - bars_short bars that precede the short window. Both are
    converted to a geometric per-bar pace; ratio = recent / prior.

      ret_short <= 0 < ret_long      -> "pulling_back" (already round-tripping)
      ratio > PACE_ACCEL_RATIO       -> "accelerating" (late-stage, blow-off prone)
      ratio < PACE_DECEL_RATIO       -> "decelerating" (cooling)
      otherwise / prior pace <= 0    -> "steady"
    Returns None when either return is unknown or the windows are unusable."""
    if ret_short is None or ret_long is None:
        return None
    bars_prior = bars_long - bars_short
    if bars_short <= 0 or bars_prior <= 0:
        return None
    f_short = 1.0 + float(ret_short)
    f_long = 1.0 + float(ret_long)
    if f_short <= 0 or f_long <= 0:
        return None
    prior_factor = f_long / f_short
    prior_return = prior_factor - 1.0
    pace_recent = f_short ** (1.0 / bars_short) - 1.0
    pace_prior = max(prior_factor, 1e-9) ** (1.0 / bars_prior) - 1.0
    out = {
        "label": "steady",
        "ratio": None,
        "prior_return": round(prior_return, 4),
        "pace_recent": round(pace_recent, 6),
        "pace_prior": round(pace_prior, 6),
        "bars_short": bars_short,
        "bars_prior": bars_prior,
    }
    if ret_short <= 0 and ret_long > 0:
        out["label"] = "pulling_back"
        return out
    if pace_prior <= 0:
        return out                      # no usable prior pace to compare against
    ratio = pace_recent / pace_prior
    out["ratio"] = round(ratio, 3)
    if ratio > PACE_ACCEL_RATIO:
        out["label"] = "accelerating"
    elif ratio < PACE_DECEL_RATIO:
        out["label"] = "decelerating"
    return out


def classify_theme(sector: str | None, industry: str | None) -> str:
    """Broad theme for the crowding check from yfinance sector/industry text."""
    hay = f"{industry or ''} | {sector or ''}".lower()
    for needle, theme in THEME_RULES:
        if needle in hay:
            return theme
    return (sector or industry or "Unknown").strip() or "Unknown"


def load_sectors(config_dir: Path | None, output_dir: Path | None = None) -> dict[str, dict]:
    """Ticker -> {"sector", "industry", "theme"?} from the static
    config/sectors.json (hand-maintained overrides) merged over the live
    lookup cache in the output dir. Static entries win. Missing files = {}."""
    merged: dict[str, dict] = {}
    for path in ((Path(output_dir) / SECTOR_CACHE_NAME) if output_dir else None,
                 (Path(config_dir) / SECTORS_FILE_NAME) if config_dir else None):
        if path is None or not path.is_file():
            continue
        try:
            raw = json.loads(path.read_text())
        except (OSError, ValueError):
            logger.warning("sector file %s unreadable; ignored", path)
            continue
        table = raw.get("tickers") if isinstance(raw, dict) and "tickers" in raw else raw
        if not isinstance(table, dict):
            continue
        for t, info in table.items():
            if isinstance(info, str):
                info = {"sector": info}
            if isinstance(info, dict):
                merged[str(t).upper()] = {**merged.get(str(t).upper(), {}), **info}
    return merged


def save_sector_cache(output_dir: Path, sectors: dict[str, dict]) -> Path | None:
    """Remember live sector lookups so each ticker is fetched at most once."""
    try:
        path = Path(output_dir) / SECTOR_CACHE_NAME
        existing = load_sectors(None, output_dir)
        existing.update({k: v for k, v in sectors.items() if v})
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(json.dumps({"tickers": existing}, indent=2, sort_keys=True))
        os.replace(tmp, path)
        return path
    except OSError:
        logger.warning("sector cache not written", exc_info=True)
        return None


def _default_fetch_sector(ticker: str) -> dict | None:
    """Sector/industry via yfinance .info (finalists only, like market cap)."""
    try:
        yf = _import_yfinance()
        info = yf.Ticker(ticker).info or {}
    except Exception:  # noqa: BLE001
        return None
    sector, industry = info.get("sector"), info.get("industry")
    if not sector and not industry:
        return None
    return {"sector": sector, "industry": industry}


def theme_of(sector_info: dict | None) -> str:
    """Theme for a sector-table entry: an explicit "theme" wins, else the
    THEME_RULES classification; "Unknown" without any entry."""
    info = sector_info or {}
    if info.get("theme"):
        return str(info["theme"])
    if info:
        return classify_theme(info.get("sector"), info.get("industry"))
    return "Unknown"


def enrich_row_analytics(row: dict, windows: list[int], sector_info: dict | None,
                         *, with_pace: bool = True) -> None:
    """Attach sector / industry / theme (and, for long-term winners, the
    90d/270d pace test) to one finalist row in place. Uses the shortest and
    longest configured doubler windows for the pace."""
    if with_pace:
        ws = sorted(windows)
        pace = None
        if len(ws) >= 2:
            pace = pace_analysis(row.get(f"ret_{ws[0]}d"), row.get(f"ret_{ws[-1]}d"),
                                 trading_days_for_window(ws[0]), trading_days_for_window(ws[-1]))
        row["pace"] = pace
    info = sector_info or {}
    row["sector"] = info.get("sector")
    row["industry"] = info.get("industry")
    row["theme"] = theme_of(info)


def sector_concentration(rows: list[dict], warn_share: float = CONCENTRATION_WARN_SHARE) -> dict:
    """Group rows by theme -> crowding summary. Share is by ticker COUNT
    (market-cap-weighted share is reported alongside)."""
    groups: dict[str, dict] = {}
    total_cap = 0.0
    for r in rows:
        theme = r.get("theme") or "Unknown"
        g = groups.setdefault(theme, {"theme": theme, "count": 0, "tickers": [], "market_cap": 0.0})
        g["count"] += 1
        g["tickers"].append(r.get("ticker"))
        cap = r.get("market_cap")
        if isinstance(cap, (int, float)):
            g["market_cap"] += float(cap)
            total_cap += float(cap)
    n = len(rows)
    out_groups = []
    for g in sorted(groups.values(), key=lambda g: (-g["count"], g["theme"])):
        g["share"] = round(g["count"] / n, 4) if n else 0.0
        g["cap_share"] = round(g["market_cap"] / total_cap, 4) if total_cap else None
        g["market_cap"] = round(g["market_cap"], 0)
        out_groups.append(g)
    top = out_groups[0] if out_groups else None
    known = sum(g["count"] for g in out_groups if g["theme"] != "Unknown")
    return {
        "n": n,
        "groups": out_groups,
        "top_theme": top["theme"] if top else None,
        "top_share": top["share"] if top else 0.0,
        "crowded": bool(top and top["theme"] != "Unknown" and top["share"] >= warn_share),
        "warn_share": warn_share,
        "unknown": n - known,
    }


def top_ticker_persistence(hits: list[dict], n_days: int = 6) -> dict:
    """Across the most recent `n_days` hit-days (rows with any candidate or
    doubler), count how often each top ticker appears. Few distinct tickers
    = one recurring theme surfacing repeatedly, not independent signals."""
    recent = [h for h in (hits or [])
              if isinstance(h, dict) and h.get("date")
              and ((h.get("n_candidates") or 0) > 0 or (h.get("n_doublers") or 0) > 0)]
    recent = sorted(recent, key=lambda h: h["date"], reverse=True)[:n_days]
    counts: dict[str, int] = {}
    for h in recent:
        top = h.get("top") or {}
        td = h.get("top_doubler") or {}
        t = top.get("ticker") or td.get("ticker")
        if t:
            counts[t] = counts.get(t, 0) + 1
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
    return {
        "days": len(recent),
        "distinct": len(counts),
        "counts": [{"ticker": t, "days": c} for t, c in ordered],
        "concentrated": bool(recent) and len(counts) <= max(1, len(recent) // 2),
    }


# ---------------------------------------------------------------------------
# v3 research funnel — Discovery -> Validation -> Decision queue
#   per row : relative strength, acceleration, setup, risk flags /
#             confirmations, absolute 0-100 score, queue tier, freshness,
#             earnings catalyst, data-quality check
#   per list: benchmarks, regime, research queue, theme breadth, activity
# Every function here is pure; run_screen injects the (cached) lookups.
# ---------------------------------------------------------------------------

SCORE_MAX = {"short_momentum": 25, "medium_momentum": 20, "trend": 15, "volume": 10,
             "high_proximity": 10, "acceleration": 10, "relative_strength": 10}
SCORE_SCALING = {
    "short_momentum": "clip(ret_5d / 0.30, 0, 1) * 25 (ret_5d <= 0 -> 0)",
    "medium_momentum": "clip(ret_20d / 0.30, 0, 1) * 12 + clip(ret_60d / 0.50, 0, 1) * 8 (null -> 0)",
    "trend": "strong_uptrend 15 / uptrend 11 / mixed 5 / downtrend 0 / unknown 0",
    "volume": "clip((rvol - 0.5) / 1.5, 0, 1) * 10",
    "high_proximity": "clip(1 + pct_from_52w_high / 0.25, 0, 1) * 10 (0 at -25% or worse; null -> 5)",
    "acceleration": "accelerating 10 / steady 6 / decelerating 3 / pulling_back 0 / unknown 5",
    "relative_strength": "clip((rs_20d + 0.05) / 0.25, 0, 1) * 10 (null -> 5)",
}
TREND_POINTS = {"strong_uptrend": 15, "uptrend": 11, "mixed": 5, "downtrend": 0, "unknown": 0}
ACCELERATION_POINTS = {"accelerating": 10, "steady": 6, "decelerating": 3,
                       "pulling_back": 0, "unknown": 5}
ACCELERATION_LABELS = ("accelerating", "steady", "decelerating", "pulling_back", "unknown")

SETUP_ORDER = ("EXHAUSTION", "BREAKOUT", "ACCELERATION", "PULLBACK", "TREND", "REVERSAL_WATCH")
SETUP_RULES = {
    "EXHAUSTION": "(ret_5d >= 0.25 or dist_ma20 >= 0.25) and rvol >= 2.5",
    "BREAKOUT": "pct_from_52w_high >= -0.03 and ret_5d >= 0.05 and rvol >= 1.5",
    "ACCELERATION": "acceleration.label == accelerating and ret_20d > 0 and rvol_trend > 1.2 (rising)",
    "PULLBACK": "ret_60d > 0 and ret_5d < 0 and price above MA50",
    "TREND": "trend.state in (strong_uptrend, uptrend) and ret_20d > 0 and ret_60d > 0",
    "REVERSAL_WATCH": "ret_60d < 0 and ret_5d > 0 and rvol >= 1.2",
    "NONE": "none of the above (evaluated in SETUP_ORDER, first match wins)",
}
RISK_THRESHOLDS = {
    "extended_ma20": 0.25,          # dist_ma20 above this
    "rvol_falling": RVOL_TREND_FALLING,
    "big_5d_move": 0.40,            # ret_5d above this
    "far_from_high": -0.20,         # pct_from_52w_high below this
    "momentum_divergence_rvol": 1.5,  # ret_5d < 0 while rvol >= this
    "confirmation_rvol": 1.5,       # rvol_confirmed
}
QUEUE_RULES = {
    "D": "trend downtrend OR liquidity tier D OR setup EXHAUSTION OR data_quality flag",
    "A": "score >= 75 AND rs_20d > 0 AND trend in (strong_uptrend, uptrend) AND no risk flags other than crowded_theme",
    "C": "setup PULLBACK OR (ret_270d > 1.0 and ret_20d <= 0) OR score < 55",
    "B": "otherwise, score >= 55",
}
QUEUE_SCORE_A, QUEUE_SCORE_B = 75, 55
FRESHNESS_STAGES = {"fresh": "1 day in the bucket", "developing": "2-5 consecutive snapshots",
                    "mature": "> 5 consecutive snapshots",
                    "stale": "dropped out since the previous snapshot (activity.dropped_out)"}
REGIME_RISK_ON_BREADTH, REGIME_RISK_OFF_BREADTH = 0.6, 0.4
BREADTH_BROAD, BREADTH_NARROW = 0.6, 0.4
SECTOR_RS_MIN_PEERS = 3
PERSISTENT_MIN_DAYS = 2


def _num(x) -> float | None:
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _gt(a, b) -> bool:
    return a is not None and b is not None and a > b


def _ge(a, b) -> bool:
    return a is not None and b is not None and a >= b


def _lt(a, b) -> bool:
    return a is not None and b is not None and a < b


def _diff(a, b) -> float | None:
    return round(float(a) - float(b), 4) if _num(a) is not None and _num(b) is not None else None


def _clip01(x: float) -> float:
    return max(0.0, min(1.0, x))


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if _num(v) is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _median(vals: list) -> float | None:
    vals = sorted(v for v in vals if _num(v) is not None)
    if not vals:
        return None
    n = len(vals)
    return round(vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0, 4)


def _share(hits: int, n: int) -> float | None:
    return round(hits / n, 4) if n else None


def pace_per_bar(ret: float | None, bars: int) -> float | None:
    """(1 + ret) ** (1 / bars) - 1 — geometric per-bar pace; None when the
    return is unknown or <= -100%."""
    r = _num(ret)
    if r is None or bars <= 0 or 1.0 + r <= 0:
        return None
    return (1.0 + r) ** (1.0 / bars) - 1.0


def classify_acceleration(ret_5d: float | None, ret_20d: float | None,
                          ret_60d: float | None) -> dict:
    """Short-horizon pace comparison on the 5/20/60-bar returns.

      pulling_back  : ret_5d < 0 and ret_20d > 0
                      (or ret_20d < 0 and ret_60d > 0 when ret_5d is missing)
      accelerating  : pace_5d > pace_20d > pace_60d >= 0
      decelerating  : pace_5d < pace_20d and ret_20d > 0
      steady        : otherwise
      unknown       : ret_5d or ret_20d missing (and no pulling_back fallback)
    pace_N = (1 + ret_N) ** (1/N) - 1 (per bar), reported alongside."""
    p5, p20, p60 = pace_per_bar(ret_5d, 5), pace_per_bar(ret_20d, 20), pace_per_bar(ret_60d, 60)
    out = {"label": "unknown",
           "pace_5d": round(p5, 6) if p5 is not None else None,
           "pace_20d": round(p20, 6) if p20 is not None else None,
           "pace_60d": round(p60, 6) if p60 is not None else None}
    r5, r20, r60 = _num(ret_5d), _num(ret_20d), _num(ret_60d)
    if r5 is None:
        if r20 is not None and r60 is not None and r20 < 0 < r60:
            out["label"] = "pulling_back"
        return out
    if r20 is None or p5 is None or p20 is None:
        return out
    if r5 < 0 < r20:
        out["label"] = "pulling_back"
    elif p60 is not None and p5 > p20 > p60 >= 0:
        out["label"] = "accelerating"
    elif p5 < p20 and r20 > 0:
        out["label"] = "decelerating"
    else:
        out["label"] = "steady"
    return out


def classify_setup(row: dict) -> str:
    """First matching setup in SETUP_ORDER (see SETUP_RULES), else "NONE".
    Unknown inputs never satisfy a comparison."""
    r5, r20, r60 = _num(row.get("ret_5d")), _num(row.get("ret_20d")), _num(row.get("ret_60d"))
    rvol, d20 = _num(row.get("rvol")), _num(row.get("dist_ma20"))
    pct = _num(row.get("pct_from_52w_high"))
    rv_trend = _num(row.get("rvol_trend"))
    trend = row.get("trend") or {}
    accel = (row.get("acceleration") or {}).get("label")
    if (_ge(r5, 0.25) or _ge(d20, 0.25)) and _ge(rvol, 2.5):
        return "EXHAUSTION"
    if _ge(pct, -0.03) and _ge(r5, 0.05) and _ge(rvol, 1.5):
        return "BREAKOUT"
    if accel == "accelerating" and _gt(r20, 0) and _gt(rv_trend, RVOL_TREND_RISING):
        return "ACCELERATION"
    if _gt(r60, 0) and _lt(r5, 0) and trend.get("above_ma50"):
        return "PULLBACK"
    if trend.get("state") in ("strong_uptrend", "uptrend") and _gt(r20, 0) and _gt(r60, 0):
        return "TREND"
    if _lt(r60, 0) and _gt(r5, 0) and _ge(rvol, 1.2):
        return "REVERSAL_WATCH"
    return "NONE"


def risk_flags_for(row: dict, theme_share: float | None = None,
                   warn_share: float = CONCENTRATION_WARN_SHARE) -> list[str]:
    """Short risk tags (see RISK_THRESHOLDS); `theme_share` is the row's
    theme share within its bucket (None = not crowd-checked)."""
    flags: list[str] = []
    r5, d20 = _num(row.get("ret_5d")), _num(row.get("dist_ma20"))
    rv_trend, rvol = _num(row.get("rvol_trend")), _num(row.get("rvol"))
    pct = _num(row.get("pct_from_52w_high"))
    rs20 = _num((row.get("rs") or {}).get("20d"))
    if _gt(d20, RISK_THRESHOLDS["extended_ma20"]):
        flags.append("extended_ma20")
    if _lt(rv_trend, RISK_THRESHOLDS["rvol_falling"]):
        flags.append("rvol_falling")
    if _gt(r5, RISK_THRESHOLDS["big_5d_move"]):
        flags.append("big_5d_move")
    if row.get("liquidity_tier") == "D":
        flags.append("low_liquidity")
    theme = row.get("theme") or "Unknown"
    if theme != "Unknown" and _ge(theme_share, warn_share):
        flags.append("crowded_theme")
    if _lt(pct, RISK_THRESHOLDS["far_from_high"]):
        flags.append("far_from_high")
    if _lt(rs20, 0):
        flags.append("weak_rs")
    if (row.get("data_quality") or {}).get("status") == "warning":
        flags.append("data_quality")
    if _lt(r5, 0) and _ge(rvol, RISK_THRESHOLDS["momentum_divergence_rvol"]):
        flags.append("momentum_divergence")
    return flags


def confirmations_for(row: dict) -> list[str]:
    """Positive confirmations: new_52w_high, rvol_confirmed (rvol >= 1.5),
    ma20_gt_ma50, rs_positive (rs 20d > 0), rs_improving (rs 5d > 0 AND rs
    20d > 0), earnings_catalyst (catalyst.kind == "earnings")."""
    out: list[str] = []
    rs = row.get("rs") or {}
    rs5, rs20 = _num(rs.get("5d")), _num(rs.get("20d"))
    if row.get("new_52w_high") is True:
        out.append("new_52w_high")
    if _ge(_num(row.get("rvol")), RISK_THRESHOLDS["confirmation_rvol"]):
        out.append("rvol_confirmed")
    if (row.get("trend") or {}).get("ma20_gt_ma50"):
        out.append("ma20_gt_ma50")
    if _gt(rs20, 0):
        out.append("rs_positive")
    if _gt(rs5, 0) and _gt(rs20, 0):
        out.append("rs_improving")
    if (row.get("catalyst") or {}).get("kind") == "earnings":
        out.append("earnings_catalyst")
    return out


def score_row(row: dict) -> dict:
    """ABSOLUTE 0-100 score (same scale every day, so rows compare across
    snapshots). Components and scaling per SCORE_SCALING / SCORE_MAX:
      short_momentum    25  clip(ret_5d / 0.30)
      medium_momentum   20  clip(ret_20d / 0.30) * 12 + clip(ret_60d / 0.50) * 8
      trend             15  strong 15 / up 11 / mixed 5 / down 0
      volume            10  clip((rvol - 0.5) / 1.5)
      high_proximity    10  clip(1 + pct_from_52w_high / 0.25)   (null -> 5)
      acceleration      10  accelerating 10 / steady 6 / decel 3 / pulling_back 0 (unknown 5)
      relative_strength 10  clip((rs_20d + 0.05) / 0.25)        (null -> 5)
    Returns {"total": int, "components": {name: {"pts", "max"}}}."""
    r5, r20, r60 = _num(row.get("ret_5d")), _num(row.get("ret_20d")), _num(row.get("ret_60d"))
    rvol, pct = _num(row.get("rvol")), _num(row.get("pct_from_52w_high"))
    rs20 = _num((row.get("rs") or {}).get("20d"))
    trend_state = (row.get("trend") or {}).get("state", "unknown")
    accel = (row.get("acceleration") or {}).get("label", "unknown")

    pts = {
        "short_momentum": _clip01(r5 / 0.30) * 25 if r5 is not None else 0.0,
        "medium_momentum": ((_clip01(r20 / 0.30) * 12 if r20 is not None else 0.0)
                            + (_clip01(r60 / 0.50) * 8 if r60 is not None else 0.0)),
        "trend": float(TREND_POINTS.get(trend_state, 0)),
        "volume": _clip01((rvol - 0.5) / 1.5) * 10 if rvol is not None else 0.0,
        "high_proximity": _clip01(1.0 + pct / 0.25) * 10 if pct is not None else 5.0,
        "acceleration": float(ACCELERATION_POINTS.get(accel, ACCELERATION_POINTS["unknown"])),
        "relative_strength": _clip01((rs20 + 0.05) / 0.25) * 10 if rs20 is not None else 5.0,
    }
    total = int(round(max(0.0, min(100.0, sum(pts.values())))))
    return {"total": total,
            "components": {k: {"pts": round(v, 1), "max": SCORE_MAX[k]} for k, v in pts.items()}}


def queue_tier(row: dict) -> dict:
    """Research-queue tier (QUEUE_RULES), evaluated D -> A -> C -> B -> C."""
    flags = row.get("risk_flags") or []
    score = _num((row.get("score") or {}).get("total")) or 0.0
    trend_state = (row.get("trend") or {}).get("state", "unknown")
    setup = row.get("setup")
    rs20 = _num((row.get("rs") or {}).get("20d"))
    r20, r270 = _num(row.get("ret_20d")), _num(row.get("ret_270d"))
    if trend_state == "downtrend":
        return {"tier": "D", "reason": "downtrend (below MA20 and MA50)"}
    if row.get("liquidity_tier") == "D":
        return {"tier": "D", "reason": "liquidity tier D (< $20M/day median)"}
    if setup == "EXHAUSTION":
        return {"tier": "D", "reason": "exhaustion setup (parabolic + extreme volume)"}
    if "data_quality" in flags:
        return {"tier": "D", "reason": "data-quality warning — verify the price series first"}
    other_flags = [f for f in flags if f != "crowded_theme"]
    if (score >= QUEUE_SCORE_A and _gt(rs20, 0)
            and trend_state in ("strong_uptrend", "uptrend") and not other_flags):
        return {"tier": "A", "reason": f"score {int(score)} >= {QUEUE_SCORE_A}, positive RS, "
                                       f"{trend_state}, no risk flags"}
    if setup == "PULLBACK":
        return {"tier": "C", "reason": "pullback in progress — wait for confirmation"}
    if _gt(r270, 1.0) and _le_zero(r20):
        return {"tier": "C", "reason": "extended long-term winner stalling (270d > +100%, 20d <= 0)"}
    if score >= QUEUE_SCORE_B:
        why = ", ".join(other_flags) if other_flags else "did not meet every tier-A condition"
        return {"tier": "B", "reason": f"score {int(score)} >= {QUEUE_SCORE_B}; {why}"}
    return {"tier": "C", "reason": f"score {int(score)} < {QUEUE_SCORE_B}"}


def _le_zero(x) -> bool:
    return x is not None and x <= 0


def relative_strength(row: dict, benchmarks: dict | None) -> tuple[dict, float | None]:
    """(rs vs SPY over 5d/20d/90d, rs vs QQQ over 20d) — excess returns,
    None wherever the benchmark figure is missing."""
    bench = benchmarks or {}
    spy = bench.get(BENCHMARK_PRIMARY) or {}
    qqq = bench.get("QQQ") or {}
    rs = {"5d": _diff(row.get("ret_5d"), spy.get("ret_5d")),
          "20d": _diff(row.get("ret_20d"), spy.get("ret_20d")),
          "90d": _diff(row.get("ret_90d"), spy.get("ret_90d"))}
    return rs, _diff(row.get("ret_20d"), qqq.get("ret_20d"))


def sector_relative_strength(ticker: str, theme_map: dict[str, str], ret20_map: dict[str, float | None],
                             min_peers: int = SECTOR_RS_MIN_PEERS) -> float | None:
    """ret_20d minus the mean ret_20d of the OTHER universe tickers sharing
    the ticker's theme; None when the theme is Unknown or < min_peers peers
    have a 20d return."""
    theme = theme_map.get(ticker, "Unknown")
    own = _num(ret20_map.get(ticker))
    if own is None or theme == "Unknown":
        return None
    peers = [_num(ret20_map.get(p)) for p, th in theme_map.items()
             if p != ticker and th == theme and _num(ret20_map.get(p)) is not None]
    if len(peers) < min_peers:
        return None
    return round(own - sum(peers) / len(peers), 4)


def empty_catalyst() -> dict:
    return {"kind": "unknown", "earnings_date": None, "days_since_earnings": None,
            "days_to_earnings": None, "dates_known": False}


def catalyst_for(dates, snapshot_date: date, window_days: int = CATALYST_WINDOW_DAYS) -> dict:
    """Earnings catalyst relative to `snapshot_date` from a list of known
    earnings dates (date objects or ISO strings; None = nothing known).
    kind "earnings" when the NEAREST known date lies within
    -window_days..+window_days, else "unknown". earnings_date = that nearest
    known date (also outside the window, for context); days_since / days_to
    refer to the latest past / next future date."""
    out = empty_catalyst()
    if dates is None:
        return out
    parsed: list[date] = []
    for d in dates:
        if isinstance(d, datetime):
            parsed.append(d.date())
        elif isinstance(d, date):
            parsed.append(d)
        else:
            try:
                parsed.append(date.fromisoformat(str(d)[:10]))
            except ValueError:
                continue
    parsed = sorted(set(parsed))
    out["dates_known"] = True
    if not parsed:
        return out
    past = [d for d in parsed if d <= snapshot_date]
    future = [d for d in parsed if d > snapshot_date]
    if past:
        out["days_since_earnings"] = (snapshot_date - past[-1]).days
    if future:
        out["days_to_earnings"] = (future[0] - snapshot_date).days
    nearest = min(parsed, key=lambda d: (abs((d - snapshot_date).days), d))
    out["earnings_date"] = nearest.isoformat()
    if abs((nearest - snapshot_date).days) <= window_days:
        out["kind"] = "earnings"
    return out


def needs_data_quality_check(window_rets: dict, trigger: float = DATA_QUALITY_TRIGGER_RETURN) -> bool:
    """Only moves beyond +-trigger (default 200%) over 90d or 270d get the
    (split / jump-day) data-quality check."""
    for key in ("ret_90d", "ret_270d"):
        r = _num(window_rets.get(key))
        if r is not None and abs(r) > trigger:
            return True
    return False


def data_quality_check(closes: pd.Series, splits: list[dict] | None, *, bars: int,
                       asof: date | None = None, jump_move: float = DATA_QUALITY_JUMP_MOVE) -> dict:
    """Sanity check on the last `bars` closes of a huge mover: the largest
    absolute daily move, every "jump day" (|move| > jump_move) and any split
    dated inside the window. status "warning" when a jump day or an in-window
    split exists — auto_adjust should already have handled splits, so a
    warning means VERIFY, not "wrong". `splits` None = unknown (noted)."""
    c = closes.dropna()
    c = c.iloc[-(bars + 1):] if len(c) > bars + 1 else c
    moves = c.pct_change().dropna()
    idx = moves.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    jump_days = [pd.Timestamp(ts).date().isoformat() for ts, mv in zip(idx, moves.values)
                 if abs(float(mv)) > jump_move]
    max_move = round(float(moves.abs().max()), 4) if not moves.empty else None
    window_start = pd.Timestamp(c.index[0]).date() if len(c) else None
    in_window: list[dict] = []
    notes: list[str] = []
    if splits is None:
        notes.append("splits unknown (no cached split data in replay mode)")
    else:
        for s in splits:
            try:
                sd = date.fromisoformat(str(s.get("date"))[:10])
            except (TypeError, ValueError):
                continue
            if window_start is not None and sd >= window_start and (asof is None or sd <= asof):
                in_window.append({"date": sd.isoformat(), "ratio": _num(s.get("ratio"))})
    if jump_days:
        notes.append(f"{len(jump_days)} day(s) with |move| > {int(jump_move * 100)}% — check for "
                     "unadjusted splits, reverse splits or bad prints")
    if in_window:
        notes.append("split(s) inside the window — auto_adjust should have handled them; verify")
    status = "warning" if (jump_days or in_window) else "ok"
    if status == "ok":
        notes.append("no jump days" + ("" if splits is None else " and no splits in the window"))
    return {"checked": True, "status": status, "max_daily_move": max_move,
            "jump_days": jump_days, "splits": in_window, "notes": notes}


def enrich_row_v3(row: dict, *, closes: pd.Series | None = None, volumes: pd.Series | None = None,
                  benchmarks: dict | None = None, sector_rs_20d: float | None = None,
                  theme_share: float | None = None, warn_share: float = CONCENTRATION_WARN_SHARE,
                  catalyst: dict | None = None, data_quality: dict | None = None) -> dict:
    """Attach the funnel fields to one finalist row IN PLACE (and return it):
    rs / rs_qqq_20d / sector_rs_20d, trend (if missing), acceleration,
    catalyst, data_quality, setup, risk_flags, confirmations, score, queue.
    When `closes`/`volumes` are given and the base metrics are missing, they
    are computed first (compute_ticker_metrics + standard window returns)."""
    if closes is not None and "trend" not in row:
        base = compute_ticker_metrics(closes, volumes if volumes is not None else pd.Series(dtype=float))
        for k, v in (base or {}).items():
            row.setdefault(k, v)
        for k, n in benchmark_window_bars().items():
            row.setdefault(k, bar_return(closes, n))
    rs, rs_qqq = relative_strength(row, benchmarks)
    row["rs"] = rs
    row["rs_qqq_20d"] = rs_qqq
    row["sector_rs_20d"] = sector_rs_20d
    if "trend" not in row:
        row["trend"] = classify_trend(_num(row.get("price")), _num(row.get("ma20")), _num(row.get("ma50")))
    row["acceleration"] = classify_acceleration(row.get("ret_5d"), row.get("ret_20d"), row.get("ret_60d"))
    row["catalyst"] = catalyst if catalyst is not None else empty_catalyst()
    row["data_quality"] = data_quality if data_quality is not None else {"checked": False}
    row["setup"] = classify_setup(row)
    row["risk_flags"] = risk_flags_for(row, theme_share, warn_share)
    row["confirmations"] = confirmations_for(row)
    row["score"] = score_row(row)
    row["queue"] = queue_tier(row)
    return row


def compute_regime(metrics: dict[str, dict], benchmarks: dict | None) -> dict:
    """Market regime over ALL computed universe tickers + the SPY/QQQ 20d
    returns. risk_on: pct_above_ma50 >= 0.6 and SPY 20d > 0; risk_off:
    pct_above_ma50 <= 0.4 and SPY 20d < 0; else neutral (also when the data
    is missing)."""
    rows = list(metrics.values())
    n = len(rows)
    bench = benchmarks or {}
    spy20 = _num((bench.get(BENCHMARK_PRIMARY) or {}).get("ret_20d"))
    qqq20 = _num((bench.get("QQQ") or {}).get("ret_20d"))
    with_r20 = [m for m in rows if _num(m.get("ret_20d")) is not None]
    pct_above_ma20 = _share(sum(1 for m in rows if _gt(_num(m.get("dist_ma20")), 0)), n)
    pct_above_ma50 = _share(sum(1 for m in rows if _gt(_num(m.get("dist_ma50")), 0)), n)
    pct_positive_20d = _share(sum(1 for m in with_r20 if m["ret_20d"] > 0), len(with_r20))
    label = "neutral"
    if pct_above_ma50 is not None and spy20 is not None:
        if pct_above_ma50 >= REGIME_RISK_ON_BREADTH and spy20 > 0:
            label = "risk_on"
        elif pct_above_ma50 <= REGIME_RISK_OFF_BREADTH and spy20 < 0:
            label = "risk_off"
    return {"spy_20d": spy20, "qqq_20d": qqq20,
            "pct_above_ma20": pct_above_ma20, "pct_above_ma50": pct_above_ma50,
            "pct_positive_20d": pct_positive_20d,
            "new_20d_highs": sum(1 for m in rows if m.get("new_20d_high") is True),
            "n_computed": n, "label": label}


def build_research_queue(buckets: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Union of every bucket, each ticker ONCE (its best-scoring bucket),
    grouped by queue tier and sorted by score desc."""
    best: dict[str, dict] = {}
    for bucket, rows in buckets.items():
        for r in rows:
            t = r.get("ticker")
            s = _num((r.get("score") or {}).get("total")) or 0.0
            if t not in best or s > best[t]["score"]:
                best[t] = {"ticker": t, "bucket": bucket, "score": int(s),
                           "setup": r.get("setup"), "reason": (r.get("queue") or {}).get("reason"),
                           "_tier": (r.get("queue") or {}).get("tier") or "C"}
    queue: dict[str, list[dict]] = {"A": [], "B": [], "C": [], "D": []}
    for e in sorted(best.values(), key=lambda e: (-e["score"], e["ticker"])):
        tier = e.pop("_tier")
        queue.setdefault(tier, []).append(e)
    return queue


def theme_breadth(rows: list[dict]) -> list[dict]:
    """Per-theme breadth over a list of rows (the long-term winners):
    broadening when pct_positive_20d >= 0.6 and pct_above_ma20 >= 0.6,
    narrowing when pct_positive_20d <= 0.4, else mixed."""
    groups: dict[str, list[dict]] = {}
    for r in rows:
        groups.setdefault(r.get("theme") or "Unknown", []).append(r)
    n_all = len(rows)
    total_cap = sum(_num(r.get("market_cap")) or 0.0 for r in rows)
    out = []
    for theme, rs in sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        n = len(rs)
        r20 = [r for r in rs if _num(r.get("ret_20d")) is not None]
        r5 = [r for r in rs if _num(r.get("ret_5d")) is not None]
        h52 = [r for r in rs if r.get("new_52w_high") is not None]
        p20 = _share(sum(1 for r in r20 if r["ret_20d"] > 0), len(r20))
        pa20 = _share(sum(1 for r in rs if _gt(_num(r.get("dist_ma20")), 0)), n)
        if p20 is not None and pa20 is not None and p20 >= BREADTH_BROAD and pa20 >= BREADTH_BROAD:
            label = "broadening"
        elif p20 is not None and p20 <= BREADTH_NARROW:
            label = "narrowing"
        else:
            label = "mixed"
        cap = sum(_num(r.get("market_cap")) or 0.0 for r in rs)
        out.append({
            "theme": theme, "n": n, "share": _share(n, n_all),
            "cap_share": round(cap / total_cap, 4) if total_cap else None,
            "avg_270d": _mean([r.get("ret_270d") for r in rs]),
            "median_270d": _median([r.get("ret_270d") for r in rs]),
            "pct_above_ma20": pa20,
            "pct_above_ma50": _share(sum(1 for r in rs if _gt(_num(r.get("dist_ma50")), 0)), n),
            "pct_positive_20d": p20,
            "pct_positive_5d": _share(sum(1 for r in r5 if r["ret_5d"] > 0), len(r5)),
            "pct_new_52w_high": _share(sum(1 for r in h52 if r["new_52w_high"] is True), len(h52)),
            "breadth": label,
        })
    return out


def analytics_block(windows: list[int]) -> dict:
    """Everything the UI needs to explain the numbers: weights, rules,
    thresholds. Pure constants, written into every snapshot."""
    return {
        "version": 3,
        "score_weights": dict(SCORE_MAX),
        "score_scaling": dict(SCORE_SCALING),
        "setup_order": list(SETUP_ORDER),
        "setup_rules": dict(SETUP_RULES),
        "queue_rules": dict(QUEUE_RULES),
        "risk_thresholds": dict(RISK_THRESHOLDS),
        "rvol_classes": {"weak": "< 0.7", "normal": "0.7-1.2", "confirmed": "1.2-2.0",
                         "high": "2.0-3.0", "exceptional": "> 3.0"},
        "rvol_trend": {"rising": f"> {RVOL_TREND_RISING}", "falling": f"< {RVOL_TREND_FALLING}",
                       "definition": "mean volume last 5 bars / mean volume prior 20 bars"},
        "liquidity_tiers": {"A": "> $500M", "B": "$100-500M", "C": "$20-100M", "D": "< $20M",
                            "basis": "20d median daily dollar volume"},
        "trend_states": {"strong_uptrend": "price > MA20 > MA50",
                         "uptrend": "price above both, MA20 <= MA50",
                         "mixed": "above exactly one average",
                         "downtrend": "at/below both averages"},
        "freshness_stages": dict(FRESHNESS_STAGES),
        "regime": {"risk_on": f"pct_above_ma50 >= {REGIME_RISK_ON_BREADTH} and SPY 20d > 0",
                   "risk_off": f"pct_above_ma50 <= {REGIME_RISK_OFF_BREADTH} and SPY 20d < 0",
                   "neutral": "otherwise"},
        "theme_breadth": {"broadening": f"pct_positive_20d >= {BREADTH_BROAD} and pct_above_ma20 >= {BREADTH_BROAD}",
                          "narrowing": f"pct_positive_20d <= {BREADTH_NARROW}", "mixed": "otherwise"},
        "catalyst": {"window_days": CATALYST_WINDOW_DAYS, "cache_ttl_days": CATALYST_CACHE_TTL_DAYS,
                     "source": "yfinance get_earnings_dates (finalists only, live mode only)"},
        "data_quality": {"trigger_abs_return": DATA_QUALITY_TRIGGER_RETURN,
                         "jump_day_move": DATA_QUALITY_JUMP_MOVE,
                         "splits_cache_ttl_days": SPLITS_CACHE_TTL_DAYS,
                         "source": "yfinance Ticker.splits (finalists only, live mode only)"},
        "sector_rs_min_peers": SECTOR_RS_MIN_PEERS,
        "benchmarks": list(BENCHMARK_TICKERS),
        "pace": {"accel_ratio": PACE_ACCEL_RATIO, "decel_ratio": PACE_DECEL_RATIO,
                 "windows_days": sorted(windows)},
        "concentration_warn_share": CONCENTRATION_WARN_SHARE,
        "crowded_flag_min_rows": CROWDED_FLAG_MIN_ROWS,
    }


# --- small JSON caches for the finalists-only lookups -----------------------

def load_json_cache(path: Path) -> dict:
    """{ticker: entry} from a cache file; {} when missing/unreadable."""
    try:
        p = Path(path)
        if not p.is_file():
            return {}
        raw = json.loads(p.read_text())
        return raw if isinstance(raw, dict) else {}
    except (OSError, ValueError):
        logger.warning("cache %s unreadable; starting empty", path)
        return {}


def save_json_cache(path: Path, data: dict) -> Path | None:
    """Atomic write (tmp + rename); failures only log."""
    try:
        p = Path(path)
        tmp = p.with_name(p.name + ".tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True))
        os.replace(tmp, p)
        return p
    except OSError:
        logger.warning("cache %s not written", path, exc_info=True)
        return None


def _normalize_dates(values) -> list[str]:
    out = set()
    for d in values or []:
        if isinstance(d, datetime):
            out.add(d.date().isoformat())
        elif isinstance(d, date):
            out.add(d.isoformat())
        else:
            try:
                out.add(pd.Timestamp(d).date().isoformat())
            except (TypeError, ValueError):
                continue
    return sorted(out)


def _normalize_splits(values) -> list[dict]:
    out = []
    for s in values or []:
        if not isinstance(s, dict):
            continue
        try:
            d = pd.Timestamp(s.get("date")).date().isoformat()
        except (TypeError, ValueError):
            continue
        out.append({"date": d, "ratio": _num(s.get("ratio"))})
    return sorted(out, key=lambda s: s["date"])


def cached_lookup(cache: dict, ticker: str, today: date, ttl_days: int, fetch, field: str,
                  normalize=lambda v: v):
    """Value of `field` for `ticker` from `cache` ({ticker: {"fetched": iso,
    field: value}}), refreshed through `fetch(ticker)` when older than
    ttl_days. fetch=None (replay mode) returns whatever is cached, however
    old, else None. A failed fetch keeps the stale value; a successful one
    (even an empty list) is cached under today's date."""
    entry = cache.get(ticker) if isinstance(cache, dict) else None
    cached = entry.get(field) if isinstance(entry, dict) and field in entry else None
    if fetch is None:
        return cached
    if cached is not None:
        try:
            fetched = date.fromisoformat(str(entry.get("fetched"))[:10])
            if 0 <= (today - fetched).days < ttl_days:
                return cached
        except ValueError:
            pass
    try:
        value = fetch(ticker)
    except Exception:  # noqa: BLE001
        logger.warning("%s lookup failed for %s", field, ticker)
        value = None
    if value is None:
        return cached
    value = normalize(value)
    cache[ticker] = {"fetched": today.isoformat(), field: value}
    return value


def _default_fetch_earnings(ticker: str) -> list[date]:
    """Earnings dates via yfinance get_earnings_dates(limit=12); [] on any
    failure (missing yfinance included) — never raises."""
    try:
        yf = _import_yfinance()
        frame = yf.Ticker(ticker).get_earnings_dates(limit=12)
    except Exception:  # noqa: BLE001
        return []
    if frame is None or getattr(frame, "empty", True):
        return []
    out = []
    for ts in frame.index:
        try:
            out.append(pd.Timestamp(ts).date())
        except (TypeError, ValueError):
            continue
    return sorted(set(out))


def _default_fetch_splits(ticker: str) -> list[dict]:
    """Split history via yfinance Ticker.splits; [] on any failure."""
    try:
        yf = _import_yfinance()
        s = yf.Ticker(ticker).splits
    except Exception:  # noqa: BLE001
        return []
    if s is None or getattr(s, "empty", True):
        return []
    out = []
    for ts, ratio in s.items():
        try:
            out.append({"date": pd.Timestamp(ts).date().isoformat(), "ratio": float(ratio)})
        except (TypeError, ValueError):
            continue
    return out


# --- freshness: consecutive prior snapshots in the same bucket ---------------

def bucket_tickers_of(doc: dict) -> dict[str, set[str]]:
    """{bucket: tickers} for a snapshot document — v3 `buckets` when present,
    else the legacy candidates/doublers lists."""
    out = {b: set() for b in BUCKET_NAMES}
    buckets = doc.get("buckets") if isinstance(doc, dict) else None
    if isinstance(buckets, dict):
        for b in BUCKET_NAMES:
            out[b] = {r.get("ticker") for r in (buckets.get(b) or []) if isinstance(r, dict) and r.get("ticker")}
        return out
    out["fresh_momentum"] = {r.get("ticker") for r in (doc.get("candidates") or []) if isinstance(r, dict)}
    out["long_term_winners"] = {r.get("ticker") for r in (doc.get("doublers") or []) if isinstance(r, dict)}
    out["fresh_momentum"].discard(None)
    out["long_term_winners"].discard(None)
    return out


def build_history_index(output_dir: Path, before: date | None = None) -> dict[str, dict[str, set[str]]]:
    """{date_iso: {bucket: tickers}} from screener/<date>.json files dated
    strictly BEFORE `before` (None = all). Unreadable files are skipped."""
    screener_dir = Path(output_dir) / "screener"
    index: dict[str, dict[str, set[str]]] = {}
    if not screener_dir.is_dir():
        return index
    for p in sorted(screener_dir.glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if before is not None and d >= before:
            continue
        try:
            snap = json.loads(p.read_text())
        except (OSError, ValueError):
            continue
        if isinstance(snap, dict):
            index[d.isoformat()] = bucket_tickers_of(snap)
    return index


def freshness_for(history_index: dict, bucket: str, ticker: str, today: date) -> dict:
    """Walk back through the prior snapshot dates (newest first) while the
    ticker sits in the same bucket. days_in_list = that streak + today;
    stage fresh (1) / developing (2-5) / mature (> 5). first_seen is the
    oldest date of the streak (today when fresh). history_days = how many
    prior snapshots exist at all (0 = no history, everything is "fresh")."""
    today_iso = today.isoformat() if isinstance(today, date) else str(today)
    prior = sorted((d for d in (history_index or {}) if d < today_iso), reverse=True)
    streak, first_seen = 0, today_iso
    for d in prior:
        if ticker not in ((history_index[d] or {}).get(bucket) or ()):
            break
        streak += 1
        first_seen = d
    days = streak + 1
    stage = "fresh" if days == 1 else ("developing" if days <= 5 else "mature")
    return {"first_seen": first_seen, "days_in_list": days, "stage": stage,
            "history_days": len(prior)}


def compute_freshness(output_dir: Path, bucket: str, tickers: list[str], today: date) -> dict[str, dict]:
    """{ticker: freshness} straight from the snapshot files (convenience
    wrapper over build_history_index + freshness_for)."""
    index = build_history_index(output_dir, before=today)
    return {t: freshness_for(index, bucket, t, today) for t in tickers}


# --- activity: what the hits index says about the signal flow ---------------

def _hit_row_buckets(h: dict) -> dict[str, set[str]]:
    """Per-bucket top tickers of one hits row; legacy rows (no top_tickers)
    fall back to the single top / top_doubler ticker."""
    out = {b: set() for b in BUCKET_NAMES}
    tt = h.get("top_tickers")
    if isinstance(tt, dict):
        for b in BUCKET_NAMES:
            out[b] = {t for t in (tt.get(b) or []) if isinstance(t, str)}
        return out
    top = (h.get("top") or {}).get("ticker")
    td = (h.get("top_doubler") or {}).get("ticker")
    if top:
        out["fresh_momentum"].add(top)
    if td:
        out["long_term_winners"].add(td)
    return out


def compute_activity(hits: list[dict], *, persist_min_days: int = PERSISTENT_MIN_DAYS) -> dict:
    """Signal-flow summary from the hits index (rows newest first):
      today_candidates         n_candidates of the newest row
      avg_5d / avg_20d         mean n_candidates over the newest 5 / 20 rows
      last_hit_date            newest date with >= 1 fresh-momentum candidate
      streak_days_without_hit  consecutive newest rows with 0 candidates
      persistent               tickers in the newest row's per-bucket top list
                               that were also there on >= persist_min_days
                               consecutive prior rows
      dropped_out              tickers in the previous row's top lists missing
                               today (stage "stale")
    Rows without top_tickers (pre-v3) degrade to their top / top_doubler."""
    rows = sorted((h for h in (hits or []) if isinstance(h, dict) and h.get("date")),
                  key=lambda h: h["date"], reverse=True)
    empty = {"today_candidates": 0, "avg_5d": None, "avg_20d": None, "last_hit_date": None,
             "streak_days_without_hit": 0, "n_days": 0, "persistent": [], "dropped_out": []}
    if not rows:
        return empty
    counts = [int(h.get("n_candidates") or 0) for h in rows]
    streak = 0
    for c in counts:
        if c > 0:
            break
        streak += 1
    last_hit = next((h["date"] for h, c in zip(rows, counts) if c > 0), None)
    sets = [_hit_row_buckets(h) for h in rows]
    persistent = []
    for b in BUCKET_NAMES:
        for t in sorted(sets[0][b]):
            n = 1
            for s in sets[1:]:
                if t in s[b]:
                    n += 1
                else:
                    break
            if n >= persist_min_days:
                persistent.append({"ticker": t, "consecutive_days": n, "bucket": b})
    persistent.sort(key=lambda e: (-e["consecutive_days"], e["bucket"], e["ticker"]))
    dropped = []
    if len(sets) >= 2:
        for b in BUCKET_NAMES:
            for t in sorted(sets[1][b] - sets[0][b]):
                dropped.append({"ticker": t, "bucket": b, "last_seen": rows[1]["date"], "stage": "stale"})
    return {
        "today_candidates": counts[0],
        "avg_5d": round(sum(counts[:5]) / len(counts[:5]), 2),
        "avg_20d": round(sum(counts[:20]) / len(counts[:20]), 2),
        "last_hit_date": last_hit,
        "streak_days_without_hit": streak,
        "n_days": len(rows),
        "persistent": persistent,
        "dropped_out": dropped,
    }


def _slice_asof(frame: pd.DataFrame, asof: date) -> pd.DataFrame:
    """Rows dated <= asof ONLY — the look-ahead guard for historical replay.
    Inclusive of asof's own date; tz-aware indexes are compared date-wise."""
    if frame is None or frame.empty:
        return frame
    idx = frame.index
    if getattr(idx, "tz", None) is not None:
        idx = idx.tz_localize(None)
    cutoff = pd.Timestamp(asof) + pd.Timedelta(days=1)
    return frame[idx < cutoff]


def passes_price_filters(m: dict, criteria: dict) -> bool:
    """All PRICE-derived filters (everything except market cap).

    Only min_return_2d / min_return_5d always gate. The others are optional
    tightening knobs: a threshold of 0 (min_price / min_rvol /
    min_avg_dollar_vol) or a false require_above_ma20/50 means "gate OFF" —
    and 0 / false ARE the shipped defaults (see DEFAULT_CRITERIA), so by
    default this is a loose screen: return thresholds only, with the metric
    values kept purely informational."""
    if criteria["min_price"] > 0 and m["price"] < criteria["min_price"]:
        return False
    if m["ret_2d"] < criteria["min_return_2d"]:
        return False
    if m["ret_5d"] < criteria["min_return_5d"]:
        return False
    if criteria["min_rvol"] > 0 and m["rvol"] < criteria["min_rvol"]:
        return False
    if criteria["min_avg_dollar_vol"] > 0 and m["avg_dollar_vol"] < criteria["min_avg_dollar_vol"]:
        return False
    if criteria["require_above_ma20"] and m["dist_ma20"] <= 0:
        return False
    if criteria["require_above_ma50"] and m["dist_ma50"] <= 0:
        return False
    return True


# ---------------------------------------------------------------------------
# The screen itself
# ---------------------------------------------------------------------------

def run_screen(cfg: dict, universe: dict, *, fetch=None,
               fetch_market_cap=None, fetch_52w=None,
               history: tuple[pd.DataFrame, pd.DataFrame] | None = None,
               asof: date | None = None,
               sectors: dict[str, dict] | None = None,
               fetch_sector=None,
               fetch_earnings=None, fetch_splits=None,
               catalyst_cache: dict | None = None, splits_cache: dict | None = None,
               history_index: dict | None = None) -> dict:
    """Run the full screen and return the snapshot document (nothing written).
    `cfg` is load_screener_config() output; `universe` is load_universe()
    output. Every fetcher is injectable so tests run fully offline:
    fetch (universe bars), fetch_market_cap, fetch_52w, fetch_sector,
    fetch_earnings, fetch_splits — the last three default to None = "no
    network, cache/static table only"; the entry points pass the live ones.

    `history` = (closes, volumes) skips the universe fetch (the daily job and
    --backfill fetch once and reuse). `asof` slices whatever data is used down
    to rows dated <= asof BEFORE any math (the look-ahead guard) and marks the
    snapshot "backfilled": true with the current-market-cap caveat note.
    `catalyst_cache` / `splits_cache` are {ticker: {"fetched", ...}} dicts
    updated IN PLACE (the caller persists them); `history_index` is
    build_history_index() output for the freshness stage."""
    fetch_market_cap = fetch_market_cap or _default_fetch_market_cap
    fetch_52w = fetch_52w or _default_fetch_52w
    catalyst_cache = catalyst_cache if catalyst_cache is not None else {}
    splits_cache = splits_cache if splits_cache is not None else {}
    history_index = history_index or {}

    # benchmarks ride along in the fetch but are never scanned or listed
    tickers = [t for t in universe["tickers"] if t not in BENCHMARK_TICKERS]
    if history is not None:
        closes, volumes = history
    else:
        closes, volumes = fetch_universe_history(with_benchmarks(tickers), fetch=fetch)
    if asof is not None:
        closes = _slice_asof(closes, asof)
        volumes = _slice_asof(volumes, asof)
    run_day = asof if asof is not None else datetime.now(timezone.utc).date()

    windows = cfg["doubler_windows_days"]
    std_bars = benchmark_window_bars()
    metrics: dict[str, dict] = {}
    window_rets: dict[str, dict] = {}
    for t in tickers:
        if t not in getattr(closes, "columns", []):
            continue
        m = compute_ticker_metrics(closes[t], volumes[t] if t in volumes.columns else pd.Series(dtype=float))
        if m is not None:
            metrics[t] = m
            # ret_90d / ret_270d are always present (RS + data-quality trigger)
            # even when the doubler windows are configured differently
            wr = {k: bar_return(closes[t], n) for k, n in std_bars.items() if k not in m}
            wr.update(compute_window_returns(closes[t], windows))
            window_rets[t] = wr
    skipped = len(tickers) - len(metrics)

    benchmarks = {b: benchmark_returns(closes, b, std_bars) for b in BENCHMARK_TICKERS}
    regime = compute_regime(metrics, benchmarks)

    # Stage 1: price-derived gates for the three buckets — cheap, applied to
    # everything. fresh_momentum = the 2d/5d screen; established_momentum =
    # 20d/60d + stacked MAs; long_term_winners = >= doubler_min_return over
    # ANY configured window (+ the optional price/$vol knobs).
    pre_cap = [t for t, m in metrics.items() if passes_price_filters(m, cfg)]
    # established honours the same optional price/$vol knobs as the doublers
    est_pre = [t for t, m in metrics.items()
               if passes_doubler_gates(m, cfg) and passes_established_gates(m, cfg)]
    doubler_pre = [t for t, m in metrics.items()
                   if passes_doubler_gates(m, cfg)
                   and doubler_window_hits(window_rets[t], windows, cfg["doubler_min_return"])]
    logger.info("screener: %d/%d tickers computed, %d fresh-momentum, %d established, "
                "%d long-term-winner finalists (market-cap lookup only for those)",
                len(metrics), len(tickers), len(pre_cap), len(est_pre), len(doubler_pre))

    # Stage 2: market cap ONLY for the (few) survivors of any bucket — each
    # ticker looked up once even when it appears on several lists.
    caps: dict[str, float | None] = {}
    for t in dict.fromkeys(pre_cap + est_pre + doubler_pre):
        cap = None
        try:
            cap = fetch_market_cap(t)
        except Exception:  # noqa: BLE001
            logger.warning("market cap lookup failed for %s; keeping it flagged cap_unknown", t)
        caps[t] = cap

    def _cap_ok(t: str) -> bool:
        cap = caps.get(t)
        return cap is None or cap >= cfg["min_market_cap"]  # unknown kept + flagged

    def _base_row(t: str) -> dict:
        cap = caps.get(t)
        row = {"ticker": t, **metrics[t], **window_rets[t]}
        row["market_cap"] = round(float(cap), 0) if cap is not None else None
        row["cap_unknown"] = cap is None  # kept but flagged, never silently dropped
        return row

    candidates = [_base_row(t) for t in pre_cap if _cap_ok(t)]
    established = [_base_row(t) for t in est_pre if _cap_ok(t)]
    doublers = []
    for t in doubler_pre:
        if not _cap_ok(t):
            continue
        hits = doubler_window_hits(window_rets[t], windows, cfg["doubler_min_return"])
        row = _base_row(t)
        row["window_hit"] = "both" if len(hits) >= 2 else hits[0]
        doublers.append(row)
    buckets = {"fresh_momentum": candidates, "established_momentum": established,
               "long_term_winners": doublers}
    all_rows = [r for rows in buckets.values() for r in rows]
    finalists = list(dict.fromkeys(r["ticker"] for r in all_rows))

    # Stage 3: 52-week highs. The ~400-day main window already covers 252
    # bars for most tickers; only fresh-momentum rows still unknown get the
    # finalists-only period="1y" fetch — and never in as-of mode, where a
    # fetch anchored to "now" would be look-ahead. The answer is copied to
    # every bucket row of that ticker.
    missing_52w = [c["ticker"] for c in candidates if c["new_52w_high"] is None]
    if missing_52w and asof is None:
        try:
            year_closes = fetch_52w(missing_52w)
            for t in missing_52w:
                if t not in getattr(year_closes, "columns", []):
                    continue
                s = year_closes[t].dropna()
                if s.empty:
                    continue
                high, last = float(s.max()), float(s.iloc[-1])
                fill = {"new_52w_high": bool(last >= high), "high_52w": round(high, 4),
                        "pct_from_52w_high": round(last / high - 1.0, 4) if high > 0 else None,
                        "days_since_52w_high": int(len(s) - 1 - int(s.values.argmax()))}
                for row in all_rows:
                    if row["ticker"] == t and row.get("new_52w_high") is None:
                        row.update(fill)
        except Exception:  # noqa: BLE001
            logger.warning("1y fetch for 52w highs failed; new_52w_high left null", exc_info=True)

    # Stage 4: sector / theme for every finalist row (+ the 90d/270d pace test
    # on long-term winners). Sector lookups (like market caps) only happen for
    # finalists; static config/sectors.json entries win, live lookups are
    # remembered by the caller via doc["sector_lookups"] -> save_sector_cache().
    # fetch_sector=None (the default) means "static table only, no network".
    sector_table = {k.upper(): v for k, v in (sectors or {}).items()}
    looked_up: dict[str, dict] = {}
    for bucket_name, rows in buckets.items():
        for row in rows:
            t = row["ticker"]
            info = sector_table.get(t.upper())
            if info is None and t not in looked_up and fetch_sector is not None:
                try:
                    info = fetch_sector(t)
                except Exception:  # noqa: BLE001
                    logger.warning("sector lookup failed for %s", t)
                    info = None
                looked_up[t] = info or {}
                if info:
                    sector_table[t.upper()] = info
            elif info is None:
                info = looked_up.get(t) or None
            enrich_row_analytics(row, windows, info, with_pace=(bucket_name == "long_term_winners"))
    # theme-relative strength peers: every computed universe ticker with a
    # KNOWN theme (static table + cache + today's lookups)
    theme_map = {t: theme_of(sector_table.get(t.upper())) for t in metrics}
    ret20_map = {t: metrics[t].get("ret_20d") for t in metrics}

    # Stage 5: the two tiny finalists-only lookups — earnings dates (catalyst
    # window) and, for > +-200% movers only, splits for the data-quality
    # check. Both go through the caches; fetchers are None in replay mode.
    catalyst_by: dict[str, dict] = {}
    dq_by: dict[str, dict] = {}
    dq_bars = trading_days_for_window(max(windows) if windows else 270)
    for t in finalists:
        dates = cached_lookup(catalyst_cache, t, run_day, CATALYST_CACHE_TTL_DAYS,
                              fetch_earnings, "dates", _normalize_dates)
        catalyst_by[t] = catalyst_for(dates, run_day)
        if needs_data_quality_check(window_rets[t]):
            splits = cached_lookup(splits_cache, t, run_day, SPLITS_CACHE_TTL_DAYS,
                                   fetch_splits, "splits", _normalize_splits)
            dq_by[t] = data_quality_check(closes[t], splits, bars=dq_bars, asof=run_day)
        else:
            dq_by[t] = {"checked": False}

    # Stage 6: the funnel fields per bucket row (RS, acceleration, setup,
    # flags, score, queue, freshness); crowded_theme uses the row's theme
    # share WITHIN its bucket (buckets below CROWDED_FLAG_MIN_ROWS never flag).
    for bucket_name, rows in buckets.items():
        shares: dict[str, float] = {}
        if len(rows) >= CROWDED_FLAG_MIN_ROWS:
            shares = {g["theme"]: g["share"] for g in sector_concentration(rows)["groups"]}
        for row in rows:
            t = row["ticker"]
            enrich_row_v3(row, benchmarks=benchmarks,
                          sector_rs_20d=sector_relative_strength(t, theme_map, ret20_map),
                          theme_share=shares.get(row.get("theme")),
                          catalyst=catalyst_by[t], data_quality=dq_by[t])
            row["bucket"] = bucket_name
            row["freshness"] = freshness_for(history_index, bucket_name, t, run_day)
        rows.sort(key=lambda r: (-r["score"]["total"], r["ticker"]))
    concentration = sector_concentration(doublers)

    doc = {
        "date": run_day.isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": universe["name"],
        "criteria": {**{k: cfg[k] for k in DEFAULT_CRITERIA},
                     "doubler_windows_days": list(windows),
                     "doubler_min_return": cfg["doubler_min_return"]},
        "score_weights": dict(SCORE_MAX),
        "candidates": candidates,
        "doublers": doublers,
        "buckets": buckets,
        "counts": {b: len(rows) for b, rows in buckets.items()},
        "benchmarks": benchmarks,
        "regime": regime,
        "research_queue": build_research_queue(buckets),
        "theme_breadth": theme_breadth(doublers),
        "concentration": concentration,
        "analytics": analytics_block(windows),
        "sector_lookups": {t: v for t, v in looked_up.items() if v},
        "scanned": len(tickers),
        "passed_filters": len(candidates),
        "skipped": skipped,
        "notes": "Discovery screen, not buy signals — a +30% week or a +100% "
                 "quarter can be accumulation, squeeze, or hype; the queue "
                 "tier is a research priority, not a trade.",
    }
    if asof is not None:
        doc["backfilled"] = True
        doc["note"] = ("Backfilled/as-of snapshot: price data <= the snapshot date "
                       "only, but market-cap filtering uses CURRENT market caps "
                       "(free data has no historical caps) — same limitation as "
                       "the backtest.")
    return doc


def write_outputs(output_dir: Path, doc: dict, *, include_latest: bool = True) -> list[Path]:
    """Write screener/<date>.json (and, unless include_latest=False,
    screener-latest.json) atomically (tmp + rename), mirroring the
    correlation snapshots. Replay/backfill passes include_latest=False so a
    historical rerun never masquerades as the latest live screen. Every
    write also upserts the date's row into screener-hits.json (best-effort:
    a hits-index failure only logs, it never loses the snapshot)."""
    screener_dir = output_dir / "screener"
    screener_dir.mkdir(parents=True, exist_ok=True)
    targets = [screener_dir / f"{doc['date']}.json"]
    if include_latest:
        targets.append(output_dir / "screener-latest.json")
    payload = json.dumps(doc, indent=2)
    for target in targets:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(payload)
        os.replace(tmp, target)
    try:
        update_hits_index(output_dir, doc)
    except Exception:  # noqa: BLE001
        logger.warning("hits-index update failed; snapshot(s) still written", exc_info=True)
    return targets


# ---------------------------------------------------------------------------
# Hits index: one row per snapshot date, kept in sync on every write
# ---------------------------------------------------------------------------

def _score_value(row: dict) -> float | None:
    """Numeric score of a row: v3 {"total": ...} dict or the legacy float."""
    s = row.get("score")
    if isinstance(s, dict):
        s = s.get("total")
    return s if isinstance(s, (int, float)) and not isinstance(s, bool) else None


def _hits_entry(doc: dict) -> dict:
    """One screener-hits.json row for a snapshot document: counts, the top
    pick per legacy list, and up to HITS_TOP_TICKERS tickers per bucket
    (`top_tickers`, in list order = score desc) so persistence can be
    computed per bucket. Legacy snapshots (no `buckets`) map candidates ->
    fresh_momentum and doublers -> long_term_winners."""
    candidates = doc.get("candidates") or []
    doublers = doc.get("doublers") or []
    buckets = doc.get("buckets") if isinstance(doc.get("buckets"), dict) else {
        "fresh_momentum": candidates, "established_momentum": [], "long_term_winners": doublers}
    top_tickers = {
        b: [r.get("ticker") for r in (buckets.get(b) or [])[:HITS_TOP_TICKERS]
            if isinstance(r, dict) and r.get("ticker")]
        for b in BUCKET_NAMES
    }

    top = None
    if candidates:
        best = max(candidates, key=lambda c: _score_value(c) if _score_value(c) is not None else float("-inf"))
        top = {"ticker": best.get("ticker"), "score": _score_value(best)}

    def _best_ret(row: dict) -> float:
        rets = [v for k, v in row.items()
                if k.startswith("ret_") and k not in ("ret_2d", "ret_5d")
                and isinstance(v, (int, float))]
        return max(rets) if rets else float("-inf")

    top_doubler = None
    if doublers:
        best = max(doublers, key=_best_ret)
        br = _best_ret(best)
        top_doubler = {"ticker": best.get("ticker"),
                       "ret": round(br, 4) if br != float("-inf") else None}

    return {
        "date": doc.get("date"),
        "n_candidates": len(candidates),
        "n_doublers": len(doublers),
        "n_established": len(buckets.get("established_momentum") or []),
        "top": top,
        "top_doubler": top_doubler,
        "top_tickers": top_tickers,
        "backfilled": bool(doc.get("backfilled", False)),
    }


def rebuild_hits_index(output_dir: Path) -> list[dict]:
    """Rebuild the hits entries by scanning every screener/<date>.json —
    used when screener-hits.json is missing or unreadable, so pre-index
    history (e.g. an old backfill) is never lost. Unreadable or non-dated
    files are simply skipped."""
    screener_dir = Path(output_dir) / "screener"
    entries: list[dict] = []
    if not screener_dir.is_dir():
        return entries
    for p in sorted(screener_dir.glob("*.json")):
        try:
            date.fromisoformat(p.stem)
        except ValueError:
            continue
        try:
            snap = json.loads(p.read_text())
        except (OSError, ValueError):
            logger.warning("hits rebuild: unreadable snapshot %s skipped", p.name)
            continue
        if isinstance(snap, dict) and snap.get("date"):
            entries.append(_hits_entry(snap))
    return entries


def update_hits_index(output_dir: Path, doc: dict) -> Path:
    """Upsert this snapshot's row into <output>/screener-hits.json.

    Entries are deduped by date (a re-run rewrites its date's row), sorted
    newest-first and capped at HITS_MAX_ENTRIES. A missing/unreadable index
    is rebuilt from the dated snapshot files first. Written atomically
    (tmp + rename), like the snapshots themselves."""
    output_dir = Path(output_dir)
    index_path = output_dir / HITS_INDEX_NAME
    entries: list[dict] = []
    if index_path.is_file():
        try:
            raw = json.loads(index_path.read_text())
            entries = [e for e in (raw.get("hits") or [])
                       if isinstance(e, dict) and e.get("date")]
        except (OSError, ValueError):
            logger.warning("hits index unreadable; rebuilding from snapshot files")
            entries = rebuild_hits_index(output_dir)
    else:
        entries = rebuild_hits_index(output_dir)

    by_date = {e["date"]: e for e in entries}
    by_date[doc["date"]] = _hits_entry(doc)
    hits = sorted(by_date.values(), key=lambda e: e["date"], reverse=True)[:HITS_MAX_ENTRIES]

    payload = json.dumps({
        "updated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "hits": hits,
    }, indent=2)
    tmp = index_path.with_name(index_path.name + ".tmp")
    tmp.write_text(payload)
    os.replace(tmp, index_path)
    return index_path


# ---------------------------------------------------------------------------
# Signal report card: how did past snapshots' picks actually do?
# ---------------------------------------------------------------------------

def evaluate_past_signals(output_dir: Path, closes: pd.DataFrame,
                          lookbacks: tuple[int, ...] = REPORT_CARD_LOOKBACKS,
                          tolerance_days: int = REPORT_CARD_TOLERANCE_DAYS) -> dict:
    """Grade past screener snapshots against today's closes.

    For each lookback L (trading days, counted on the fetched closes index),
    find the screener/<date>.json nearest the date L trading days ago (within
    +-tolerance_days CALENDAR days; nearest wins, ties go to the earlier
    file). Each recorded pick's realized return = latest close / recorded
    price - 1 (picks whose ticker has no close in the frame are skipped).

    Returns {"momentum": {...}, "doublers": {...}, "established_momentum":
    {...}} (the last from `buckets.established_momentum` of v3 snapshots)
    with per-lookback stats {n, snapshot_date, win_rate, mean, median,
    best:{ticker,ret}, worst:{ticker,ret}, avg_winner, avg_loser,
    profit_factor, max_drawdown, benchmark_ret, excess} — win = strictly
    positive return; avg_loser averages the non-positive ones; profit_factor
    = sum(wins) / |sum(losses)| (None without losses); max_drawdown = the
    WORST per-pick drawdown, min close after the snapshot date / recorded
    price - 1 (<= 0); benchmark_ret = SPY close-to-close over the same span
    (None without an SPY column); excess = mean - benchmark_ret. Lookbacks
    with no matching file or no gradable picks are omitted; empty groups are
    dropped; an entirely empty result is {} (the caller then writes no
    report_card)."""
    screener_dir = Path(output_dir) / "screener"
    if closes is None or getattr(closes, "empty", True) or not screener_dir.is_dir():
        return {}
    closes = closes.sort_index()
    latest: dict[str, float] = {}
    for t in closes.columns:
        s = closes[t].dropna()
        if not s.empty:
            latest[t] = float(s.iloc[-1])
    if not latest:
        return {}

    files: dict[date, Path] = {}
    for p in screener_dir.glob("*.json"):
        try:
            files[date.fromisoformat(p.stem)] = p
        except ValueError:
            continue
    if not files:
        return {}

    idx = closes.index
    bar_dates = pd.DatetimeIndex(idx.tz_localize(None) if getattr(idx, "tz", None) is not None else idx).date

    def _benchmark_ret(chosen: date) -> float | None:
        if BENCHMARK_PRIMARY not in closes.columns:
            return None
        s = closes[BENCHMARK_PRIMARY]
        before = s[bar_dates <= chosen].dropna()
        if before.empty or float(before.iloc[-1]) <= 0 or BENCHMARK_PRIMARY not in latest:
            return None
        return latest[BENCHMARK_PRIMARY] / float(before.iloc[-1]) - 1.0

    def _grade(rows: list, chosen: date) -> dict | None:
        rets: list[tuple[str, float]] = []
        drawdowns: list[float] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            t, p0 = row.get("ticker"), row.get("price")
            if t in latest and isinstance(p0, (int, float)) and p0 > 0:
                rets.append((t, latest[t] / float(p0) - 1.0))
                path = closes[t][bar_dates > chosen].dropna()
                if not path.empty:
                    drawdowns.append(min(0.0, float(path.min()) / float(p0) - 1.0))
        if not rets:
            return None
        vals = sorted(r for _, r in rets)
        n = len(vals)
        median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
        best = max(rets, key=lambda x: x[1])
        worst = min(rets, key=lambda x: x[1])
        wins = [v for v in vals if v > 0]
        losses = [v for v in vals if v <= 0]
        mean = sum(vals) / n
        bench = _benchmark_ret(chosen)
        return {
            "n": n,
            "snapshot_date": chosen.isoformat(),
            "win_rate": round(len(wins) / n, 4),
            "mean": round(mean, 4),
            "median": round(median, 4),
            "best": {"ticker": best[0], "ret": round(best[1], 4)},
            "worst": {"ticker": worst[0], "ret": round(worst[1], 4)},
            "avg_winner": round(sum(wins) / len(wins), 4) if wins else None,
            "avg_loser": round(sum(losses) / len(losses), 4) if losses else None,
            "profit_factor": (round(sum(wins) / abs(sum(losses)), 4)
                              if losses and sum(losses) < 0 else None),
            "max_drawdown": round(min(drawdowns), 4) if drawdowns else None,
            "benchmark_ret": round(bench, 4) if bench is not None else None,
            "excess": round(mean - bench, 4) if bench is not None else None,
        }

    out: dict[str, dict] = {"momentum": {}, "doublers": {}, "established_momentum": {}}
    for lb in lookbacks:
        if len(idx) <= lb:
            continue
        target = bar_dates[-1 - lb]
        near = [d for d in files if abs((d - target).days) <= tolerance_days]
        if not near:
            continue
        chosen = min(near, key=lambda d: (abs((d - target).days), d))
        try:
            snap = json.loads(files[chosen].read_text())
        except (OSError, ValueError):
            logger.warning("report card: unreadable snapshot %s; lookback %dd skipped",
                           files[chosen], lb)
            continue
        groups = (("momentum", snap.get("candidates") or []),
                  ("doublers", snap.get("doublers") or []),
                  ("established_momentum", (snap.get("buckets") or {}).get("established_momentum") or []))
        for out_key, rows in groups:
            stats = _grade(rows, chosen)
            if stats is not None:
                out[out_key][str(lb)] = stats
    return {k: v for k, v in out.items() if v}


# ---------------------------------------------------------------------------
# Entry points: the daily job + the historical-replay CLI
# ---------------------------------------------------------------------------

def _load_cfg(config_path: Path) -> dict:
    with open(config_path) as f:
        return load_screener_config(json.load(f))


def _resolve_universe(config_path: Path, universe_path: Path | None = None) -> dict | None:
    """Universe next to tickers.json (or SCREENER_UNIVERSE / explicit path);
    None (after a warning) when the file is missing or lists nothing."""
    if universe_path is None:
        universe_path = (Path(DEFAULT_UNIVERSE_PATH) if DEFAULT_UNIVERSE_PATH
                         else Path(config_path).parent / "universe.json")
    if not Path(universe_path).is_file():
        logger.warning("momentum screener skipped: universe file %s not found", universe_path)
        return None
    universe = load_universe(Path(universe_path))
    if not universe["tickers"]:
        logger.warning("momentum screener skipped: universe %s lists no tickers", universe_path)
        return None
    return universe


def run_daily_screen(config_path: Path, output_dir: Path,
                     universe_path: Path | None = None) -> dict | None:
    """Entry point for the daily job: load config + universe, screen, grade
    past snapshots (report card), write. Returns the written doc, or None
    when disabled / no universe file. The caller (run_once) wraps this in
    try/except — but even here nothing is raised for the expected
    disabled/missing-universe states."""
    cfg = _load_cfg(config_path)
    if not cfg["enabled"]:
        logger.info("momentum screener disabled in config (screener.enabled=false)")
        return None
    universe = _resolve_universe(config_path, universe_path)
    if universe is None:
        return None

    logger.info("momentum screener starting: %d tickers (+%d benchmarks) x ~%d days in "
                "universe %r — this fetch is much heavier than the correlation one",
                len(universe["tickers"]), len(BENCHMARK_TICKERS), HISTORY_CALENDAR_DAYS,
                universe["name"])
    out_dir = Path(output_dir)
    closes, volumes = fetch_universe_history(with_benchmarks(universe["tickers"]))
    sectors = load_sectors(Path(config_path).parent, out_dir)
    catalyst_cache = load_json_cache(out_dir / CATALYST_CACHE_NAME)
    splits_cache = load_json_cache(out_dir / SPLITS_CACHE_NAME)
    today = datetime.now(timezone.utc).date()
    doc = run_screen(cfg, universe, history=(closes, volumes), sectors=sectors,
                     fetch_sector=_default_fetch_sector,
                     fetch_earnings=_default_fetch_earnings, fetch_splits=_default_fetch_splits,
                     catalyst_cache=catalyst_cache, splits_cache=splits_cache,
                     history_index=build_history_index(out_dir, before=today))
    if doc.get("sector_lookups"):
        save_sector_cache(out_dir, doc["sector_lookups"])
    if catalyst_cache:
        save_json_cache(out_dir / CATALYST_CACHE_NAME, catalyst_cache)
    if splits_cache:
        save_json_cache(out_dir / SPLITS_CACHE_NAME, splits_cache)

    # Report card: best-effort — grading past snapshots must never lose today's.
    try:
        card = evaluate_past_signals(Path(output_dir), closes)
    except Exception:  # noqa: BLE001
        logger.warning("report-card evaluation failed; snapshot written without it",
                       exc_info=True)
        card = {}
    if card:
        doc["report_card"] = card

    targets = write_outputs(Path(output_dir), doc)
    logger.info("screener wrote %s (scanned=%d, passed=%d, doublers=%d, skipped=%d)",
                " + ".join(str(t) for t in targets),
                doc["scanned"], doc["passed_filters"], len(doc["doublers"]), doc["skipped"])
    return doc


def run_asof(config_path: Path, output_dir: Path, asof: date, *,
             universe_path: Path | None = None,
             fetch=None, fetch_market_cap=None) -> dict | None:
    """Historical replay of ONE day: fetch a window ending at `asof`, run the
    full screen on data <= asof only, write screener/<asof>.json (dated file
    ONLY — screener-latest.json is never touched by replays). Runs even when
    screener.enabled=false: invoking the CLI is explicit enough."""
    cfg = _load_cfg(config_path)
    universe = _resolve_universe(config_path, universe_path)
    if universe is None:
        return None
    fetch = fetch or _make_live_fetch(end_date=asof)
    out_dir = Path(output_dir)
    closes, volumes = fetch_universe_history(with_benchmarks(universe["tickers"]), fetch=fetch)
    sectors = load_sectors(Path(config_path).parent, out_dir)
    # replay: earnings/splits come from the caches ONLY (fetchers None) and
    # are evaluated relative to the as-of date; freshness from files < asof
    doc = run_screen(cfg, universe, history=(closes, volumes), asof=asof,
                     fetch_market_cap=fetch_market_cap, sectors=sectors,
                     fetch_sector=_default_fetch_sector if getattr(fetch, "_live_yahoo", False) else None,
                     catalyst_cache=load_json_cache(out_dir / CATALYST_CACHE_NAME),
                     splits_cache=load_json_cache(out_dir / SPLITS_CACHE_NAME),
                     history_index=build_history_index(out_dir, before=asof))
    if doc.get("sector_lookups"):
        save_sector_cache(Path(output_dir), doc["sector_lookups"])
    targets = write_outputs(Path(output_dir), doc, include_latest=False)
    logger.info("as-of screen wrote %s (candidates=%d, doublers=%d) — market caps are CURRENT",
                targets[0], doc["passed_filters"], len(doc["doublers"]))
    return doc


def run_backfill(config_path: Path, output_dir: Path, n_days: int | None = None, *,
                 force: bool = False, universe_path: Path | None = None,
                 fetch=None, fetch_market_cap=None,
                 start: date | None = None, end: date | None = None) -> list[Path] | None:
    """Replay the screen for a run of trading days from ONE extended fetch,
    writing screener/<date>.json per day (dated files only).

    Either the last `n_days` trading days, or — for a historical study such
    as "what would the screener have shown in H2 2025" — an explicit
    `start`..`end` calendar window (end defaults to today). Dates that
    already have a file are skipped unless `force`. Market caps are looked
    up once per ticker for the whole backfill (they are CURRENT caps either
    way — see the snapshot note). Returns the written paths, or None when
    the universe is unusable."""
    if n_days is None and start is None:
        raise ValueError("run_backfill needs n_days or start")
    cfg = _load_cfg(config_path)
    universe = _resolve_universe(config_path, universe_path)
    if universe is None:
        return None

    today = datetime.now(timezone.utc).date()
    if start is not None:
        end = end or today
        if end < start:
            raise ValueError("backfill end date is before start date")
        extra_days = (today - start).days + 10   # fetch window must reach back to start
    else:
        extra_days = int(n_days * 365 / 252) + 10  # calendar slack for the extra trading days
    fetch = fetch or _make_live_fetch(extra_days=extra_days)
    out_dir = Path(output_dir)
    closes, volumes = fetch_universe_history(with_benchmarks(universe["tickers"]), fetch=fetch)
    if closes.empty:
        logger.error("backfill: universe fetch returned no data; nothing written")
        return []
    all_dates = sorted({ts.date() for ts in closes.index})
    if start is not None:
        trading_dates = [d for d in all_dates if start <= d <= end]
    else:
        trading_dates = all_dates[-n_days:]
    if not trading_dates:
        logger.error("backfill: no trading dates in the requested window; nothing written")
        return []
    # caches are read-only in replay mode; the freshness index is built once
    # from the existing files and extended with every day written below
    catalyst_cache = load_json_cache(out_dir / CATALYST_CACHE_NAME)
    splits_cache = load_json_cache(out_dir / SPLITS_CACHE_NAME)
    history_index = build_history_index(out_dir)

    cap_cache: dict[str, float | None] = {}
    base_cap = fetch_market_cap or _default_fetch_market_cap

    def cached_cap(t: str) -> float | None:
        if t not in cap_cache:
            cap_cache[t] = base_cap(t)
        return cap_cache[t]

    # sectors: static file + cache, and every live lookup during the backfill
    # is reused for the following days (and persisted at the end)
    sectors = load_sectors(Path(config_path).parent, Path(output_dir))
    new_sectors: dict[str, dict] = {}

    screener_dir = Path(output_dir) / "screener"
    written: list[Path] = []
    for d in trading_dates:
        target = screener_dir / f"{d.isoformat()}.json"
        if target.is_file() and not force:
            logger.info("backfill: %s exists, skipping (use --force to rewrite)", target.name)
            continue
        doc = run_screen(cfg, universe, history=(closes, volumes), asof=d,
                         fetch_market_cap=cached_cap, sectors=sectors,
                         fetch_sector=_default_fetch_sector if getattr(fetch, "_live_yahoo", False) else None,
                         catalyst_cache=catalyst_cache, splits_cache=splits_cache,
                         history_index=history_index)
        for t, info in (doc.get("sector_lookups") or {}).items():
            sectors[t.upper()] = info
            new_sectors[t] = info
        written.extend(write_outputs(out_dir, doc, include_latest=False))
        history_index[d.isoformat()] = bucket_tickers_of(doc)   # next day's freshness
        logger.info("backfill %s: candidates=%d, doublers=%d",
                    d.isoformat(), doc["passed_filters"], len(doc["doublers"]))
    if new_sectors:
        save_sector_cache(Path(output_dir), new_sectors)
    logger.info("backfill done: %d/%d dates written (market caps are CURRENT — "
                "see the 'note' field in each snapshot)", len(written), len(trading_dates))
    return written


# ---------------------------------------------------------------------------
# Period digest: "what would the screener have shown between two dates?"
# Pure aggregation over the dated snapshot files (run a --backfill-range first).
# ---------------------------------------------------------------------------

def load_snapshots(output_dir: Path, start: date, end: date) -> list[dict]:
    """Dated screener/<date>.json files with start <= date <= end, oldest first."""
    screener_dir = Path(output_dir) / "screener"
    out: list[dict] = []
    if not screener_dir.is_dir():
        return out
    for p in sorted(screener_dir.glob("*.json")):
        try:
            d = date.fromisoformat(p.stem)
        except ValueError:
            continue
        if start <= d <= end:
            try:
                snap = json.loads(p.read_text())
            except (OSError, ValueError):
                continue
            if isinstance(snap, dict) and snap.get("date"):
                out.append(snap)
    return out


def summarize_period(snapshots: list[dict]) -> dict:
    """Aggregate a run of snapshots into one period report (pure).

    Counts per bucket, hit-day statistics, how often each ticker reached each
    research-queue tier, setup and regime distributions, the most persistent
    long-term winners, theme shares, and the candidates' forward returns when
    the LAST snapshot's prices allow it (price in the last snapshot / price on
    the signal day − 1; a crude but hindsight-free "did it keep going")."""
    if not snapshots:
        return {"days": 0}
    snaps = sorted(snapshots, key=lambda s: s["date"])
    days = len(snaps)
    buckets = ("fresh_momentum", "established_momentum", "long_term_winners")

    def rows(s, b):
        bk = s.get("buckets") or {}
        if b in bk:
            return bk[b] or []
        return (s.get("candidates") if b == "fresh_momentum"
                else s.get("doublers") if b == "long_term_winners" else []) or []

    counts = {b: [len(rows(s, b)) for s in snaps] for b in buckets}
    hit_days = [s["date"] for s in snaps if len(rows(s, "fresh_momentum")) > 0]

    queue_days: dict[str, dict[str, int]] = {t: {} for t in "ABCD"}
    setups: dict[str, int] = {}
    regimes: dict[str, int] = {}
    appearances: dict[str, dict[str, int]] = {b: {} for b in buckets}
    themes: dict[str, int] = {}
    first_seen: dict[str, dict] = {}
    for s in snaps:
        rq = s.get("research_queue") or {}
        for tier in "ABCD":
            for e in rq.get(tier) or []:
                t = e.get("ticker")
                if t:
                    queue_days[tier][t] = queue_days[tier].get(t, 0) + 1
        lbl = (s.get("regime") or {}).get("label")
        if lbl:
            regimes[lbl] = regimes.get(lbl, 0) + 1
        for b in buckets:
            for r in rows(s, b):
                t = r.get("ticker")
                if not t:
                    continue
                appearances[b][t] = appearances[b].get(t, 0) + 1
                if r.get("setup"):
                    setups[r["setup"]] = setups.get(r["setup"], 0) + 1
                if b == "long_term_winners" and r.get("theme"):
                    themes[r["theme"]] = themes.get(r["theme"], 0) + 1
                if b == "fresh_momentum" and t not in first_seen:
                    first_seen[t] = {"date": s["date"], "price": r.get("price"),
                                     "score": (r.get("score") or {}).get("total"),
                                     "setup": r.get("setup"),
                                     "queue": (r.get("queue") or {}).get("tier")}

    # forward return of each fresh-momentum first signal to the last snapshot
    last = snaps[-1]
    last_prices: dict[str, float] = {}
    for b in buckets:
        for r in rows(last, b):
            if r.get("ticker") and isinstance(r.get("price"), (int, float)):
                last_prices[r["ticker"]] = float(r["price"])
    fwd = []
    for t, info in first_seen.items():
        p0, p1 = info.get("price"), last_prices.get(t)
        if isinstance(p0, (int, float)) and p0 > 0 and p1:
            fwd.append({"ticker": t, "signal_date": info["date"], "setup": info.get("setup"),
                        "queue": info.get("queue"), "score": info.get("score"),
                        "ret_to_period_end": round(p1 / float(p0) - 1.0, 4)})
    fwd.sort(key=lambda x: x["signal_date"])
    fwd_vals = [f["ret_to_period_end"] for f in fwd]

    def top(d: dict, n=10):
        return [{"ticker": k, "days": v} for k, v in
                sorted(d.items(), key=lambda kv: (-kv[1], kv[0]))[:n]]

    spy = [((s.get("benchmarks") or {}).get("SPY") or {}).get("ret_20d") for s in snaps]
    spy = [v for v in spy if isinstance(v, (int, float))]
    return {
        "start": snaps[0]["date"], "end": last["date"], "days": days,
        "universe": last.get("universe"), "scanned": last.get("scanned"),
        "backfilled_days": sum(1 for s in snaps if s.get("backfilled")),
        "counts": {b: {"total": sum(v), "avg_per_day": round(sum(v) / days, 2),
                       "max": max(v), "days_nonzero": sum(1 for x in v if x > 0)}
                   for b, v in counts.items()},
        "hit_days": {"n": len(hit_days), "share": round(len(hit_days) / days, 3),
                     "first": hit_days[0] if hit_days else None,
                     "last": hit_days[-1] if hit_days else None},
        "queue": {tier: top(queue_days[tier]) for tier in "ABCD"},
        "distinct_queue_a": len(queue_days["A"]),
        "setups": dict(sorted(setups.items(), key=lambda kv: -kv[1])),
        "regimes": regimes,
        "persistent": {b: top(appearances[b]) for b in buckets},
        "themes": dict(sorted(themes.items(), key=lambda kv: -kv[1])),
        "fresh_signals": fwd,
        "fresh_signal_stats": ({"n": len(fwd_vals),
                                "mean": round(sum(fwd_vals) / len(fwd_vals), 4),
                                "median": round(sorted(fwd_vals)[len(fwd_vals) // 2], 4),
                                "win_rate": round(sum(v > 0 for v in fwd_vals) / len(fwd_vals), 3)}
                               if fwd_vals else None),
        "spy_20d_mean": round(sum(spy) / len(spy), 4) if spy else None,
        "caveats": [
            "replayed snapshots use CURRENT market caps and the CURRENT universe (survivorship bias)",
            "forward returns are signal-day close to the last snapshot's close, no costs, no exits",
            "earnings/split checks only where the caches already held data",
        ],
    }


def render_period_markdown(rep: dict) -> str:
    if not rep or not rep.get("days"):
        return "No snapshots in the requested window — run --backfill-range first.\n"
    L = [f"# Screener period digest {rep['start']} → {rep['end']}",
         f"{rep['days']} trading days · universe {rep.get('universe')} · scanned {rep.get('scanned')}"
         f" · {rep['backfilled_days']} replayed days", ""]
    L.append("## Activity")
    for b, c in rep["counts"].items():
        L.append(f"- {b}: {c['total']} rows over the period, {c['avg_per_day']}/day, max {c['max']},"
                 f" non-empty on {c['days_nonzero']} days")
    h = rep["hit_days"]
    L.append(f"- fresh-momentum hit days: {h['n']} ({h['share']:.0%}), first {h['first']}, last {h['last']}")
    if rep.get("regimes"):
        L.append("- regime days: " + ", ".join(f"{k} {v}" for k, v in rep["regimes"].items()))
    if rep.get("spy_20d_mean") is not None:
        L.append(f"- SPY mean 20d return across the period: {rep['spy_20d_mean']:+.1%}")
    L += ["", "## Research queue (days a ticker held the tier)"]
    for tier, label in (("A", "A — investigate"), ("B", "B — monitor"), ("C", "C — watch"), ("D", "D — excluded")):
        items = rep["queue"].get(tier) or []
        L.append(f"- {label}: " + (", ".join(f"{i['ticker']} ({i['days']})" for i in items) or "—"))
    L += ["", "## Setups seen", ", ".join(f"{k} {v}" for k, v in rep["setups"].items()) or "—"]
    L += ["", "## Most persistent names"]
    for b, items in rep["persistent"].items():
        L.append(f"- {b}: " + (", ".join(f"{i['ticker']} ({i['days']}d)" for i in items) or "—"))
    if rep.get("themes"):
        L += ["", "## Long-term winner themes (row-days)",
              ", ".join(f"{k} {v}" for k, v in rep["themes"].items())]
    st = rep.get("fresh_signal_stats")
    L += ["", "## Fresh-momentum signals → return to period end"]
    if st:
        L.append(f"n={st['n']} · mean {st['mean']:+.1%} · median {st['median']:+.1%} · win rate {st['win_rate']:.0%}")
        L.append("")
        L.append("| signal date | ticker | setup | queue | score | ret to end |")
        L.append("|---|---|---|---|---|---|")
        for f in rep["fresh_signals"][:60]:
            L.append(f"| {f['signal_date']} | {f['ticker']} | {f.get('setup') or '—'} | {f.get('queue') or '—'} "
                     f"| {f.get('score') if f.get('score') is not None else '—'} | {f['ret_to_period_end']:+.1%} |")
    else:
        L.append("no fresh-momentum signals in the window")
    L += ["", "## Caveats"] + [f"- {c}" for c in rep["caveats"]]
    return "\n".join(L) + "\n"


def run_period_summary(output_dir: Path, start: date, end: date) -> dict:
    rep = summarize_period(load_snapshots(output_dir, start, end))
    md = render_period_markdown(rep)
    if rep.get("days"):
        target_dir = Path(output_dir) / "screener"
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / f"digest-{start.isoformat()}-{end.isoformat()}.md").write_text(md, encoding="utf-8")
        (target_dir / f"digest-{start.isoformat()}-{end.isoformat()}.json").write_text(
            json.dumps(rep, indent=2), encoding="utf-8")
    try:
        print(md)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(md.encode("utf-8", errors="replace"))
    return rep


def _parse_range(text: str) -> tuple[date, date]:
    """'YYYY-MM-DD:YYYY-MM-DD' (end optional → today)."""
    parts = text.split(":")
    start = date.fromisoformat(parts[0])
    end = date.fromisoformat(parts[1]) if len(parts) > 1 and parts[1] else datetime.now(timezone.utc).date()
    if end < start:
        raise ValueError("range end is before start")
    return start, end


def main() -> None:
    from app.daily_correlation import DEFAULT_CONFIG_PATH, DEFAULT_OUTPUT_DIR

    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--asof", metavar="YYYY-MM-DD",
                      help="historical replay: run the full screen using only data "
                           "<= this date; writes screener/<date>.json only")
    mode.add_argument("--backfill", type=int, metavar="N",
                      help="replay each of the last N trading days from one fetch; "
                           "skips dates that already have files (see --force)")
    mode.add_argument("--backfill-range", metavar="FROM:TO",
                      help="replay every trading day in a calendar window, e.g. "
                           "2025-07-01:2025-12-31 (TO optional = today); one fetch")
    mode.add_argument("--summary", metavar="FROM:TO",
                      help="aggregate the dated snapshots in a window into a period "
                           "digest (markdown + json under screener/); no fetch")
    parser.add_argument("--force", action="store_true",
                        help="with --backfill: rewrite dates that already have files")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH, help="path to tickers.json")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR, help="snapshot output directory")
    parser.add_argument("--universe", default=None,
                        help="universe.json path (default: next to tickers.json)")
    args = parser.parse_args()

    config_path, output_dir = Path(args.config), Path(args.output_dir)
    universe_path = Path(args.universe) if args.universe else None

    if args.asof:
        try:
            asof = date.fromisoformat(args.asof)
        except ValueError:
            parser.error("--asof must look like YYYY-MM-DD")
        doc = run_asof(config_path, output_dir, asof, universe_path=universe_path)
        sys.exit(0 if doc is not None else 1)
    if args.backfill is not None:
        if args.backfill < 1:
            parser.error("--backfill needs N >= 1")
        written = run_backfill(config_path, output_dir, args.backfill,
                               force=args.force, universe_path=universe_path)
        sys.exit(0 if written is not None else 1)
    if args.backfill_range:
        try:
            start, end = _parse_range(args.backfill_range)
        except ValueError as exc:
            parser.error(f"--backfill-range: {exc}")
        written = run_backfill(config_path, output_dir, force=args.force,
                               universe_path=universe_path, start=start, end=end)
        sys.exit(0 if written is not None else 1)
    if args.summary:
        try:
            start, end = _parse_range(args.summary)
        except ValueError as exc:
            parser.error(f"--summary: {exc}")
        rep = run_period_summary(output_dir, start, end)
        sys.exit(0 if rep.get("days") else 1)
    doc = run_daily_screen(config_path, output_dir, universe_path)
    sys.exit(0 if doc is not None else 1)


if __name__ == "__main__":
    main()
