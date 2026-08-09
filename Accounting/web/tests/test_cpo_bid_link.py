"""
Offline logic tests for BidDataStore.find_bid_by_ref_or_title /
append_bid_note (the CPO -> Bid linking helpers). No DB: get_tenant_cursor
is monkeypatched with an in-memory fake that emulates the two statements'
semantics (reference_number equality OR case-insensitive title match).
"""
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import bid_data_store as bds


class FakeCursor:
    """Emulates just the queries the linking helpers issue."""

    def __init__(self, rows):
        self.rows = rows
        self._result = None
        self.rowcount = 0
        self.executed = []

    def execute(self, sql, params=()):
        self.executed.append((sql, params))
        if sql.lstrip().upper().startswith("SELECT"):
            cid, ref, title = params
            matches = [
                r for r in self.rows
                if r["company_id"] == cid
                and (r.get("reference_number") == ref
                     or (r.get("title") or "").lower() == title.lower())
            ]
            matches.sort(key=lambda r: r.get("created_at", ""), reverse=True)
            self._result = matches[0] if matches else None
        elif sql.lstrip().upper().startswith("UPDATE"):
            text, _now, bid_id, cid = params
            hit = [r for r in self.rows
                   if r["id"] == bid_id and r["company_id"] == cid]
            for r in hit:
                r["notes"] = (r.get("notes") or "") + text
            self.rowcount = len(hit)

    def fetchone(self):
        return self._result


@pytest.fixture
def store(monkeypatch):
    rows = [
        {"id": "b1", "company_id": "default", "title": "Airport Expansion",
         "reference_number": "RFP-2026-0001", "notes": "orig",
         "created_at": "2026-01-01"},
        {"id": "b2", "company_id": "default", "title": "Road Works",
         "reference_number": "CPO-ABC12345", "notes": "",
         "created_at": "2026-02-01"},
        {"id": "b3", "company_id": "other", "title": "Airport Expansion",
         "reference_number": "X-1", "notes": "", "created_at": "2026-03-01"},
    ]
    calls = {"cid": []}

    @contextmanager
    def fake_tenant_cursor(cid):
        calls["cid"].append(cid)
        yield FakeCursor(rows)

    monkeypatch.setattr(bds, "get_tenant_cursor", fake_tenant_cursor)
    s = bds.BidDataStore()
    s._rows = rows
    s._calls = calls
    return s


def test_match_by_reference_number(store):
    hit = store.find_bid_by_ref_or_title(None, "CPO-ABC12345")
    assert hit and hit["id"] == "b2"


def test_match_by_title_case_insensitive(store):
    hit = store.find_bid_by_ref_or_title("default", "  aIrPoRt eXpAnSiOn ")
    assert hit and hit["id"] == "b1"


def test_scoped_to_company(store):
    # b3 has the same title but lives in company 'other' — default scope
    # (None -> 'default') must not see it via its unique ref.
    assert store.find_bid_by_ref_or_title(None, "X-1") is None
    assert store._calls["cid"][-1] == "default"


def test_no_match_returns_none(store):
    assert store.find_bid_by_ref_or_title(None, "does-not-exist") is None


def test_blank_text_short_circuits(store):
    assert store.find_bid_by_ref_or_title(None, "   ") is None
    assert store.find_bid_by_ref_or_title(None, "") is None
    assert store._calls["cid"] == []  # no cursor opened


def test_append_bid_note(store):
    assert store.append_bid_note("b1", None, "\n[CPO] Linked CPO x") is True
    assert store._rows[0]["notes"] == "orig\n[CPO] Linked CPO x"


def test_append_bid_note_missing_bid(store):
    assert store.append_bid_note("nope", None, "text") is False


def test_append_bid_note_empty_text(store):
    assert store.append_bid_note("b1", None, "") is False
    assert store._calls["cid"] == []
