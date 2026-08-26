"""Backtest tests: synthetic OHLCV frames with KNOWN outcomes, fully offline.

The load-bearing ones are the look-ahead guards: entry MUST be the NEXT day's
open (a test fails if anyone 'simplifies' it to the signal-day close) and the
exit MUST be the close exactly H trading days after the entry day.

No network, no yfinance import, no real sleeps anywhere.
"""
import json

import numpy as np
import pandas as pd
import pytest

import app.backtest as bt


# ---------------------------------------------------------------------------
# synthetic bars
#
# SIG_CLOSES: flat 100 then a spike; with th2=0.10 / th5=0.30 the ONLY signal
# is at position 9 (0-based): ret2d = 132/120-1 = 0.10, ret5d = 132/100-1 = 0.32.
# Opens are deliberately DIFFERENT from closes so any close-based entry is
# caught: open[i] = close[i] + 50.
# ---------------------------------------------------------------------------

SIG_CLOSES = [100.0, 100.0, 100.0, 100.0, 100.0,
              105.0, 110.0, 120.0, 126.0, 132.0,      # signal at position 9
              120.0, 110.0, 100.0, 90.0, 80.0, 70.0]  # then it reverses hard
SIG_OPENS = [c + 50.0 for c in SIG_CLOSES]
ENTRY_OPEN = SIG_OPENS[10]                             # 190.0 — next day's OPEN


def _series(values, start="2026-01-01"):
    return pd.Series(list(values), index=pd.bdate_range(start, periods=len(values)))


def _flat_vol(n, level=1e6):
    return [float(level)] * n


def trades_for(closes, opens=None, volumes=None, *, th2=0.10, th5=0.30,
               holdings=(1, 3, 5, 10), curve_days=5, **kwargs):
    opens = opens if opens is not None else closes
    volumes = volumes if volumes is not None else _flat_vol(len(closes))
    rows = bt.build_ticker_trades(
        "SYN", _series(opens), _series(closes), _series(volumes),
        th2=th2, th5=th5, holdings=list(holdings), curve_days=curve_days,
        **kwargs)
    return rows


# ---------------------------------------------------------------------------
# signals: dates exact, thresholds are >= (boundary included)
# ---------------------------------------------------------------------------

def test_signal_dates_exact():
    rows = trades_for(SIG_CLOSES, SIG_OPENS)
    assert len(rows) == 1                       # exactly one signal
    idx = pd.bdate_range("2026-01-01", periods=len(SIG_CLOSES))
    assert rows[0]["signal_date"] == idx[9]     # the 132-close day
    assert rows[0]["entry_date"] == idx[10]     # the very next trading day
    assert rows[0]["ret_2d"] == pytest.approx(0.10)   # exactly AT the gate
    assert rows[0]["ret_5d"] == pytest.approx(0.32)


def test_no_signal_below_either_threshold():
    assert trades_for(SIG_CLOSES, SIG_OPENS, th2=0.1001) == []   # ret2d just misses
    assert trades_for(SIG_CLOSES, SIG_OPENS, th5=0.3201) == []   # ret5d just misses


def test_signal_on_last_bar_has_no_entry_and_is_dropped():
    closes = SIG_CLOSES[:10]                    # series ENDS on the signal day
    assert trades_for(closes) == []


# ---------------------------------------------------------------------------
# LOOK-AHEAD GUARD: entry is the NEXT day's OPEN, never the signal-day close
# ---------------------------------------------------------------------------

def test_entry_is_next_days_open_not_signal_close():
    (trade,) = trades_for(SIG_CLOSES, SIG_OPENS)
    assert trade["entry_open"] == ENTRY_OPEN                    # 190.0
    assert trade["entry_open"] != SIG_CLOSES[9]                 # NOT 132 (close t)
    assert trade["entry_open"] != SIG_CLOSES[10]                # NOT 140 (close t+1)
    # the H=1 return must be computed off that open — anyone switching the
    # entry to a close changes this exact value and fails here
    assert trade["fwd_1"] == pytest.approx(SIG_CLOSES[11] / ENTRY_OPEN - 1.0)


def test_exit_indexing_exact_h_days_after_entry():
    (trade,) = trades_for(SIG_CLOSES, SIG_OPENS)
    idx = pd.bdate_range("2026-01-01", periods=len(SIG_CLOSES))
    # entry at position 10; exit close for H is position 10+H
    for h in (1, 3, 5):
        assert trade[f"fwd_{h}"] == pytest.approx(SIG_CLOSES[10 + h] / ENTRY_OPEN - 1.0)
        assert trade[f"exit_date_{h}"] == idx[10 + h]
    # H=10 runs past the data end (needs position 20, we have 16 bars) -> NaN
    assert np.isnan(trade["fwd_10"])
    assert pd.isna(trade["exit_date_10"])


def test_incomplete_exit_window_excluded_from_stats():
    (trade,) = trades_for(SIG_CLOSES, SIG_OPENS)
    stats = bt.distribution_stats([trade["fwd_10"]])   # NaN only
    assert stats == {"n_trades": 0}


# ---------------------------------------------------------------------------
# cost subtraction
# ---------------------------------------------------------------------------

def test_cost_subtraction_exact():
    net = bt.apply_cost(np.array([0.05, -0.02]), 25.0)   # 25 bps round trip
    assert net == pytest.approx([0.05 - 0.0025, -0.02 - 0.0025])
    assert bt.apply_cost(np.array([0.05]), 0.0)[0] == pytest.approx(0.05)


# ---------------------------------------------------------------------------
# continuation-curve math
# ---------------------------------------------------------------------------

def test_continuation_curve_mean_and_median_exact():
    # two synthetic trades with known cum_1..cum_3; curve over full windows only
    trades = pd.DataFrame([
        {"cum_1": 0.10, "cum_2": 0.20, "cum_3": 0.30},
        {"cum_1": 0.00, "cum_2": -0.10, "cum_3": 0.10},
        {"cum_1": 0.50, "cum_2": np.nan, "cum_3": np.nan},   # incomplete -> excluded
    ])
    curve = bt.continuation_curve(trades, curve_days=3)
    assert curve["n_signals"] == 2
    assert curve["mean"] == pytest.approx([0.05, 0.05, 0.20])
    assert curve["median"] == pytest.approx([0.05, 0.05, 0.20])


def test_continuation_curve_matches_close_over_entry_open():
    (trade,) = trades_for(SIG_CLOSES, SIG_OPENS, curve_days=5)
    for d in range(1, 6):
        assert trade[f"cum_{d}"] == pytest.approx(SIG_CLOSES[10 + d] / ENTRY_OPEN - 1.0)


# ---------------------------------------------------------------------------
# distribution stats
# ---------------------------------------------------------------------------

def test_distribution_stats_known_values():
    r = np.array([0.10, 0.20, -0.05, -0.15, 0.30])
    s = bt.distribution_stats(r)
    assert s["n_trades"] == 5
    assert s["win_rate"] == pytest.approx(0.6)
    assert s["mean"] == pytest.approx(0.08)
    assert s["median"] == pytest.approx(0.10)
    assert s["best"] == pytest.approx(0.30)
    assert s["worst"] == pytest.approx(-0.15)
    assert s["profit_factor"] == pytest.approx(0.60 / 0.20)   # 3.0
    assert s["avg_winner"] == pytest.approx(0.20)
    assert s["avg_loser"] == pytest.approx(-0.10)
    assert s["p25"] == pytest.approx(np.percentile(r, 25))
    assert s["p90"] == pytest.approx(np.percentile(r, 90))


def test_distribution_stats_no_losers_has_null_profit_factor():
    s = bt.distribution_stats(np.array([0.1, 0.2]))
    assert s["profit_factor"] is None
    assert s["avg_loser"] is None


# ---------------------------------------------------------------------------
# equity curve max drawdown (H=5, non-overlapping, sequential compounding)
# ---------------------------------------------------------------------------

def test_equity_max_drawdown_known_sequence():
    idx = pd.bdate_range("2026-01-01", periods=40)
    trades = pd.DataFrame([
        # taken: +10%, then -20% (entry after previous exit), then +5%
        {"ticker": "A", "entry_date": idx[0], "fwd_5": 0.10, "exit_date_5": idx[5]},
        {"ticker": "B", "entry_date": idx[2], "fwd_5": 9.99, "exit_date_5": idx[7]},   # overlaps -> skipped
        {"ticker": "C", "entry_date": idx[6], "fwd_5": -0.20, "exit_date_5": idx[11]},
        {"ticker": "D", "entry_date": idx[12], "fwd_5": 0.05, "exit_date_5": idx[17]},
    ])
    dd = bt.equity_max_drawdown(trades, holding=5, cost_bps=0.0)
    assert dd["n_trades_used"] == 3
    # equity: 1.0 -> 1.10 (peak) -> 0.88 -> 0.924; max dd = 1 - 0.88/1.10 = 0.20
    assert dd["max_drawdown"] == pytest.approx(0.20)
    assert dd["final_equity"] == pytest.approx(1.10 * 0.80 * 1.05)


# ---------------------------------------------------------------------------
# grid: cell counts and forward-return math
# ---------------------------------------------------------------------------

def test_grid_cell_counts_and_means():
    trades = pd.DataFrame([
        {"ret_2d": 0.06, "ret_5d": 0.16, "fwd_10": 0.10},
        {"ret_2d": 0.11, "ret_5d": 0.31, "fwd_10": 0.20},
        {"ret_2d": 0.21, "ret_5d": 0.51, "fwd_10": -0.10},
        {"ret_2d": 0.30, "ret_5d": 0.60, "fwd_10": np.nan},   # no forward window
    ])
    grid = bt.grid_scan(trades, th2_list=(0.05, 0.10, 0.20), th5_list=(0.15, 0.30, 0.50))
    ns = [[cell["n_signals"] for cell in row] for row in grid]
    assert ns == [[3, 2, 1],    # th2=0.05: all / the two big ones / the biggest
                  [2, 2, 1],    # th2=0.10
                  [1, 1, 1]]    # th2=0.20
    assert grid[0][0]["mean"] == pytest.approx((0.10 + 0.20 - 0.10) / 3)
    assert grid[0][0]["median"] == pytest.approx(0.10)
    assert grid[2][2]["mean"] == pytest.approx(-0.10)


# ---------------------------------------------------------------------------
# variants: RVOL and 52-week-high filters
# ---------------------------------------------------------------------------

def test_variant_filters_partition_correctly():
    trades = pd.DataFrame([
        {"fwd_10": 0.10, "rvol": 3.0, "is_52w_high": True},
        {"fwd_10": 0.20, "rvol": 1.0, "is_52w_high": True},
        {"fwd_10": -0.10, "rvol": 2.5, "is_52w_high": False},
        {"fwd_10": np.nan, "rvol": 5.0, "is_52w_high": True},   # no window -> excluded
    ])
    by = {v["variant"]: v for v in bt.variant_stats(trades, holding=10)}
    assert by["baseline"]["n_trades"] == 3
    assert by["rvol>=2"]["n_trades"] == 2
    assert by["rvol>=2"]["mean"] == pytest.approx(0.0)
    assert by["52w_high"]["n_trades"] == 2
    assert by["52w_high"]["mean"] == pytest.approx(0.15)


def test_rvol_and_52w_high_computed_at_signal_close():
    # 300 flat bars then the spike: the signal close IS the 252-bar high, and
    # signal-day volume 5M vs prior-20-day mean 1M -> rvol = 5.0
    closes = [100.0] * 300 + SIG_CLOSES
    opens = [c + 50.0 for c in closes]
    volumes = _flat_vol(len(closes))
    volumes[-7] = 5_000_000.0                   # position of the 132-close day
    rows = trades_for(closes, opens, volumes)
    (trade,) = rows
    assert trade["rvol"] == pytest.approx(5.0)
    assert trade["is_52w_high"] is True

    # cap the history so the top was higher before: not a 52w high anymore
    closes2 = [200.0] * 300 + SIG_CLOSES
    (trade2,) = trades_for(closes2, [c + 50 for c in closes2])
    assert trade2["is_52w_high"] is False


# ---------------------------------------------------------------------------
# excess returns vs SPY: exact alignment on the SAME entry/exit dates
# ---------------------------------------------------------------------------

def _spy_linear(n, start="2026-01-01"):
    """SPY closes 100, 110, 120, ... on the same bdate calendar as _series."""
    return pd.Series([100.0 + 10.0 * i for i in range(n)],
                     index=pd.bdate_range(start, periods=n))


def test_excess_return_alignment_known_spy():
    n = len(SIG_CLOSES)
    rows = bt.build_ticker_trades(
        "SYN", _series(SIG_OPENS), _series(SIG_CLOSES), _series(_flat_vol(n)),
        th2=0.10, th5=0.30, holdings=[1, 3, 5], curve_days=3,
        spy_closes=_spy_linear(n))
    (trade,) = rows
    # entry at position 10 (SPY 200); exits at 11/13/15 (SPY 210/230/250)
    assert trade["exc_1"] == pytest.approx(trade["fwd_1"] - (210.0 / 200.0 - 1.0))
    assert trade["exc_3"] == pytest.approx(trade["fwd_3"] - (230.0 / 200.0 - 1.0))
    assert trade["exc_5"] == pytest.approx(trade["fwd_5"] - (250.0 / 200.0 - 1.0))
    for d in (1, 2, 3):
        spy_cum = (200.0 + 10.0 * d) / 200.0 - 1.0
        assert trade[f"exc_cum_{d}"] == pytest.approx(trade[f"cum_{d}"] - spy_cum)


def test_excess_spy_gaps_forward_filled():
    n = len(SIG_CLOSES)
    idx = pd.bdate_range("2026-01-01", periods=n)
    spy_gapped = _spy_linear(n).drop(idx[11])   # SPY has no bar on the H=1 exit date
    rows = bt.build_ticker_trades(
        "SYN", _series(SIG_OPENS), _series(SIG_CLOSES), _series(_flat_vol(n)),
        th2=0.10, th5=0.30, holdings=[1, 3], curve_days=3, spy_closes=spy_gapped)
    (trade,) = rows
    # forward-fill: SPY leg uses the last close on/before the exit date
    # (idx[10] -> 200), so the SPY return over the gap is 0 and exc == fwd
    assert trade["exc_1"] == pytest.approx(trade["fwd_1"])
    # dates where SPY does trade are unaffected by the gap
    assert trade["exc_3"] == pytest.approx(trade["fwd_3"] - (230.0 / 200.0 - 1.0))

    # no SPY data at all -> excess is NaN, never guessed
    rows2 = bt.build_ticker_trades(
        "SYN", _series(SIG_OPENS), _series(SIG_CLOSES), _series(_flat_vol(n)),
        th2=0.10, th5=0.30, holdings=[1], curve_days=3,
        spy_closes=pd.Series(dtype=float))
    assert np.isnan(rows2[0]["exc_1"])


def test_continuation_curve_excess_lines():
    trades = pd.DataFrame([
        {"cum_1": 0.10, "cum_2": 0.20, "exc_cum_1": 0.06, "exc_cum_2": 0.12},
        {"cum_1": 0.00, "cum_2": 0.10, "exc_cum_1": -0.02, "exc_cum_2": 0.04},
        {"cum_1": 0.30, "cum_2": 0.40, "exc_cum_1": np.nan, "exc_cum_2": np.nan},
    ])
    curve = bt.continuation_curve(trades, curve_days=2)
    assert curve["n_signals"] == 3
    assert curve["n_signals_excess"] == 2       # SPY coverage can be smaller
    assert curve["excess_mean"] == pytest.approx([0.02, 0.08])
    assert curve["excess_median"] == pytest.approx([0.02, 0.08])


# ---------------------------------------------------------------------------
# dynamic exits: ma10 crossover exactness, trail2atr stop math, cap at 20,
# and the ATR no-look-ahead guard
# ---------------------------------------------------------------------------

# 20 flat bars, the SIG-style ramp (only signal at position 24, entry at 25),
# then a controlled decay: MA10 crossover happens exactly at position 28.
MA10_CLOSES = ([100.0] * 20 + [105.0, 110.0, 120.0, 126.0, 132.0]
               + [130.0, 128.0, 126.0, 100.0, 100.0, 100.0])


def test_ma10_exit_day_exact():
    rows = trades_for(MA10_CLOSES, [c + 50.0 for c in MA10_CLOSES],
                      holdings=(1,), exit_styles=("ma10",))
    (trade,) = rows
    idx = pd.bdate_range("2026-01-01", periods=len(MA10_CLOSES))
    # by hand: MA10 at positions 25/26/27 = 112.3/115.1/117.7 (close above),
    # at position 28 = 117.7 with close 100 -> first cross, day 3 after entry
    assert trade["dyn_ma10"] == pytest.approx(100.0 / 180.0 - 1.0)  # o[25]=130+50
    assert trade["dyn_ma10_days"] == 3.0
    assert trade["dyn_ma10_exit_date"] == idx[28]


def test_ma10_entry_day_counts_as_day0():
    closes = [100.0] * 20 + [105.0, 110.0, 120.0, 126.0, 132.0] + [90.0, 90.0]
    rows = trades_for(closes, [c + 50.0 for c in closes],
                      holdings=(1,), exit_styles=("ma10",))
    (trade,) = rows
    # entry-day close 90 < MA10 108.3 -> exit at the ENTRY DAY's close (day 0)
    assert trade["dyn_ma10"] == pytest.approx(90.0 / 140.0 - 1.0)   # o[25]=90+50
    assert trade["dyn_ma10_days"] == 0.0


# same ramp; constant TR=2 history so the ATR is exactly computable by hand
TRAIL_CLOSES = ([100.0] * 20 + [105.0, 110.0, 120.0, 126.0, 132.0]
                + [130.0, 128.0, 118.0, 118.0, 118.0])


def _hlc(closes):
    c = np.array(closes, dtype=float)
    return c + 1.0, c - 1.0, c


def test_atr_series_known_values():
    h, lo, c = _hlc(TRAIL_CLOSES)
    atr = bt.atr_series(h, lo, c)
    assert np.isnan(atr[13])                    # needs 14 TRs, TR needs prev close
    assert atr[14] == pytest.approx(2.0)        # constant-range history: TR = 2
    # by hand: ATR@25 = (8*2 + 6+6+11+7+7+3)/14 = 56/14
    assert atr[25] == pytest.approx(4.0)
    assert atr[27] == pytest.approx(66.0 / 14.0)


def test_trail2atr_stop_math_exact():
    h, lo, c = _hlc(TRAIL_CLOSES)
    rows = bt.build_ticker_trades(
        "SYN", _series([x + 50.0 for x in TRAIL_CLOSES]), _series(TRAIL_CLOSES),
        _series(_flat_vol(len(TRAIL_CLOSES))), th2=0.10, th5=0.30,
        holdings=[1], curve_days=3, high_s=_series(h), low_s=_series(lo),
        exit_styles=("trail2atr",))
    (trade,) = rows
    idx = pd.bdate_range("2026-01-01", periods=len(TRAIL_CLOSES))
    # stop = highest close since entry (130) - 2*ATR:
    #   pos25: 130 - 8.0     = 122.00 -> close 130 above, no exit
    #   pos26: 130 - 8.1429  = 121.86 -> close 128 above, no exit
    #   pos27: 130 - 9.4286  = 120.57 -> close 118 BELOW -> exit day 2
    assert trade["dyn_trail2atr"] == pytest.approx(118.0 / 180.0 - 1.0)  # o[25]=130+50
    assert trade["dyn_trail2atr_days"] == 2.0
    assert trade["dyn_trail2atr_exit_date"] == idx[27]

    # no High/Low data -> trail columns stay NaN (never a silent fallback)
    rows2 = trades_for(TRAIL_CLOSES, [x + 50.0 for x in TRAIL_CLOSES],
                       holdings=(1,), exit_styles=("trail2atr",))
    assert np.isnan(rows2[0]["dyn_trail2atr"])
    assert np.isnan(rows2[0]["dyn_trail2atr_days"])


def test_trail2atr_no_look_ahead_guard():
    h, lo, c = _hlc(TRAIL_CLOSES)
    atr_full = bt.atr_series(h, lo, c)
    assert bt.dynamic_exit_position(c, 25, "trail2atr", atr=atr_full) == 27
    # ATR is causal: truncating the series at day j leaves atr[j] unchanged
    atr_trunc = bt.atr_series(h[:28], lo[:28], c[:28])
    assert atr_trunc[27] == pytest.approx(atr_full[27])
    # mutating every bar AFTER the exit day must not move the exit
    c2, h2, lo2 = c.copy(), h.copy(), lo.copy()
    c2[28:], h2[28:], lo2[28:] = 500.0, 501.0, 499.0
    atr2 = bt.atr_series(h2, lo2, c2)
    assert bt.dynamic_exit_position(c2, 25, "trail2atr", atr=atr2) == 27


def test_dynamic_exit_cap_at_20_days():
    # closes keep rising after entry -> neither style ever triggers -> cap
    closes = ([100.0] * 20 + [105.0, 110.0, 120.0, 126.0, 132.0]
              + [133.0 + i for i in range(23)])          # positions 25..47
    h, lo, _ = _hlc(closes)
    rows = bt.build_ticker_trades(
        "SYN", _series([x + 50.0 for x in closes]), _series(closes),
        _series(_flat_vol(len(closes))), th2=0.10, th5=0.30,
        holdings=[1], curve_days=3, high_s=_series(h), low_s=_series(lo),
        exit_styles=("ma10", "trail2atr"))
    (trade,) = rows
    idx = pd.bdate_range("2026-01-01", periods=len(closes))
    for style in ("ma10", "trail2atr"):
        assert trade[f"dyn_{style}_days"] == 20.0                 # capped
        assert trade[f"dyn_{style}"] == pytest.approx(153.0 / 183.0 - 1.0)
        assert trade[f"dyn_{style}_exit_date"] == idx[45]         # e=25 + 20

    # data ends before the cap AND before any trigger -> NaN, never guessed
    short = closes[:40]
    rows2 = bt.build_ticker_trades(
        "SYN", _series([x + 50.0 for x in short]), _series(short),
        _series(_flat_vol(len(short))), th2=0.10, th5=0.30,
        holdings=[1], curve_days=3, high_s=_series(_hlc(short)[0]),
        low_s=_series(_hlc(short)[1]), exit_styles=("ma10",))
    assert np.isnan(rows2[0]["dyn_ma10"])


def test_dynamic_exit_stats_rows_and_avg_days():
    trades = pd.DataFrame([
        {"fwd_10": 0.10, "exc_10": 0.04, "fwd_20": 0.12, "exc_20": 0.02,
         "dyn_ma10": 0.08, "dyn_ma10_days": 4.0, "exc_dyn_ma10": 0.03,
         "is_52w_high": True},
        {"fwd_10": -0.10, "exc_10": -0.12, "fwd_20": -0.05, "exc_20": -0.06,
         "dyn_ma10": -0.02, "dyn_ma10_days": 2.0, "exc_dyn_ma10": -0.05,
         "is_52w_high": False},
    ])
    rows = bt.dynamic_exit_stats(trades, ("ma10",), cost_bps=0.0)
    by = {(r["variant"], r["exit"]): r for r in rows}
    assert set(by) == {(v, e) for v in ("baseline", "52w_high")
                       for e in ("fixed H=10", "fixed H=20", "ma10")}
    assert by[("baseline", "ma10")]["n_trades"] == 2
    assert by[("baseline", "ma10")]["avg_holding_days"] == pytest.approx(3.0)
    assert by[("baseline", "ma10")]["excess_mean"] == pytest.approx(-0.01)
    assert by[("baseline", "ma10")]["mean"] == pytest.approx(0.03)
    assert by[("52w_high", "ma10")]["n_trades"] == 1
    assert by[("52w_high", "ma10")]["avg_holding_days"] == pytest.approx(4.0)
    assert by[("52w_high", "fixed H=10")]["avg_holding_days"] == 10.0
    assert by[("52w_high", "fixed H=10")]["excess_median"] == pytest.approx(0.04)


# ---------------------------------------------------------------------------
# per-variant train / validation / out-of-sample split
# ---------------------------------------------------------------------------

def test_variant_segment_split_counts_and_warnings():
    dates = pd.bdate_range("2026-01-01", periods=10)
    train_end, val_end = bt.segment_boundaries(dates)
    trades = pd.DataFrame([
        {"entry_date": dates[0], "fwd_10": 0.10, "exc_10": 0.05,
         "rvol": 3.0, "is_52w_high": True},
        {"entry_date": dates[1], "fwd_10": -0.20, "exc_10": -0.25,
         "rvol": 1.0, "is_52w_high": False},
        {"entry_date": dates[6], "fwd_10": 0.05, "exc_10": 0.01,
         "rvol": 2.5, "is_52w_high": True},
        {"entry_date": dates[8], "fwd_10": -0.10, "exc_10": -0.12,
         "rvol": 1.0, "is_52w_high": True},
        {"entry_date": dates[9], "fwd_10": np.nan, "exc_10": np.nan,
         "rvol": 9.0, "is_52w_high": True},   # no forward window -> excluded
    ])
    vs = bt.variant_segment_stats(trades, holding=10, cost_bps=0.0,
                                  train_end=train_end, val_end=val_end)
    by = {v["variant"]: v["segments"] for v in vs["variants"]}
    assert [v["variant"] for v in vs["variants"]] == list(bt.VARIANT_NAMES)
    assert by["baseline"]["train"]["n_trades"] == 2
    assert by["baseline"]["validation"]["n_trades"] == 1
    assert by["baseline"]["oos"]["n_trades"] == 1
    assert by["rvol>=2"]["train"]["n_trades"] == 1
    assert by["rvol>=2"]["validation"]["n_trades"] == 1
    assert by["rvol>=2"]["oos"]["n_trades"] == 0
    assert by["52w_high"]["train"]["n_trades"] == 1
    assert by["52w_high"]["validation"]["n_trades"] == 1
    assert by["52w_high"]["oos"]["n_trades"] == 1
    # values + excess flow through per segment
    assert by["baseline"]["train"]["mean"] == pytest.approx(-0.05)
    assert by["baseline"]["train"]["excess_mean"] == pytest.approx(-0.10)
    assert by["52w_high"]["oos"]["excess_median"] == pytest.approx(-0.12)
    # sign-flip warning fires PER VARIANT: only 52w_high flips (train +0.10
    # vs oos -0.10); baseline stays negative, rvol>=2 has no oos trades
    assert len(vs["warnings"]) == 1
    assert "52w_high" in vs["warnings"][0]

    # net-of-cost: a 25 bps haircut shifts each segment mean by exactly 25 bps
    vs2 = bt.variant_segment_stats(trades, holding=10, cost_bps=25.0,
                                   train_end=train_end, val_end=val_end)
    by2 = {v["variant"]: v["segments"] for v in vs2["variants"]}
    assert by2["baseline"]["train"]["mean"] == pytest.approx(-0.05 - 0.0025)


# ---------------------------------------------------------------------------
# train / validation / out-of-sample split
# ---------------------------------------------------------------------------

def test_segment_boundaries_60_20_20_by_date_count():
    dates = pd.bdate_range("2026-01-01", periods=10)
    train_end, val_end = bt.segment_boundaries(dates)
    assert train_end == dates[5]                 # first 6 dates = train (60%)
    assert val_end == dates[7]                   # next 2 = validation (20%)
    assert bt.segment_of(dates[0], train_end, val_end) == "train"
    assert bt.segment_of(dates[5], train_end, val_end) == "train"
    assert bt.segment_of(dates[6], train_end, val_end) == "validation"
    assert bt.segment_of(dates[7], train_end, val_end) == "validation"
    assert bt.segment_of(dates[8], train_end, val_end) == "oos"
    assert bt.segment_of(dates[9], train_end, val_end) == "oos"


def test_segment_stats_and_oos_sign_flip_warning():
    dates = pd.bdate_range("2026-01-01", periods=10)
    train_end, val_end = bt.segment_boundaries(dates)
    trades = pd.DataFrame([
        {"entry_date": dates[0], "fwd_5": 0.10},
        {"entry_date": dates[6], "fwd_5": 0.05},
        {"entry_date": dates[9], "fwd_5": -0.10},
    ])
    seg = bt.segment_stats(trades, [5], 0.0, train_end, val_end)
    assert seg["segments"]["train"]["5"]["n_trades"] == 1
    assert seg["segments"]["train"]["5"]["mean"] == pytest.approx(0.10)
    assert seg["segments"]["validation"]["5"]["mean"] == pytest.approx(0.05)
    assert seg["segments"]["oos"]["5"]["mean"] == pytest.approx(-0.10)
    assert len(seg["warnings"]) == 1 and "H=5" in seg["warnings"][0]

    # same sign -> no warning
    trades.loc[2, "fwd_5"] = 0.02
    assert bt.segment_stats(trades, [5], 0.0, train_end, val_end)["warnings"] == []


# ---------------------------------------------------------------------------
# benchmark math
# ---------------------------------------------------------------------------

def test_benchmark_total_and_per_window_means():
    spy = _series([100.0, 110.0, 121.0, 133.1])
    b = bt.benchmark_stats(spy, [1, 3])
    assert b["total_return"] == pytest.approx(0.331)
    assert b["mean_return_by_holding"]["1"] == pytest.approx(0.10)   # +10% each day
    assert b["mean_return_by_holding"]["3"] == pytest.approx(0.331)
    assert bt.benchmark_stats(pd.Series(dtype=float), [1]) == {"available": False}


# ---------------------------------------------------------------------------
# cache: csv.gz round trip, manifest reuse, --refresh-data, years mismatch
# ---------------------------------------------------------------------------

def _yf_frame(tickers, closes_by_ticker, start="2026-01-01"):
    """yf.download group_by='column'-shaped frame with O/H/L/C/Volume."""
    n = max(len(v) for v in closes_by_ticker.values())
    idx = pd.bdate_range(start, periods=n)
    data = {}
    for t in tickers:
        c = closes_by_ticker[t]
        data[("Open", t)] = [x + 50.0 for x in c]
        data[("High", t)] = [x + 60.0 for x in c]
        data[("Low", t)] = [x - 10.0 for x in c]
        data[("Close", t)] = list(c)
        data[("Volume", t)] = _flat_vol(len(c))
    return pd.DataFrame(data, index=idx)


def test_cache_roundtrip_and_reuse(tmp_path):
    calls = {"batch": 0, "spy": 0}

    def fake_batch(batch, years):
        calls["batch"] += 1
        return _yf_frame(batch, {t: SIG_CLOSES for t in batch})

    def fake_spy(batch, years):
        calls["spy"] += 1
        return _yf_frame(["SPY"], {"SPY": [100.0, 110.0, 121.0]})

    tickers = ["AAA", "BBB"]
    o1, hi1, lo1, c1, v1, spy1, m1 = bt.load_or_fetch_history(
        tickers, 8, tmp_path, fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 1, "spy": 1}
    assert list(c1.columns) == tickers
    assert list(hi1.columns) == tickers and list(lo1.columns) == tickers
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "batch-001.csv.gz").is_file()
    assert (tmp_path / "spy.csv.gz").is_file()
    assert m1["failed_tickers"] == []
    assert m1["fields"] == list(bt.FIELDS)

    # second call: cache hit, fetchers NOT called, identical data back
    o2, hi2, lo2, c2, v2, spy2, m2 = bt.load_or_fetch_history(
        tickers, 8, tmp_path, fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 1, "spy": 1}
    # csv round trip loses index freq / axis names by design — values must match
    pd.testing.assert_frame_equal(c1, c2, check_freq=False, check_names=False)
    pd.testing.assert_frame_equal(o1, o2, check_freq=False, check_names=False)
    pd.testing.assert_frame_equal(hi1, hi2, check_freq=False, check_names=False)
    pd.testing.assert_series_equal(spy1, spy2, check_freq=False, check_names=False)

    # --refresh-data forces a refetch
    bt.load_or_fetch_history(tickers, 8, tmp_path, refresh=True,
                             fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 2, "spy": 2}

    # different --years invalidates the cache
    bt.load_or_fetch_history(tickers, 4, tmp_path,
                             fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 3, "spy": 3}

    # a pre-High/Low manifest (no "fields" key) is invalid -> refetch
    manifest = json.loads((tmp_path / "manifest.json").read_text())
    del manifest["fields"]
    (tmp_path / "manifest.json").write_text(json.dumps(manifest))
    bt.load_or_fetch_history(tickers, 4, tmp_path,
                             fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 4, "spy": 4}


def test_failed_batch_recorded_not_fatal(tmp_path, monkeypatch):
    # with_retries would sleep for real; patch it to a single-attempt call
    monkeypatch.setattr(bt, "with_retries", lambda call, **kw: call())

    def fake_batch(batch, years):
        if "BAD" in batch:
            raise RuntimeError("yahoo down")
        return _yf_frame(batch, {t: SIG_CLOSES for t in batch})

    def fake_spy(batch, years):
        return _yf_frame(["SPY"], {"SPY": [100.0, 110.0]})

    monkeypatch.setattr(bt, "BATCH_SIZE", 1)
    opens, highs, lows, closes, volumes, spy, manifest = bt.load_or_fetch_history(
        ["GOOD", "BAD"], 8, tmp_path, fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert list(closes.columns) == ["GOOD"]
    assert manifest["failed_tickers"] == ["BAD"]


# ---------------------------------------------------------------------------
# end-to-end: run_backtest + markdown + json sanitizing
# ---------------------------------------------------------------------------

def _frames(closes_by_ticker):
    raw = _yf_frame(list(closes_by_ticker), closes_by_ticker)
    return raw["Open"], raw["High"], raw["Low"], raw["Close"], raw["Volume"]


def test_run_backtest_end_to_end_offline():
    opens, highs, lows, closes, volumes = _frames(
        {"SYN": SIG_CLOSES, "FLAT": [100.0] * 16})
    spy = _series([100.0] * 16)
    report = bt.run_backtest(opens, highs, lows, closes, volumes, spy,
                             th2=0.10, th5=0.30,
                             holdings=[1, 3], costs_bps=[10.0],
                             run_grid=True, run_variants=True,
                             exit_styles=("ma10", "trail2atr"))
    assert report["n_signals"] == 1
    assert report["n_tickers_with_signals"] == 1
    h1 = report["per_holding"]["1"]
    gross = h1["0bps"]["mean"]
    net = h1["10bps"]["mean"]
    assert gross == pytest.approx(SIG_CLOSES[11] / ENTRY_OPEN - 1.0)
    assert net == pytest.approx(gross - 0.0010)          # 10 bps
    # SPY is flat -> the excess return equals the raw return, net of the same cost
    assert h1["0bps"]["excess_mean"] == pytest.approx(gross)
    assert h1["10bps"]["excess_mean"] == pytest.approx(net)
    assert report["limitations"] == bt.LIMITATIONS
    assert "grid" in report and "variants_h10" in report
    assert "variants_h10_segments" in report
    assert report["dynamic_exits"]["styles"] == ["ma10", "trail2atr"]
    assert {r["variant"] for r in report["dynamic_exits"]["rows"]} == {"baseline", "52w_high"}
    assert report["benchmark_spy"]["total_return"] == pytest.approx(0.0)

    md = bt.render_markdown(report)
    assert md.splitlines()[2].startswith("## Limitations")  # limitations lead
    assert "SURVIVORSHIP BIAS" in md
    assert "VARIANT / EXIT SELECTION RISK" in md
    assert "Holding H=1" in md
    assert "exc mean" in md
    assert "Threshold grid" in md
    assert "Variants × train/val/OOS" in md
    assert "Dynamic exits vs fixed H" in md
    assert "closes only" in md
    assert "Train / validation / out-of-sample" in md

    # strict JSON: NaN/np types sanitized, timestamps stringified
    payload = json.dumps(bt._jsonable(report))
    assert "NaN" not in payload
    json.loads(payload)


def test_write_report_creates_dated_pair(tmp_path):
    report = {"params": {}, "x": np.float64(1.5)}
    json_path, md_path = bt.write_report(tmp_path, report, "# md body")
    assert json_path.parent == tmp_path / "backtest"
    assert json_path.name.startswith("report-") and json_path.suffix == ".json"
    assert md_path.name == json_path.name.replace(".json", ".md")
    assert json.loads(json_path.read_text())["x"] == 1.5
    assert md_path.read_text(encoding="utf-8") == "# md body"
    assert not list(tmp_path.rglob("*.tmp"))


# ---------------------------------------------------------------------------
# CLI arg parsing helpers
# ---------------------------------------------------------------------------

def test_parse_lists():
    assert bt._parse_int_list("20,1,5,5") == [1, 5, 20]
    assert bt._parse_float_list("50,5,10") == [5.0, 10.0, 50.0]
    with pytest.raises(Exception):
        bt._parse_int_list("0,5")
    with pytest.raises(Exception):
        bt._parse_float_list("-1")
