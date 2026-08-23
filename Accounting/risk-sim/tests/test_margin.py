"""CFD margin-account tests for app.daily_correlation.

Pure math + fake bars only — no yfinance import, no network. The fetch
functions are monkeypatched wherever build_margin_account is exercised.
"""
import datetime
import json

import pandas as pd

import app.daily_correlation as dc

RATE = 0.20
DAY = datetime.date(2026, 8, 13)


def _row(ticker, qty, o, m, c, source="intraday"):
    return {"ticker": ticker, "quantity": qty,
            "open": o, "midday": m, "close": c, "midday_source": source}


def _daily_frame(bars: dict) -> pd.DataFrame:
    """bars: ticker -> (open, high, low, close), single trading day."""
    data = {}
    for t, (o, h, low, c) in bars.items():
        for field, v in zip(("Open", "High", "Low", "Close"), (o, h, low, c)):
            data[(t, field)] = [v]
    return pd.DataFrame(data, index=pd.DatetimeIndex([pd.Timestamp("2026-08-13")]))


def _intraday_series(date_str, hours, values):
    idx = pd.DatetimeIndex([pd.Timestamp(f"{date_str} {h:02d}:00") for h in hours])
    return pd.Series([float(v) for v in values], index=idx)


# ---- compute_margin_account: pure math ----

def test_margin_math_single_ticker():
    doc = dc.compute_margin_account([_row("AAPL", 100, 100.0, 110.0, 120.0)], RATE)
    (row,) = doc["rows"]
    assert row["margin_open"] == 2000.0     # 100 * 100 * 0.2
    assert row["margin_midday"] == 2200.0   # 110 * 100 * 0.2
    assert row["margin_close"] == 2400.0    # 120 * 100 * 0.2
    t = doc["totals"]
    assert t["position_value_open"] == 10000.0
    assert t["position_value_close"] == 12000.0
    assert t["peak_margin"] == 2400.0
    assert doc["margin_rate"] == RATE


def test_totals_and_peak_across_tickers():
    rows = [_row("AAPL", 100, 100, 130, 90), _row("MSFT", 10, 200, 210, 220)]
    doc = dc.compute_margin_account(rows, RATE)
    t = doc["totals"]
    assert t["margin_open"] == 2400.0    # (100*100 + 10*200) * 0.2
    assert t["margin_midday"] == 3020.0  # (100*130 + 10*210) * 0.2
    assert t["margin_close"] == 2240.0   # (100*90  + 10*220) * 0.2
    assert t["peak_margin"] == 3020.0    # midday is the peak, not close


def test_zero_quantity_omitted_from_rows():
    rows = [_row("AAPL", 100, 100, 100, 100), _row("ZERO", 0, 50, 50, 50)]
    doc = dc.compute_margin_account(rows, RATE)
    assert [r["ticker"] for r in doc["rows"]] == ["AAPL"]
    assert doc["totals"]["margin_open"] == 2000.0  # ZERO contributes nothing


def test_empty_rows_give_zero_totals():
    doc = dc.compute_margin_account([], RATE)
    assert doc["rows"] == []
    assert doc["totals"]["peak_margin"] == 0.0
    assert doc["totals"]["margin_midday"] == 0.0


# ---- midday_from_intraday ----

def test_midday_picks_bar_nearest_session_midpoint():
    s = _intraday_series("2026-08-13", range(9, 16), [1, 2, 3, 4, 5, 6, 7])
    # session 09:00-15:00 -> midpoint 12:00 -> 4th bar
    assert dc.midday_from_intraday(s, DAY) == 4.0


def test_midday_none_when_wrong_date_or_empty():
    s = _intraday_series("2026-08-12", [9, 10], [1, 2])  # previous session only
    assert dc.midday_from_intraday(s, DAY) is None
    assert dc.midday_from_intraday(pd.Series(dtype=float), DAY) is None


# ---- build_margin_account with fake fetchers (no Yahoo) ----

def test_hl_midpoint_fallback_when_no_intraday(monkeypatch):
    monkeypatch.setattr(dc, "fetch_daily_ohlc",
                        lambda tickers: _daily_frame({"AAPL": (100, 120, 100, 110)}))
    monkeypatch.setattr(dc, "fetch_intraday_closes", lambda tickers: None)
    cfg = {"tickers": ["AAPL", "MSFT"], "positions": {"AAPL": 10, "MSFT": 0},
           "margin_rate": RATE}
    doc = dc.build_margin_account(cfg)
    (row,) = doc["rows"]  # MSFT qty 0 -> omitted
    assert row["midday_source"] == "hl_midpoint_proxy"
    assert row["midday"] == 110.0            # (120 + 100) / 2
    assert row["margin_midday"] == 220.0     # 110 * 10 * 0.2
    assert doc["skipped"] == {}


def test_intraday_midday_used_when_available(monkeypatch):
    monkeypatch.setattr(dc, "fetch_daily_ohlc",
                        lambda tickers: _daily_frame({"AAPL": (100, 120, 100, 110)}))
    intra = pd.DataFrame({"AAPL": _intraday_series("2026-08-13", [9, 12, 15],
                                                   [101, 105, 109])})
    monkeypatch.setattr(dc, "fetch_intraday_closes", lambda tickers: intra)
    cfg = {"tickers": ["AAPL", "MSFT"], "positions": {"AAPL": 10, "MSFT": 0},
           "margin_rate": RATE}
    (row,) = dc.build_margin_account(cfg)["rows"]
    assert row["midday_source"] == "intraday"
    assert row["midday"] == 105.0
    assert row["margin_midday"] == 210.0


def test_positioned_ticker_without_data_is_skipped(monkeypatch):
    monkeypatch.setattr(dc, "fetch_daily_ohlc",
                        lambda tickers: _daily_frame({"AAPL": (100, 120, 100, 110)}))
    monkeypatch.setattr(dc, "fetch_intraday_closes", lambda tickers: None)
    cfg = {"tickers": ["AAPL", "MSFT"], "positions": {"AAPL": 10, "MSFT": 5},
           "margin_rate": RATE}
    doc = dc.build_margin_account(cfg)
    assert [r["ticker"] for r in doc["rows"]] == ["AAPL"]
    assert "MSFT" in doc["skipped"]


def test_returns_none_without_any_position():
    # returns before any fetch — would raise if it tried to hit Yahoo
    cfg = {"tickers": ["AAPL", "MSFT"], "positions": {"AAPL": 0, "MSFT": 0},
           "margin_rate": RATE}
    assert dc.build_margin_account(cfg) is None


# ---- margin_timeseries: YTD open/midday/close totals per trading day ----

def _multi_day_frame(days: dict) -> pd.DataFrame:
    """days: date_str -> {ticker: (open, high, low, close) or None (no bar)}."""
    tickers = sorted({t for bars in days.values() for t in bars})
    data = {(t, f): [] for t in tickers for f in ("Open", "High", "Low", "Close")}
    for bars in days.values():
        for t in tickers:
            ohlc = bars.get(t)
            for f, v in zip(("Open", "High", "Low", "Close"),
                            ohlc if ohlc is not None else (None,) * 4):
                data[(t, f)].append(v)
    return pd.DataFrame(data, index=pd.DatetimeIndex(pd.to_datetime(list(days))))


def test_timeseries_known_sums_across_tickers():
    daily = _multi_day_frame({
        "2026-01-02": {"AAPL": (100, 120, 100, 110), "MSFT": (200, 220, 180, 210)},
        "2026-01-05": {"AAPL": (110, 130, 110, 120), "MSFT": (210, 230, 190, 220)},
    })
    doc = dc.compute_margin_timeseries(daily, {"AAPL": 10, "MSFT": 5}, RATE)
    assert doc["margin_rate"] == RATE
    assert doc["midday_source"] == "hl_midpoint_proxy"
    p1, p2 = doc["points"]
    assert p1 == {"date": "2026-01-02",
                  "open": 400.0,    # (100*10 + 200*5) * 0.2
                  "midday": 420.0,  # (110*10 + 200*5) * 0.2  (hl midpoints 110, 200)
                  "close": 430.0,   # (110*10 + 210*5) * 0.2
                  "n_tickers": 2}
    assert p2["date"] == "2026-01-05"
    assert p2["open"] == 430.0        # (110*10 + 210*5) * 0.2
    assert p2["n_tickers"] == 2


def test_timeseries_partial_day_sums_present_tickers_only():
    daily = _multi_day_frame({
        "2026-01-02": {"AAPL": (100, 120, 100, 110), "MSFT": (200, 220, 180, 210)},
        "2026-01-05": {"AAPL": (110, 130, 110, 120), "MSFT": None},  # MSFT holiday
    })
    doc = dc.compute_margin_timeseries(daily, {"AAPL": 10, "MSFT": 5}, RATE)
    p1, p2 = doc["points"]
    assert p1["n_tickers"] == 2
    assert p2["n_tickers"] == 1          # partial day is visible, not hidden
    assert p2["open"] == 220.0           # 110*10*0.2 — AAPL only
    assert p2["close"] == 240.0          # 120*10*0.2


def test_timeseries_zero_quantity_ticker_excluded():
    daily = _multi_day_frame({
        "2026-01-02": {"AAPL": (100, 120, 100, 110), "ZERO": (50, 60, 40, 55)},
    })
    doc = dc.compute_margin_timeseries(daily, {"AAPL": 10, "ZERO": 0}, RATE)
    (p,) = doc["points"]
    assert p["n_tickers"] == 1
    assert p["open"] == 200.0            # ZERO contributes nothing


def test_timeseries_empty_frame_gives_none():
    assert dc.compute_margin_timeseries(pd.DataFrame(), {"AAPL": 10}, RATE) is None
    assert dc.compute_margin_timeseries(None, {"AAPL": 10}, RATE) is None
    # frame with data but no positioned ticker present
    daily = _multi_day_frame({"2026-01-02": {"MSFT": (200, 220, 180, 210)}})
    assert dc.compute_margin_timeseries(daily, {"AAPL": 10, "MSFT": 0}, RATE) is None


# ---- build_margin_timeseries + today's intraday refinement ----

def _ts_cfg():
    return {"tickers": ["AAPL"], "positions": {"AAPL": 10}, "margin_rate": RATE}


def _one_day_ytd(monkeypatch):
    daily = _multi_day_frame({"2026-08-12": {"AAPL": (90, 100, 80, 95)},
                              "2026-08-13": {"AAPL": (100, 120, 100, 110)}})
    monkeypatch.setattr(dc, "fetch_ytd_ohlc", lambda tickers: daily)


def test_build_timeseries_refines_today_with_intraday_midday(monkeypatch):
    _one_day_ytd(monkeypatch)
    account = {"rows": [{"ticker": "AAPL", "midday_source": "intraday"}],
               "totals": {"margin_close": 220.0,   # matches 110*10*0.2 -> same session
                          "margin_midday": 212.34}}
    doc = dc.build_margin_timeseries(_ts_cfg(), account)
    last = doc["points"][-1]
    assert last["midday"] == 212.34               # real intraday total copied in
    assert last["midday_source"] == "intraday"
    assert "midday_source" not in doc["points"][0]  # history stays proxy
    assert doc["points"][0]["midday"] == 180.0      # (100+80)/2 * 10 * 0.2


def test_build_timeseries_keeps_proxy_when_sessions_mismatch(monkeypatch):
    _one_day_ytd(monkeypatch)
    account = {"rows": [{"ticker": "AAPL", "midday_source": "intraday"}],
               "totals": {"margin_close": 999.0,   # different session/bars
                          "margin_midday": 212.34}}
    doc = dc.build_margin_timeseries(_ts_cfg(), account)
    assert doc["points"][-1]["midday"] == 220.0    # (120+100)/2 * 10 * 0.2
    assert "midday_source" not in doc["points"][-1]


def test_build_timeseries_keeps_proxy_when_account_is_proxy_too(monkeypatch):
    _one_day_ytd(monkeypatch)
    account = {"rows": [{"ticker": "AAPL", "midday_source": "hl_midpoint_proxy"}],
               "totals": {"margin_close": 220.0, "margin_midday": 212.34}}
    doc = dc.build_margin_timeseries(_ts_cfg(), account)
    assert doc["points"][-1]["midday"] == 220.0    # nothing better to copy


def test_build_timeseries_none_on_fetch_failure(monkeypatch):
    def boom(tickers):
        raise RuntimeError("yahoo down")
    monkeypatch.setattr(dc, "fetch_ytd_ohlc", boom)
    assert dc.build_margin_timeseries(_ts_cfg(), None) is None  # never raises


def test_build_timeseries_none_without_positions():
    cfg = {"tickers": ["AAPL"], "positions": {"AAPL": 0}, "margin_rate": RATE}
    assert dc.build_margin_timeseries(cfg, None) is None  # returns before any fetch


# ---- config parsing ----

def test_load_config_positions_and_margin_rate(tmp_path):
    p = tmp_path / "tickers.json"
    p.write_text(json.dumps({"tickers": ["AAPL", "MSFT"],
                             "positions": {"AAPL": 100}, "margin_rate": 0.25}))
    cfg = dc.load_config(p)
    assert cfg["positions"] == {"AAPL": 100.0, "MSFT": 0.0}  # missing -> 0
    assert cfg["margin_rate"] == 0.25


def test_load_config_margin_defaults(tmp_path):
    p = tmp_path / "tickers.json"
    p.write_text(json.dumps({"tickers": ["AAPL", "MSFT"]}))
    cfg = dc.load_config(p)
    assert cfg["positions"] == {"AAPL": 0.0, "MSFT": 0.0}
    assert cfg["margin_rate"] == 0.20
