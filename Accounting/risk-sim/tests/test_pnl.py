"""CFD account P&L (pnl_timeseries) tests for app.daily_correlation.

Pure math + fake bars only — no yfinance import, no network. The YTD fetch is
monkeypatched (or a prefetched frame is passed) wherever the builder runs.
"""
import datetime
import json

import pandas as pd

import app.daily_correlation as dc

TODAY = datetime.date(2026, 8, 24)


def _frame(days: dict) -> pd.DataFrame:
    """days: date_str -> {ticker: close or None (no bar)} — Close-only frame
    with the MultiIndex (ticker, field) layout fetch_ytd_ohlc returns."""
    tickers = sorted({t for bars in days.values() for t in bars})
    data = {(t, "Close"): [] for t in tickers}
    for bars in days.values():
        for t in tickers:
            data[(t, "Close")].append(bars.get(t))
    return pd.DataFrame(data, index=pd.DatetimeIndex(pd.to_datetime(list(days))))


# ---- compute_pnl_timeseries: pure math ----

def test_pnl_known_quantities_first_close_basis():
    daily = _frame({
        "2026-01-02": {"AAPL": 100.0, "MSFT": 200.0},
        "2026-01-05": {"AAPL": 110.0, "MSFT": 195.0},
        "2026-01-06": {"AAPL": 105.0, "MSFT": 210.0},
    })
    doc = dc.compute_pnl_timeseries(daily, {"AAPL": 10, "MSFT": 5}, today=TODAY)
    assert doc["basis_by_ticker"] == {
        "AAPL": {"basis": "first_close", "value": 100.0},
        "MSFT": {"basis": "first_close", "value": 200.0},
    }
    p1, p2, p3 = doc["points"]
    assert p1 == {"date": "2026-01-02", "pnl": 0.0, "daily_change": None, "n_tickers": 2}
    # 10*(110-100) + 5*(195-200) = 100 - 25 = 75
    assert p2 == {"date": "2026-01-05", "pnl": 75.0, "daily_change": 75.0, "n_tickers": 2}
    # 10*(105-100) + 5*(210-200) = 50 + 50 = 100
    assert p3 == {"date": "2026-01-06", "pnl": 100.0, "daily_change": 25.0, "n_tickers": 2}


def test_pnl_entry_price_basis_beats_first_close():
    daily = _frame({
        "2026-01-02": {"AAPL": 100.0, "MSFT": 200.0},
        "2026-01-05": {"AAPL": 110.0, "MSFT": 210.0},
    })
    doc = dc.compute_pnl_timeseries(daily, {"AAPL": 10, "MSFT": 5},
                                    entry_prices={"AAPL": 90.0}, today=TODAY)
    assert doc["basis_by_ticker"]["AAPL"] == {"basis": "entry_price", "value": 90.0}
    assert doc["basis_by_ticker"]["MSFT"] == {"basis": "first_close", "value": 200.0}
    p1, p2 = doc["points"]
    assert p1["pnl"] == 100.0     # AAPL 10*(100-90); MSFT 5*(200-200)=0
    assert p1["daily_change"] is None
    assert p2["pnl"] == 250.0     # 10*(110-90) + 5*(210-200)
    assert p2["daily_change"] == 150.0


def test_pnl_missing_bars_skip_ticker_and_track_n_tickers():
    daily = _frame({
        "2026-01-02": {"AAPL": 100.0, "MSFT": 200.0},
        "2026-01-05": {"AAPL": 110.0, "MSFT": None},   # MSFT holiday
        "2026-01-06": {"AAPL": 120.0, "MSFT": 220.0},
    })
    doc = dc.compute_pnl_timeseries(daily, {"AAPL": 10, "MSFT": 5}, today=TODAY)
    p1, p2, p3 = doc["points"]
    assert (p1["n_tickers"], p2["n_tickers"], p3["n_tickers"]) == (2, 1, 2)
    assert p2["pnl"] == 100.0     # AAPL only: 10*(110-100)
    assert p3["pnl"] == 300.0     # 10*(120-100) + 5*(220-200)
    assert p3["daily_change"] == 200.0


def test_pnl_drops_todays_partial_bar():
    daily = _frame({
        "2026-08-21": {"AAPL": 100.0},
        "2026-08-24": {"AAPL": 999.0},   # == today: live partial session
    })
    doc = dc.compute_pnl_timeseries(daily, {"AAPL": 10}, today=TODAY)
    assert [p["date"] for p in doc["points"]] == ["2026-08-21"]
    # and today's bar must not have become the basis either
    assert doc["basis_by_ticker"]["AAPL"]["value"] == 100.0


def test_pnl_only_today_gives_none():
    daily = _frame({"2026-08-24": {"AAPL": 100.0}})
    assert dc.compute_pnl_timeseries(daily, {"AAPL": 10}, today=TODAY) is None


def test_pnl_zero_quantity_and_empty_frame_give_none():
    assert dc.compute_pnl_timeseries(pd.DataFrame(), {"AAPL": 10}, today=TODAY) is None
    assert dc.compute_pnl_timeseries(None, {"AAPL": 10}, today=TODAY) is None
    daily = _frame({"2026-01-02": {"AAPL": 100.0}})
    assert dc.compute_pnl_timeseries(daily, {"AAPL": 0}, today=TODAY) is None


# ---- build_pnl_timeseries ----

def _cfg(entry_prices=None):
    return {"tickers": ["AAPL"], "positions": {"AAPL": 10},
            "entry_prices": entry_prices or {}, "margin_rate": 0.20}


def test_build_pnl_uses_prefetched_frame_without_fetching(monkeypatch):
    def boom(tickers):
        raise AssertionError("must not fetch when a frame is supplied")
    monkeypatch.setattr(dc, "fetch_ytd_ohlc", boom)
    daily = _frame({"2026-01-02": {"AAPL": 100.0}, "2026-01-05": {"AAPL": 110.0}})
    doc = dc.build_pnl_timeseries(_cfg(), daily=daily)
    assert doc["points"][-1]["pnl"] == 100.0


def test_build_pnl_fetches_when_no_frame_given(monkeypatch):
    daily = _frame({"2026-01-02": {"AAPL": 100.0}, "2026-01-05": {"AAPL": 108.0}})
    monkeypatch.setattr(dc, "fetch_ytd_ohlc", lambda tickers: daily)
    doc = dc.build_pnl_timeseries(_cfg(entry_prices={"AAPL": 95.0}))
    assert doc["basis_by_ticker"]["AAPL"]["basis"] == "entry_price"
    assert doc["points"][-1]["pnl"] == 130.0    # 10*(108-95)


def test_build_pnl_none_on_fetch_failure(monkeypatch):
    def boom(tickers):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(dc, "fetch_ytd_ohlc", boom)
    assert dc.build_pnl_timeseries(_cfg()) is None    # never raises


def test_build_pnl_none_without_positions():
    cfg = {"tickers": ["AAPL"], "positions": {"AAPL": 0}, "entry_prices": {}}
    assert dc.build_pnl_timeseries(cfg) is None       # returns before any fetch


# ---- config normalization: both position forms ----

def test_load_config_plain_quantity_form(tmp_path):
    p = tmp_path / "tickers.json"
    p.write_text(json.dumps({"tickers": ["AAPL", "MSFT"],
                             "positions": {"AAPL": 100}}))
    cfg = dc.load_config(p)
    assert cfg["positions"] == {"AAPL": 100.0, "MSFT": 0.0}
    assert cfg["entry_prices"] == {}


def test_load_config_object_form_with_entry_price(tmp_path):
    p = tmp_path / "tickers.json"
    p.write_text(json.dumps({"tickers": ["AAPL", "MSFT", "CSCO"], "positions": {
        "AAPL": {"qty": 100, "entry_price": 185.5},   # object form, full
        "MSFT": {"qty": 50},                          # object form, no entry
        "CSCO": 25,                                   # plain form, mixed freely
    }}))
    cfg = dc.load_config(p)
    assert cfg["positions"] == {"AAPL": 100.0, "MSFT": 50.0, "CSCO": 25.0}
    assert cfg["entry_prices"] == {"AAPL": 185.5}


def test_load_config_bad_values_fall_back(tmp_path):
    p = tmp_path / "tickers.json"
    p.write_text(json.dumps({"tickers": ["AAPL", "MSFT"], "positions": {
        "AAPL": {"qty": "not-a-number", "entry_price": "nope"},
        "MSFT": {"entry_price": 100.0},               # no qty at all -> 0
    }}))
    cfg = dc.load_config(p)
    assert cfg["positions"] == {"AAPL": 0.0, "MSFT": 0.0}
    assert cfg["entry_prices"] == {"MSFT": 100.0}
