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
               holdings=(1, 3, 5, 10), curve_days=5):
    opens = opens if opens is not None else closes
    volumes = volumes if volumes is not None else _flat_vol(len(closes))
    rows = bt.build_ticker_trades(
        "SYN", _series(opens), _series(closes), _series(volumes),
        th2=th2, th5=th5, holdings=list(holdings), curve_days=curve_days)
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
    """yf.download group_by='column'-shaped frame with Open/Close/Volume."""
    n = max(len(v) for v in closes_by_ticker.values())
    idx = pd.bdate_range(start, periods=n)
    data = {}
    for t in tickers:
        c = closes_by_ticker[t]
        data[("Open", t)] = [x + 50.0 for x in c]
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
    o1, c1, v1, spy1, m1 = bt.load_or_fetch_history(
        tickers, 8, tmp_path, fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 1, "spy": 1}
    assert list(c1.columns) == tickers
    assert (tmp_path / "manifest.json").is_file()
    assert (tmp_path / "batch-001.csv.gz").is_file()
    assert (tmp_path / "spy.csv.gz").is_file()
    assert m1["failed_tickers"] == []

    # second call: cache hit, fetchers NOT called, identical data back
    o2, c2, v2, spy2, m2 = bt.load_or_fetch_history(
        tickers, 8, tmp_path, fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 1, "spy": 1}
    # csv round trip loses index freq / axis names by design — values must match
    pd.testing.assert_frame_equal(c1, c2, check_freq=False, check_names=False)
    pd.testing.assert_frame_equal(o1, o2, check_freq=False, check_names=False)
    pd.testing.assert_series_equal(spy1, spy2, check_freq=False, check_names=False)

    # --refresh-data forces a refetch
    bt.load_or_fetch_history(tickers, 8, tmp_path, refresh=True,
                             fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 2, "spy": 2}

    # different --years invalidates the cache
    bt.load_or_fetch_history(tickers, 4, tmp_path,
                             fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert calls == {"batch": 3, "spy": 3}


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
    opens, closes, volumes, spy, manifest = bt.load_or_fetch_history(
        ["GOOD", "BAD"], 8, tmp_path, fetch_batch=fake_batch, fetch_spy=fake_spy)
    assert list(closes.columns) == ["GOOD"]
    assert manifest["failed_tickers"] == ["BAD"]


# ---------------------------------------------------------------------------
# end-to-end: run_backtest + markdown + json sanitizing
# ---------------------------------------------------------------------------

def _frames(closes_by_ticker):
    raw = _yf_frame(list(closes_by_ticker), closes_by_ticker)
    return raw["Open"], raw["Close"], raw["Volume"]


def test_run_backtest_end_to_end_offline():
    opens, closes, volumes = _frames({"SYN": SIG_CLOSES, "FLAT": [100.0] * 16})
    spy = _series([100.0] * 16)
    report = bt.run_backtest(opens, closes, volumes, spy, th2=0.10, th5=0.30,
                             holdings=[1, 3], costs_bps=[10.0],
                             run_grid=True, run_variants=True)
    assert report["n_signals"] == 1
    assert report["n_tickers_with_signals"] == 1
    h1 = report["per_holding"]["1"]
    gross = h1["0bps"]["mean"]
    net = h1["10bps"]["mean"]
    assert gross == pytest.approx(SIG_CLOSES[11] / ENTRY_OPEN - 1.0)
    assert net == pytest.approx(gross - 0.0010)          # 10 bps
    assert report["limitations"] == bt.LIMITATIONS
    assert "grid" in report and "variants_h10" in report
    assert report["benchmark_spy"]["total_return"] == pytest.approx(0.0)

    md = bt.render_markdown(report)
    assert md.splitlines()[2].startswith("## Limitations")  # limitations lead
    assert "SURVIVORSHIP BIAS" in md
    assert "Holding H=1" in md
    assert "Threshold grid" in md
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
