"""Dashboard + correlation-endpoint tests (run from risk-sim/: python -m pytest)."""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main

client = TestClient(main.app)

SAMPLE_DOC = {
    "date": "2026-08-13",
    "generated_at_utc": "2026-08-13T07:00:00+00:00",
    "config": {"lookback_days": 90, "interval": "1d"},
    "tickers": ["AAPL", "MSFT"],
    "skipped": {"BOGUS": "no data returned from Yahoo Finance"},
    "per_ticker": {
        "AAPL": {"last_price": 232.5, "annual_volatility": 0.28, "annual_drift": 0.11,
                 "return_observations": 89},
        "MSFT": {"last_price": 512.1, "annual_volatility": 0.22, "annual_drift": -0.03,
                 "return_observations": 89},
    },
    "correlation_matrix": [[1.0, 0.61], [0.61, 1.0]],
    "simulate_payload": {},
    "margin_account": {
        "margin_rate": 0.2,
        "rows": [
            {"ticker": "AAPL", "quantity": 100.0, "open": 230.0, "midday": 231.0,
             "close": 232.5, "midday_source": "intraday",
             "margin_open": 4600.0, "margin_midday": 4620.0, "margin_close": 4650.0},
        ],
        "totals": {"position_value_open": 23000.0, "position_value_midday": 23100.0,
                   "position_value_close": 23250.0, "margin_open": 4600.0,
                   "margin_midday": 4620.0, "margin_close": 4650.0,
                   "peak_margin": 4650.0},
        "skipped": {},
    },
    "margin_timeseries": {
        "margin_rate": 0.2,
        "midday_source": "hl_midpoint_proxy",
        "points": [
            {"date": "2026-08-12", "open": 4580.0, "midday": 4590.0, "close": 4600.0,
             "n_tickers": 1},
            {"date": "2026-08-13", "open": 4600.0, "midday": 4620.0, "close": 4650.0,
             "n_tickers": 1, "midday_source": "intraday"},
        ],
    },
}


@pytest.fixture
def missing_output_dir(tmp_path, monkeypatch):
    """Point the API at a directory that does not exist (pre-first-job state)."""
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path / "nope")


@pytest.fixture
def populated_output_dir(tmp_path, monkeypatch):
    corr = tmp_path / "correlations"
    corr.mkdir()
    (corr / "2026-08-13.json").write_text(json.dumps(SAMPLE_DOC))
    (tmp_path / "latest.json").write_text(json.dumps(SAMPLE_DOC))
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)


# ---- dashboard page ----

@pytest.mark.parametrize("path", ["/", "/dashboard"])
def test_dashboard_serves_html(path, missing_output_dir):
    r = client.get(path)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/html")
    body = r.text
    assert 'id="heatmap"' in body          # heatmap container
    assert 'id="tiles"' in body            # stat tiles container
    assert 'id="date-select"' in body      # filter row
    assert 'id="margin-section"' in body   # CFD margin block (hidden until data)
    assert 'id="margin-tiles"' in body     # margin stat tiles container
    assert 'id="margin-table"' in body     # per-ticker margin table
    assert 'id="margin-ts-section"' in body  # YTD margin chart block (hidden until data)
    assert 'id="margin-ts-chart"' in body    # inline-SVG line chart
    assert 'id="margin-ts-tooltip"' in body  # nearest-point hover tooltip
    assert 'id="margin-ts-legend"' in body   # 3-series legend
    assert "app.daily_correlation" in body  # empty-state runbook command
    assert "http://" not in body and "https://" not in body  # no external CDNs


def test_dashboard_ok_when_output_dir_missing(missing_output_dir):
    # the page itself never touches the output dir; both must still work
    assert client.get("/").status_code == 200
    assert client.get("/api/v1/correlations").json() == {"dates": []}


# ---- endpoint regressions ----

def test_list_empty_when_no_data(missing_output_dir):
    assert client.get("/api/v1/correlations").json() == {"dates": []}


def test_latest_404_when_no_data(missing_output_dir):
    assert client.get("/api/v1/correlations/latest").status_code == 404


def test_by_date_404_when_no_data(missing_output_dir):
    assert client.get("/api/v1/correlations/2026-08-13").status_code == 404


def test_bad_date_format_404(missing_output_dir):
    assert client.get("/api/v1/correlations/not-a-date").status_code == 404


def test_list_and_fetch_populated(populated_output_dir):
    assert client.get("/api/v1/correlations").json() == {"dates": ["2026-08-13"]}
    for url in ("/api/v1/correlations/latest", "/api/v1/correlations/2026-08-13"):
        r = client.get(url)
        assert r.status_code == 200
        doc = r.json()
        assert doc["tickers"] == ["AAPL", "MSFT"]
        assert doc["correlation_matrix"][0][1] == 0.61
        assert doc["margin_account"]["totals"]["peak_margin"] == 4650.0


def test_snapshot_without_margin_account_still_served(tmp_path, monkeypatch):
    """Old snapshots (pre-margin) must round-trip untouched; the dashboard
    hides the sections client-side when the keys are absent."""
    legacy = {k: v for k, v in SAMPLE_DOC.items()
              if k not in ("margin_account", "margin_timeseries")}
    corr = tmp_path / "correlations"
    corr.mkdir()
    (corr / "2026-08-13.json").write_text(json.dumps(legacy))
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)
    doc = client.get("/api/v1/correlations/2026-08-13").json()
    assert "margin_account" not in doc
    assert "margin_timeseries" not in doc


def test_snapshot_with_margin_but_no_timeseries_still_served(tmp_path, monkeypatch):
    """Snapshots from between the two margin features (table but no YTD
    series) must also round-trip untouched."""
    legacy = {k: v for k, v in SAMPLE_DOC.items() if k != "margin_timeseries"}
    corr = tmp_path / "correlations"
    corr.mkdir()
    (corr / "2026-08-13.json").write_text(json.dumps(legacy))
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)
    doc = client.get("/api/v1/correlations/2026-08-13").json()
    assert doc["margin_account"]["totals"]["peak_margin"] == 4650.0
    assert "margin_timeseries" not in doc


def test_snapshot_with_timeseries_served_intact(populated_output_dir):
    doc = client.get("/api/v1/correlations/latest").json()
    ts = doc["margin_timeseries"]
    assert ts["midday_source"] == "hl_midpoint_proxy"
    assert [p["date"] for p in ts["points"]] == ["2026-08-12", "2026-08-13"]
    assert ts["points"][-1]["midday_source"] == "intraday"


def test_health_probes():
    assert client.get("/healthz").json() == {"status": "ok"}
    assert client.get("/api/v1/version").json()["version"] == main.APP_VERSION
