"""Tests for the three screener extensions: the DOUBLERS criterion, the
historical-replay CLI (--asof / --backfill), and the signal report card.

All offline: fake frames, injected fetchers, tmp_path outputs. No network,
no yfinance import, no real sleeps.
"""
import json
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.momentum_screener as ms

client = TestClient(main.app)


# ---------------------------------------------------------------------------
# helpers: fake daily bars (mirrors test_screener.py's builders)
# ---------------------------------------------------------------------------

def _series(values, n=None):
    n = n or len(values)
    idx = pd.bdate_range("2025-08-01", periods=n)
    padded = [np.nan] * (n - len(values)) + list(values)
    return pd.Series(padded, index=idx)


def _frame(series: dict, start="2025-08-01") -> pd.DataFrame:
    """series: ticker -> (closes, volumes) -> yf group_by="column" shape."""
    n = max(len(c) for c, _ in series.values())
    idx = pd.bdate_range(start, periods=n)
    data = {}
    for t, (c, v) in series.items():
        data[("Close", t)] = [np.nan] * (n - len(c)) + list(c)
        data[("Volume", t)] = [np.nan] * (n - len(v)) + list(v)
    return pd.DataFrame(data, index=idx)


def _universe(*tickers):
    return {"name": "test-universe", "tickers": list(tickers)}


# Most feature tests exercise the TIGHTENED screen — the optional knobs
# (price / RVOL / $vol / MA), OFF in the shipped defaults, re-enabled at
# their old values so the doubler-vs-momentum gate distinctions stay visible.
CFG = ms.load_screener_config({"screener": {
    "min_price": 10, "min_rvol": 1.5, "min_avg_dollar_vol": 20e6,
    "require_above_ma20": True, "require_above_ma50": True,
}})

# 260 trading days each (>= 252, so 52w highs resolve from the main window).
# DBL doubles over both windows; NINETY only over the 90d one.
DBL = ([50.0] * 197 + [55.0] * 62 + [120.0], [5e6] * 260)
NINETY = ([100.0] * 197 + [50.0] * 62 + [105.0], [5e6] * 260)
CHEAP = ([2.0] * 197 + [2.2] * 62 + [5.0], [1e9] * 260)      # fails min_price
THINVOL = ([50.0] * 197 + [55.0] * 62 + [120.0], [1e3] * 260)  # fails $-vol gate
FLAT = ([100.0] * 260, [5e6] * 260)

# a momentum winner with 260 days of history (exact 2d/5d returns at the end)
WIN260_CLOSES = [100.0] * 254 + [100.0, 105.0, 110.0, 120.0, 126.0, 132.0]
WIN260_VOLUMES = [1e6] * 259 + [3e6]

# the classic 60-day winner from test_screener.py (ramps in the last 5 days)
WIN60_CLOSES = [100.0] * 54 + [100.0, 105.0, 110.0, 120.0, 126.0, 132.0]
WIN60_VOLUMES = [1e6] * 59 + [3e6]


def _boom(_):
    raise AssertionError("this fetcher must not be called")


# ---------------------------------------------------------------------------
# A. doubler window math + gates
# ---------------------------------------------------------------------------

def test_trading_day_derivation():
    # round(window * 252/365), per the documented formula
    assert ms.trading_days_for_window(90) == 62
    assert ms.trading_days_for_window(270) == 186
    assert ms.trading_days_for_window(365) == 252
    assert ms.trading_days_for_window(1) == 1     # floor of 1 bar


def test_window_returns_exact_and_none_when_short():
    closes, _ = DBL
    r = ms.compute_window_returns(_series(closes), [90, 270])
    # ret_90d = 120 / close[-63] - 1 = 120/55 - 1;  ret_270d = 120/50 - 1
    assert r["ret_90d"] == pytest.approx(round(120 / 55 - 1, 4))
    assert r["ret_270d"] == pytest.approx(1.4)
    short = ms.compute_window_returns(_series([50.0] * 37 + [55.0] * 62 + [120.0]), [90, 270])
    assert short["ret_90d"] == pytest.approx(round(120 / 55 - 1, 4))
    assert short["ret_270d"] is None              # only 100 closes < 187 bars


def test_doubler_window_hits_logic():
    assert ms.doubler_window_hits({"ret_90d": 1.2, "ret_270d": 1.4}, [90, 270], 1.0) == ["90d", "270d"]
    assert ms.doubler_window_hits({"ret_90d": 1.0, "ret_270d": 0.5}, [90, 270], 1.0) == ["90d"]
    assert ms.doubler_window_hits({"ret_90d": 0.99, "ret_270d": 0.99}, [90, 270], 1.0) == []
    # None (insufficient history) never counts as a hit
    assert ms.doubler_window_hits({"ret_90d": None, "ret_270d": 1.5}, [90, 270], 1.0) == ["270d"]


def test_doubler_gates_price_and_dollar_volume_only():
    ok = {"price": 120.0, "avg_dollar_vol": 3e8}
    assert ms.passes_doubler_gates(ok, CFG) is True
    assert ms.passes_doubler_gates({**ok, "price": 9.99}, CFG) is False
    assert ms.passes_doubler_gates({**ok, "avg_dollar_vol": 19e6}, CFG) is False


def test_doubler_gates_off_by_default():
    """Shipped defaults: both doubler knobs are 0 = off, so nothing
    pre-gates a doubler (its hard gate is the window return + cap check)."""
    loose = ms.load_screener_config({})
    assert ms.passes_doubler_gates({"price": 0.5, "avg_dollar_vol": 1e3}, loose) is True


def test_doubler_config_defaults_and_overrides():
    cfg = ms.load_screener_config({})
    assert cfg["doubler_windows_days"] == [90, 270]
    assert cfg["doubler_min_return"] == 1.0
    cfg = ms.load_screener_config({"screener": {
        "doubler_windows_days": [60, "bogus", 120], "doubler_min_return": 0.5}})
    assert cfg["doubler_windows_days"] == [60, 120]
    assert cfg["doubler_min_return"] == 0.5
    cfg = ms.load_screener_config({"screener": {
        "doubler_windows_days": "not-a-list", "doubler_min_return": "nope"}})
    assert cfg["doubler_windows_days"] == [90, 270]
    assert cfg["doubler_min_return"] == 1.0


def test_run_screen_doublers_end_to_end():
    frame = _frame({"DBL": DBL, "NINETY": NINETY, "CHEAP": CHEAP,
                    "THINVOL": THINVOL, "FLAT": FLAT})
    cap_calls = []

    def fake_cap(t):
        cap_calls.append(t)
        return 50e9

    doc = ms.run_screen(CFG, _universe("DBL", "NINETY", "CHEAP", "THINVOL", "FLAT"),
                        fetch=lambda b: frame, fetch_market_cap=fake_cap,
                        fetch_52w=_boom)  # no momentum candidate -> never called
    assert doc["passed_filters"] == 0                     # RVOL 1.0 fails momentum
    assert [d["ticker"] for d in doc["doublers"]] == ["DBL", "NINETY"]  # ranked by max ret
    assert sorted(cap_calls) == ["DBL", "NINETY"]         # caps fetched for doublers too
    dbl, ninety = doc["doublers"]
    assert dbl["window_hit"] == "both"
    assert dbl["ret_90d"] == pytest.approx(round(120 / 55 - 1, 4))
    assert dbl["ret_270d"] == pytest.approx(1.4)
    assert dbl["rvol"] == pytest.approx(1.0)
    assert dbl["market_cap"] == 50e9 and dbl["cap_unknown"] is False
    assert dbl["new_52w_high"] is True                    # from the main 260d window
    assert ninety["window_hit"] == "90d"
    assert ninety["ret_270d"] == pytest.approx(0.05)      # below min -> not a hit
    # criteria block documents the doubler settings
    assert doc["criteria"]["doubler_windows_days"] == [90, 270]
    assert doc["criteria"]["doubler_min_return"] == 1.0


def test_doubler_market_cap_same_as_momentum_finalists():
    frame = _frame({"DBL": DBL})
    doc = ms.run_screen(CFG, _universe("DBL"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 1e9, fetch_52w=_boom)
    assert doc["doublers"] == []                          # known small cap dropped
    doc = ms.run_screen(CFG, _universe("DBL"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: None, fetch_52w=_boom)
    (d,) = doc["doublers"]                                # unknown cap kept, flagged
    assert d["market_cap"] is None and d["cap_unknown"] is True


def test_momentum_candidates_gain_window_returns():
    frame = _frame({"WIN": (WIN260_CLOSES, WIN260_VOLUMES)})
    doc = ms.run_screen(CFG, _universe("WIN"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 50e9, fetch_52w=_boom)
    (cand,) = doc["candidates"]
    assert cand["ret_90d"] == pytest.approx(0.32)         # 132/100 - 1
    assert cand["ret_270d"] == pytest.approx(0.32)
    # 260 closes >= 252 -> 52w high resolved from the MAIN window (no 1y fetch)
    assert cand["new_52w_high"] is True
    assert doc["doublers"] == []                          # +32% is not a doubler


def test_short_history_candidate_has_null_window_returns():
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    doc = ms.run_screen(CFG, _universe("WIN"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 50e9,
                        fetch_52w=lambda ts: pd.DataFrame())
    (cand,) = doc["candidates"]
    assert cand["ret_90d"] is None and cand["ret_270d"] is None
    assert cand["new_52w_high"] is None                   # 60 bars: unknowable


# ---------------------------------------------------------------------------
# B. historical replay: --asof slicing (look-ahead guard) + --backfill
# ---------------------------------------------------------------------------

def test_slice_asof_is_inclusive_and_drops_later_rows():
    frame = _frame({"A": ([1.0] * 10, [1.0] * 10)})
    asof = frame.index[3].date()
    sliced = ms._slice_asof(frame, asof)
    assert len(sliced) == 4
    assert sliced.index[-1].date() == asof


def test_asof_uses_only_data_up_to_that_date():
    """Look-ahead guard: WIN only becomes a winner in the last 5 bars; an
    as-of run dated before the ramp must see nothing."""
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    asof = frame.index[54].date()          # last five (ramp) rows are AFTER asof

    live = ms.run_screen(CFG, _universe("WIN"), fetch=lambda b: frame,
                         fetch_market_cap=lambda t: 50e9,
                         fetch_52w=lambda ts: pd.DataFrame())
    assert live["passed_filters"] == 1     # the same frame, unsliced, does hit

    doc = ms.run_screen(CFG, _universe("WIN"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 50e9, fetch_52w=_boom,
                        asof=asof)
    assert doc["candidates"] == [] and doc["doublers"] == []
    assert doc["date"] == asof.isoformat()
    assert doc["backfilled"] is True
    assert "CURRENT market caps" in doc["note"]           # the honest caveat


@pytest.fixture
def screen_env(tmp_path):
    (tmp_path / "tickers.json").write_text(json.dumps({"tickers": ["A", "B"]}))
    (tmp_path / "universe.json").write_text(json.dumps(
        {"name": "mini", "tickers": ["WIN"]}))
    return tmp_path


def test_run_asof_writes_dated_file_only(screen_env):
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    asof = frame.index[-1].date()          # ramp included -> candidate exists
    doc = ms.run_asof(screen_env / "tickers.json", screen_env, asof,
                      universe_path=screen_env / "universe.json",
                      fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9)
    assert doc["backfilled"] is True
    assert doc["candidates"][0]["ticker"] == "WIN"
    assert (screen_env / "screener" / f"{asof.isoformat()}.json").is_file()
    assert not (screen_env / "screener-latest.json").exists()   # latest untouched


def test_backfill_writes_n_files_and_skips_existing(screen_env):
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    dates = [ts.date() for ts in frame.index[-3:]]

    written = ms.run_backfill(screen_env / "tickers.json", screen_env, 3,
                              universe_path=screen_env / "universe.json",
                              fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9)
    assert len(written) == 3
    for d in dates:
        snap = json.loads((screen_env / "screener" / f"{d.isoformat()}.json").read_text())
        assert snap["date"] == d.isoformat()
        assert snap["backfilled"] is True
        assert "CURRENT market caps" in snap["note"]
    assert not (screen_env / "screener-latest.json").exists()

    # second run: everything already on disk -> nothing rewritten
    again = ms.run_backfill(screen_env / "tickers.json", screen_env, 3,
                            universe_path=screen_env / "universe.json",
                            fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9)
    assert again == []

    # --force rewrites all three
    forced = ms.run_backfill(screen_env / "tickers.json", screen_env, 3, force=True,
                             universe_path=screen_env / "universe.json",
                             fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9)
    assert len(forced) == 3


def test_backfill_caches_market_caps_across_dates(screen_env):
    # a doubler that qualifies on the last TWO trading days
    closes = [50.0] * 195 + [55.0] * 62 + [115.0, 118.0, 120.0]
    frame = _frame({"DBL": (closes, [5e6] * 260)})
    (screen_env / "universe.json").write_text(json.dumps(
        {"name": "mini", "tickers": ["DBL"]}))
    calls = []

    def cap(t):
        calls.append(t)
        return 50e9

    written = ms.run_backfill(screen_env / "tickers.json", screen_env, 2,
                              universe_path=screen_env / "universe.json",
                              fetch=lambda b: frame, fetch_market_cap=cap)
    assert len(written) == 2
    for p in written:
        assert json.loads(p.read_text())["doublers"][0]["ticker"] == "DBL"
    assert calls == ["DBL"]                # looked up ONCE for the whole backfill


# ---------------------------------------------------------------------------
# C. signal report card
# ---------------------------------------------------------------------------

def _closes_frame(last_prices: dict, periods=30):
    idx = pd.bdate_range("2026-07-01", periods=periods)
    return pd.DataFrame({t: [p] * periods for t, p in last_prices.items()}, index=idx)


def _write_snap(output_dir, snap_date, candidates=(), doublers=()):
    sdir = output_dir / "screener"
    sdir.mkdir(parents=True, exist_ok=True)
    (sdir / f"{snap_date.isoformat()}.json").write_text(json.dumps(
        {"date": snap_date.isoformat(),
         "candidates": list(candidates), "doublers": list(doublers)}))


def test_report_card_exact_math_and_absent_lookbacks_omitted(tmp_path):
    closes = _closes_frame({"A": 110.0, "B": 180.0, "C": 75.0})
    idx = closes.index
    # 5 trading days back: momentum picks A@100 (+10%) and B@200 (-10%),
    # doubler pick C@50 (+50%)
    _write_snap(tmp_path, idx[-6].date(),
                candidates=[{"ticker": "A", "price": 100.0},
                            {"ticker": "B", "price": 200.0}],
                doublers=[{"ticker": "C", "price": 50.0}])
    # 20 days back: file offset by +2 calendar days (within the +-2 tolerance)
    t20 = idx[-21].date() + timedelta(days=2)
    _write_snap(tmp_path, t20, candidates=[{"ticker": "A", "price": 50.0}])
    # no file anywhere near the 10-day lookback

    card = ms.evaluate_past_signals(tmp_path, closes)
    m5 = card["momentum"]["5"]
    assert m5["n"] == 2
    assert m5["win_rate"] == 0.5
    assert m5["mean"] == pytest.approx(0.0)
    assert m5["median"] == pytest.approx(0.0)
    assert m5["best"] == {"ticker": "A", "ret": 0.1}
    assert m5["worst"] == {"ticker": "B", "ret": -0.1}
    assert m5["snapshot_date"] == idx[-6].date().isoformat()
    d5 = card["doublers"]["5"]
    assert d5 == {"n": 1, "snapshot_date": idx[-6].date().isoformat(),
                  "win_rate": 1.0, "mean": 0.5, "median": 0.5,
                  "best": {"ticker": "C", "ret": 0.5},
                  "worst": {"ticker": "C", "ret": 0.5}}
    m20 = card["momentum"]["20"]
    assert m20["mean"] == pytest.approx(1.2)               # 110/50 - 1
    assert m20["snapshot_date"] == t20.isoformat()         # +-2-day match accepted
    assert "10" not in card["momentum"]                    # no file -> omitted
    assert "20" not in card["doublers"]                    # that snap had no doublers


def test_report_card_picks_nearest_file_within_tolerance(tmp_path):
    closes = _closes_frame({"A": 110.0})
    target = closes.index[-6].date()
    _write_snap(tmp_path, target + timedelta(days=1),
                candidates=[{"ticker": "A", "price": 100.0}])   # |1| day off
    _write_snap(tmp_path, target - timedelta(days=2),
                candidates=[{"ticker": "A", "price": 55.0}])    # |2| days off
    card = ms.evaluate_past_signals(tmp_path, closes)
    assert card["momentum"]["5"]["snapshot_date"] == (target + timedelta(days=1)).isoformat()
    assert card["momentum"]["5"]["mean"] == pytest.approx(0.1)  # graded from the nearest


def test_report_card_empty_states(tmp_path):
    closes = _closes_frame({"A": 110.0})
    assert ms.evaluate_past_signals(tmp_path, closes) == {}     # no screener dir
    assert ms.evaluate_past_signals(tmp_path, pd.DataFrame()) == {}
    # a snapshot whose tickers have no closes today -> nothing gradable
    _write_snap(tmp_path, closes.index[-6].date(),
                candidates=[{"ticker": "GONE", "price": 10.0}])
    assert ms.evaluate_past_signals(tmp_path, closes) == {}


def test_run_daily_screen_attaches_report_card(tmp_path, monkeypatch):
    (tmp_path / "tickers.json").write_text(json.dumps({"tickers": ["A", "B"]}))
    (tmp_path / "universe.json").write_text(json.dumps(
        {"name": "mini", "tickers": ["WIN"]}))
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    # yesterday's picks: WIN recorded at 100; latest close is 132 -> +32%
    _write_snap(tmp_path, frame.index[-6].date(),
                candidates=[{"ticker": "WIN", "price": 100.0}])
    monkeypatch.setattr(ms, "_default_fetch", lambda batch: frame)
    monkeypatch.setattr(ms, "_default_fetch_market_cap", lambda t: 30e9)
    monkeypatch.setattr(ms, "_default_fetch_52w", lambda ts: pd.DataFrame())

    doc = ms.run_daily_screen(tmp_path / "tickers.json", tmp_path,
                              universe_path=tmp_path / "universe.json")
    assert doc["report_card"]["momentum"]["5"]["mean"] == pytest.approx(0.32)
    latest = json.loads((tmp_path / "screener-latest.json").read_text())
    assert latest["report_card"]["momentum"]["5"]["n"] == 1
    assert "doublers" in latest                            # snapshot shape addition


# ---------------------------------------------------------------------------
# dashboard: the two new blocks ship with the page (incl. empty states)
# ---------------------------------------------------------------------------

def test_dashboard_has_doublers_and_report_card_blocks():
    body = client.get("/").text
    assert 'id="doublers-block"' in body        # whole block, hidden for old snaps
    assert 'id="doublers-table"' in body
    assert 'id="doublers-empty"' in body        # its own empty state
    assert "No doublers" in body
    assert 'id="report-card"' in body           # hidden when report_card absent
    assert 'id="report-card-table"' in body
    assert "Report card — how past picks did" in body
    assert "Doublers (≥100% in 90d/270d)" in body
    # ONE caveat line covers both tables
    assert body.count("Discovery screen, not buy signals") == 1
    assert "Applies to both tables" in body
