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
  2. Pure per-ticker metric math: last close, 2d/5d close-to-close returns,
     RVOL (last volume / mean of the prior 20 days' volume), 20d average
     dollar volume, MA20/MA50 + % distance above, 20d/50d new-high flags,
     plus the doubler window returns (ret_90d / ret_270d by default).
  3. Price-based filters. HARD gates by default: min_return_2d and
     min_return_5d only (plus the market-cap check in step 4). Everything
     else — min_price, min_rvol, min_avg_dollar_vol, require_above_ma20/50 —
     is an OPTIONAL tightening knob, OFF by default (0 / false = "gate off"):
     the metrics are still computed, displayed and scored, they just don't
     filter until re-enabled in config. In parallel, the DOUBLERS screen:
     the same optional price/$vol gates (also off by default, so its hard
     gate is just >= doubler_min_return, default +100%, over ANY doubler
     window — plus the cap check).
     Trading-day window lengths are derived from the calendar windows as
     round(window * 252/365) — 90d -> 62 bars, 270d -> 186 bars.
  4. Market cap via yf fast_info/info ONLY for the few tickers that survived
     step 3 (momentum candidates + doubler finalists, retried per ticker).
     Caps below min_market_cap are dropped; UNKNOWN caps are KEPT but
     flagged "cap_unknown": true — a missing Yahoo field must not hide an
     otherwise-valid candidate.
  5. new_52w_high: computed from the main window when a ticker has >= 252
     closes; momentum candidates that still lack it fall back to a
     finalists-only period="1y" fetch (null when that fails; never fatal).
     Doublers use the main-window figure only (cheap) — null otherwise.
  6. Momentum Score 0-100: each component min-max normalized ACROSS the
     day's survivors (a lone survivor scores 100), weighted per config.

Config lives in the "screener" section of tickers.json (all keys optional,
see DEFAULT_CRITERIA / DEFAULT_SCORE_WEIGHTS / the doubler defaults). The
daily correlation job calls run_daily_screen() after its own outputs; any
failure here only logs — it NEVER fails the correlation run. Note this fetch
is much heavier than the correlation one (~500 tickers x ~400 days vs a
handful x 90).

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
    "min_market_cap": 20e9,
    "min_price": 0.0,             # 0 = off (was 10.0 when it gated by default)
    "min_return_2d": 0.10,
    "min_return_5d": 0.30,
    "min_rvol": 0.0,              # 0 = off (was 1.5)
    "min_avg_dollar_vol": 0.0,    # 0 = off (was 20e6)
    "require_above_ma20": False,  # false = off (was True)
    "require_above_ma50": False,  # false = off (was True)
}
DEFAULT_SCORE_WEIGHTS = {
    "ret5d": 0.30, "ret2d": 0.20, "rvol": 0.20, "dist_ma20": 0.15, "dist_ma50": 0.15,
}
DEFAULT_DOUBLER_WINDOWS = [90, 270]   # calendar days; trading bars derived below
DEFAULT_DOUBLER_MIN_RETURN = 1.00     # +100% over any window = a DOUBLER

HITS_INDEX_NAME = "screener-hits.json"  # per-date hits index next to screener-latest.json
HITS_MAX_ENTRIES = 400                  # newest-first cap (~1.5 years of trading days)

REPORT_CARD_LOOKBACKS = (5, 10, 20)   # trading days back to grade
REPORT_CARD_TOLERANCE_DAYS = 2        # accept the nearest snapshot within +-2 days

DEFAULT_UNIVERSE_PATH = os.getenv("SCREENER_UNIVERSE", "")  # default: next to tickers.json


# ---------------------------------------------------------------------------
# Config / universe loading
# ---------------------------------------------------------------------------

def load_screener_config(raw_cfg: dict) -> dict:
    """Extract the "screener" section of tickers.json with full defaults.
    Every key is optional; unknown keys are ignored. Weight keys outside the
    known component set are dropped so a typo can't silently skew the score."""
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
    weights = {}
    raw_weights = sc.get("score_weights") or {}
    for key, default in DEFAULT_SCORE_WEIGHTS.items():
        try:
            weights[key] = float(raw_weights.get(key, default))
        except (TypeError, ValueError):
            weights[key] = float(default)
    out["score_weights"] = weights

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


def fetch_universe_history(tickers: list[str], fetch=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Daily close + volume history for the whole universe, chunked into
    batches of <= BATCH_SIZE, each batch retried independently. A batch that
    still fails after retries is logged and its tickers simply come back as
    missing columns (they land in the `skipped` count)."""
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

def compute_ticker_metrics(closes: pd.Series, volumes: pd.Series) -> dict | None:
    """Per-ticker momentum metrics from aligned daily close/volume series.
    Returns None when there is not enough history (< MIN_CLOSES closes or
    missing volume data) — the ticker is then counted as skipped.

    Definitions (all close-to-close, on the fetched auto-adjusted bars):
      ret_2d  = close[-1] / close[-3] - 1         (2 trading days back)
      ret_5d  = close[-1] / close[-6] - 1
      rvol    = volume[-1] / mean(volume of the PRIOR 20 days)  (excludes today
                so a huge spike doesn't dilute its own denominator)
      avg_dollar_vol = mean(close*volume over the LAST 20 days, incl. today)
      dist_maN = close[-1] / mean(last N closes) - 1
      new_Nd_high = close[-1] >= max(last N closes)
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

    dollar = (c.reindex(v.index) * v).iloc[-VOLUME_WINDOW:]
    avg_dollar_vol = float(dollar.mean())

    ma20 = float(c.iloc[-20:].mean())
    ma50 = float(c.iloc[-50:].mean())
    return {
        "price": round(price, 4),
        "ret_2d": round(ret_2d, 4),
        "ret_5d": round(ret_5d, 4),
        "rvol": round(rvol, 2),
        "avg_dollar_vol": round(avg_dollar_vol, 0),
        "dist_ma20": round(price / ma20 - 1.0, 4),
        "dist_ma50": round(price / ma50 - 1.0, 4),
        "new_20d_high": bool(price >= float(c.iloc[-20:].max())),
        "new_50d_high": bool(price >= float(c.iloc[-50:].max())),
    }


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


def score_candidates(candidates: list[dict], weights: dict) -> None:
    """Attach a 0-100 momentum score, min-max normalized per component ACROSS
    the day's survivors. A component with no spread (incl. a single survivor)
    normalizes to 1.0, so one lone candidate scores exactly 100."""
    if not candidates:
        return
    component_key = {"ret5d": "ret_5d", "ret2d": "ret_2d", "rvol": "rvol",
                     "dist_ma20": "dist_ma20", "dist_ma50": "dist_ma50"}
    total_w = sum(weights.values()) or 1.0
    ranges = {}
    for comp, key in component_key.items():
        vals = [c[key] for c in candidates]
        ranges[comp] = (min(vals), max(vals))
    for cand in candidates:
        score = 0.0
        for comp, key in component_key.items():
            lo, hi = ranges[comp]
            norm = (cand[key] - lo) / (hi - lo) if hi > lo else 1.0
            score += weights.get(comp, 0.0) * norm
        cand["score"] = round(100.0 * score / total_w, 1)
    candidates.sort(key=lambda c: (-c["score"], c["ticker"]))


# ---------------------------------------------------------------------------
# The screen itself
# ---------------------------------------------------------------------------

def run_screen(cfg: dict, universe: dict, *, fetch=None,
               fetch_market_cap=None, fetch_52w=None,
               history: tuple[pd.DataFrame, pd.DataFrame] | None = None,
               asof: date | None = None) -> dict:
    """Run the full screen and return the snapshot document (nothing written).
    `cfg` is load_screener_config() output; `universe` is load_universe()
    output. The three fetchers are injectable so tests run fully offline.

    `history` = (closes, volumes) skips the universe fetch (the daily job and
    --backfill fetch once and reuse). `asof` slices whatever data is used down
    to rows dated <= asof BEFORE any math (the look-ahead guard) and marks the
    snapshot "backfilled": true with the current-market-cap caveat note."""
    fetch_market_cap = fetch_market_cap or _default_fetch_market_cap
    fetch_52w = fetch_52w or _default_fetch_52w

    tickers = universe["tickers"]
    if history is not None:
        closes, volumes = history
    else:
        closes, volumes = fetch_universe_history(tickers, fetch=fetch)
    if asof is not None:
        closes = _slice_asof(closes, asof)
        volumes = _slice_asof(volumes, asof)

    windows = cfg["doubler_windows_days"]
    metrics: dict[str, dict] = {}
    window_rets: dict[str, dict] = {}
    for t in tickers:
        if t not in getattr(closes, "columns", []):
            continue
        m = compute_ticker_metrics(closes[t], volumes[t] if t in volumes.columns else pd.Series(dtype=float))
        if m is not None:
            metrics[t] = m
            window_rets[t] = compute_window_returns(closes[t], windows)
    skipped = len(tickers) - len(metrics)

    # Stage 1: price-derived filters — cheap, applied to everything. Doublers
    # run in parallel on the same metrics: price + dollar-vol gates AND >=
    # doubler_min_return over ANY configured window.
    pre_cap = [t for t, m in metrics.items() if passes_price_filters(m, cfg)]
    doubler_pre = [t for t, m in metrics.items()
                   if passes_doubler_gates(m, cfg)
                   and doubler_window_hits(window_rets[t], windows, cfg["doubler_min_return"])]
    logger.info("screener: %d/%d tickers computed, %d passed price filters, "
                "%d doubler finalists (market-cap lookup only for those)",
                len(metrics), len(tickers), len(pre_cap), len(doubler_pre))

    # Stage 2: market cap ONLY for the (few) survivors of either screen —
    # each ticker looked up once even when it appears on both lists.
    caps: dict[str, float | None] = {}
    for t in dict.fromkeys(pre_cap + doubler_pre):
        cap = None
        try:
            cap = fetch_market_cap(t)
        except Exception:  # noqa: BLE001
            logger.warning("market cap lookup failed for %s; keeping it flagged cap_unknown", t)
        caps[t] = cap

    candidates = []
    for t in pre_cap:
        cap = caps.get(t)
        if cap is not None and cap < cfg["min_market_cap"]:
            continue  # known and too small -> genuinely filtered out
        cand = {"ticker": t, **metrics[t], **window_rets[t]}
        cand["market_cap"] = round(float(cap), 0) if cap is not None else None
        cand["cap_unknown"] = cap is None  # kept but flagged, never silently dropped
        candidates.append(cand)

    doublers = []
    for t in doubler_pre:
        cap = caps.get(t)
        if cap is not None and cap < cfg["min_market_cap"]:
            continue  # same market-cap treatment as the momentum finalists
        hits = doubler_window_hits(window_rets[t], windows, cfg["doubler_min_return"])
        doublers.append({
            "ticker": t,
            "price": metrics[t]["price"],
            **window_rets[t],
            "window_hit": "both" if len(hits) >= 2 else hits[0],
            "rvol": metrics[t]["rvol"],
            "market_cap": round(float(cap), 0) if cap is not None else None,
            "cap_unknown": cap is None,
            # cheap: from the main ~400d window only (null when too short)
            "new_52w_high": new_52w_high_from_history(closes[t]),
        })
    # ranked by the best window return, ties broken alphabetically
    doublers.sort(key=lambda d: (-max(v for k, v in d.items()
                                      if k.startswith("ret_") and v is not None),
                                 d["ticker"]))

    # Stage 3: 52-week highs. The ~400-day main window already covers 252
    # bars for most tickers; only momentum candidates still unknown get the
    # finalists-only period="1y" fetch — and never in as-of mode, where a
    # fetch anchored to "now" would be look-ahead.
    for cand in candidates:
        cand["new_52w_high"] = new_52w_high_from_history(closes[cand["ticker"]])
    missing_52w = [c["ticker"] for c in candidates if c["new_52w_high"] is None]
    if missing_52w and asof is None:
        try:
            year_closes = fetch_52w(missing_52w)
            for cand in candidates:
                t = cand["ticker"]
                if cand["new_52w_high"] is None and t in getattr(year_closes, "columns", []):
                    s = year_closes[t].dropna()
                    if not s.empty:
                        cand["new_52w_high"] = bool(float(s.iloc[-1]) >= float(s.max()))
        except Exception:  # noqa: BLE001
            logger.warning("1y fetch for 52w highs failed; new_52w_high left null", exc_info=True)

    score_candidates(candidates, cfg["score_weights"])

    run_date = asof.isoformat() if asof is not None else datetime.now(timezone.utc).date().isoformat()
    doc = {
        "date": run_date,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": universe["name"],
        "criteria": {**{k: cfg[k] for k in DEFAULT_CRITERIA},
                     "doubler_windows_days": list(windows),
                     "doubler_min_return": cfg["doubler_min_return"]},
        "score_weights": cfg["score_weights"],
        "candidates": candidates,
        "doublers": doublers,
        "scanned": len(tickers),
        "passed_filters": len(candidates),
        "skipped": skipped,
        "notes": "Discovery screen, not buy signals — a +30% week or a +100% "
                 "quarter can be accumulation, squeeze, or hype; do "
                 "second-stage analysis.",
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

def _hits_entry(doc: dict) -> dict:
    """One screener-hits.json row for a snapshot document."""
    candidates = doc.get("candidates") or []
    doublers = doc.get("doublers") or []

    top = None
    if candidates:
        best = max(candidates,
                   key=lambda c: c.get("score") if isinstance(c.get("score"), (int, float)) else float("-inf"))
        top = {"ticker": best.get("ticker"), "score": best.get("score")}

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
        "top": top,
        "top_doubler": top_doubler,
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

    Returns {"momentum": {...}, "doublers": {...}} with per-lookback stats
    {n, snapshot_date, win_rate, mean, median, best:{ticker,ret},
    worst:{ticker,ret}} — win = strictly positive return. Lookbacks with no
    matching file or no gradable picks are omitted; empty groups are dropped;
    an entirely empty result is {} (the caller then writes no report_card)."""
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
    out: dict[str, dict] = {"momentum": {}, "doublers": {}}
    for lb in lookbacks:
        if len(idx) <= lb:
            continue
        target = idx[-1 - lb].date()
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
        for src_key, out_key in (("candidates", "momentum"), ("doublers", "doublers")):
            rets: list[tuple[str, float]] = []
            for row in snap.get(src_key) or []:
                t, p0 = row.get("ticker"), row.get("price")
                if t in latest and isinstance(p0, (int, float)) and p0 > 0:
                    rets.append((t, latest[t] / float(p0) - 1.0))
            if not rets:
                continue
            vals = sorted(r for _, r in rets)
            n = len(vals)
            median = vals[n // 2] if n % 2 else (vals[n // 2 - 1] + vals[n // 2]) / 2.0
            best = max(rets, key=lambda x: x[1])
            worst = min(rets, key=lambda x: x[1])
            out[out_key][str(lb)] = {
                "n": n,
                "snapshot_date": chosen.isoformat(),
                "win_rate": round(sum(v > 0 for v in vals) / n, 4),
                "mean": round(sum(vals) / n, 4),
                "median": round(median, 4),
                "best": {"ticker": best[0], "ret": round(best[1], 4)},
                "worst": {"ticker": worst[0], "ret": round(worst[1], 4)},
            }
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

    logger.info("momentum screener starting: %d tickers x ~%d days in universe %r — "
                "this fetch is much heavier than the correlation one",
                len(universe["tickers"]), HISTORY_CALENDAR_DAYS, universe["name"])
    closes, volumes = fetch_universe_history(universe["tickers"])
    doc = run_screen(cfg, universe, history=(closes, volumes))

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
    closes, volumes = fetch_universe_history(universe["tickers"], fetch=fetch)
    doc = run_screen(cfg, universe, history=(closes, volumes), asof=asof,
                     fetch_market_cap=fetch_market_cap)
    targets = write_outputs(Path(output_dir), doc, include_latest=False)
    logger.info("as-of screen wrote %s (candidates=%d, doublers=%d) — market caps are CURRENT",
                targets[0], doc["passed_filters"], len(doc["doublers"]))
    return doc


def run_backfill(config_path: Path, output_dir: Path, n_days: int, *,
                 force: bool = False, universe_path: Path | None = None,
                 fetch=None, fetch_market_cap=None) -> list[Path] | None:
    """Replay the screen for each of the last `n_days` trading days from ONE
    extended fetch, writing screener/<date>.json per day (dated files only).
    Dates that already have a file are skipped unless `force`. Market caps
    are looked up once per ticker for the whole backfill (they are CURRENT
    caps either way — see the snapshot note). Returns the written paths, or
    None when the universe is unusable."""
    cfg = _load_cfg(config_path)
    universe = _resolve_universe(config_path, universe_path)
    if universe is None:
        return None

    extra_days = int(n_days * 365 / 252) + 10  # calendar slack for the extra trading days
    fetch = fetch or _make_live_fetch(extra_days=extra_days)
    closes, volumes = fetch_universe_history(universe["tickers"], fetch=fetch)
    if closes.empty:
        logger.error("backfill: universe fetch returned no data; nothing written")
        return []
    trading_dates = sorted({ts.date() for ts in closes.index})[-n_days:]

    cap_cache: dict[str, float | None] = {}
    base_cap = fetch_market_cap or _default_fetch_market_cap

    def cached_cap(t: str) -> float | None:
        if t not in cap_cache:
            cap_cache[t] = base_cap(t)
        return cap_cache[t]

    screener_dir = Path(output_dir) / "screener"
    written: list[Path] = []
    for d in trading_dates:
        target = screener_dir / f"{d.isoformat()}.json"
        if target.is_file() and not force:
            logger.info("backfill: %s exists, skipping (use --force to rewrite)", target.name)
            continue
        doc = run_screen(cfg, universe, history=(closes, volumes), asof=d,
                         fetch_market_cap=cached_cap)
        written.extend(write_outputs(Path(output_dir), doc, include_latest=False))
        logger.info("backfill %s: candidates=%d, doublers=%d",
                    d.isoformat(), doc["passed_filters"], len(doc["doublers"]))
    logger.info("backfill done: %d/%d dates written (market caps are CURRENT — "
                "see the 'note' field in each snapshot)", len(written), len(trading_dates))
    return written


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
    doc = run_daily_screen(config_path, output_dir, universe_path)
    sys.exit(0 if doc is not None else 1)


if __name__ == "__main__":
    main()
