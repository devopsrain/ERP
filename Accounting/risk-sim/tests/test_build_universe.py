"""Universe builder: directory parsing, eligibility rules, Yahoo symbol
mapping, resumable cap cache and output file. All offline."""
import json
from datetime import date

import app.build_universe as bu

NASDAQ = """Symbol|Security Name|Market Category|Test Issue|Financial Status|Round Lot Size|ETF|NextShares
AAPL|Apple Inc. - Common Stock|Q|N|N|100|N|N
QQQ|Invesco QQQ Trust, Series 1|G|N|N|100|Y|N
ZTST|Test Issue|Q|Y|N|100|N|N
ABCW|Abc Corp - Warrant|S|N|N|100|N|N
SMLL|Small Biotech Inc. - Common Stock|S|N|N|100|N|N
DLNQ|Delinquent Co - Common Stock|Q|N|D|100|N|N
File Creation Time: 1001202517:03|||||||
"""
OTHER = """ACT Symbol|Security Name|Exchange|CQS Symbol|ETF|Round Lot Size|Test Issue|NASDAQ Symbol
BRK B|Berkshire Hathaway Inc. Class B|N|BRK B|N|100|N|BRK.B
BABA|Alibaba Group Holding Limited American Depositary Shares|N|BABA|N|100|N|BABA
XOM|Exxon Mobil Corporation Common Stock|N|XOM|N|100|N|XOM
BAC$L|Bank of America Corporation Depositary Shares Preferred|N|BAC$L|N|100|N|BAC-L
SPY|SPDR S&P 500 ETF Trust|P|SPY|Y|100|N|SPY
PLD|Prologis, Inc. Common Stock|N|PLD|N|100|N|PLD
File Creation Time: 1001202517:03|||||||
"""


def test_parse_and_filter_rules():
    rows = bu.parse_symbol_directory(NASDAQ, "nasdaq")
    assert [r["symbol"] for r in rows] == ["AAPL", "QQQ", "ZTST", "ABCW", "SMLL", "DLNQ"]
    assert rows[1]["etf"] is True and rows[2]["test"] is True
    syms = bu.candidate_symbols([(NASDAQ, "nasdaq"), (OTHER, "other")])
    assert set(syms) == {"AAPL", "SMLL", "BRK-B", "XOM", "PLD"}   # REIT kept, ADR/pref/ETF/test/warrant/delinquent dropped
    with_adr = bu.candidate_symbols([(OTHER, "other")], keep_adrs=True)
    assert "BABA" in with_adr


def test_yahoo_symbol_mapping():
    assert bu.to_yahoo_symbol("BRK B") == "BRK-B"
    assert bu.to_yahoo_symbol("BRK.B") == "BRK-B"
    assert bu.to_yahoo_symbol("BAC$L") is None
    assert bu.to_yahoo_symbol("TOOLONGX") is None
    assert bu.to_yahoo_symbol("AAPL") == "AAPL"


def test_lookup_caps_resumes_from_cache(tmp_path):
    cache_path = tmp_path / "caps.json"
    today = date(2026, 10, 2)
    cache = {"AAPL": {"cap": 3e12, "checked": "2026-09-20"},           # fresh
             "XOM": {"cap": 4e11, "checked": "2026-01-01"}}            # stale -> re-checked
    calls = []

    def cap(s):
        calls.append(s)
        return {"XOM": 4.5e11, "SMLL": 8e8, "PLD": None}.get(s)

    sleeps = []
    bu.lookup_caps(["AAPL", "XOM", "SMLL", "PLD"], cache, fetch_cap=cap, cache_path=cache_path,
                   pause_s=0.6, refresh_days=30, today=today, sleep=sleeps.append)
    assert calls == ["XOM", "SMLL", "PLD"]
    assert cache["XOM"]["cap"] == 4.5e11 and cache["PLD"]["cap"] is None
    assert json.loads(cache_path.read_text())["SMLL"]["cap"] == 8e8   # persisted
    assert sleeps == [0.6, 0.6]                                       # no pause after the last

    # --limit caps live lookups; untouched symbols stay pending
    cache2: dict = {}
    bu.lookup_caps(["A", "B", "C"], cache2, fetch_cap=lambda s: 1e9, pause_s=0, limit=2, today=today)
    assert set(cache2) == {"A", "B"}


def test_select_and_write_universe(tmp_path):
    syms = {"AAPL": "Apple", "SMLL": "Small Biotech", "PLD": "Prologis", "XOM": "Exxon"}
    cache = {"AAPL": {"cap": 3e12}, "SMLL": {"cap": 8e8}, "PLD": {"cap": 1.1e11}, "XOM": {"cap": None}}
    picked = bu.select_universe(syms, cache, 1e9)
    assert [p["ticker"] for p in picked] == ["AAPL", "PLD"]
    out = tmp_path / "universe.json"
    out.write_text('{"name":"old","tickers":["OLD"]}')
    bu.write_universe(out, picked, min_cap=1e9, scanned=4, unknown=1)
    doc = json.loads(out.read_text())
    assert doc["tickers"] == ["AAPL", "PLD"] and doc["min_market_cap"] == 1e9
    assert doc["names"]["PLD"] == "Prologis" and ">= $1B" in doc["note"]
    assert json.loads((tmp_path / "universe.json.bak").read_text())["tickers"] == ["OLD"]
    # the screener's loader accepts the generated file
    from app.momentum_screener import load_universe
    assert load_universe(out)["tickers"] == ["AAPL", "PLD"]


def test_build_end_to_end_offline(tmp_path):
    texts = {bu.NASDAQ_LISTED_URL: NASDAQ, bu.OTHER_LISTED_URL: OTHER}
    caps = {"AAPL": 3e12, "SMLL": 8e8, "BRK-B": 9e11, "XOM": 4e11, "PLD": 1.1e11}
    out = tmp_path / "universe.json"
    res = bu.build(out, min_cap=1e9, fetch_text=texts.__getitem__, fetch_cap=caps.get,
                   pause_s=0, sleep=lambda s: None)
    assert res["eligible"] == 5 and res["selected"] == 4 and res["pending_lookups"] == 0
    assert json.loads(out.read_text())["tickers"] == ["AAPL", "BRK-B", "XOM", "PLD"]
    assert (tmp_path / bu.CACHE_NAME).is_file()
    dry = bu.build(tmp_path / "other.json", min_cap=5e11, fetch_text=texts.__getitem__,
                   fetch_cap=caps.get, cache_path=tmp_path / bu.CACHE_NAME, pause_s=0, dry_run=True)
    assert dry["selected"] == 2 and not (tmp_path / "other.json").exists()
