"""
Daily momentum screener.

Scans a large static universe (config/universe.json, ~500 US large caps) for
short-term momentum candidates and writes, next to the correlation snapshots:

  screener/YYYY-MM-DD.json   dated snapshot (kept forever = history)
  screener-latest.json       same content under a stable name

Pipeline (deliberately staged so the expensive lookups stay tiny):

  1. ONE pass of batched yf.download daily bars for the whole universe,
     chunked into batches of <= 100 tickers, each batch retried via
     with_retries (shared with app.daily_correlation). The window is ~110
     calendar days — the task brief says "~70", but MA50 / 50d-high need 50
     TRADING days which is ~72+ calendar days, so 110 buys holiday slack
     while staying one light daily-bars call per batch.
  2. Pure per-ticker metric math: last close, 2d/5d close-to-close returns,
     RVOL (last volume / mean of the prior 20 days' volume), 20d average
     dollar volume, MA20/MA50 + % distance above, 20d/50d new-high flags.
  3. Price-based filters (price, returns, RVOL, dollar volume, above-MA).
  4. Market cap via yf fast_info/info ONLY for the few tickers that survived
     step 3 (retried per ticker). Caps below min_market_cap are dropped;
     UNKNOWN caps are KEPT but flagged "cap_unknown": true — a missing
     Yahoo field must not hide an otherwise-valid candidate.
  5. period="1y" second-pass fetch for the FINALISTS only -> new_52w_high
     (null when that fetch fails; never fatal).
  6. Momentum Score 0-100: each component min-max normalized ACROSS the
     day's survivors (a lone survivor scores 100), weighted per config.

Config lives in the "screener" section of tickers.json (all keys optional,
see DEFAULT_CRITERIA / DEFAULT_SCORE_WEIGHTS). The daily correlation job
calls run_daily_screen() after its own outputs; any failure here only logs —
it NEVER fails the correlation run. Note this fetch is much heavier than the
correlation one (~500 tickers vs a handful).

An empty candidates list is a NORMAL outcome — the default thresholds
(+10% in 2 days, +30% in 5 days) are strict on purpose. This is a discovery
screen, not a buy signal: a +30% week can be accumulation, a short squeeze
or pure hype — always do second-stage analysis.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd

from app.daily_correlation import _import_yfinance, with_retries

logger = logging.getLogger("risk-sim.screener")

BATCH_SIZE = 100              # tickers per yf.download call
INTER_BATCH_PAUSE_S = 3       # courtesy pause between live Yahoo batches
HISTORY_CALENDAR_DAYS = 110   # ~75 trading days: MA50/50d-high need 50 bars
VOLUME_WINDOW = 20            # days for RVOL denominator + avg dollar volume
MIN_CLOSES = 51               # 50 bars for MA50 + the bar being screened

DEFAULT_CRITERIA = {
    "min_market_cap": 20e9,
    "min_price": 10.0,
    "min_return_2d": 0.10,
    "min_return_5d": 0.30,
    "min_rvol": 1.5,
    "min_avg_dollar_vol": 20e6,
    "require_above_ma20": True,
    "require_above_ma50": True,
}
DEFAULT_SCORE_WEIGHTS = {
    "ret5d": 0.30, "ret2d": 0.20, "rvol": 0.20, "dist_ma20": 0.15, "dist_ma50": 0.15,
}

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

def _default_fetch(batch: list[str]) -> pd.DataFrame:
    """One yf.download of ~110 calendar days of daily bars for one batch."""
    yf = _import_yfinance()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=HISTORY_CALENDAR_DAYS)
    return yf.download(
        batch,
        start=start.date().isoformat(),
        end=(end + timedelta(days=1)).date().isoformat(),  # yf `end` is exclusive
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )


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
        if i and fetch is _default_fetch:
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


def passes_price_filters(m: dict, criteria: dict) -> bool:
    """All PRICE-derived filters (everything except market cap)."""
    if m["price"] < criteria["min_price"]:
        return False
    if m["ret_2d"] < criteria["min_return_2d"]:
        return False
    if m["ret_5d"] < criteria["min_return_5d"]:
        return False
    if m["rvol"] < criteria["min_rvol"]:
        return False
    if m["avg_dollar_vol"] < criteria["min_avg_dollar_vol"]:
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
               fetch_market_cap=None, fetch_52w=None) -> dict:
    """Run the full screen and return the snapshot document (nothing written).
    `cfg` is load_screener_config() output; `universe` is load_universe()
    output. The three fetchers are injectable so tests run fully offline."""
    fetch_market_cap = fetch_market_cap or _default_fetch_market_cap
    fetch_52w = fetch_52w or _default_fetch_52w

    tickers = universe["tickers"]
    closes, volumes = fetch_universe_history(tickers, fetch=fetch)

    metrics: dict[str, dict] = {}
    for t in tickers:
        if t not in getattr(closes, "columns", []):
            continue
        m = compute_ticker_metrics(closes[t], volumes[t] if t in volumes.columns else pd.Series(dtype=float))
        if m is not None:
            metrics[t] = m
    skipped = len(tickers) - len(metrics)

    # Stage 1: price-derived filters — cheap, applied to everything.
    pre_cap = [t for t, m in metrics.items() if passes_price_filters(m, cfg)]
    logger.info("screener: %d/%d tickers computed, %d passed price filters "
                "(market-cap lookup only for those)", len(metrics), len(tickers), len(pre_cap))

    # Stage 2: market cap ONLY for the (few) price-filter survivors.
    candidates = []
    for t in pre_cap:
        cap = None
        try:
            cap = fetch_market_cap(t)
        except Exception:  # noqa: BLE001
            logger.warning("market cap lookup failed for %s; keeping it flagged cap_unknown", t)
        if cap is not None and cap < cfg["min_market_cap"]:
            continue  # known and too small -> genuinely filtered out
        cand = {"ticker": t, **metrics[t]}
        cand["market_cap"] = round(float(cap), 0) if cap is not None else None
        cand["cap_unknown"] = cap is None  # kept but flagged, never silently dropped
        candidates.append(cand)

    # Stage 3: 52-week highs need 1y of data — fetched for FINALISTS only.
    for cand in candidates:
        cand["new_52w_high"] = None  # unknown until the 1y pass succeeds
    if candidates:
        try:
            year_closes = fetch_52w([c["ticker"] for c in candidates])
            for cand in candidates:
                t = cand["ticker"]
                if t in getattr(year_closes, "columns", []):
                    s = year_closes[t].dropna()
                    if not s.empty:
                        cand["new_52w_high"] = bool(float(s.iloc[-1]) >= float(s.max()))
        except Exception:  # noqa: BLE001
            logger.warning("1y fetch for 52w highs failed; new_52w_high left null", exc_info=True)

    score_candidates(candidates, cfg["score_weights"])

    return {
        "date": datetime.now(timezone.utc).date().isoformat(),
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "universe": universe["name"],
        "criteria": {k: cfg[k] for k in DEFAULT_CRITERIA},
        "score_weights": cfg["score_weights"],
        "candidates": candidates,
        "scanned": len(tickers),
        "passed_filters": len(candidates),
        "skipped": skipped,
        "notes": "Discovery screen, not buy signals — a +30% week can be "
                 "accumulation, squeeze, or hype; do second-stage analysis.",
    }


def write_outputs(output_dir: Path, doc: dict) -> list[Path]:
    """Write screener/<date>.json and screener-latest.json atomically
    (tmp + rename), mirroring the correlation snapshots."""
    screener_dir = output_dir / "screener"
    screener_dir.mkdir(parents=True, exist_ok=True)
    targets = [screener_dir / f"{doc['date']}.json", output_dir / "screener-latest.json"]
    payload = json.dumps(doc, indent=2)
    for target in targets:
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(payload)
        os.replace(tmp, target)
    return targets


def run_daily_screen(config_path: Path, output_dir: Path,
                     universe_path: Path | None = None) -> dict | None:
    """Entry point for the daily job: load config + universe, screen, write.
    Returns the written doc, or None when disabled / no universe file. The
    caller (run_once) wraps this in try/except — but even here nothing is
    raised for the expected disabled/missing-universe states."""
    with open(config_path) as f:
        raw_cfg = json.load(f)
    cfg = load_screener_config(raw_cfg)
    if not cfg["enabled"]:
        logger.info("momentum screener disabled in config (screener.enabled=false)")
        return None

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

    logger.info("momentum screener starting: %d tickers in universe %r — this fetch is "
                "much heavier than the correlation one", len(universe["tickers"]), universe["name"])
    doc = run_screen(cfg, universe)
    targets = write_outputs(Path(output_dir), doc)
    logger.info("screener wrote %s (scanned=%d, passed=%d, skipped=%d)",
                " + ".join(str(t) for t in targets),
                doc["scanned"], doc["passed_filters"], doc["skipped"])
    return doc
