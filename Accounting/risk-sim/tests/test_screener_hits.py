"""Tests for the hit-days history: the screener-hits.json index (written on
every snapshot write, deduped by date, rebuilt from files when missing,
capped), the guarded /api/v1/screener/hits endpoint, and the dashboard's
"Hit days" block. All offline: tmp_path outputs, no network.
"""
import json
from datetime import date, timedelta

from fastapi.testclient import TestClient

import app.main as main
import app.momentum_screener as ms

client = TestClient(main.app)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

CAND = {"ticker": "WIN", "price": 132.0, "score": 87.5}
CAND_LOW = {"ticker": "ALSO", "price": 50.0, "score": 55.0}
DBLR = {"ticker": "DBL", "price": 120.0, "ret_90d": 1.1818, "ret_270d": 1.4,
        "window_hit": "both"}
DBLR_LOW = {"ticker": "MEH", "price": 30.0, "ret_90d": 1.05, "ret_270d": None,
            "window_hit": "90d"}


def _doc(d, candidates=(), doublers=(), backfilled=False):
    doc = {"date": d, "universe": "mini", "criteria": {},
           "candidates": list(candidates), "doublers": list(doublers),
           "scanned": 5, "passed_filters": len(candidates), "skipped": 0}
    if backfilled:
        doc["backfilled"] = True
    return doc


def _read_hits(output_dir):
    return json.loads((output_dir / "screener-hits.json").read_text())["hits"]


# ---------------------------------------------------------------------------
# index write / entry shape / dedupe / ordering / cap
# ---------------------------------------------------------------------------

def test_write_outputs_updates_hits_index(tmp_path):
    ms.write_outputs(tmp_path, _doc("2026-08-25", [CAND_LOW, CAND], [DBLR_LOW, DBLR]))
    hits = _read_hits(tmp_path)
    assert hits == [{
        "date": "2026-08-25",
        "n_candidates": 2,
        "n_doublers": 2,
        "top": {"ticker": "WIN", "score": 87.5},          # max score wins
        "top_doubler": {"ticker": "DBL", "ret": 1.4},      # best window return
        "backfilled": False,
    }]
    assert not list(tmp_path.rglob("*.tmp"))               # atomic, no temp left


def test_hits_entry_nulls_on_empty_day_and_backfill_flag(tmp_path):
    ms.write_outputs(tmp_path, _doc("2026-08-24", backfilled=True),
                     include_latest=False)                 # replay-style write
    (entry,) = _read_hits(tmp_path)
    assert entry["n_candidates"] == 0 and entry["n_doublers"] == 0
    assert entry["top"] is None and entry["top_doubler"] is None
    assert entry["backfilled"] is True


def test_hits_index_dedupes_by_date_on_rerun(tmp_path):
    ms.write_outputs(tmp_path, _doc("2026-08-25", [CAND]))
    ms.write_outputs(tmp_path, _doc("2026-08-25", [CAND, CAND_LOW], [DBLR]))
    (entry,) = _read_hits(tmp_path)                        # ONE row for the date
    assert entry["n_candidates"] == 2 and entry["n_doublers"] == 1


def test_hits_index_sorted_newest_first(tmp_path):
    for d in ("2026-08-20", "2026-08-25", "2026-08-22"):
        ms.write_outputs(tmp_path, _doc(d), include_latest=False)
    assert [h["date"] for h in _read_hits(tmp_path)] == \
        ["2026-08-25", "2026-08-22", "2026-08-20"]


def test_hits_index_rebuilt_from_files_when_missing(tmp_path):
    ms.write_outputs(tmp_path, _doc("2026-08-20", [CAND]), include_latest=False)
    ms.write_outputs(tmp_path, _doc("2026-08-21", doublers=[DBLR]), include_latest=False)
    (tmp_path / "screener-hits.json").unlink()             # index lost
    # junk that the rebuild must skip: non-dated name + unreadable file
    (tmp_path / "screener" / "notes.json").write_text("{}")
    (tmp_path / "screener" / "2026-08-19.json").write_text("{broken")
    ms.write_outputs(tmp_path, _doc("2026-08-22"), include_latest=False)
    hits = _read_hits(tmp_path)
    assert [h["date"] for h in hits] == ["2026-08-22", "2026-08-21", "2026-08-20"]
    assert hits[2]["top"] == {"ticker": "WIN", "score": 87.5}
    assert hits[1]["top_doubler"] == {"ticker": "DBL", "ret": 1.4}


def test_hits_index_capped_at_max_entries(tmp_path, monkeypatch):
    monkeypatch.setattr(ms, "HITS_MAX_ENTRIES", 5)         # keep the test fast
    start = date(2026, 1, 1)
    for i in range(7):
        ms.update_hits_index(tmp_path, _doc((start + timedelta(days=i)).isoformat()))
    hits = _read_hits(tmp_path)
    assert len(hits) == 5
    assert hits[0]["date"] == "2026-01-07"                 # newest kept
    assert hits[-1]["date"] == "2026-01-03"                # oldest two dropped


def test_hits_index_failure_never_loses_the_snapshot(tmp_path, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("index broke")
    monkeypatch.setattr(ms, "update_hits_index", boom)
    targets = ms.write_outputs(tmp_path, _doc("2026-08-25"))
    assert all(t.is_file() for t in targets)               # snapshots still written


# ---------------------------------------------------------------------------
# endpoint: guarded, and not shadowed by the /{date} route
# ---------------------------------------------------------------------------

def test_hits_endpoint_missing_index_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path / "nope")
    assert client.get("/api/v1/screener/hits").json() == {"hits": []}


def test_hits_endpoint_unreadable_index_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)
    (tmp_path / "screener-hits.json").write_text("{not json")
    assert client.get("/api/v1/screener/hits").json() == {"hits": []}
    (tmp_path / "screener-hits.json").write_text(json.dumps({"hits": "not-a-list"}))
    assert client.get("/api/v1/screener/hits").json() == {"hits": []}


def test_hits_endpoint_serves_the_index(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "CORRELATION_OUTPUT_DIR", tmp_path)
    ms.write_outputs(tmp_path, _doc("2026-08-25", [CAND], [DBLR]))
    body = client.get("/api/v1/screener/hits").json()
    assert body["hits"][0]["date"] == "2026-08-25"
    assert body["hits"][0]["top"] == {"ticker": "WIN", "score": 87.5}
    # the literal "hits" path wins over /{date} (declared first); a real
    # date still hits the snapshot route
    snap = client.get("/api/v1/screener/2026-08-25").json()
    assert snap["candidates"][0]["ticker"] == "WIN"


# ---------------------------------------------------------------------------
# dashboard: hit-days block, click plumbing, empty state
# ---------------------------------------------------------------------------

def test_dashboard_has_hitdays_block():
    body = client.get("/").text
    assert 'id="hitdays-block"' in body           # whole block (in-card, above tables)
    assert 'id="hitdays-table"' in body
    assert 'id="hitdays-empty"' in body           # empty state container
    assert "No hit days recorded yet" in body
    assert "backfill to seed history" in body
    assert "/api/v1/screener/hits" in body        # fetches the index
    assert "viewScreenerDate" in body             # row click -> per-date snapshot
    assert 'id="screener-viewing"' in body        # "viewing <date>" banner
    assert "back to latest" in body               # and its way home
    assert "info only" in body                    # criteria line labels off gates
