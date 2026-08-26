"""Momentum-screener tests: pure metric math on fake OHLCV frames, filter
application, score normalization, offline end-to-end runs with injected
fetchers, the API endpoints, and the dashboard card container.

No network, no yfinance import, no real sleeps anywhere.
"""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.momentum_screener as ms

client = TestClient(main.app)


# ---------------------------------------------------------------------------
# helpers: fake daily bars
# ---------------------------------------------------------------------------

# 60 trading days; the last six closes give exact 2d/5d returns
# (2 trading days back = iloc[-3], 5 back = iloc[-6]):
#   ret_2d = 132/120 - 1 = 0.10     ret_5d = 132/100 - 1 = 0.32
WIN_CLOSES = [100.0] * 54 + [100.0, 105.0, 110.0, 120.0, 126.0, 132.0]
# constant 1M shares, 3M on the last day -> RVOL = 3.0 exactly
WIN_VOLUMES = [1_000_000.0] * 59 + [3_000_000.0]


def _series(values, n=None):
    n = n or len(values)
    idx = pd.bdate_range("2026-05-01", periods=n)
    padded = [np.nan] * (n - len(values)) + list(values)
    return pd.Series(padded, index=idx)


def _frame(series: dict) -> pd.DataFrame:
    """series: ticker -> (closes, volumes). Builds a yf.download
    group_by="column"-shaped frame: MultiIndex columns (field, ticker)."""
    n = max(len(c) for c, _ in series.values())
    idx = pd.bdate_range("2026-05-01", periods=n)
    data = {}
    for t, (c, v) in series.items():
        data[("Close", t)] = [np.nan] * (n - len(c)) + list(c)
        data[("Volume", t)] = [np.nan] * (n - len(v)) + list(v)
    return pd.DataFrame(data, index=idx)


# ---------------------------------------------------------------------------
# metric math (exact values on fake frames)
# ---------------------------------------------------------------------------

def test_metrics_exact_values():
    m = ms.compute_ticker_metrics(_series(WIN_CLOSES), _series(WIN_VOLUMES))
    assert m["price"] == 132.0
    assert m["ret_2d"] == pytest.approx(0.10, abs=1e-9)   # 132/120 - 1
    assert m["ret_5d"] == pytest.approx(0.32, abs=1e-9)   # 132/100 - 1
    assert m["rvol"] == pytest.approx(3.0)                # 3M / avg(prior 20 = 1M)
    # last 20 dollar bars: 15 x 100*1M, then 105/110/120/126*1M and 132*3M
    expected_dollar = (15 * 100e6 + 105e6 + 110e6 + 120e6 + 126e6 + 132.0 * 3e6) / 20
    assert m["avg_dollar_vol"] == pytest.approx(expected_dollar)
    ma20 = (15 * 100 + 105 + 110 + 120 + 126 + 132) / 20      # 104.65
    ma50 = (45 * 100 + 105 + 110 + 120 + 126 + 132) / 50      # 101.86
    assert m["dist_ma20"] == pytest.approx(132 / ma20 - 1, abs=1e-4)
    assert m["dist_ma50"] == pytest.approx(132 / ma50 - 1, abs=1e-4)
    assert m["new_20d_high"] is True
    assert m["new_50d_high"] is True


def test_not_a_new_high_when_below_prior_max():
    closes = [100.0] * 55 + [90.0, 91.0, 92.0, 93.0, 94.0]  # never re-takes 100
    m = ms.compute_ticker_metrics(_series(closes), _series([1e6] * 60))
    assert m["new_20d_high"] is False   # a 100-close sits inside the last 20
    assert m["new_50d_high"] is False


def test_rvol_denominator_excludes_the_last_day():
    # if the spike day were inside its own denominator, rvol would be
    # 2e6 / mean(19x1e6 + 2e6) ~ 1.90 instead of exactly 2.0
    m = ms.compute_ticker_metrics(_series([100.0] * 60),
                                  _series([1_000_000.0] * 59 + [2_000_000.0]))
    assert m["rvol"] == pytest.approx(2.0)


def test_insufficient_history_returns_none():
    assert ms.compute_ticker_metrics(_series([100.0] * 30), _series([1e6] * 30)) is None
    # enough closes but volume missing on the last bar
    vols = _series([1e6] * 60)
    vols.iloc[-1] = np.nan
    assert ms.compute_ticker_metrics(_series(WIN_CLOSES), vols) is None


# ---------------------------------------------------------------------------
# config defaults + filter application
# ---------------------------------------------------------------------------

def test_screener_config_defaults_and_overrides():
    cfg = ms.load_screener_config({})            # no "screener" section at all
    assert cfg["enabled"] is True
    assert cfg["min_market_cap"] == 20e9
    assert cfg["min_return_5d"] == 0.30
    assert cfg["require_above_ma50"] is True
    assert cfg["score_weights"]["ret5d"] == 0.30

    cfg = ms.load_screener_config({"screener": {
        "enabled": False, "min_price": 5, "min_rvol": "not-a-number",
        "score_weights": {"ret5d": 0.5, "bogus_component": 9.9},
    }})
    assert cfg["enabled"] is False
    assert cfg["min_price"] == 5.0
    assert cfg["min_rvol"] == 1.5                # unparsable -> default
    assert cfg["score_weights"]["ret5d"] == 0.5
    assert "bogus_component" not in cfg["score_weights"]


PASSING_METRICS = {
    "price": 132.0, "ret_2d": 0.20, "ret_5d": 0.32, "rvol": 3.0,
    "avg_dollar_vol": 1.18e8, "dist_ma20": 0.26, "dist_ma50": 0.30,
    "new_20d_high": True, "new_50d_high": True,
}


@pytest.mark.parametrize("field,bad_value", [
    ("price", 9.99),            # below min_price 10
    ("ret_2d", 0.09),           # below min_return_2d 0.10
    ("ret_5d", 0.29),           # below min_return_5d 0.30
    ("rvol", 1.49),             # below min_rvol 1.5
    ("avg_dollar_vol", 19e6),   # below min_avg_dollar_vol 20e6
    ("dist_ma20", -0.01),       # below MA20 while required
    ("dist_ma50", 0.0),         # not ABOVE MA50 while required
])
def test_each_filter_rejects(field, bad_value):
    cfg = ms.load_screener_config({})
    assert ms.passes_price_filters(PASSING_METRICS, cfg) is True
    assert ms.passes_price_filters({**PASSING_METRICS, field: bad_value}, cfg) is False


def test_ma_filters_can_be_disabled():
    cfg = ms.load_screener_config({"screener": {"require_above_ma20": False,
                                                "require_above_ma50": False}})
    below_both = {**PASSING_METRICS, "dist_ma20": -0.1, "dist_ma50": -0.2}
    assert ms.passes_price_filters(below_both, cfg) is True


# ---------------------------------------------------------------------------
# score normalization
# ---------------------------------------------------------------------------

def _cand(t, r5, r2, rv, d20, d50):
    return {"ticker": t, "ret_5d": r5, "ret_2d": r2, "rvol": rv,
            "dist_ma20": d20, "dist_ma50": d50}


def test_scores_min_max_normalized_across_survivors():
    cands = [
        _cand("TOP", 0.50, 0.20, 3.00, 0.100, 0.200),   # max on every component
        _cand("MID", 0.40, 0.15, 2.25, 0.075, 0.150),   # exact midpoint everywhere
        _cand("LOW", 0.30, 0.10, 1.50, 0.050, 0.100),   # min on every component
    ]
    ms.score_candidates(cands, dict(ms.DEFAULT_SCORE_WEIGHTS))
    by = {c["ticker"]: c["score"] for c in cands}
    assert by == {"TOP": 100.0, "MID": 50.0, "LOW": 0.0}
    assert [c["ticker"] for c in cands] == ["TOP", "MID", "LOW"]  # ranked desc


def test_single_survivor_scores_100():
    cands = [_cand("ONLY", 0.31, 0.11, 1.6, 0.01, 0.02)]
    ms.score_candidates(cands, dict(ms.DEFAULT_SCORE_WEIGHTS))
    assert cands[0]["score"] == 100.0


def test_scores_respect_weights():
    # only ret5d differs; with all weight on ret5d the spread is the full 0-100
    cands = [_cand("A", 0.50, 0.10, 2.0, 0.05, 0.05),
             _cand("B", 0.30, 0.10, 2.0, 0.05, 0.05)]
    ms.score_candidates(cands, {"ret5d": 1.0, "ret2d": 0.0, "rvol": 0.0,
                                "dist_ma20": 0.0, "dist_ma50": 0.0})
    by = {c["ticker"]: c["score"] for c in cands}
    assert by == {"A": 100.0, "B": 0.0}


# ---------------------------------------------------------------------------
# run_screen: offline end-to-end with injected fetchers
# ---------------------------------------------------------------------------

FLAT = ([100.0] * 60, [50_000_000.0] * 60)          # liquid but zero momentum
THIN = ([100.0] * 10, [1e6] * 10)                    # not enough history


def _universe(*tickers):
    return {"name": "test-universe", "tickers": list(tickers)}


def _year_frame(ticker, closes):
    idx = pd.bdate_range("2025-08-25", periods=len(closes))
    return pd.DataFrame({ticker: closes}, index=idx)


def test_run_screen_end_to_end():
    cfg = ms.load_screener_config({})
    frame = _frame({"WIN": (WIN_CLOSES, WIN_VOLUMES), "LOSE": FLAT, "THIN": THIN})
    cap_calls = []

    def fake_cap(t):
        cap_calls.append(t)
        return 50e9

    doc = ms.run_screen(
        cfg, _universe("WIN", "LOSE", "THIN"),
        fetch=lambda batch: frame,
        fetch_market_cap=fake_cap,
        fetch_52w=lambda ts: _year_frame("WIN", [80.0] * 200 + [132.0]),
    )
    assert doc["scanned"] == 3
    assert doc["passed_filters"] == 1
    assert doc["skipped"] == 1                      # THIN: insufficient history
    assert doc["universe"] == "test-universe"
    assert doc["criteria"]["min_return_5d"] == 0.30
    assert cap_calls == ["WIN"]                     # cap fetched ONLY for survivors
    (cand,) = doc["candidates"]
    assert cand["ticker"] == "WIN"
    assert cand["score"] == 100.0                   # lone survivor
    assert cand["market_cap"] == 50e9
    assert cand["cap_unknown"] is False
    assert cand["new_52w_high"] is True
    # exactly AT the min_return_2d gate (0.10): >= passes, boundary included
    assert cand["ret_2d"] == pytest.approx(0.10, abs=1e-4)


def test_unknown_market_cap_kept_but_flagged():
    cfg = ms.load_screener_config({})
    frame = _frame({"WIN": (WIN_CLOSES, WIN_VOLUMES)})
    doc = ms.run_screen(cfg, _universe("WIN"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: None,
                        fetch_52w=lambda ts: _year_frame("WIN", [132.0]))
    (cand,) = doc["candidates"]
    assert cand["market_cap"] is None
    assert cand["cap_unknown"] is True


def test_known_small_cap_dropped():
    cfg = ms.load_screener_config({})
    frame = _frame({"WIN": (WIN_CLOSES, WIN_VOLUMES)})
    doc = ms.run_screen(cfg, _universe("WIN"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 1e9,
                        fetch_52w=lambda ts: pd.DataFrame())
    assert doc["candidates"] == []
    assert doc["passed_filters"] == 0


def test_52w_flag_null_when_1y_fetch_fails():
    cfg = ms.load_screener_config({})
    frame = _frame({"WIN": (WIN_CLOSES, WIN_VOLUMES)})

    def boom(ts):
        raise RuntimeError("yahoo down")

    doc = ms.run_screen(cfg, _universe("WIN"), fetch=lambda b: frame,
                        fetch_market_cap=lambda t: 50e9, fetch_52w=boom)
    assert doc["candidates"][0]["new_52w_high"] is None


def test_empty_universe():
    cfg = ms.load_screener_config({})
    doc = ms.run_screen(cfg, _universe(), fetch=lambda b: pd.DataFrame(),
                        fetch_market_cap=lambda t: None,
                        fetch_52w=lambda ts: pd.DataFrame())
    assert doc["scanned"] == 0
    assert doc["candidates"] == []
    assert doc["passed_filters"] == 0
    assert doc["skipped"] == 0


def test_universe_fetch_is_chunked_into_batches_of_100():
    batches = []

    def fake_fetch(batch):
        batches.append(len(batch))
        return pd.DataFrame()

    tickers = [f"T{i}" for i in range(250)]
    ms.fetch_universe_history(tickers, fetch=fake_fetch)
    assert batches == [100, 100, 50]


def test_write_outputs_dated_plus_latest(tmp_path):
    doc = {"date": "2026-08-25", "candidates": [], "scanned": 0,
           "passed_filters": 0, "skipped": 0}
    targets = ms.write_outputs(tmp_path, doc)
    assert [t.name for t in targets] == ["2026-08-25.json", "screener-latest.json"]
    for t in targets:
        assert json.loads(t.read_text())["date"] == "2026-08-25"
    assert not list(tmp_path.rglob("*.tmp"))       # atomic write leaves no temp file


# ---------------------------------------------------------------------------
# run_daily_screen gating (never touches the network in these states)
# ---------------------------------------------------------------------------

def test_run_daily_screen_disabled(tmp_path):
    cfg_path = tmp_path / "tickers.json"
    cfg_path.write_text(json.dumps({"tickers": ["A", "B"],
                                    "screener": {"enabled": False}}))
    assert ms.run_daily_screen(cfg_path, tmp_path) is None
    assert not (tmp_path / "screener-latest.json").exists()


def test_run_daily_screen_missing_universe(tmp_path):
    cfg_path = tmp_path / "tickers.json"
    cfg_path.write_text(json.dumps({"tickers": ["A", "B"]}))
    assert ms.run_daily_screen(cfg_path, tmp_path,
                               universe_path=tmp_path / "nope.json") is None


def test_run_daily_screen_writes_outputs(tmp_path, monkeypatch):
    cfg_path = tmp_path / "tickers.json"
    cfg_path.write_text(json.dumps({"tickers": ["A", "B"]}))
    (tmp_path / "universe.json").write_text(json.dumps(
        {"name": "mini", "tickers": ["WIN"]}))
    frame = _frame({"WIN": (WIN_CLOSES, WIN_VOLUMES)})
    monkeypatch.setattr(ms, "_default_fetch", lambda batch: frame)
    monkeypatch.setattr(ms, "_default_fetch_market_cap", lambda t: 30e9)
    monkeypatch.setattr(ms, "_default_fetch_52w",
                        lambda ts: _year_frame("WIN", [132.0]))
    doc = ms.run_daily_screen(cfg_path, tmp_path,
                              universe_path=tmp_path / "universe.json")
    assert doc["passed_filters"] == 1
    latest = json.loads((tmp_path / "screener-latest.json").read_text())
    assert latest["candidates"][0]["ticker"] == "WIN"
    assert (tmp_path / "screener" / f"{doc['date']}.json").is_file()


# ---------------------------------------------------------------------------
# API endpoints (same guarded pattern as the correlation endpoints)
# ---------------------------------------------------------------------------

SCREEN_DOC = {"date": "2026-08-25", "universe": "us-large-cap",
              "criteria": {}, "candidates": [], "scanned": 480,
              "passed_filters": 0, "skipped": 3}


@pytest.fixture
def screener_output_dir(tmp_path, monkeypatch):
    sdir = tmp_path / "screener"
    sdir.mkdir()
    (sdir / "2026-08-25.json").write_text(json.dumps(SCREEN_DOC))
    (tmp_path / "screener-latest.json").write_text(json.dumps(SCREEN_DOC))
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)


def test_screener_endpoints_empty_state(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path / "nope")
    assert client.get("/api/v1/screener").json() == {"dates": []}
    assert client.get("/api/v1/screener/latest").status_code == 404
    assert client.get("/api/v1/screener/2026-08-25").status_code == 404
    assert client.get("/api/v1/screener/not-a-date").status_code == 404


def test_screener_endpoints_populated(screener_output_dir):
    assert client.get("/api/v1/screener").json() == {"dates": ["2026-08-25"]}
    for url in ("/api/v1/screener/latest", "/api/v1/screener/2026-08-25"):
        doc = client.get(url).json()
        assert doc["scanned"] == 480
        assert doc["candidates"] == []


# ---------------------------------------------------------------------------
# dashboard card container (incl. the empty state + caveat copy)
# ---------------------------------------------------------------------------

def test_dashboard_has_screener_card():
    body = client.get("/").text
    assert 'id="screener-section"' in body       # whole card, hidden until data
    assert 'id="screener-table"' in body         # ranked candidates table
    assert 'id="screener-meta"' in body          # date + scanned/passed + criteria
    assert 'id="screener-empty"' in body         # empty state container
    assert "normal" in body                       # empty state says it's expected
    assert "Discovery screen, not buy signals" in body   # the caveat line
    assert "scorebar" in body                     # score meter styles shipped
