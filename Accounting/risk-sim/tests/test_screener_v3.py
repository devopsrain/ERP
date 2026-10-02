"""v3 research-funnel tests for the momentum screener: per-row metrics
(trading-bar returns, relative strength, trend states, 52w distance, RVOL
classes/trend, liquidity tiers, acceleration, setups, flags/confirmations,
absolute score, queue tiers, freshness, catalyst, data quality), the
list-level blocks (regime, theme breadth, research queue, activity, report
card extensions) and an offline end-to-end run_screen contract check.

All offline: fake frames, injected fetchers, tmp_path caches. No network.
"""
import json
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.momentum_screener as ms

client = TestClient(main.app)

TODAY = date(2026, 10, 2)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _series(values, start="2025-08-01"):
    idx = pd.bdate_range(start, periods=len(values))
    return pd.Series(list(values), index=idx, dtype=float)


def _frame(series: dict, start="2025-08-01") -> pd.DataFrame:
    """ticker -> (closes, volumes) -> yf group_by="column" shaped frame."""
    n = max(len(c) for c, _ in series.values())
    idx = pd.bdate_range(start, periods=n)
    data = {}
    for t, (c, v) in series.items():
        data[("Close", t)] = [np.nan] * (n - len(c)) + list(c)
        data[("Volume", t)] = [np.nan] * (n - len(v)) + list(v)
    return pd.DataFrame(data, index=idx)


def _geo(start, rate, n):
    return [start * (1.0 + rate) ** k for k in range(n)]


# exact 5/20/60-bar anchors: close[-61]=100, close[-21]=120, close[-6]=150, close[-1]=165
LADDER = [90.0] * 199 + [100.0] + [110.0] * 39 + [120.0] + [130.0] * 14 + [150.0] + [160.0] * 4 + [165.0]
FLAT_VOL = [1e6] * 260


# ---------------------------------------------------------------------------
# 1. trading-bar returns + benchmark / relative strength maths
# ---------------------------------------------------------------------------

def test_bar_returns_5_20_60_exact_and_none_when_short():
    s = _series(LADDER)
    assert ms.bar_return(s, 5) == pytest.approx(0.10)       # 165/150
    assert ms.bar_return(s, 20) == pytest.approx(0.375)     # 165/120
    assert ms.bar_return(s, 60) == pytest.approx(0.65)      # 165/100
    assert ms.bar_return(_series([100.0] * 30), 60) is None
    m = ms.compute_ticker_metrics(s, _series(FLAT_VOL))
    assert m["ret_5d"] == pytest.approx(0.10)
    assert m["ret_20d"] == pytest.approx(0.375)
    assert m["ret_60d"] == pytest.approx(0.65)
    # 51-60 bars: enough for the screen, too short for ret_60d
    short = ms.compute_ticker_metrics(_series([100.0] * 55), _series([1e6] * 55))
    assert short["ret_60d"] is None and short["ret_20d"] == 0.0


def test_benchmark_returns_and_relative_strength():
    frame = _frame({"SPY": (LADDER, FLAT_VOL)})
    closes = frame["Close"]
    b = ms.benchmark_returns(closes, "SPY")
    assert b["ret_5d"] == pytest.approx(0.10) and b["ret_20d"] == pytest.approx(0.375)
    assert set(b) == {"ret_5d", "ret_20d", "ret_60d", "ret_90d", "ret_270d"}
    assert ms.benchmark_returns(closes, "QQQ") == {k: None for k in b}   # missing column

    row = {"ret_5d": 0.30, "ret_20d": 0.40, "ret_90d": 0.50}
    rs, rs_qqq = ms.relative_strength(row, {"SPY": b, "QQQ": {"ret_20d": 0.10}})
    assert rs["5d"] == pytest.approx(0.20)
    assert rs["20d"] == pytest.approx(0.40 - 0.375)
    assert rs["90d"] == pytest.approx(0.50 - b["ret_90d"])
    assert rs_qqq == pytest.approx(0.30)
    rs_none, q_none = ms.relative_strength(row, {})
    assert rs_none == {"5d": None, "20d": None, "90d": None} and q_none is None


def test_sector_relative_strength_needs_three_known_peers():
    theme_map = {"A": "Semis", "B": "Semis", "C": "Semis", "D": "Semis", "E": "Energy", "F": "Unknown"}
    ret20 = {"A": 0.30, "B": 0.10, "C": 0.20, "D": 0.00, "E": 0.5, "F": 0.9}
    assert ms.sector_relative_strength("A", theme_map, ret20) == pytest.approx(0.30 - 0.10)   # mean(B,C,D)=0.10
    assert ms.sector_relative_strength("E", theme_map, ret20) is None      # no peers
    assert ms.sector_relative_strength("F", theme_map, ret20) is None      # Unknown theme
    theme_map["D"] = "Other"
    assert ms.sector_relative_strength("A", theme_map, ret20) is None      # only 2 peers left


def test_with_benchmarks_dedupes_and_keeps_order():
    assert ms.with_benchmarks(["AAA", "SPY", "BBB"]) == ["AAA", "SPY", "BBB", "QQQ"]
    assert ms.BENCHMARK_TICKERS == ("SPY", "QQQ")


# ---------------------------------------------------------------------------
# 2. trend states, 52w distance, RVOL classes/trend, liquidity tiers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("price,ma20,ma50,state", [
    (105, 100, 90, "strong_uptrend"),   # price > MA20 > MA50
    (105, 100, 102, "uptrend"),         # above both, MA20 <= MA50
    (99, 100, 90, "mixed"),             # above MA50 only (pullback into MA20)
    (101, 100, 110, "mixed"),           # above MA20 only (bounce under MA50)
    (80, 100, 90, "downtrend"),         # under both
    (100, 100, 100, "downtrend"),       # AT both averages is not "above"
])
def test_trend_states(price, ma20, ma50, state):
    t = ms.classify_trend(price, ma20, ma50)
    assert t["state"] == state
    assert t["above_ma20"] == (price > ma20) and t["above_ma50"] == (price > ma50)
    assert t["ma20_gt_ma50"] == (ma20 > ma50)
    assert t["dist_ma20"] == pytest.approx(price / ma20 - 1, abs=1e-4)


def test_trend_unknown_without_inputs():
    assert ms.classify_trend(None, 100, 90)["state"] == "unknown"
    assert ms.classify_trend(100, 0, 90)["state"] == "unknown"


def test_pct_from_52w_high_and_days_since():
    closes = [100.0] * 250 + [200.0] + [150.0] * 9        # 260 bars, high 10 bars ago
    h = ms.high_52w_stats(_series(closes))
    assert h["high_52w"] == 200.0
    assert h["pct_from_52w_high"] == pytest.approx(-0.25)
    assert h["days_since_52w_high"] == 9
    assert h["new_52w_high"] is False
    at_high = ms.high_52w_stats(_series([100.0] * 259 + [120.0]))
    assert at_high["new_52w_high"] is True and at_high["days_since_52w_high"] == 0
    assert at_high["pct_from_52w_high"] == 0.0
    assert ms.high_52w_stats(_series([100.0] * 100)) == {
        "high_52w": None, "pct_from_52w_high": None, "days_since_52w_high": None, "new_52w_high": None}


@pytest.mark.parametrize("rvol,label", [
    (0.5, "weak"), (0.69, "weak"), (0.7, "normal"), (1.19, "normal"), (1.2, "confirmed"),
    (1.99, "confirmed"), (2.0, "high"), (2.99, "high"), (3.0, "exceptional"), (7.5, "exceptional"),
    (None, "unknown"),
])
def test_rvol_classes(rvol, label):
    assert ms.classify_rvol(rvol) == label


def test_rvol_trend_ratio_and_labels():
    rising = _series([1e6] * 20 + [1.5e6] * 5)
    falling = _series([1e6] * 20 + [0.5e6] * 5)
    assert ms.rvol_trend_ratio(rising) == pytest.approx(1.5)
    assert ms.rvol_trend_label(1.5) == "rising"
    assert ms.rvol_trend_ratio(falling) == pytest.approx(0.5)
    assert ms.rvol_trend_label(0.5) == "falling"
    assert ms.rvol_trend_label(1.0) == "flat" and ms.rvol_trend_label(None) == "unknown"
    assert ms.rvol_trend_ratio(_series([1e6] * 24)) is None       # needs 25 bars
    m = ms.compute_ticker_metrics(_series([100.0] * 60), _series([1e6] * 55 + [1.5e6] * 5))
    assert m["rvol_trend"] == pytest.approx(1.5) and m["rvol_trend_label"] == "rising"
    assert m["rvol_class"] == "confirmed"                            # 1.5e6 / 1.1e6 ~ 1.36


@pytest.mark.parametrize("dv,tier", [
    (600e6, "A"), (500e6, "B"), (100.1e6, "B"), (100e6, "C"), (20.1e6, "C"), (20e6, "D"),
    (1e6, "D"), (None, "D"),
])
def test_liquidity_tiers(dv, tier):
    assert ms.liquidity_tier(dv) == tier


def test_liquidity_tier_uses_the_20d_median_not_the_mean():
    # 19 thin days + one monster day: the mean says tier B, the median says D
    vols = [1e5] * 59 + [2e7]
    m = ms.compute_ticker_metrics(_series([100.0] * 60), _series(vols))
    assert m["avg_dollar_vol"] == pytest.approx((19 * 1e7 + 2e9) / 20)
    assert m["median_dollar_vol_20d"] == pytest.approx(1e7)
    assert m["liquidity_tier"] == "D"


# ---------------------------------------------------------------------------
# 3. acceleration + setups
# ---------------------------------------------------------------------------

def test_acceleration_labels_including_pulling_back():
    assert ms.classify_acceleration(0.10, 0.20, 0.30)["label"] == "accelerating"   # pace 5 > 20 > 60 > 0
    assert ms.classify_acceleration(0.05, 0.05, 0.30)["label"] == "steady"         # pace20 < pace60
    assert ms.classify_acceleration(0.00, 0.20, 0.30)["label"] == "decelerating"   # pace5 < pace20, 20d > 0
    assert ms.classify_acceleration(-0.05, 0.20, 0.30)["label"] == "pulling_back"
    assert ms.classify_acceleration(None, 0.10, 0.20)["label"] == "unknown"
    assert ms.classify_acceleration(None, -0.10, 0.20)["label"] == "pulling_back"  # fallback rule
    assert ms.classify_acceleration(0.10, None, None)["label"] == "unknown"
    a = ms.classify_acceleration(0.10, 0.20, None)
    assert a["label"] == "steady" and a["pace_60d"] is None          # cannot prove acceleration
    acc = ms.classify_acceleration(0.10, 0.20, 0.30)
    assert acc["pace_5d"] == pytest.approx(1.10 ** 0.2 - 1, abs=1e-6)
    assert acc["pace_20d"] == pytest.approx(1.20 ** 0.05 - 1, abs=1e-6)


def _row(**kw):
    base = {"ret_5d": 0.01, "ret_20d": 0.02, "ret_60d": 0.03, "rvol": 1.0, "dist_ma20": 0.01,
            "pct_from_52w_high": -0.10, "rvol_trend": 1.0,
            "trend": {"state": "mixed", "above_ma20": True, "above_ma50": True, "ma20_gt_ma50": False},
            "acceleration": {"label": "steady"}}
    base.update(kw)
    return base


@pytest.mark.parametrize("kw,setup", [
    (dict(ret_5d=0.30, rvol=3.0, pct_from_52w_high=0.0), "EXHAUSTION"),            # beats BREAKOUT
    (dict(ret_5d=0.10, dist_ma20=0.30, rvol=2.6), "EXHAUSTION"),                   # via extension
    (dict(ret_5d=0.08, rvol=1.6, pct_from_52w_high=-0.01), "BREAKOUT"),
    (dict(acceleration={"label": "accelerating"}, ret_20d=0.10, rvol_trend=1.5), "ACCELERATION"),
    (dict(ret_60d=0.20, ret_5d=-0.03), "PULLBACK"),
    (dict(trend={"state": "strong_uptrend", "above_ma50": True}, ret_20d=0.05, ret_60d=0.10), "TREND"),
    (dict(trend={"state": "downtrend", "above_ma50": False}, ret_60d=-0.20, ret_5d=0.04, rvol=1.3),
     "REVERSAL_WATCH"),
    (dict(), "NONE"),
    (dict(ret_5d=None, ret_20d=None, ret_60d=None, pct_from_52w_high=None), "NONE"),  # unknowns never match
])
def test_setup_rules_and_order(kw, setup):
    assert ms.classify_setup(_row(**kw)) == setup


def test_setups_from_constructed_series():
    # BREAKOUT: new 52w high on a +10% week with 2x volume (below the 2.5x exhaustion bar)
    bo_c = [100.0] * 254 + [100.0, 102.0, 104.0, 106.0, 108.0, 110.0]
    bo_v = [1e6] * 259 + [2e6]
    row = ms.enrich_row_v3({"ticker": "BO"}, closes=_series(bo_c), volumes=_series(bo_v))
    assert row["setup"] == "BREAKOUT" and row["new_52w_high"] is True
    # EXHAUSTION: +32% in 5 bars on 3x volume
    ex_c = [100.0] * 254 + [100.0, 105.0, 110.0, 120.0, 126.0, 132.0]
    ex_v = [1e6] * 259 + [3e6]
    row = ms.enrich_row_v3({"ticker": "EX"}, closes=_series(ex_c), volumes=_series(ex_v))
    assert row["setup"] == "EXHAUSTION" and row["queue"]["tier"] == "D"
    # TREND: steady 0.5%/bar grind, quiet volume -> stacked MAs, positive 20/60d
    tr_c = [100.0] * 100 + _geo(100.0, 0.005, 160)
    row = ms.enrich_row_v3({"ticker": "TR"}, closes=_series(tr_c), volumes=_series(FLAT_VOL))
    assert row["trend"]["state"] == "strong_uptrend" and row["setup"] == "TREND"
    # PULLBACK: long uptrend, last week red (20d still green), still above MA50
    pb_c = [100.0] * 100 + _geo(100.0, 0.005, 155) + [214.0, 212.0, 210.0, 209.0, 208.0]
    row = ms.enrich_row_v3({"ticker": "PB"}, closes=_series(pb_c), volumes=_series(FLAT_VOL))
    assert row["setup"] == "PULLBACK" and row["acceleration"]["label"] == "pulling_back"
    assert row["queue"]["tier"] == "C"


# ---------------------------------------------------------------------------
# 4. risk flags / confirmations / score / queue
# ---------------------------------------------------------------------------

def test_risk_flags_every_rule():
    row = {"dist_ma20": 0.30, "rvol_trend": 0.5, "ret_5d": 0.50, "liquidity_tier": "D",
           "theme": "AI hardware supply chain", "pct_from_52w_high": -0.30,
           "rs": {"20d": -0.10}, "data_quality": {"checked": True, "status": "warning"}, "rvol": 2.0}
    assert ms.risk_flags_for(row, theme_share=0.7) == [
        "extended_ma20", "rvol_falling", "big_5d_move", "low_liquidity", "crowded_theme",
        "far_from_high", "weak_rs", "data_quality"]
    assert ms.risk_flags_for({"ret_5d": -0.02, "rvol": 1.6}) == ["momentum_divergence"]
    assert ms.risk_flags_for({"ret_5d": -0.02, "rvol": 1.4}) == []
    # crowded needs a KNOWN theme and a share at/over the warn share
    assert ms.risk_flags_for({"theme": "Unknown"}, theme_share=0.9) == []
    assert ms.risk_flags_for({"theme": "Energy"}, theme_share=0.59) == []
    assert ms.risk_flags_for({"theme": "Energy"}, theme_share=0.60) == ["crowded_theme"]
    assert ms.risk_flags_for({}) == []


def test_confirmations_every_rule():
    row = {"new_52w_high": True, "rvol": 1.5, "trend": {"ma20_gt_ma50": True},
           "rs": {"5d": 0.01, "20d": 0.02}, "catalyst": {"kind": "earnings"}}
    assert ms.confirmations_for(row) == ["new_52w_high", "rvol_confirmed", "ma20_gt_ma50",
                                         "rs_positive", "rs_improving", "earnings_catalyst"]
    assert ms.confirmations_for({"rs": {"5d": 0.05, "20d": -0.01}}) == []     # improving needs both > 0
    assert ms.confirmations_for({"new_52w_high": None, "rvol": 1.49}) == []


def test_score_components_bounds_and_scaling():
    full = {"ret_5d": 0.30, "ret_20d": 0.30, "ret_60d": 0.50, "rvol": 2.0, "pct_from_52w_high": 0.0,
            "trend": {"state": "strong_uptrend"}, "acceleration": {"label": "accelerating"},
            "rs": {"20d": 0.20}}
    s = ms.score_row(full)
    assert s["total"] == 100
    assert {k: v["pts"] for k, v in s["components"].items()} == {
        "short_momentum": 25, "medium_momentum": 20, "trend": 15, "volume": 10,
        "high_proximity": 10, "acceleration": 10, "relative_strength": 10}
    assert {k: v["max"] for k, v in s["components"].items()} == ms.SCORE_MAX

    zero = {"ret_5d": -0.10, "ret_20d": 0.0001, "ret_60d": -0.10, "rvol": 0.4, "pct_from_52w_high": -0.30,
            "trend": {"state": "downtrend"}, "acceleration": {"label": "pulling_back"}, "rs": {"20d": -0.10}}
    assert ms.score_row(zero)["total"] == 0

    mid = {"ret_5d": 0.15, "ret_20d": 0.15, "ret_60d": None, "rvol": 1.25, "pct_from_52w_high": None,
           "trend": {"state": "uptrend"}, "acceleration": {"label": "steady"}, "rs": {"20d": None}}
    c = ms.score_row(mid)["components"]
    assert c["short_momentum"]["pts"] == 12.5          # 0.15/0.30 * 25
    assert c["medium_momentum"]["pts"] == 6.0          # 0.15/0.30 * 12 + 0 (ret_60d null)
    assert c["trend"]["pts"] == 11
    assert c["volume"]["pts"] == 5.0                   # (1.25-0.5)/1.5 * 10
    assert c["high_proximity"]["pts"] == 5.0           # null -> half
    assert c["acceleration"]["pts"] == 6
    assert c["relative_strength"]["pts"] == 5.0        # null -> half
    assert ms.score_row(mid)["total"] == 50            # 12.5+6+11+5+5+6+5 = 50.5 -> banker's rounding
    # high_proximity: 0 at -25% or worse, linear up to the high
    assert ms.score_row({**mid, "pct_from_52w_high": -0.25})["components"]["high_proximity"]["pts"] == 0.0
    assert ms.score_row({**mid, "pct_from_52w_high": -0.125})["components"]["high_proximity"]["pts"] == 5.0
    # negative short-term return scores 0, never negative
    assert ms.score_row({**mid, "ret_5d": -0.5})["components"]["short_momentum"]["pts"] == 0.0
    assert ms.score_row({})["total"] == 15             # unknown everything: 5 + 5 + 5 halves


def _queue_row(score=60, trend="strong_uptrend", tier="A", setup="TREND", flags=(), rs20=0.05,
               ret_20d=0.1, ret_270d=0.5):
    return {"score": {"total": score}, "trend": {"state": trend}, "liquidity_tier": tier,
            "setup": setup, "risk_flags": list(flags), "rs": {"20d": rs20},
            "ret_20d": ret_20d, "ret_270d": ret_270d}


def test_queue_tiers_each_rule():
    assert ms.queue_tier(_queue_row(score=90, trend="downtrend"))["tier"] == "D"
    assert ms.queue_tier(_queue_row(score=90, tier="D"))["tier"] == "D"
    assert ms.queue_tier(_queue_row(score=90, setup="EXHAUSTION"))["tier"] == "D"
    assert ms.queue_tier(_queue_row(score=90, flags=["data_quality"]))["tier"] == "D"
    a = ms.queue_tier(_queue_row(score=75, flags=["crowded_theme"]))
    assert a["tier"] == "A" and "no risk flags" in a["reason"]
    assert ms.queue_tier(_queue_row(score=75, trend="uptrend"))["tier"] == "A"
    assert ms.queue_tier(_queue_row(score=74))["tier"] == "B"                       # score just under
    assert ms.queue_tier(_queue_row(score=80, rs20=-0.01))["tier"] == "B"           # negative RS
    assert ms.queue_tier(_queue_row(score=80, trend="mixed"))["tier"] == "B"
    b = ms.queue_tier(_queue_row(score=80, flags=["extended_ma20"]))
    assert b["tier"] == "B" and "extended_ma20" in b["reason"]
    # the A rule is evaluated BEFORE the C rules: a flawless 75+ pullback stays A ...
    assert ms.queue_tier(_queue_row(score=80, setup="PULLBACK"))["tier"] == "A"
    # ... while below the A bar the C rules beat the B rule
    assert ms.queue_tier(_queue_row(score=70, setup="PULLBACK"))["tier"] == "C"
    assert ms.queue_tier(_queue_row(score=70, ret_270d=1.5, ret_20d=0.0))["tier"] == "C"   # stalling winner
    assert ms.queue_tier(_queue_row(score=70, ret_270d=1.5, ret_20d=0.01))["tier"] == "B"  # still moving
    assert ms.queue_tier(_queue_row(score=55, flags=["weak_rs"], rs20=-0.1))["tier"] == "B"
    assert ms.queue_tier(_queue_row(score=54))["tier"] == "C"
    assert ms.queue_tier({})["tier"] == "C"                                          # nothing known


def test_enrich_row_v3_wires_everything_and_rs_from_benchmarks():
    row = ms.enrich_row_v3({"ticker": "X"}, closes=_series(LADDER), volumes=_series(FLAT_VOL),
                           benchmarks={"SPY": {"ret_5d": 0.02, "ret_20d": 0.03, "ret_90d": 0.04},
                                       "QQQ": {"ret_20d": 0.05}},
                           sector_rs_20d=0.12, theme_share=0.7,
                           catalyst={"kind": "earnings", "earnings_date": "2026-10-05",
                                     "days_since_earnings": None, "days_to_earnings": 3},
                           data_quality={"checked": False})
    for key in ("rs", "rs_qqq_20d", "sector_rs_20d", "trend", "acceleration", "setup", "risk_flags",
                "confirmations", "score", "queue", "catalyst", "data_quality", "ret_5d", "ret_20d",
                "ret_60d", "ret_90d", "ret_270d", "rvol_class", "rvol_trend", "liquidity_tier",
                "high_52w", "pct_from_52w_high", "days_since_52w_high"):
        assert key in row, key
    assert row["rs"]["5d"] == pytest.approx(0.08) and row["rs"]["20d"] == pytest.approx(0.345)
    assert row["rs_qqq_20d"] == pytest.approx(0.325)
    assert row["sector_rs_20d"] == 0.12
    assert "earnings_catalyst" in row["confirmations"]
    assert "crowded_theme" not in row["risk_flags"]       # theme missing -> Unknown -> no crowd flag
    assert row["score"]["total"] == ms.score_row(row)["total"]


# ---------------------------------------------------------------------------
# 5. freshness
# ---------------------------------------------------------------------------

def _hist(days: dict) -> dict:
    return {d: {"fresh_momentum": set(ts), "established_momentum": set(), "long_term_winners": set()}
            for d, ts in days.items()}


def test_freshness_stages_from_injected_history_index():
    idx = _hist({"2026-09-24": ["M"], "2026-09-25": ["M"], "2026-09-28": ["A", "B", "M"],
                 "2026-09-29": ["A", "M"], "2026-09-30": ["A", "M"], "2026-10-01": ["A", "M"],
                 "2026-10-02": ["IGNORED"], "2026-10-03": ["IGNORED"]})   # today/future ignored
    a = ms.freshness_for(idx, "fresh_momentum", "A", TODAY)
    assert a == {"first_seen": "2026-09-28", "days_in_list": 5, "stage": "developing", "history_days": 6}
    m = ms.freshness_for(idx, "fresh_momentum", "M", TODAY)
    assert m["days_in_list"] == 7 and m["stage"] == "mature" and m["first_seen"] == "2026-09-24"
    b = ms.freshness_for(idx, "fresh_momentum", "B", TODAY)        # streak broken yesterday
    assert b == {"first_seen": "2026-10-02", "days_in_list": 1, "stage": "fresh", "history_days": 6}
    # a different bucket is a different streak
    assert ms.freshness_for(idx, "long_term_winners", "A", TODAY)["stage"] == "fresh"
    # two consecutive snapshots = developing (boundary)
    two = ms.freshness_for(_hist({"2026-10-01": ["A"]}), "fresh_momentum", "A", TODAY)
    assert two["days_in_list"] == 2 and two["stage"] == "developing"
    assert ms.freshness_for({}, "fresh_momentum", "A", TODAY)["history_days"] == 0


def test_build_history_index_reads_v3_and_legacy_files(tmp_path):
    sdir = tmp_path / "screener"
    sdir.mkdir()
    (sdir / "2026-09-30.json").write_text(json.dumps({           # legacy shape
        "date": "2026-09-30", "candidates": [{"ticker": "A"}], "doublers": [{"ticker": "D"}]}))
    (sdir / "2026-10-01.json").write_text(json.dumps({           # v3 shape
        "date": "2026-10-01", "buckets": {"fresh_momentum": [{"ticker": "A"}],
                                          "established_momentum": [{"ticker": "E"}],
                                          "long_term_winners": [{"ticker": "D"}]}}))
    (sdir / "2026-10-02.json").write_text(json.dumps({"date": "2026-10-02", "candidates": [{"ticker": "Z"}]}))
    (sdir / "notes.json").write_text("{}")
    (sdir / "2026-09-29.json").write_text("{broken")
    idx = ms.build_history_index(tmp_path, before=TODAY)
    assert sorted(idx) == ["2026-09-30", "2026-10-01"]           # today + junk excluded
    assert idx["2026-09-30"]["fresh_momentum"] == {"A"} and idx["2026-09-30"]["long_term_winners"] == {"D"}
    assert idx["2026-10-01"]["established_momentum"] == {"E"}
    fresh = ms.compute_freshness(tmp_path, "fresh_momentum", ["A", "E"], TODAY)
    assert fresh["A"]["days_in_list"] == 3 and fresh["A"]["first_seen"] == "2026-09-30"
    assert fresh["E"]["stage"] == "fresh"
    assert ms.build_history_index(tmp_path / "nowhere") == {}


# ---------------------------------------------------------------------------
# 6. catalyst window + cache round-trip
# ---------------------------------------------------------------------------

def test_catalyst_window_logic():
    c = ms.catalyst_for(["2026-10-05", "2026-07-01"], TODAY)
    assert c["kind"] == "earnings" and c["earnings_date"] == "2026-10-05"
    assert c["days_to_earnings"] == 3 and c["days_since_earnings"] == 93
    past = ms.catalyst_for([date(2026, 9, 25)], TODAY)                 # 7 days ago -> in window
    assert past["kind"] == "earnings" and past["days_since_earnings"] == 7
    far = ms.catalyst_for(["2026-09-01", "2026-12-01"], TODAY)
    assert far["kind"] == "unknown" and far["earnings_date"] == "2026-09-01"   # nearest kept for context
    assert far["days_since_earnings"] == 31 and far["days_to_earnings"] == 60
    edge = ms.catalyst_for(["2026-10-12"], TODAY)                       # exactly +10 -> in
    assert edge["kind"] == "earnings"
    assert ms.catalyst_for(["2026-10-13"], TODAY)["kind"] == "unknown"  # +11 -> out
    none = ms.catalyst_for(None, TODAY)
    assert none == {"kind": "unknown", "earnings_date": None, "days_since_earnings": None,
                    "days_to_earnings": None, "dates_known": False}
    empty = ms.catalyst_for([], TODAY)
    assert empty["kind"] == "unknown" and empty["dates_known"] is True
    assert ms.catalyst_for(["garbage"], TODAY)["earnings_date"] is None


def test_cached_lookup_ttl_and_round_trip(tmp_path):
    calls = []

    def fetch(t):
        calls.append(t)
        return [date(2026, 10, 5)]

    cache = {}
    got = ms.cached_lookup(cache, "A", TODAY, 7, fetch, "dates", ms._normalize_dates)
    assert got == ["2026-10-05"] and calls == ["A"]
    assert cache["A"] == {"fetched": "2026-10-02", "dates": ["2026-10-05"]}
    # within the TTL: served from the cache, no second fetch
    assert ms.cached_lookup(cache, "A", TODAY + timedelta(days=6), 7, fetch, "dates") == ["2026-10-05"]
    assert calls == ["A"]
    # expired: refetched
    ms.cached_lookup(cache, "A", TODAY + timedelta(days=7), 7, fetch, "dates", ms._normalize_dates)
    assert calls == ["A", "A"]
    # replay mode (fetch=None): stale cache is still used, unknown ticker -> None
    assert ms.cached_lookup(cache, "A", TODAY + timedelta(days=400), 7, None, "dates") == ["2026-10-05"]
    assert ms.cached_lookup(cache, "ZZZ", TODAY, 7, None, "dates") is None

    # a failing fetch keeps the stale value and caches nothing new
    def boom(t):
        raise RuntimeError("yahoo down")
    cache["B"] = {"fetched": "2020-01-01", "dates": ["2020-02-01"]}
    assert ms.cached_lookup(cache, "B", TODAY, 7, boom, "dates") == ["2020-02-01"]
    assert cache["B"]["fetched"] == "2020-01-01"
    assert ms.cached_lookup(cache, "C", TODAY, 7, boom, "dates") is None
    # an EMPTY answer is a valid answer and is cached (no refetch for 7 days)
    assert ms.cached_lookup(cache, "D", TODAY, 7, lambda t: [], "dates", ms._normalize_dates) == []
    assert cache["D"]["dates"] == []

    # file round-trip
    path = tmp_path / ms.CATALYST_CACHE_NAME
    assert ms.save_json_cache(path, cache) == path
    assert ms.load_json_cache(path) == cache
    assert ms.load_json_cache(tmp_path / "missing.json") == {}
    (tmp_path / "bad.json").write_text("{nope")
    assert ms.load_json_cache(tmp_path / "bad.json") == {}
    assert not list(tmp_path.glob("*.tmp"))


# ---------------------------------------------------------------------------
# 7. data quality
# ---------------------------------------------------------------------------

def test_data_quality_warning_on_jump_day_and_ok_otherwise():
    smooth = _geo(10.0, 0.01, 200)                      # +~620% but never a big day
    ok = ms.data_quality_check(_series(smooth), [], bars=186, asof=TODAY)
    assert ok["status"] == "ok" and ok["jump_days"] == [] and ok["splits"] == []
    assert ok["max_daily_move"] == pytest.approx(0.01, abs=1e-4)
    assert ok["checked"] is True

    jump = smooth[:150] + [smooth[149] * 1.6] + [smooth[149] * 1.6] * 49   # one +60% day
    s = _series(jump)
    warn = ms.data_quality_check(s, [], bars=186, asof=TODAY)
    assert warn["status"] == "warning"
    assert warn["jump_days"] == [s.index[150].date().isoformat()]
    assert warn["max_daily_move"] == pytest.approx(0.6, abs=1e-4)
    assert any("jump" in n.lower() or "day" in n.lower() for n in warn["notes"])

    # a split inside the window -> warning (verify), outside -> ignored; None -> noted
    in_split = [{"date": s.index[160].date().isoformat(), "ratio": 10.0}]
    out_split = [{"date": "2020-01-01", "ratio": 2.0}]
    sw = ms.data_quality_check(_series(smooth), in_split, bars=186, asof=TODAY)
    assert sw["status"] == "warning" and sw["splits"] == in_split
    assert ms.data_quality_check(_series(smooth), out_split, bars=186, asof=TODAY)["status"] == "ok"
    unknown = ms.data_quality_check(_series(smooth), None, bars=186, asof=TODAY)
    assert unknown["status"] == "ok" and any("unknown" in n for n in unknown["notes"])


def test_data_quality_trigger_only_for_huge_movers():
    assert ms.needs_data_quality_check({"ret_90d": 2.5, "ret_270d": 0.5}) is True
    assert ms.needs_data_quality_check({"ret_90d": 0.5, "ret_270d": -2.5}) is True
    assert ms.needs_data_quality_check({"ret_90d": 1.9, "ret_270d": 2.0}) is False   # strictly above
    assert ms.needs_data_quality_check({"ret_90d": None, "ret_270d": None}) is False
    # the flag + tier D follow from a warning
    row = ms.enrich_row_v3(_row(), data_quality={"checked": True, "status": "warning"})
    assert "data_quality" in row["risk_flags"] and row["queue"]["tier"] == "D"


# ---------------------------------------------------------------------------
# 8. regime, theme breadth, research queue
# ---------------------------------------------------------------------------

def _m(d20, d50, r20, hi=False):
    return {"dist_ma20": d20, "dist_ma50": d50, "ret_20d": r20, "new_20d_high": hi}


def test_regime_labels_and_breadth_counts():
    up = {f"U{i}": _m(0.05, 0.10, 0.02, hi=(i < 2)) for i in range(7)}
    down = {f"D{i}": _m(-0.05, -0.10, -0.02) for i in range(3)}
    r = ms.compute_regime({**up, **down}, {"SPY": {"ret_20d": 0.03}, "QQQ": {"ret_20d": 0.04}})
    assert r["label"] == "risk_on"
    assert r["pct_above_ma50"] == 0.7 and r["pct_above_ma20"] == 0.7 and r["pct_positive_20d"] == 0.7
    assert r["new_20d_highs"] == 2 and r["n_computed"] == 10
    assert r["spy_20d"] == 0.03 and r["qqq_20d"] == 0.04
    off = ms.compute_regime({**dict(list(up.items())[:3]), **down,
                             **{f"E{i}": _m(-0.01, -0.01, 0.0) for i in range(4)}},
                            {"SPY": {"ret_20d": -0.02}})
    assert off["label"] == "risk_off" and off["pct_above_ma50"] == 0.3
    assert ms.compute_regime(up, {"SPY": {"ret_20d": -0.01}})["label"] == "neutral"   # breadth up, SPY down
    assert ms.compute_regime(up, {})["label"] == "neutral"                           # SPY unknown
    empty = ms.compute_regime({}, {})
    assert empty["label"] == "neutral" and empty["n_computed"] == 0 and empty["pct_above_ma50"] is None


def _ltw(t, theme, r20, d20, cap=1e10, r270=2.0, r5=0.01, hi=True, d50=0.1):
    return {"ticker": t, "theme": theme, "ret_20d": r20, "dist_ma20": d20, "dist_ma50": d50,
            "market_cap": cap, "ret_270d": r270, "ret_5d": r5, "new_52w_high": hi}


def test_theme_breadth_labels_and_shares():
    rows = [_ltw("A", "Semis", 0.1, 0.05, r270=1.0), _ltw("B", "Semis", 0.2, 0.02, r270=3.0),
            _ltw("C", "Semis", -0.1, -0.02, r270=2.0, hi=False, r5=-0.1),
            _ltw("D", "Energy", -0.1, -0.1, cap=3e10, hi=None), _ltw("E", "Energy", -0.2, 0.1),
            _ltw("F", "Gold", 0.1, -0.1)]
    tb = ms.theme_breadth(rows)
    by = {g["theme"]: g for g in tb}
    assert [g["theme"] for g in tb] == ["Semis", "Energy", "Gold"]       # by size, then name
    semis = by["Semis"]
    assert semis["n"] == 3 and semis["share"] == 0.5
    two_thirds = pytest.approx(2 / 3, abs=1e-4)                       # shares are rounded to 4 dp
    assert semis["pct_positive_20d"] == two_thirds and semis["pct_above_ma20"] == two_thirds
    assert semis["breadth"] == "broadening"
    assert semis["avg_270d"] == pytest.approx(2.0) and semis["median_270d"] == pytest.approx(2.0)
    assert semis["pct_new_52w_high"] == two_thirds and semis["pct_positive_5d"] == two_thirds
    assert semis["cap_share"] == pytest.approx(3e10 / 8e10)
    assert by["Energy"]["breadth"] == "narrowing" and by["Energy"]["pct_new_52w_high"] == 1.0  # None skipped
    assert by["Gold"]["breadth"] == "mixed"                      # positive 20d, under MA20
    assert ms.theme_breadth([]) == []


def test_research_queue_each_ticker_once_best_bucket():
    def r(t, total, tier, setup="TREND"):
        return {"ticker": t, "score": {"total": total}, "setup": setup,
                "queue": {"tier": tier, "reason": f"{t}-{tier}"}}
    buckets = {"fresh_momentum": [r("X", 80, "A"), r("Y", 50, "C")],
               "established_momentum": [r("X", 70, "B"), r("Z", 60, "B")],
               "long_term_winners": [r("Y", 90, "A", "BREAKOUT"), r("W", 20, "D")]}
    q = ms.build_research_queue(buckets)
    assert set(q) == {"A", "B", "C", "D"}
    assert [e["ticker"] for e in q["A"]] == ["Y", "X"]                   # score desc
    assert q["A"][0] == {"ticker": "Y", "bucket": "long_term_winners", "score": 90,
                         "setup": "BREAKOUT", "reason": "Y-A"}
    assert q["B"] == [{"ticker": "Z", "bucket": "established_momentum", "score": 60,
                       "setup": "TREND", "reason": "Z-B"}]
    assert q["C"] == [] and [e["ticker"] for e in q["D"]] == ["W"]
    assert ms.build_research_queue({b: [] for b in ms.BUCKET_NAMES}) == {"A": [], "B": [], "C": [], "D": []}


# ---------------------------------------------------------------------------
# 9. activity (hits index) + endpoint
# ---------------------------------------------------------------------------

def _hit(d, nc, fresh=(), est=(), ltw=()):
    return {"date": d, "n_candidates": nc, "n_doublers": len(ltw),
            "top_tickers": {"fresh_momentum": list(fresh), "established_momentum": list(est),
                            "long_term_winners": list(ltw)}}


V3_HITS = [
    _hit("2026-10-02", 0, fresh=[], est=["E1", "E2"], ltw=["L1", "L2"]),
    _hit("2026-10-01", 0, fresh=["F1"], est=["E1"], ltw=["L1", "L2", "L3"]),
    _hit("2026-09-30", 2, fresh=["F1", "F2"], est=["E1"], ltw=["L1"]),
    _hit("2026-09-29", 3, fresh=["F2"], est=["E9"], ltw=["L1"]),
    _hit("2026-09-28", 5, fresh=[], est=[], ltw=["L1"]),
    _hit("2026-09-25", 10, fresh=[], est=[], ltw=[]),
]


def test_activity_streak_averages_persistence_and_dropouts():
    a = ms.compute_activity(V3_HITS)
    assert a["today_candidates"] == 0
    assert a["avg_5d"] == pytest.approx((0 + 0 + 2 + 3 + 5) / 5)
    assert a["avg_20d"] == pytest.approx(20 / 6, abs=0.01)             # rounded to 2 dp
    assert a["last_hit_date"] == "2026-09-30"
    assert a["streak_days_without_hit"] == 2
    assert a["n_days"] == 6
    assert a["persistent"] == [
        {"ticker": "L1", "consecutive_days": 5, "bucket": "long_term_winners"},
        {"ticker": "E1", "consecutive_days": 3, "bucket": "established_momentum"},
        {"ticker": "L2", "consecutive_days": 2, "bucket": "long_term_winners"},
    ]                                                   # E2 is new today -> not persistent
    assert a["dropped_out"] == [
        {"ticker": "F1", "bucket": "fresh_momentum", "last_seen": "2026-10-01", "stage": "stale"},
        {"ticker": "L3", "bucket": "long_term_winners", "last_seen": "2026-10-01", "stage": "stale"},
    ]
    # order-insensitive input, legacy rows fall back to top / top_doubler
    legacy = [{"date": "2026-09-23", "n_candidates": 1, "top": {"ticker": "ARM", "score": 100.0}},
              {"date": "2026-09-24", "n_candidates": 1, "top": {"ticker": "ARM", "score": 90.0},
               "top_doubler": {"ticker": "SNDK", "ret": 6.0}}]
    la = ms.compute_activity(legacy)
    assert la["today_candidates"] == 1 and la["streak_days_without_hit"] == 0
    assert la["persistent"] == [{"ticker": "ARM", "consecutive_days": 2, "bucket": "fresh_momentum"}]
    assert ms.compute_activity([])["persistent"] == [] and ms.compute_activity([])["avg_5d"] is None


def test_hits_endpoint_returns_activity(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)
    (tmp_path / "screener-hits.json").write_text(json.dumps({"hits": V3_HITS}))
    body = client.get("/api/v1/screener/hits").json()
    assert set(body) == {"hits", "persistence", "activity"}
    assert body["activity"]["streak_days_without_hit"] == 2
    assert body["activity"]["persistent"][0]["ticker"] == "L1"
    (tmp_path / "screener-hits.json").unlink()
    assert client.get("/api/v1/screener/hits").json() == {"hits": []}   # empty case unchanged


def test_hits_entry_carries_top_tickers_per_bucket_and_v3_score():
    def row(t, total):
        return {"ticker": t, "score": {"total": total}, "price": 1.0}
    doc = {"date": "2026-10-02",
           "candidates": [row("A", 90), row("B", 80)],
           "doublers": [{"ticker": f"D{i}", "ret_270d": 1.0 + i, "score": {"total": 50}} for i in range(12)],
           "buckets": {"fresh_momentum": [row("A", 90), row("B", 80)],
                       "established_momentum": [row("E", 70)],
                       "long_term_winners": [{"ticker": f"D{i}"} for i in range(12)]}}
    e = ms._hits_entry(doc)
    assert e["top"] == {"ticker": "A", "score": 90}
    assert e["top_doubler"] == {"ticker": "D11", "ret": 12.0}
    assert e["n_established"] == 1
    assert e["top_tickers"]["fresh_momentum"] == ["A", "B"]
    assert e["top_tickers"]["established_momentum"] == ["E"]
    assert len(e["top_tickers"]["long_term_winners"]) == ms.HITS_TOP_TICKERS == 10


# ---------------------------------------------------------------------------
# 10. report card extensions
# ---------------------------------------------------------------------------

def test_report_card_profit_factor_drawdown_benchmark_and_established(tmp_path):
    idx = pd.bdate_range("2026-07-01", periods=30)
    a = [100.0] * 27 + [80.0, 90.0, 110.0]            # dips 20% under entry before closing +10%
    b = [200.0] * 27 + [190.0, 185.0, 180.0]          # straight down -10%
    c = [50.0] * 27 + [60.0, 70.0, 75.0]              # +50%, never under entry
    spy = [400.0] * 24 + [400.0] + [410.0] * 4 + [420.0]
    closes = pd.DataFrame({"A": a, "B": b, "C": c, "SPY": spy}, index=idx)
    snap_date = idx[-6].date()                         # 5 trading days back; SPY 400 that day
    sdir = tmp_path / "screener"
    sdir.mkdir()
    (sdir / f"{snap_date.isoformat()}.json").write_text(json.dumps({
        "date": snap_date.isoformat(),
        "candidates": [{"ticker": "A", "price": 100.0}, {"ticker": "B", "price": 200.0}],
        "doublers": [],
        "buckets": {"fresh_momentum": [], "established_momentum": [{"ticker": "C", "price": 50.0}],
                    "long_term_winners": []}}))
    card = ms.evaluate_past_signals(tmp_path, closes)
    m5 = card["momentum"]["5"]
    assert m5["n"] == 2 and m5["mean"] == pytest.approx(0.0)
    assert m5["avg_winner"] == pytest.approx(0.1) and m5["avg_loser"] == pytest.approx(-0.1)
    assert m5["profit_factor"] == pytest.approx(1.0)
    assert m5["max_drawdown"] == pytest.approx(-0.2)               # A's 80 print
    assert m5["benchmark_ret"] == pytest.approx(0.05)              # SPY 400 -> 420
    assert m5["excess"] == pytest.approx(-0.05)
    e5 = card["established_momentum"]["5"]
    assert e5["n"] == 1 and e5["mean"] == pytest.approx(0.5)
    assert e5["avg_loser"] is None and e5["profit_factor"] is None
    assert e5["max_drawdown"] == 0.0 and e5["excess"] == pytest.approx(0.45)
    assert "doublers" not in card                                  # empty group dropped


# ---------------------------------------------------------------------------
# 11. run_screen end-to-end, offline: the full v3 contract
# ---------------------------------------------------------------------------

WIN = ([100.0] * 254 + [100.0, 105.0, 110.0, 120.0, 126.0, 132.0], [1e6] * 259 + [3e6])   # fresh (+ established)
EST = ([100.0] * 199 + _geo(100.0, 0.01, 61), FLAT_VOL)        # 20d +22%, 60d +82%, stacked MAs
DBL = ([50.0] * 60 + _geo(50.0, 0.004, 200), [5e6] * 260)      # 270d +110%, slow -> winners only
FLAT = ([100.0] * 260, [5e7] * 260)
SPY = ([400.0 + i * 0.1 for i in range(260)], [1e8] * 260)
QQQ = ([300.0 + i * 0.2 for i in range(260)], [1e8] * 260)
SECTORS = {t: {"sector": "Technology", "industry": "Semiconductors"} for t in ("WIN", "EST", "DBL", "FLAT")}

CONTRACT_ROW_KEYS = {
    "ticker", "price", "ret_2d", "ret_5d", "ret_20d", "ret_60d", "ret_90d", "ret_270d", "rs", "rs_qqq_20d",
    "sector_rs_20d", "trend", "high_52w", "pct_from_52w_high", "new_52w_high", "days_since_52w_high",
    "rvol", "rvol_class", "rvol_trend", "avg_dollar_vol", "median_dollar_vol_20d", "liquidity_tier",
    "acceleration", "setup", "risk_flags", "confirmations", "score", "queue", "freshness", "catalyst",
    "data_quality", "market_cap", "cap_unknown", "sector", "industry", "theme", "bucket",
    "dist_ma20", "dist_ma50", "new_20d_high", "new_50d_high",
}
CONTRACT_DOC_KEYS = {
    "date", "generated_at_utc", "universe", "criteria", "score_weights", "candidates", "doublers",
    "buckets", "counts", "benchmarks", "regime", "research_queue", "theme_breadth", "concentration",
    "analytics", "sector_lookups", "scanned", "passed_filters", "skipped", "notes",
}


def test_run_screen_end_to_end_v3_contract():
    frame = _frame({"WIN": WIN, "EST": EST, "DBL": DBL, "FLAT": FLAT, "SPY": SPY, "QQQ": QQQ})
    earnings_calls, batches = [], []

    def fake_earnings(t):
        earnings_calls.append(t)
        return [TODAY + timedelta(days=4)] if t == "WIN" else []

    def fake_fetch(batch):
        batches.append(list(batch))
        return frame

    cache = {}
    history = _hist({"2026-09-30": ["WIN"], "2026-10-01": ["WIN"]})
    doc = ms.run_screen(ms.load_screener_config({}),
                        {"name": "u", "tickers": ["WIN", "EST", "DBL", "FLAT", "SPY"]},   # SPY in the universe: ignored
                        fetch=fake_fetch, fetch_market_cap=lambda t: 50e9,
                        fetch_52w=lambda ts: pd.DataFrame(), sectors=SECTORS,
                        fetch_earnings=fake_earnings, fetch_splits=lambda t: [],
                        catalyst_cache=cache, history_index=history)

    assert batches == [["WIN", "EST", "DBL", "FLAT", "SPY", "QQQ"]]        # benchmarks appended, deduped
    assert doc["scanned"] == 4 and doc["skipped"] == 0                     # SPY/QQQ never counted
    assert CONTRACT_DOC_KEYS <= set(doc)
    buckets = doc["buckets"]
    assert set(buckets) == set(ms.BUCKET_NAMES)
    assert buckets["fresh_momentum"] is doc["candidates"] and buckets["long_term_winners"] is doc["doublers"]
    names = {b: [r["ticker"] for r in rows] for b, rows in buckets.items()}
    assert names["fresh_momentum"] == ["WIN"]
    assert set(names["established_momentum"]) == {"WIN", "EST"}          # WIN sits in two buckets
    assert names["long_term_winners"] == ["DBL"]
    assert doc["counts"] == {"fresh_momentum": 1, "established_momentum": 2, "long_term_winners": 1}
    for rows in buckets.values():
        for r in rows:
            assert r["ticker"] not in ms.BENCHMARK_TICKERS
            assert CONTRACT_ROW_KEYS <= set(r), CONTRACT_ROW_KEYS - set(r)
            assert "quality" not in r
        totals = [r["score"]["total"] for r in rows]
        assert totals == sorted(totals, reverse=True)
    for tier_rows in doc["research_queue"].values():
        for e in tier_rows:
            assert e["ticker"] not in ms.BENCHMARK_TICKERS

    # benchmarks + relative strength + regime
    assert doc["benchmarks"]["SPY"]["ret_20d"] == pytest.approx(ms.bar_return(frame["Close"]["SPY"], 20))
    assert doc["benchmarks"]["QQQ"]["ret_5d"] is not None
    win = doc["candidates"][0]
    assert win["rs"]["5d"] == pytest.approx(win["ret_5d"] - doc["benchmarks"]["SPY"]["ret_5d"])
    assert win["rs_qqq_20d"] == pytest.approx(win["ret_20d"] - doc["benchmarks"]["QQQ"]["ret_20d"])
    assert win["sector_rs_20d"] is not None                                # 3 same-theme peers
    assert doc["regime"]["n_computed"] == 4 and doc["regime"]["label"] in ("risk_on", "neutral", "risk_off")
    assert doc["regime"]["spy_20d"] == doc["benchmarks"]["SPY"]["ret_20d"]

    # funnel fields on the fresh-momentum row
    assert win["setup"] == "EXHAUSTION" and win["queue"]["tier"] == "D"
    assert win["freshness"] == {"first_seen": "2026-09-30", "days_in_list": 3, "stage": "developing",
                                "history_days": 2}
    assert win["catalyst"]["kind"] == "earnings" and win["catalyst"]["days_to_earnings"] == 4
    assert "earnings_catalyst" in win["confirmations"]
    assert win["data_quality"] == {"checked": False}                      # +32% needs no check
    est = next(r for r in buckets["established_momentum"] if r["ticker"] == "EST")
    assert est["trend"]["state"] == "strong_uptrend" and est["setup"] in ("TREND", "ACCELERATION")
    assert est["freshness"]["stage"] == "fresh"
    dbl = doc["doublers"][0]
    assert dbl["window_hit"] == "270d" and "pace" in dbl and "pace" not in win
    assert dbl["bucket"] == "long_term_winners"

    # research queue: each ticker once, best bucket
    all_q = [e["ticker"] for rows in doc["research_queue"].values() for e in rows]
    assert sorted(all_q) == ["DBL", "EST", "WIN"]
    assert all(set(e) == {"ticker", "bucket", "score", "setup", "reason"} for rows in doc["research_queue"].values() for e in rows)

    # earnings fetched for FINALISTS only (each once), cached under today's date
    assert sorted(earnings_calls) == ["DBL", "EST", "WIN"]
    assert set(cache) == {"DBL", "EST", "WIN"} and cache["WIN"]["dates"] == [(TODAY + timedelta(days=4)).isoformat()]

    # list blocks + analytics documentation
    assert doc["theme_breadth"][0]["theme"] == "AI hardware supply chain" and doc["theme_breadth"][0]["n"] == 1
    assert doc["concentration"]["n"] == 1
    assert doc["score_weights"] == ms.SCORE_MAX
    an = doc["analytics"]
    for key in ("score_weights", "score_scaling", "setup_rules", "setup_order", "queue_rules",
                "risk_thresholds", "rvol_classes", "liquidity_tiers", "freshness_stages", "regime",
                "theme_breadth", "catalyst", "data_quality", "pace", "concentration_warn_share"):
        assert key in an, key
    assert doc["criteria"]["est_min_return_20d"] == 0.20 and doc["criteria"]["est_min_return_60d"] == 0.30

    # hits row + activity from this very snapshot
    entry = ms._hits_entry(doc)
    assert entry["top_tickers"]["fresh_momentum"] == ["WIN"] and entry["n_established"] == 2
    assert ms.compute_activity([entry])["today_candidates"] == 1


def test_run_screen_replay_mode_uses_caches_only_and_no_fetchers():
    frame = _frame({"WIN": WIN, "SPY": SPY})
    asof = frame.index[-1].date()
    cache = {"WIN": {"fetched": "2020-01-01", "dates": [(asof + timedelta(days=2)).isoformat()]}}

    def boom(t):
        raise AssertionError("must not fetch in replay mode")

    doc = ms.run_screen(ms.load_screener_config({}), {"name": "u", "tickers": ["WIN"]},
                        fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9, fetch_52w=boom,
                        asof=asof, catalyst_cache=cache, splits_cache={})
    (win,) = doc["candidates"]
    assert doc["backfilled"] is True
    assert win["catalyst"]["kind"] == "earnings" and win["catalyst"]["days_to_earnings"] == 2   # stale cache, as-of date
    assert win["freshness"]["stage"] == "fresh"                        # no history index given
    assert win["rs"]["20d"] is not None                                # SPY sliced with the universe


def test_established_bucket_gates_and_cap_check():
    cfg = ms.load_screener_config({"screener": {"est_min_return_20d": 0.25}})   # EST's +22% now fails
    frame = _frame({"EST": EST, "FLAT": FLAT})
    doc = ms.run_screen(cfg, {"name": "u", "tickers": ["EST", "FLAT"]}, fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 50e9, fetch_52w=lambda ts: pd.DataFrame())
    assert doc["buckets"]["established_momentum"] == []
    cfg = ms.load_screener_config({})
    calls = []

    def small_cap(t):
        calls.append(t)
        return 5e8   # below the $1B floor

    doc = ms.run_screen(cfg, {"name": "u", "tickers": ["EST", "FLAT"]}, fetch=lambda b: frame,
                        fetch_market_cap=small_cap, fetch_52w=lambda ts: pd.DataFrame())
    assert calls == ["EST"] and doc["buckets"]["established_momentum"] == []   # known small cap dropped
    assert doc["counts"]["established_momentum"] == 0
    m = ms.compute_ticker_metrics(_series(EST[0]), _series(EST[1]))
    assert ms.passes_established_gates(m, cfg) is True
    assert ms.passes_established_gates({**m, "ret_60d": None}, cfg) is False
    assert ms.passes_established_gates({**m, "ma20": m["ma50"] - 1}, cfg) is False   # MAs not stacked


def test_backfill_extends_freshness_day_by_day(tmp_path):
    """Day 2 of a backfill must see day 1's file (written moments earlier)."""
    (tmp_path / "tickers.json").write_text(json.dumps({"tickers": ["A"]}))
    (tmp_path / "universe.json").write_text(json.dumps({"name": "mini", "tickers": ["DBL"]}))
    closes = [50.0] * 58 + _geo(50.0, 0.004, 202)
    frame = _frame({"DBL": (closes, [5e6] * 260)})
    written = ms.run_backfill(tmp_path / "tickers.json", tmp_path, 3,
                              universe_path=tmp_path / "universe.json",
                              fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9)
    stages = []
    for p in written:
        snap = json.loads(p.read_text())
        (row,) = snap["doublers"]
        stages.append((row["freshness"]["days_in_list"], row["freshness"]["stage"]))
    assert stages == [(1, "fresh"), (2, "developing"), (3, "developing")]
    # the caches were never created (replay mode fetches nothing)
    assert not (tmp_path / ms.CATALYST_CACHE_NAME).exists()
    assert not (tmp_path / ms.SPLITS_CACHE_NAME).exists()


def test_run_daily_screen_persists_caches_and_fetches_finalists_only(tmp_path, monkeypatch):
    (tmp_path / "tickers.json").write_text(json.dumps({"tickers": ["A"]}))
    (tmp_path / "universe.json").write_text(json.dumps({"name": "mini", "tickers": ["WIN", "FLAT"]}))
    frame = _frame({"WIN": WIN, "FLAT": FLAT, "SPY": SPY})
    earnings_calls, splits_calls = [], []
    monkeypatch.setattr(ms, "_default_fetch", lambda batch: frame)
    monkeypatch.setattr(ms, "_default_fetch_market_cap", lambda t: 30e9)
    monkeypatch.setattr(ms, "_default_fetch_52w", lambda ts: pd.DataFrame())
    monkeypatch.setattr(ms, "_default_fetch_sector", lambda t: None)
    monkeypatch.setattr(ms, "_default_fetch_earnings",
                        lambda t: earnings_calls.append(t) or [date(2026, 10, 20)])
    monkeypatch.setattr(ms, "_default_fetch_splits", lambda t: splits_calls.append(t) or [])
    doc = ms.run_daily_screen(tmp_path / "tickers.json", tmp_path, universe_path=tmp_path / "universe.json")
    assert earnings_calls == ["WIN"] and splits_calls == []            # finalists only; no huge mover
    cache = json.loads((tmp_path / ms.CATALYST_CACHE_NAME).read_text())
    assert cache["WIN"]["dates"] == ["2026-10-20"]
    assert doc["candidates"][0]["catalyst"]["earnings_date"] == "2026-10-20"
    latest = json.loads((tmp_path / "screener-latest.json").read_text())
    assert set(latest["buckets"]) == set(ms.BUCKET_NAMES)
    hits = json.loads((tmp_path / "screener-hits.json").read_text())["hits"]
    assert hits[0]["top_tickers"]["fresh_momentum"] == ["WIN"]
