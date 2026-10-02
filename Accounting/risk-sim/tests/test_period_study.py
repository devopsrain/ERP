"""Historical-period study tools: --backfill-range, --summary (period digest)
and the backtest's --start/--end entry-date window. All offline."""
import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

import app.backtest as bt
import app.momentum_screener as ms
from tests.test_backtest import SIG_CLOSES, _frames, _series
from tests.test_screener_features import WIN60_CLOSES, WIN60_VOLUMES, _frame, screen_env  # noqa: F401


# ---------------------------------------------------------------------------
# --backfill-range
# ---------------------------------------------------------------------------

def test_backfill_range_writes_only_the_window(screen_env):
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    dates = [ts.date() for ts in frame.index]
    start, end = dates[-5], dates[-3]
    written = ms.run_backfill(screen_env / "tickers.json", screen_env,
                              universe_path=screen_env / "universe.json",
                              fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9,
                              start=start, end=end)
    assert [p.stem for p in written] == [d.isoformat() for d in dates[-5:-2]]
    assert not (screen_env / "screener-latest.json").exists()


def test_backfill_range_validation(screen_env):
    frame = _frame({"WIN": (WIN60_CLOSES, WIN60_VOLUMES)})
    with pytest.raises(ValueError):
        ms.run_backfill(screen_env / "tickers.json", screen_env,
                        universe_path=screen_env / "universe.json", fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 50e9, start=date(2026, 1, 10), end=date(2026, 1, 1))
    with pytest.raises(ValueError):
        ms.run_backfill(screen_env / "tickers.json", screen_env, universe_path=screen_env / "universe.json",
                        fetch=lambda b: frame)
    # window with no trading dates in the fetched frame -> nothing written, no crash
    assert ms.run_backfill(screen_env / "tickers.json", screen_env, universe_path=screen_env / "universe.json",
                           fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9,
                           start=date(1999, 1, 1), end=date(1999, 1, 31)) == []


def test_parse_range():
    s, e = ms._parse_range("2025-07-01:2025-12-31")
    assert (s, e) == (date(2025, 7, 1), date(2025, 12, 31))
    s, e = ms._parse_range("2025-07-01")
    assert s == date(2025, 7, 1) and e >= s
    with pytest.raises(ValueError):
        ms._parse_range("2025-12-31:2025-07-01")


# ---------------------------------------------------------------------------
# --summary digest
# ---------------------------------------------------------------------------

def _snap(d, fresh=(), winners=(), queue=None, regime="neutral", spy20=0.02):
    def row(t, price, setup="TREND", score=70, theme="AI hardware supply chain"):
        return {"ticker": t, "price": price, "setup": setup, "theme": theme,
                "score": {"total": score}, "queue": {"tier": "B"}}
    fresh_rows = [row(t, p, setup="BREAKOUT", score=80) for t, p in fresh]
    win_rows = [row(t, p) for t, p in winners]
    rq = queue or {"A": [], "B": [], "C": [], "D": []}
    return {"date": d, "universe": "u", "scanned": 10, "backfilled": True,
            "buckets": {"fresh_momentum": fresh_rows, "established_momentum": [],
                        "long_term_winners": win_rows},
            "candidates": fresh_rows, "doublers": win_rows,
            "research_queue": rq, "regime": {"label": regime},
            "benchmarks": {"SPY": {"ret_20d": spy20}}}


def test_summarize_period_and_markdown(tmp_path):
    out = tmp_path / "screener"; out.mkdir()
    snaps = [
        _snap("2025-07-01", fresh=[("AAA", 100.0)], winners=[("SNDK", 500.0)],
              queue={"A": [{"ticker": "AAA"}], "B": [], "C": [], "D": []}, regime="risk_on"),
        _snap("2025-07-02", winners=[("SNDK", 510.0)], regime="risk_on"),
        _snap("2025-07-03", fresh=[("BBB", 50.0)], winners=[("SNDK", 520.0), ("MU", 90.0)],
              queue={"A": [{"ticker": "AAA"}], "B": [{"ticker": "BBB"}], "C": [], "D": []}),
        _snap("2025-07-07", winners=[("SNDK", 530.0), ("AAA", 120.0), ("BBB", 45.0)]),
        # outside the window: must be ignored
        _snap("2025-08-01", fresh=[("ZZZ", 1.0)]),
    ]
    for s in snaps:
        (out / f"{s['date']}.json").write_text(json.dumps(s))
    (out / "digest-old.json").write_text("{}")   # non-dated file is skipped

    loaded = ms.load_snapshots(tmp_path, date(2025, 7, 1), date(2025, 7, 31))
    assert [s["date"] for s in loaded] == ["2025-07-01", "2025-07-02", "2025-07-03", "2025-07-07"]

    rep = ms.summarize_period(loaded)
    assert rep["days"] == 4 and rep["start"] == "2025-07-01" and rep["end"] == "2025-07-07"
    assert rep["hit_days"] == {"n": 2, "share": 0.5, "first": "2025-07-01", "last": "2025-07-03"}
    assert rep["counts"]["fresh_momentum"]["total"] == 2
    assert rep["counts"]["long_term_winners"]["max"] == 3
    assert rep["queue"]["A"] == [{"ticker": "AAA", "days": 2}]
    assert rep["distinct_queue_a"] == 1
    assert rep["regimes"] == {"risk_on": 2, "neutral": 2}
    assert rep["persistent"]["long_term_winners"][0] == {"ticker": "SNDK", "days": 4}
    assert rep["setups"]["TREND"] == 7 and rep["setups"]["BREAKOUT"] == 2
    # forward returns: AAA 100 -> 120 (+20%), BBB 50 -> 45 (-10%)
    fwd = {f["ticker"]: f["ret_to_period_end"] for f in rep["fresh_signals"]}
    assert fwd == {"AAA": pytest.approx(0.2), "BBB": pytest.approx(-0.1)}
    assert rep["fresh_signal_stats"]["n"] == 2 and rep["fresh_signal_stats"]["win_rate"] == 0.5
    assert rep["spy_20d_mean"] == pytest.approx(0.02)

    md = ms.render_period_markdown(rep)
    assert "2025-07-01 → 2025-07-07" in md and "AAA (2)" in md and "SNDK (4d)" in md
    assert "| 2025-07-01 | AAA | BREAKOUT |" in md

    # run_period_summary writes digest files next to the snapshots
    rep2 = ms.run_period_summary(tmp_path, date(2025, 7, 1), date(2025, 7, 31))
    assert rep2["days"] == 4
    assert (out / "digest-2025-07-01-2025-07-31.md").is_file()
    assert json.loads((out / "digest-2025-07-01-2025-07-31.json").read_text())["days"] == 4


def test_summary_handles_legacy_and_empty():
    assert ms.summarize_period([]) == {"days": 0}
    assert "run --backfill-range" in ms.render_period_markdown({"days": 0})
    legacy = {"date": "2025-07-01", "candidates": [{"ticker": "X", "price": 10.0}], "doublers": []}
    rep = ms.summarize_period([legacy])
    assert rep["counts"]["fresh_momentum"]["total"] == 1 and rep["fresh_signal_stats"]["n"] == 1


# ---------------------------------------------------------------------------
# backtest --start/--end
# ---------------------------------------------------------------------------

def test_backtest_entry_window_filters_trades():
    opens, highs, lows, closes, volumes = _frames({"SYN": SIG_CLOSES, "FLAT": [100.0] * 16})
    spy = _series([100.0] * 16)
    full = bt.run_backtest(opens, highs, lows, closes, volumes, spy, th2=0.10, th5=0.30,
                           holdings=[1], costs_bps=[0.0])
    assert full["n_signals"] == 1
    entry = pd.Timestamp(closes.index[0])  # any window ending before the only entry -> no trades
    before = bt.run_backtest(opens, highs, lows, closes, volumes, spy, th2=0.10, th5=0.30,
                             holdings=[1], costs_bps=[0.0], end=entry.date())
    assert before["n_signals"] == 0
    assert before["params"]["window_end"] == str(entry.date())
    inside = bt.run_backtest(opens, highs, lows, closes, volumes, spy, th2=0.10, th5=0.30,
                             holdings=[1], costs_bps=[0.0],
                             start=closes.index[0].date(), end=closes.index[-1].date())
    assert inside["n_signals"] == 1
    assert "Study window (entry dates)" in bt.render_markdown(inside)
