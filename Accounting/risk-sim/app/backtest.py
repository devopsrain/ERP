"""
Momentum-screen backtest — batch CLI, NOT part of the daily job.

Backtests the momentum screener's core signal (2-day AND 5-day close-to-close
return thresholds, defaults from the "screener" section of tickers.json) over
the static universe in config/universe.json, mechanically and without
hindsight:

  signal   at CLOSE of day t:  ret2d = C[t]/C[t-2]-1 >= th2
                                ret5d = C[t]/C[t-5]-1 >= th5
  entry    OPEN of day t+1  (never the signal-day close — no look-ahead)
  exit     CLOSE of day t+1+H  (H trading days after the entry day)
  return   exit/entry - 1 - round_trip_cost_bps/1e4

This is a SIGNAL STUDY, not a portfolio simulation: every signal becomes an
independent trade, overlapping positions are allowed, and no capital
constraint is applied. The one exception is the H=5 equity curve, which
compounds greedily-selected NON-overlapping trades to give a max-drawdown
figure.

STATED BIASES (also lead the generated report):
  * Survivorship: the universe is TODAY's large caps; names that crashed and
    were delisted are absent, so results are biased UP.
  * No historical market cap: the screener's cap filter is approximated by
    universe membership only.
  * Adjusted closes (auto_adjust=True) are used for both entries and exits,
    so dividends/splits are folded into returns rather than modeled.
  * Costs are a flat round-trip bps haircut — no spread/impact model.
Numbers are for hypothesis evaluation, not expected live returns.

Data: one batched yf.download of daily OHLCV per ~100-ticker chunk, --years
back (default 8), cached under <output>/backtest/history/ (csv.gz per batch +
manifest.json with the fetch date). The cache is reused unless --refresh-data
is passed or the manifest no longer matches (different --years, tickers
missing). SPY is fetched once as the benchmark and cached alongside.

Run (see RUNBOOK.md):
  python -m app.backtest [--years 8] [--holding 1,3,5,10,20]
                         [--costs 5,10,25,50] [--grid] [--variants]
                         [--refresh-data] [--config ...] [--output-dir ...]

Outputs: <output>/backtest/report-YYYYMMDD-HHMM.json (full numbers) and
report-YYYYMMDD-HHMM.md (human-readable), the .md also printed to stdout.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from app.daily_correlation import (
    DEFAULT_CONFIG_PATH,
    DEFAULT_OUTPUT_DIR,
    _import_yfinance,
    with_retries,
)
from app.momentum_screener import (
    BATCH_SIZE,
    DEFAULT_UNIVERSE_PATH,
    load_screener_config,
    load_universe,
)

logger = logging.getLogger("risk-sim.backtest")

FIELDS = ("Open", "Close", "Volume")   # what the cache stores per ticker
BENCHMARK_TICKER = "SPY"
CURVE_DAYS = 20                        # continuation curve horizon
RVOL_WINDOW = 20                       # prior-days window for the RVOL variant
RVOL_MIN = 2.0                         # Test C: RVOL >= 2 at the signal close
HIGH_LOOKBACK = 252                    # Test D: signal close is a 252-bar high
VARIANT_HOLDING = 10                   # variants are compared at H=10
DRAWDOWN_HOLDING = 5                   # equity-curve drawdown uses H=5
GRID_FWD_DAYS = 10                     # grid cells report forward 10d returns
TRAIN_FRAC, VAL_FRAC = 0.60, 0.20      # train / validation / out-of-sample

DEFAULT_HOLDINGS = (1, 3, 5, 10, 20)
DEFAULT_COSTS_BPS = (5.0, 10.0, 25.0, 50.0)
GRID_TH2 = (0.05, 0.075, 0.10, 0.125, 0.15, 0.20)
GRID_TH5 = (0.15, 0.20, 0.25, 0.30, 0.35, 0.40, 0.50)


# ---------------------------------------------------------------------------
# Data: batched fetch with an on-disk cache under <output>/backtest/history/
# ---------------------------------------------------------------------------

def _default_fetch_batch(batch: list[str], years: float) -> pd.DataFrame:
    """One yf.download of `years` of daily bars for one <=100-ticker batch.
    auto_adjust=True on purpose: adjusted opens/closes keep entry and exit on
    the same basis and fold dividends/splits into returns (stated bias)."""
    yf = _import_yfinance()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=int(years * 365.25) + 7)
    return yf.download(
        batch,
        start=start.date().isoformat(),
        end=(end + timedelta(days=1)).date().isoformat(),  # yf `end` is exclusive
        interval="1d",
        auto_adjust=True,
        progress=False,
        group_by="column",
    )


def _fields_frame(raw: pd.DataFrame | None, batch: list[str]) -> pd.DataFrame:
    """Normalize a yf.download result to MultiIndex columns (field, ticker)
    restricted to FIELDS. Single-ticker responses collapse flat in yfinance;
    they are re-expanded here. Empty results become an empty frame."""
    if raw is None or raw.empty:
        return pd.DataFrame()
    if isinstance(raw.columns, pd.MultiIndex):
        parts = {f: raw[f] for f in FIELDS if f in raw.columns.get_level_values(0)}
        if not parts:
            return pd.DataFrame()
        return pd.concat(parts, axis=1)
    cols = [f for f in FIELDS if f in raw.columns]
    if not cols:
        return pd.DataFrame()
    sub = raw[cols].copy()
    sub.columns = pd.MultiIndex.from_product([cols, batch[:1]])
    return sub


def _save_frame(path: Path, frame: pd.DataFrame) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path)  # .csv.gz suffix -> pandas gzips automatically


def _load_frame(path: Path) -> pd.DataFrame:
    try:
        frame = pd.read_csv(path, header=[0, 1], index_col=0)
    except (ValueError, pd.errors.EmptyDataError):  # empty/degenerate cache file
        return pd.DataFrame()
    frame.index = pd.to_datetime(frame.index)
    return frame


def _cache_valid(cache_dir: Path, manifest: dict | None, tickers: list[str], years: float) -> bool:
    """A cache is reusable iff the manifest exists, was built with the same
    --years, covers every requested ticker (universe edits invalidate it) and
    all referenced files are still on disk."""
    if not manifest:
        return False
    if float(manifest.get("years", -1)) != float(years):
        return False
    cached = set(manifest.get("tickers", []))
    if not set(tickers).issubset(cached):
        return False
    files = [b["file"] for b in manifest.get("batches", [])] + [manifest.get("spy_file", "")]
    return all(f and (cache_dir / f).is_file() for f in files)


def load_or_fetch_history(tickers: list[str], years: float, cache_dir: Path, *,
                          refresh: bool = False, fetch_batch=None, fetch_spy=None,
                          ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.Series, dict]:
    """(opens, closes, volumes, spy_closes, manifest) for the universe.

    Reads the csv.gz batch cache under `cache_dir` when valid and not
    `refresh`; otherwise fetches fresh (chunked <=BATCH_SIZE, each batch via
    with_retries — a batch that still fails is skipped and its tickers land in
    manifest["failed_tickers"]) and rewrites cache + manifest atomically last.
    """
    fetch_batch = fetch_batch or _default_fetch_batch
    fetch_spy = fetch_spy or _default_fetch_batch
    manifest_path = cache_dir / "manifest.json"

    manifest = None
    if not refresh and manifest_path.is_file():
        try:
            manifest = json.loads(manifest_path.read_text())
        except (OSError, ValueError):
            manifest = None
    if manifest is not None and _cache_valid(cache_dir, manifest, tickers, years):
        logger.info("reusing cached history from %s (fetched %s, %d batches)",
                    cache_dir, manifest.get("fetched_at_utc"), len(manifest["batches"]))
        parts = [_load_frame(cache_dir / b["file"]) for b in manifest["batches"]]
        combined = pd.concat(parts, axis=1).sort_index() if parts else pd.DataFrame()
        spy = _load_frame(cache_dir / manifest["spy_file"])
        if (not spy.empty and "Close" in spy.columns.get_level_values(0)
                and BENCHMARK_TICKER in spy["Close"].columns):
            spy_closes = spy["Close"][BENCHMARK_TICKER]
        else:
            spy_closes = pd.Series(dtype=float)
        return (*_split_fields(combined, tickers), spy_closes, manifest)

    logger.info("fetching %d tickers, %s years of daily bars (batches of %d)…",
                len(tickers), years, BATCH_SIZE)
    cache_dir.mkdir(parents=True, exist_ok=True)
    batches, failed, parts = [], [], []
    for i in range(0, len(tickers), BATCH_SIZE):
        if i:
            # Rate-limit courtesy between live Yahoo batches (the cache makes
            # reruns free, so this only costs seconds on the first run).
            import time as _t
            from app.momentum_screener import INTER_BATCH_PAUSE_S as _pause
            _t.sleep(_pause)
        batch = tickers[i:i + BATCH_SIZE]
        num = i // BATCH_SIZE + 1
        try:
            raw = with_retries(lambda b=batch: fetch_batch(b, years),
                               what=f"backtest yf.download batch {num} ({len(batch)} tickers)")
            frame = _fields_frame(raw, batch)
        except Exception:  # noqa: BLE001
            logger.warning("backtest batch %d failed after retries; skipping %d tickers",
                           num, len(batch), exc_info=True)
            failed.extend(batch)
            continue
        got = (set(frame.columns.get_level_values(1)) if not frame.empty else set())
        failed.extend(t for t in batch if t not in got)
        if frame.empty:
            continue
        fname = f"batch-{num:03d}.csv.gz"
        _save_frame(cache_dir / fname, frame)
        batches.append({"file": fname, "tickers": sorted(got)})
        parts.append(frame)

    spy_raw = with_retries(lambda: fetch_spy([BENCHMARK_TICKER], years),
                           what=f"backtest yf.download {BENCHMARK_TICKER}")
    spy_frame = _fields_frame(spy_raw, [BENCHMARK_TICKER])
    spy_file = "spy.csv.gz"
    _save_frame(cache_dir / spy_file, spy_frame)
    if (not spy_frame.empty and "Close" in spy_frame.columns.get_level_values(0)
            and BENCHMARK_TICKER in spy_frame["Close"].columns):
        spy_closes = spy_frame["Close"][BENCHMARK_TICKER]
    else:
        spy_closes = pd.Series(dtype=float)

    manifest = {
        "fetched_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "years": float(years),
        "tickers": sorted(tickers),
        "batches": batches,
        "failed_tickers": sorted(set(failed)),
        "spy_file": spy_file,
    }
    tmp = manifest_path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(manifest, indent=2))
    os.replace(tmp, manifest_path)

    combined = pd.concat(parts, axis=1).sort_index() if parts else pd.DataFrame()
    return (*_split_fields(combined, tickers), spy_closes, manifest)


def _split_fields(combined: pd.DataFrame, tickers: list[str]) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """MultiIndex (field, ticker) frame -> (opens, closes, volumes), columns
    limited to the requested tickers that actually have data."""
    out = []
    for field in FIELDS:
        if combined.empty or field not in combined.columns.get_level_values(0):
            out.append(pd.DataFrame())
            continue
        sub = combined[field]
        keep = [t for t in tickers if t in sub.columns]
        out.append(sub[keep])
    return tuple(out)  # opens, closes, volumes


# ---------------------------------------------------------------------------
# Trades: signal -> next-open entry -> close exit, all positional (no calendar
# look-ahead is possible because everything indexes the ticker's OWN bars)
# ---------------------------------------------------------------------------

def build_ticker_trades(ticker: str, open_s: pd.Series, close_s: pd.Series,
                        volume_s: pd.Series | None, *, th2: float, th5: float,
                        holdings: list[int], curve_days: int = CURVE_DAYS) -> list[dict]:
    """All signals for one ticker as trade rows. Pure and offline.

    Rows where open or close is NaN are dropped jointly, so position i-1/i+1
    always refer to the previous/next bar the ticker actually traded. A signal
    at position t requires t>=5 (for ret5d) and t+1<n (an entry open must
    exist). fwd_H / cum_d are NaN when the exit runs past the data end — such
    trades are excluded from the stats for that horizon (never guessed).
    """
    df = pd.DataFrame({"open": open_s, "close": close_s}).dropna()
    n = len(df)
    if n < 7:
        return []
    idx = df.index
    o = df["open"].to_numpy(dtype=float)
    c = df["close"].to_numpy(dtype=float)
    if volume_s is not None and not volume_s.empty:
        v = volume_s.reindex(idx).to_numpy(dtype=float)
    else:
        v = np.full(n, np.nan)

    rows: list[dict] = []
    for t in range(5, n - 1):
        ret2 = c[t] / c[t - 2] - 1.0
        ret5 = c[t] / c[t - 5] - 1.0
        if ret2 < th2 or ret5 < th5:
            continue
        e = t + 1
        entry = o[e]
        if not np.isfinite(entry) or entry <= 0:
            continue
        prior_v = v[t - RVOL_WINDOW:t]
        if (t >= RVOL_WINDOW and np.isfinite(v[t])
                and np.isfinite(prior_v).all() and prior_v.mean() > 0):
            rvol = float(v[t] / prior_v.mean())
        else:
            rvol = np.nan
        row = {
            "ticker": ticker,
            "signal_date": idx[t],
            "entry_date": idx[e],
            "entry_open": float(entry),
            "ret_2d": float(ret2),
            "ret_5d": float(ret5),
            "rvol": rvol,
            "is_52w_high": bool(t >= HIGH_LOOKBACK - 1
                                and c[t] >= c[t - HIGH_LOOKBACK + 1:t + 1].max()),
        }
        for h in holdings:
            x = e + h
            row[f"fwd_{h}"] = float(c[x] / entry - 1.0) if x < n else np.nan
            row[f"exit_date_{h}"] = idx[x] if x < n else pd.NaT
        for d in range(1, curve_days + 1):
            x = e + d
            row[f"cum_{d}"] = float(c[x] / entry - 1.0) if x < n else np.nan
        rows.append(row)
    return rows


def trade_columns(holdings: list[int], curve_days: int = CURVE_DAYS) -> list[str]:
    cols = ["ticker", "signal_date", "entry_date", "entry_open",
            "ret_2d", "ret_5d", "rvol", "is_52w_high"]
    for h in holdings:
        cols += [f"fwd_{h}", f"exit_date_{h}"]
    cols += [f"cum_{d}" for d in range(1, curve_days + 1)]
    return cols


def build_all_trades(opens: pd.DataFrame, closes: pd.DataFrame, volumes: pd.DataFrame,
                     *, th2: float, th5: float, holdings: list[int],
                     curve_days: int = CURVE_DAYS) -> pd.DataFrame:
    """Trade rows for every ticker present in `closes` (and `opens`)."""
    rows: list[dict] = []
    for t in closes.columns:
        if t not in getattr(opens, "columns", []):
            continue
        vol = volumes[t] if t in getattr(volumes, "columns", []) else None
        rows.extend(build_ticker_trades(t, opens[t], closes[t], vol, th2=th2,
                                        th5=th5, holdings=holdings, curve_days=curve_days))
    return pd.DataFrame(rows, columns=trade_columns(holdings, curve_days))


# ---------------------------------------------------------------------------
# Statistics (all pure numpy/pandas — fully offline-testable)
# ---------------------------------------------------------------------------

def apply_cost(returns: np.ndarray, cost_bps: float) -> np.ndarray:
    """Flat round-trip cost: subtract bps/1e4 from every trade return."""
    return np.asarray(returns, dtype=float) - cost_bps / 1e4


def distribution_stats(returns) -> dict:
    """The strategy-note distribution: n, win rate, mean, MEDIAN, std,
    best/worst, profit factor, avg winner/loser, p10/p25/p75/p90.
    NaNs (exit past data end) are excluded up front."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = int(r.size)
    if n == 0:
        return {"n_trades": 0}
    wins, losses = r[r > 0], r[r < 0]
    loss_sum = float(losses.sum())
    p10, p25, p75, p90 = (float(x) for x in np.percentile(r, [10, 25, 75, 90]))
    return {
        "n_trades": n,
        "win_rate": float((r > 0).mean()),
        "mean": float(r.mean()),
        "median": float(np.median(r)),
        "std": float(r.std(ddof=1)) if n > 1 else 0.0,
        "best": float(r.max()),
        "worst": float(r.min()),
        "profit_factor": (float(wins.sum() / -loss_sum) if loss_sum < 0 else None),
        "avg_winner": float(wins.mean()) if wins.size else None,
        "avg_loser": float(losses.mean()) if losses.size else None,
        "p10": p10, "p25": p25, "p75": p75, "p90": p90,
    }


def equity_max_drawdown(trades: pd.DataFrame, *, holding: int = DRAWDOWN_HOLDING,
                        cost_bps: float = 0.0) -> dict:
    """Max drawdown of a simple sequential equity curve: trades sorted by
    entry date, greedily taking each trade whose entry is strictly after the
    previous exit (non-overlapping), compounding (1+r). This is the only
    portfolio-ish number in the report; everything else treats signals
    independently."""
    fwd, exit_col = f"fwd_{holding}", f"exit_date_{holding}"
    if trades.empty or fwd not in trades.columns:
        return {"max_drawdown": 0.0, "n_trades_used": 0, "final_equity": 1.0}
    usable = trades.dropna(subset=[fwd, exit_col]).sort_values(["entry_date", "ticker"])
    equity = peak = 1.0
    max_dd, used, last_exit = 0.0, 0, None
    for _, row in usable.iterrows():
        if last_exit is not None and row["entry_date"] <= last_exit:
            continue
        equity *= 1.0 + (float(row[fwd]) - cost_bps / 1e4)
        used += 1
        last_exit = row[exit_col]
        peak = max(peak, equity)
        max_dd = max(max_dd, 1.0 - equity / peak)
    return {"max_drawdown": float(max_dd), "n_trades_used": used,
            "final_equity": float(equity)}


def continuation_curve(trades: pd.DataFrame, curve_days: int = CURVE_DAYS) -> dict:
    """Mean AND median cumulative return (from the entry open) at day+1 ..
    day+curve_days after entry, over the signals with a FULL forward window
    (mixing shorter windows would change the sample per day and bend the
    curve for the wrong reason)."""
    cols = [f"cum_{d}" for d in range(1, curve_days + 1)]
    if trades.empty:
        return {"n_signals": 0, "mean": [], "median": []}
    full = trades.dropna(subset=[cols[-1]])
    if full.empty:
        return {"n_signals": 0, "mean": [], "median": []}
    mat = full[cols].to_numpy(dtype=float)
    return {
        "n_signals": int(len(full)),
        "mean": [float(x) for x in mat.mean(axis=0)],
        "median": [float(x) for x in np.median(mat, axis=0)],
    }


def grid_scan(trades: pd.DataFrame, *, th2_list=GRID_TH2, th5_list=GRID_TH5,
              fwd_col: str = f"fwd_{GRID_FWD_DAYS}") -> list[list[dict]]:
    """Threshold grid: `trades` must have been built at thresholds <= the grid
    minima; each cell just re-filters on the stored signal-day ret_2d/ret_5d.
    n_signals counts trades with a valid forward return (gross, cost-free —
    the grid compares signal quality, not net economics)."""
    out = []
    valid = np.isfinite(trades[fwd_col].to_numpy(dtype=float)) if not trades.empty else None
    for th2 in th2_list:
        row = []
        for th5 in th5_list:
            if trades.empty:
                r = np.array([])
            else:
                mask = ((trades["ret_2d"] >= th2) & (trades["ret_5d"] >= th5)).to_numpy() & valid
                r = trades.loc[mask, fwd_col].to_numpy(dtype=float)
            row.append({
                "th2": th2, "th5": th5, "n_signals": int(r.size),
                "mean": float(r.mean()) if r.size else None,
                "median": float(np.median(r)) if r.size else None,
            })
        out.append(row)
    return out


def variant_stats(trades: pd.DataFrame, holding: int = VARIANT_HOLDING) -> list[dict]:
    """Baseline vs +RVOL>=2 (Test C) vs +52-week-high (Test D) at H=`holding`,
    gross of costs (costs shift every variant identically)."""
    fwd = f"fwd_{holding}"
    if trades.empty:
        return [{"variant": name, "n_trades": 0}
                for name in ("baseline", f"rvol>={RVOL_MIN:g}", "52w_high")]
    r = trades[fwd].to_numpy(dtype=float)
    base = np.isfinite(r)
    rvol_ok = base & (trades["rvol"].to_numpy(dtype=float) >= RVOL_MIN)
    high_ok = base & trades["is_52w_high"].to_numpy(dtype=bool)
    out = []
    for name, mask in (("baseline", base),
                       (f"rvol>={RVOL_MIN:g}", rvol_ok),
                       ("52w_high", high_ok)):
        out.append({"variant": name, **distribution_stats(r[mask])})
    return out


def segment_boundaries(dates) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Split the trading calendar by COUNT of dates: first 60% train, next 20%
    validation, last 20% out-of-sample. Returns (train_end, val_end) — the
    last date belonging to each of the first two segments."""
    ds = sorted(pd.to_datetime(pd.Index(dates).unique()))
    n = len(ds)
    if n == 0:
        return None, None
    i1 = max(int(n * TRAIN_FRAC) - 1, 0)
    i2 = max(int(n * (TRAIN_FRAC + VAL_FRAC)) - 1, i1)
    return ds[i1], ds[i2]


def segment_of(entry_date, train_end, val_end) -> str:
    if train_end is None or entry_date <= train_end:
        return "train"
    if entry_date <= val_end:
        return "validation"
    return "oos"


def segment_stats(trades: pd.DataFrame, holdings: list[int], cost_bps: float,
                  train_end, val_end) -> dict:
    """Headline stats (n, win rate, mean, median — net of `cost_bps`) per
    holding period, split train / validation / out-of-sample by ENTRY date.
    Also returns the sign-flip warnings the CLI must print."""
    segments = {"train": {}, "validation": {}, "oos": {}}
    if not trades.empty:
        labels = trades["entry_date"].map(lambda d: segment_of(d, train_end, val_end))
    for name in segments:
        sub = trades[labels == name] if not trades.empty else trades
        for h in holdings:
            r = np.array([]) if sub.empty else sub[f"fwd_{h}"].to_numpy(dtype=float)
            r = apply_cost(r[np.isfinite(r)], cost_bps)
            segments[name][str(h)] = {
                "n_trades": int(r.size),
                "win_rate": float((r > 0).mean()) if r.size else None,
                "mean": float(r.mean()) if r.size else None,
                "median": float(np.median(r)) if r.size else None,
            }
    warnings = []
    for h in holdings:
        tr = segments["train"][str(h)]["mean"]
        oos = segments["oos"][str(h)]["mean"]
        if tr is not None and oos is not None and tr != 0 and np.sign(tr) != np.sign(oos):
            warnings.append(
                f"H={h}: out-of-sample mean ({oos:+.4f}) flips sign vs train "
                f"({tr:+.4f}) — the edge may not be real / may have decayed.")
    return {"train_end": train_end, "val_end": val_end,
            "cost_bps": cost_bps, "segments": segments, "warnings": warnings}


def benchmark_stats(spy_closes: pd.Series, holdings: list[int]) -> dict:
    """SPY buy-and-hold total return over the whole window plus the mean
    H-trading-day return (all overlapping windows) per holding period."""
    s = spy_closes.dropna()
    if s.empty:
        return {"available": False}
    c = s.to_numpy(dtype=float)
    per_h = {}
    for h in holdings:
        per_h[str(h)] = (float((c[h:] / c[:-h] - 1.0).mean()) if len(c) > h else None)
    return {
        "available": True,
        "start": s.index[0].date().isoformat(),
        "end": s.index[-1].date().isoformat(),
        "total_return": float(c[-1] / c[0] - 1.0),
        "mean_return_by_holding": per_h,
    }


# ---------------------------------------------------------------------------
# Report assembly
# ---------------------------------------------------------------------------

LIMITATIONS = [
    "SURVIVORSHIP BIAS: the universe is a snapshot of TODAY's large caps; "
    "names that crashed/were delisted along the way are absent, so results "
    "are biased UP.",
    "NO HISTORICAL MARKET CAP: the screener's cap filter is approximated by "
    "universe membership; no point-in-time cap data is used.",
    "ADJUSTED PRICES: entries (opens) and exits (closes) use auto-adjusted "
    "bars, so dividends and splits are folded into returns rather than "
    "modeled explicitly.",
    "SIMPLISTIC COSTS: a flat round-trip bps haircut per trade; no spread, "
    "slippage, impact or borrow modeling.",
    "SIGNAL STUDY, NOT A PORTFOLIO: every signal is an independent trade, "
    "overlaps allowed, no capital constraint (except the non-overlapping "
    "H=5 equity curve used only for the drawdown figure).",
    "These numbers are for HYPOTHESIS EVALUATION, not expected live returns.",
]


def run_backtest(opens, closes, volumes, spy_closes, *, th2: float, th5: float,
                 holdings: list[int], costs_bps: list[float],
                 run_grid: bool = False, run_variants: bool = False) -> dict:
    """Full offline analysis on already-loaded frames -> report dict."""
    need_holdings = sorted(set(holdings) | {VARIANT_HOLDING, GRID_FWD_DAYS, DRAWDOWN_HOLDING})
    build_th2 = min([th2, *GRID_TH2]) if run_grid else th2
    build_th5 = min([th5, *GRID_TH5]) if run_grid else th5
    all_trades = build_all_trades(opens, closes, volumes, th2=build_th2,
                                  th5=build_th5, holdings=need_holdings)
    if all_trades.empty:
        baseline = all_trades
    else:
        base_mask = (all_trades["ret_2d"] >= th2) & (all_trades["ret_5d"] >= th5)
        baseline = all_trades[base_mask].reset_index(drop=True)

    cost_levels = [0.0] + [c for c in costs_bps if c > 0]

    per_holding = {}
    for h in holdings:
        r = (baseline[f"fwd_{h}"].to_numpy(dtype=float)
             if not baseline.empty else np.array([]))
        r = r[np.isfinite(r)]
        per_holding[str(h)] = {
            f"{c:g}bps": distribution_stats(apply_cost(r, c)) for c in cost_levels
        }

    drawdown = None
    if DRAWDOWN_HOLDING in holdings:
        drawdown = {f"{c:g}bps": equity_max_drawdown(baseline, holding=DRAWDOWN_HOLDING,
                                                     cost_bps=c)
                    for c in cost_levels}

    calendar = closes.index if not closes.empty else pd.DatetimeIndex([])
    train_end, val_end = segment_boundaries(calendar)
    ref_cost = costs_bps[1] if len(costs_bps) > 1 else (costs_bps[0] if costs_bps else 0.0)
    segments = segment_stats(baseline, holdings, ref_cost, train_end, val_end)

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "methodology": {
            "signal": f"close-to-close ret2d >= {th2:g} AND ret5d >= {th5:g}",
            "entry": "OPEN of the day after the signal close (t+1)",
            "exit": "CLOSE of entry day + H trading days",
            "costs": "flat round-trip bps subtracted per trade",
            "overlaps": "allowed — signal study, not portfolio simulation",
        },
        "limitations": LIMITATIONS,
        "params": {"th2": th2, "th5": th5, "holdings": list(holdings),
                   "costs_bps": list(costs_bps)},
        "data": {
            "n_tickers_with_data": int(len(closes.columns)) if not closes.empty else 0,
            "start": calendar[0].date().isoformat() if len(calendar) else None,
            "end": calendar[-1].date().isoformat() if len(calendar) else None,
            "n_trading_days": int(len(calendar)),
        },
        "n_signals": int(len(baseline)),
        "n_tickers_with_signals": (int(baseline["ticker"].nunique())
                                   if not baseline.empty else 0),
        "benchmark_spy": benchmark_stats(spy_closes, holdings),
        "per_holding": per_holding,
        "equity_drawdown_h5": drawdown,
        "continuation_curve": continuation_curve(baseline),
        "train_val_oos": segments,
    }
    if run_grid:
        report["grid"] = grid_scan(all_trades)
    if run_variants:
        report["variants_h10"] = variant_stats(baseline)
    return report


# ---------------------------------------------------------------------------
# Markdown rendering
# ---------------------------------------------------------------------------

def _pct(x, digits=2) -> str:
    return "—" if x is None else f"{x * 100:.{digits}f}%"


def _num(x, digits=2) -> str:
    return "—" if x is None else f"{x:.{digits}f}"


def render_markdown(report: dict) -> str:
    p = report["params"]
    lines = [
        f"# Momentum screen backtest — {report['generated_at_utc']}",
        "",
        "## Limitations & biases — read this first",
        "",
    ]
    lines += [f"- {item}" for item in report["limitations"]]
    m = report["methodology"]
    d = report["data"]
    lines += [
        "",
        "## Setup",
        "",
        f"- Signal: {m['signal']} (thresholds from the screener config)",
        f"- Entry: {m['entry']}; Exit: {m['exit']}; Overlaps: {m['overlaps']}",
        f"- Data: {d['n_tickers_with_data']} tickers, {d['start']} → {d['end']} "
        f"({d['n_trading_days']} trading days)",
        f"- Signals: {report['n_signals']} across "
        f"{report['n_tickers_with_signals']} tickers",
    ]
    b = report["benchmark_spy"]
    if b.get("available"):
        per_h = ", ".join(f"H={h}: {_pct(v)}"
                          for h, v in b["mean_return_by_holding"].items())
        lines += [f"- Benchmark SPY buy-and-hold {b['start']} → {b['end']}: "
                  f"{_pct(b['total_return'])}; mean SPY return per window — {per_h}"]
    else:
        lines += ["- Benchmark SPY: unavailable"]

    lines += ["", "## Per-holding results (rows = round-trip cost)", ""]
    header = ("| cost | n | win% | mean | median | std | best | worst "
              "| PF | avgW | avgL | p10 | p25 | p75 | p90 |")
    sep = "|" + "---|" * 15
    for h in p["holdings"]:
        lines += [f"### Holding H={h} trading days", "", header, sep]
        for cost, s in report["per_holding"][str(h)].items():
            if s.get("n_trades", 0) == 0:
                lines.append(f"| {cost} | 0 | — | — | — | — | — | — | — | — | — | — | — | — | — |")
                continue
            lines.append(
                f"| {cost} | {s['n_trades']} | {_pct(s['win_rate'], 1)} "
                f"| {_pct(s['mean'])} | {_pct(s['median'])} | {_pct(s['std'])} "
                f"| {_pct(s['best'])} | {_pct(s['worst'])} "
                f"| {_num(s['profit_factor'])} | {_pct(s['avg_winner'])} "
                f"| {_pct(s['avg_loser'])} | {_pct(s['p10'])} | {_pct(s['p25'])} "
                f"| {_pct(s['p75'])} | {_pct(s['p90'])} |")
        lines.append("")

    if report.get("equity_drawdown_h5"):
        lines += [f"## Equity-curve max drawdown (H={DRAWDOWN_HOLDING}, "
                  "sequential non-overlapping trades)", "",
                  "| cost | max drawdown | trades used | final equity (start=1.0) |",
                  "|---|---|---|---|"]
        for cost, ddd in report["equity_drawdown_h5"].items():
            lines.append(f"| {cost} | {_pct(ddd['max_drawdown'])} "
                         f"| {ddd['n_trades_used']} | {_num(ddd['final_equity'], 3)} |")
        lines.append("")

    cc = report["continuation_curve"]
    lines += ["## Momentum continuation curve (cumulative return from entry open)", ""]
    if cc["n_signals"] == 0:
        lines += ["No signals with a full forward window.", ""]
    else:
        lines += [f"{cc['n_signals']} signals with a full {len(cc['mean'])}-day window. "
                  "Mean rising = continuation; mean falling = reversal. Compare the "
                  "median: if it sits well below the mean, a few huge winners carry "
                  "the average.", "",
                  "| day after entry | mean cum. return | median cum. return |",
                  "|---|---|---|"]
        for i, (mn, md) in enumerate(zip(cc["mean"], cc["median"]), start=1):
            lines.append(f"| +{i} | {_pct(mn)} | {_pct(md)} |")
        lines.append("")

    if "grid" in report:
        lines += [f"## Threshold grid (n signals / mean / median forward "
                  f"{GRID_FWD_DAYS}d return, gross)", ""]
        th5s = [cell["th5"] for cell in report["grid"][0]]
        lines.append("| th2 \\ th5 | " + " | ".join(f"{x:g}" for x in th5s) + " |")
        lines.append("|---|" + "---|" * len(th5s))
        for row in report["grid"]:
            cells = [f"{c['n_signals']} / {_pct(c['mean'])} / {_pct(c['median'])}"
                     for c in row]
            lines.append(f"| {row[0]['th2']:g} | " + " | ".join(cells) + " |")
        lines.append("")

    if "variants_h10" in report:
        lines += [f"## Variants at H={VARIANT_HOLDING} (gross of costs)", "",
                  "| variant | n | win% | mean | median | PF |", "|---|---|---|---|---|---|"]
        for v in report["variants_h10"]:
            if v.get("n_trades", 0) == 0:
                lines.append(f"| {v['variant']} | 0 | — | — | — | — |")
            else:
                lines.append(f"| {v['variant']} | {v['n_trades']} "
                             f"| {_pct(v['win_rate'], 1)} | {_pct(v['mean'])} "
                             f"| {_pct(v['median'])} | {_num(v['profit_factor'])} |")
        lines.append("")

    seg = report["train_val_oos"]
    tr_end = seg["train_end"]
    va_end = seg["val_end"]
    lines += ["## Train / validation / out-of-sample "
              f"(by entry date, net of {seg['cost_bps']:g} bps)", "",
              f"Split by trading-day count 60/20/20: train ≤ "
              f"{tr_end.date().isoformat() if tr_end is not None else '—'}, validation ≤ "
              f"{va_end.date().isoformat() if va_end is not None else '—'}, OOS after.", "",
              "| H | train n / mean / median | val n / mean / median | oos n / mean / median |",
              "|---|---|---|---|"]
    for h in p["holdings"]:
        cells = []
        for name in ("train", "validation", "oos"):
            s = seg["segments"][name][str(h)]
            cells.append(f"{s['n_trades']} / {_pct(s['mean'])} / {_pct(s['median'])}")
        lines.append(f"| {h} | " + " | ".join(cells) + " |")
    lines.append("")
    for w in seg["warnings"]:
        lines.append(f"**WARNING:** {w}")
    if seg["warnings"]:
        lines.append("")
    lines += [
        "## How to read this",
        "",
        "- Continuation curve up and to the right = momentum continues; "
        "hump then fade = buy strength gets faded — prefer shorter holds.",
        "- Median vs mean: momentum trades are right-skewed; a positive mean "
        "with a negative median means most trades lose and a few big winners "
        "pay for everything — position sizing and patience matter more than "
        "hit rate.",
        "- Compare each holding's mean to the SPY per-window mean: an 'edge' "
        "smaller than the index drift is just beta.",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# JSON sanitizing + output writing
# ---------------------------------------------------------------------------

def _jsonable(obj):
    """Recursively convert numpy scalars / timestamps / NaN so json.dumps
    produces strict JSON (NaN -> null)."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, (np.integer,)):
        return int(obj)
    if isinstance(obj, (np.floating, float)):
        f = float(obj)
        return f if np.isfinite(f) else None
    if isinstance(obj, (pd.Timestamp, datetime)):
        return obj.isoformat()
    if isinstance(obj, np.bool_):
        return bool(obj)
    return obj


def write_report(output_dir: Path, report: dict, markdown: str) -> tuple[Path, Path]:
    """<output>/backtest/report-YYYYMMDD-HHMM.{json,md}, atomic writes."""
    bt_dir = output_dir / "backtest"
    bt_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    json_path = bt_dir / f"report-{stamp}.json"
    md_path = bt_dir / f"report-{stamp}.md"
    for path, payload in ((json_path, json.dumps(_jsonable(report), indent=2)),
                          (md_path, markdown)):
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, path)
    return json_path, md_path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_int_list(raw: str) -> list[int]:
    vals = sorted({int(x) for x in raw.split(",") if x.strip()})
    if not vals or any(v <= 0 for v in vals):
        raise argparse.ArgumentTypeError("expected comma-separated positive integers")
    return vals


def _parse_float_list(raw: str) -> list[float]:
    vals = sorted({float(x) for x in raw.split(",") if x.strip()})
    if any(v < 0 for v in vals):
        raise argparse.ArgumentTypeError("costs must be >= 0 bps")
    return vals


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    parser = argparse.ArgumentParser(
        prog="python -m app.backtest", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--years", type=float, default=8.0,
                        help="years of daily history to backtest (default 8)")
    parser.add_argument("--holding", type=_parse_int_list,
                        default=list(DEFAULT_HOLDINGS),
                        help="comma-separated holding periods in trading days")
    parser.add_argument("--costs", type=_parse_float_list,
                        default=list(DEFAULT_COSTS_BPS),
                        help="comma-separated round-trip costs in bps")
    parser.add_argument("--grid", action="store_true",
                        help="run the th2 x th5 threshold grid")
    parser.add_argument("--variants", action="store_true",
                        help="baseline vs +RVOL>=2 vs +52w-high at H=10")
    parser.add_argument("--refresh-data", action="store_true",
                        help="ignore the on-disk history cache and refetch")
    parser.add_argument("--config", default=DEFAULT_CONFIG_PATH,
                        help="tickers.json (for the screener thresholds)")
    parser.add_argument("--output-dir", default=DEFAULT_OUTPUT_DIR,
                        help="base output dir (cache + reports under backtest/)")
    parser.add_argument("--universe", default=None,
                        help="universe.json path (default: next to the config)")
    args = parser.parse_args(argv)

    config_path = Path(args.config)
    with open(config_path) as f:
        cfg = load_screener_config(json.load(f))
    th2, th5 = cfg["min_return_2d"], cfg["min_return_5d"]

    universe_path = (Path(args.universe) if args.universe
                     else Path(DEFAULT_UNIVERSE_PATH) if DEFAULT_UNIVERSE_PATH
                     else config_path.parent / "universe.json")
    universe = load_universe(universe_path)
    if not universe["tickers"]:
        logger.error("universe %s lists no tickers", universe_path)
        return 1

    output_dir = Path(args.output_dir)
    cache_dir = output_dir / "backtest" / "history"
    opens, closes, volumes, spy_closes, manifest = load_or_fetch_history(
        universe["tickers"], args.years, cache_dir, refresh=args.refresh_data)
    if closes.empty:
        logger.error("no history available for any ticker — nothing to backtest")
        return 1
    if manifest.get("failed_tickers"):
        logger.warning("%d tickers had no data and were skipped: %s",
                       len(manifest["failed_tickers"]),
                       ", ".join(manifest["failed_tickers"][:20]) + (
                           " …" if len(manifest["failed_tickers"]) > 20 else ""))

    report = run_backtest(opens, closes, volumes, spy_closes, th2=th2, th5=th5,
                          holdings=args.holding, costs_bps=args.costs,
                          run_grid=args.grid, run_variants=args.variants)
    report["params"]["years"] = args.years
    report["params"]["universe"] = universe["name"]
    report["data"]["failed_tickers"] = manifest.get("failed_tickers", [])
    report["data"]["cache_fetched_at_utc"] = manifest.get("fetched_at_utc")

    markdown = render_markdown(report)
    json_path, md_path = write_report(output_dir, report, markdown)
    try:
        print(markdown)
    except UnicodeEncodeError:  # narrow-codepage console (e.g. cp1252 on Windows)
        sys.stdout.buffer.write(markdown.encode("utf-8", errors="replace") + b"\n")
    for w in report["train_val_oos"]["warnings"]:
        logger.warning("%s", w)
    logger.info("wrote %s and %s", json_path, md_path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
