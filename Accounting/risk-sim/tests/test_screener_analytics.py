"""Second-stage analytics on screener rows: pace test, quality heuristic,
sector concentration, cross-day persistence, and their wiring into
run_screen() / the hits endpoint. All offline (fake frames, injected
fetchers, tmp_path)."""
import json

import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

import app.main as main
import app.momentum_screener as ms

client = TestClient(main.app)

B90, B270 = ms.trading_days_for_window(90), ms.trading_days_for_window(270)


# ---------------------------------------------------------------------------
# 1. pace / deceleration test
# ---------------------------------------------------------------------------

def test_pace_pulling_back_when_short_window_negative():
    # SNDK on 2026-08-29: 90d -12.4%, 270d +565% -> already round-tripping
    p = ms.pace_analysis(-0.124, 5.651, B90, B270)
    assert p["label"] == "pulling_back"
    assert p["prior_return"] > 5          # implied earlier stretch carried the whole move
    assert p["ratio"] is None


def test_pace_accelerating_when_recent_pace_faster():
    # MRNA: +202% in 90d inside +449% over 270d -> recent stretch much faster
    p = ms.pace_analysis(2.025, 4.495, B90, B270)
    assert p["label"] == "accelerating"
    assert p["ratio"] > ms.PACE_ACCEL_RATIO
    assert p["bars_short"] == B90 and p["bars_prior"] == B270 - B90


def test_pace_decelerating_and_steady():
    # DELL: +12.4% in 90d vs +257% over 270d -> recent pace well below prior
    assert ms.pace_analysis(0.124, 2.574, B90, B270)["label"] == "decelerating"
    # identical per-bar pace in both stretches -> steady (ratio == 1)
    per_bar = 0.01
    short = (1 + per_bar) ** B90 - 1
    long_ = (1 + per_bar) ** B270 - 1
    p = ms.pace_analysis(short, long_, B90, B270)
    assert p["label"] == "steady"
    assert p["ratio"] == pytest.approx(1.0, abs=0.01)


def test_pace_none_when_inputs_unusable():
    assert ms.pace_analysis(None, 2.0, B90, B270) is None
    assert ms.pace_analysis(0.5, None, B90, B270) is None
    assert ms.pace_analysis(0.5, 1.0, B270, B90) is None     # short window longer than long
    assert ms.pace_analysis(-1.5, 1.0, B90, B270) is None    # impossible (-150%) return


def test_pace_steady_when_prior_stretch_flat_or_down():
    # the whole gain happened in the short window and the prior stretch fell:
    # no positive prior pace to compare against -> steady, ratio None
    p = ms.pace_analysis(1.5, 1.2, B90, B270)
    assert p["label"] == "steady" and p["ratio"] is None
    assert p["prior_return"] < 0


# ---------------------------------------------------------------------------
# 2. quality heuristic
# ---------------------------------------------------------------------------

def test_quality_breakdown_and_bounds():
    row = {"rvol": 2.5, "new_52w_high": True, "ret_270d": 1.2}
    q = ms.quality_score(row, {"label": "steady"})
    assert q["volume_pts"] == 40            # RVOL clipped at 2x
    assert q["pace_pts"] == 30 and q["trend_pts"] == 20
    assert q["magnitude_penalty"] == 0 and q["magnitude_pts"] == 10
    assert q["total"] == 100

    worst = ms.quality_score({"rvol": 0.0, "new_52w_high": False, "ret_270d": 5.6},
                             {"label": "pulling_back"})
    assert worst["total"] == 0 and worst["magnitude_penalty"] == 10

    mid = ms.quality_score({"rvol": 1.0, "new_52w_high": None, "ret_270d": 2.0},
                           {"label": "decelerating"})
    assert mid == {"total": 45, "volume_pts": 20, "pace_pts": 20, "trend_pts": 0,
                   "magnitude_pts": 5, "magnitude_penalty": 5, "pace_label": "decelerating"}


def test_quality_tolerates_missing_inputs():
    q = ms.quality_score({}, None)
    assert q["pace_pts"] == 0 and q["volume_pts"] == 0 and q["pace_label"] is None
    assert 0 <= q["total"] <= 100
    assert ms.quality_score({"rvol": "garbage"}, None)["volume_pts"] == 0


# ---------------------------------------------------------------------------
# 3. sector / theme / concentration
# ---------------------------------------------------------------------------

def test_classify_theme_rules_and_fallback():
    assert ms.classify_theme("Technology", "Semiconductors") == "AI hardware supply chain"
    assert ms.classify_theme("Technology", "Computer Hardware") == "AI hardware supply chain"
    assert ms.classify_theme("Technology", "Software - Infrastructure") == "Software & security"
    assert ms.classify_theme("Healthcare", "Biotechnology") == "Biotech & pharma"
    assert ms.classify_theme("Consumer Cyclical", "Restaurants") == "Consumer Cyclical"
    assert ms.classify_theme(None, None) == "Unknown"


def _row(t, theme, cap):
    return {"ticker": t, "theme": theme, "market_cap": cap}


def test_sector_concentration_flags_crowding():
    rows = [_row("SNDK", "AI hardware supply chain", 2e11), _row("MU", "AI hardware supply chain", 1e12),
            _row("DELL", "AI hardware supply chain", 3e11), _row("MRNA", "Biotech & pharma", 5e10),
            _row("XYZ", None, None)]
    c = ms.sector_concentration(rows)
    assert c["n"] == 5 and c["top_theme"] == "AI hardware supply chain"
    assert c["top_share"] == pytest.approx(0.6) and c["crowded"] is True
    assert c["groups"][0]["tickers"] == ["SNDK", "MU", "DELL"]
    assert c["groups"][0]["cap_share"] == pytest.approx(1.5e12 / 1.55e12, rel=1e-3)
    assert c["unknown"] == 1
    # Unknown never counts as the crowding theme even when it is the largest group
    c2 = ms.sector_concentration([_row("A", None, None), _row("B", None, None), _row("C", "Energy", 1e10)])
    assert c2["crowded"] is False
    assert ms.sector_concentration([]) == {"n": 0, "groups": [], "top_theme": None, "top_share": 0.0,
                                           "crowded": False, "warn_share": ms.CONCENTRATION_WARN_SHARE,
                                           "unknown": 0}


def test_load_sectors_static_wins_over_cache(tmp_path):
    cfg_dir = tmp_path / "config"; cfg_dir.mkdir()
    out_dir = tmp_path / "out"; out_dir.mkdir()
    (cfg_dir / ms.SECTORS_FILE_NAME).write_text(json.dumps(
        {"tickers": {"MU": {"sector": "Technology", "industry": "Memory", "theme": "AI hardware supply chain"}}}))
    (out_dir / ms.SECTOR_CACHE_NAME).write_text(json.dumps(
        {"tickers": {"MU": {"sector": "Cached", "industry": "Cached"}, "XOM": "Energy"}}))
    table = ms.load_sectors(cfg_dir, out_dir)
    assert table["MU"]["sector"] == "Technology"           # static override wins
    assert table["MU"]["theme"] == "AI hardware supply chain"
    assert table["XOM"] == {"sector": "Energy"}            # bare string accepted
    assert ms.load_sectors(tmp_path / "nowhere", None) == {}

    # cache round-trip merges rather than overwrites
    ms.save_sector_cache(out_dir, {"NVDA": {"sector": "Technology", "industry": "Semiconductors"}})
    again = ms.load_sectors(None, out_dir)
    assert set(again) >= {"MU", "XOM", "NVDA"}


# ---------------------------------------------------------------------------
# 4. persistence across hit-days (+ endpoint)
# ---------------------------------------------------------------------------

HITS = [
    {"date": "2026-09-23", "n_candidates": 2, "n_doublers": 19, "top": {"ticker": "ARM", "score": 100.0}},
    {"date": "2026-09-22", "n_candidates": 2, "n_doublers": 19, "top": {"ticker": "ARM", "score": 100.0}},
    {"date": "2026-09-21", "n_candidates": 0, "n_doublers": 18, "top": None,
     "top_doubler": {"ticker": "SNDK", "ret": 6.54}},
    {"date": "2026-09-20", "n_candidates": 0, "n_doublers": 0, "top": None, "top_doubler": None},  # not a hit
    {"date": "2026-08-31", "n_candidates": 0, "n_doublers": 13, "top_doubler": {"ticker": "SNDK", "ret": 6.07}},
    {"date": "2026-08-30", "n_candidates": 0, "n_doublers": 13, "top_doubler": {"ticker": "SNDK", "ret": 6.07}},
    {"date": "2026-08-29", "n_candidates": 0, "n_doublers": 15, "top_doubler": {"ticker": "SNDK", "ret": 5.65}},
    {"date": "2026-08-01", "n_candidates": 1, "n_doublers": 0, "top": {"ticker": "OLD", "score": 50.0}},
]


def test_top_ticker_persistence_counts_distinct_leaders():
    p = ms.top_ticker_persistence(HITS, n_days=6)
    assert p["days"] == 6                      # the zero-hit day is skipped, OLD falls outside the window
    assert p["distinct"] == 2
    assert p["counts"] == [{"ticker": "SNDK", "days": 4}, {"ticker": "ARM", "days": 2}]
    assert p["concentrated"] is True
    assert ms.top_ticker_persistence([]) == {"days": 0, "distinct": 0, "counts": [], "concentrated": False}


def test_hits_endpoint_includes_persistence(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)
    (tmp_path / "screener-hits.json").write_text(json.dumps({"hits": HITS}))
    body = client.get("/api/v1/screener/hits").json()
    assert len(body["hits"]) == len(HITS)
    assert body["persistence"]["distinct"] == 2
    # pre-first-run state still degrades gracefully
    (tmp_path / "screener-hits.json").unlink()
    assert client.get("/api/v1/screener/hits").json()["hits"] == []


# ---------------------------------------------------------------------------
# 5. wiring: run_screen enriches rows and reports concentration, offline
# ---------------------------------------------------------------------------

def _frame(series: dict, start="2025-08-01") -> pd.DataFrame:
    n = max(len(c) for c, _ in series.values())
    idx = pd.bdate_range(start, periods=n)
    data = {}
    for t, (c, v) in series.items():
        data[("Close", t)] = [np.nan] * (n - len(c)) + list(c)
        data[("Volume", t)] = [np.nan] * (n - len(v)) + list(v)
    return pd.DataFrame(data, index=idx)


# 260 bars: DBL doubles over both windows (steady-ish), LATE only over 270d
# with a flat last 62 bars (pulling back / decelerating territory)
DBL = ([50.0] * 197 + [55.0] * 62 + [120.0], [5e6] * 260)
LATE = ([40.0] * 197 + [100.0] * 62 + [99.0], [5e6] * 260)
CFG = ms.load_screener_config({})


def test_run_screen_attaches_analytics_and_concentration():
    frame = _frame({"DBL": DBL, "LATE": LATE})
    calls = []

    def fake_sector(t):
        calls.append(t)
        return {"sector": "Technology", "industry": "Semiconductors"} if t == "LATE" else None

    static = {"DBL": {"sector": "Healthcare", "industry": "Biotechnology"}}
    doc = ms.run_screen(CFG, {"name": "u", "tickers": ["DBL", "LATE"]},
                        fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9,
                        fetch_52w=lambda t: pd.DataFrame(), sectors=static, fetch_sector=fake_sector)
    rows = {d["ticker"]: d for d in doc["doublers"]}
    assert set(rows) == {"DBL", "LATE"}
    assert calls == ["LATE"]                                  # static entry -> no lookup for DBL
    assert doc["sector_lookups"] == {"LATE": {"sector": "Technology", "industry": "Semiconductors"}}
    assert rows["DBL"]["theme"] == "Biotech & pharma"
    assert rows["LATE"]["theme"] == "AI hardware supply chain"
    assert rows["LATE"]["pace"]["label"] == "pulling_back"    # 99 < 100 over the last 62 bars
    assert rows["DBL"]["pace"]["label"] in ms.PACE_LABELS
    for r in rows.values():
        assert 0 <= r["quality"]["total"] <= 100
        assert r["quality"]["pace_label"] == r["pace"]["label"]
    c = doc["concentration"]
    assert c["n"] == 2 and c["crowded"] is False and len(c["groups"]) == 2
    assert doc["analytics"]["pace"]["windows_days"] == [90, 270]


def test_run_screen_default_makes_no_sector_lookups():
    frame = _frame({"DBL": DBL})
    doc = ms.run_screen(CFG, {"name": "u", "tickers": ["DBL"]},
                        fetch=lambda b: frame, fetch_market_cap=lambda t: 50e9,
                        fetch_52w=lambda t: pd.DataFrame())
    d = doc["doublers"][0]
    assert d["theme"] == "Unknown" and d["sector"] is None
    assert doc["sector_lookups"] == {}
    assert doc["concentration"]["unknown"] == 1
