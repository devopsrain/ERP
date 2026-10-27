"""
Pure-logic tests — Bank Reconciliation (bank_recon_logic). No database.
"""
import importlib
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import bank_recon_logic as L  # noqa: E402
from bank_recon_logic import D, q2  # noqa: E402


# ── Decimal / dates ───────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("1,234.50", "1234.50"), ("(500)", "-500"), ("500 DR", "-500"), ("500 CR", "500"),
    ("ETB 12,000", "12000"), ("", "0"), (None, "0"), ("nan", "0"), (float("nan"), "0"),
    (1234.5, "1234.5"), ("1 234,00".replace(",", "."), "1234.00"), ("-75.25", "-75.25"), ("75.25-", "-75.25"),
    ("abc", "0"), (True, "1"),
])
def test_decimal_parsing(raw, expected):
    assert D(raw) == Decimal(expected)


def test_q2_rounds_half_up():
    assert q2("1.005") == Decimal("1.01")
    assert q2(Decimal("2.345")) == Decimal("2.35")
    assert str(q2(0)) == "0.00"


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-01", date(2026, 9, 1)), ("01/09/2026", date(2026, 9, 1)), ("01-09-2026", date(2026, 9, 1)),
    ("01-Sep-2026", date(2026, 9, 1)), ("01 Sep 2026", date(2026, 9, 1)), ("2026/09/01", date(2026, 9, 1)),
    ("2026-09-01 14:33:00", date(2026, 9, 1)), ("2026-09-01T14:33:00", date(2026, 9, 1)),
    (datetime(2026, 9, 1, 10), date(2026, 9, 1)), (date(2026, 9, 1), date(2026, 9, 1)),
    (46266, date(2026, 9, 1)), (46266.0, date(2026, 9, 1)),       # Excel serial
    ("", None), (None, None), ("not a date", None), ("Total", None), (float("nan"), None),
])
def test_parse_date(raw, expected):
    assert L.parse_date(raw) == expected


def test_days_between_and_since():
    assert L.days_between("2026-09-01", date(2026, 9, 4)) == 3
    assert L.days_between("x", "2026-09-04") is None
    assert L.days_since("2026-09-01", date(2026, 10, 1)) == 30


# ── Header detection ──────────────────────────────────────────────

def test_detect_columns_template_headers():
    m = L.detect_columns(["Date", "Description", "Reference", "Debit", "Credit", "Balance"])
    assert m == {"txn_date": "Date", "description": "Description", "reference": "Reference",
                 "debit": "Debit", "credit": "Credit", "balance": "Balance"}
    assert L.missing_columns(m) == []


def test_detect_columns_cbe_awash_style():
    m = L.detect_columns(["Txn Date", "Value Date", "Narration", "Transaction ID", "Debit", "Credit", "Balance"])
    assert m["txn_date"] == "Txn Date" and m["value_date"] == "Value Date"
    assert m["description"] == "Narration" and m["external_id"] == "Transaction ID"
    assert m["debit"] == "Debit" and m["credit"] == "Credit" and m["balance"] == "Balance"


def test_detect_columns_tolerates_decoration_and_case():
    m = L.detect_columns(["TRANSACTION DATE", "Particulars", "Cheque No.", "Withdrawal (ETB)", "Deposit (ETB)", "Running Balance"])
    assert m["txn_date"] == "TRANSACTION DATE" and m["description"] == "Particulars"
    assert m["reference"] == "Cheque No." and m["debit"] == "Withdrawal (ETB)"
    assert m["credit"] == "Deposit (ETB)" and m["balance"] == "Running Balance"


def test_detect_columns_signed_amount_only():
    m = L.detect_columns(["Posting Date", "Details", "Amount"])
    assert m["txn_date"] == "Posting Date" and m["amount"] == "Amount"
    assert L.missing_columns(m) == []
    assert "debit/credit or amount" in L.missing_columns({"txn_date": "Date"})
    assert "date" in L.missing_columns({"debit": "Debit"})


def test_value_date_only_becomes_txn_date():
    m = L.detect_columns(["Value Date", "Narration", "Debit", "Credit"])
    assert m["txn_date"] == "Value Date"


# ── Row normalisation ─────────────────────────────────────────────

def _rows():
    return [
        {"Date": "2026-09-01", "Description": "Cheque 001234 supplier", "Reference": "CHQ001234", "Debit": "12,500.00", "Credit": "", "Balance": "487,500.00"},
        {"Date": "", "Description": "", "Reference": "", "Debit": "", "Credit": "", "Balance": ""},          # blank → skipped
        {"Date": "03/09/2026", "Description": "Customer transfer INV-42", "Reference": "FT26246ABC", "Debit": "", "Credit": 85000, "Balance": 572500},
        {"Date": "2026-09-30", "Description": "Service charge", "Reference": "", "Debit": 150, "Credit": "", "Balance": 572350},
        {"Date": "Total", "Description": "", "Reference": "", "Debit": 12650, "Credit": 85000, "Balance": ""},   # footer → skipped
        {"Date": "bad", "Description": "x", "Reference": "", "Debit": 1, "Credit": "", "Balance": ""},          # error
        {"Date": "2026-09-10", "Description": "no amount", "Reference": "", "Debit": "", "Credit": "", "Balance": ""},  # error
    ]


def test_normalise_rows_basic():
    lines, errors = L.normalise_rows(_rows())
    assert len(lines) == 3
    assert len(errors) == 2 and "Row 7" in errors[0] and "Row 8" in errors[1]
    first = lines[0]
    assert first["txn_date"] == date(2026, 9, 1) and first["debit"] == Decimal("12500.00") and first["credit"] == 0
    assert first["balance"] == Decimal("487500.00") and first["reference"] == "CHQ001234"
    assert lines[1]["txn_date"] == date(2026, 9, 3) and lines[1]["credit"] == Decimal("85000.00")
    assert all(len(l["row_hash"]) == 40 for l in lines)
    assert lines[0]["row_hash"] != lines[1]["row_hash"]


def test_normalise_rows_signed_amount_and_negatives():
    rows = [{"Posting Date": "2026-09-01", "Details": "A", "Amount": "-100"},
            {"Posting Date": "2026-09-02", "Details": "B", "Amount": "250.5"},
            {"Date": None}]
    lines, errors = L.normalise_rows(rows[:2])
    assert lines[0]["debit"] == Decimal("100.00") and lines[0]["credit"] == 0
    assert lines[1]["credit"] == Decimal("250.50") and lines[1]["debit"] == 0
    # negative in the debit column means withdrawal
    lines, _ = L.normalise_rows([{"Date": "2026-09-01", "Debit": "-40", "Credit": ""}])
    assert lines[0]["debit"] == Decimal("40.00")
    # both columns filled → netted
    lines, _ = L.normalise_rows([{"Date": "2026-09-01", "Debit": "40", "Credit": "100"}])
    assert lines[0]["credit"] == Decimal("60.00") and lines[0]["debit"] == 0


def test_normalise_rows_missing_columns_reports_error():
    lines, errors = L.normalise_rows([{"Foo": 1, "Bar": 2}])
    assert lines == [] and "required column" in errors[0]
    assert L.normalise_rows([]) == ([], [])


def test_dedupe_lines_by_external_id_then_hash():
    lines, _ = L.normalise_rows([
        {"Txn Date": "2026-09-01", "Narration": "A", "Transaction ID": "T1", "Debit": 10, "Credit": ""},
        {"Txn Date": "2026-09-01", "Narration": "A", "Transaction ID": "T1", "Debit": 10, "Credit": ""},   # same id
        {"Txn Date": "2026-09-01", "Narration": "B", "Transaction ID": "", "Debit": 10, "Credit": ""},
        {"Txn Date": "2026-09-01", "Narration": "B", "Transaction ID": "", "Debit": 10, "Credit": ""},     # same hash
        {"Txn Date": "2026-09-01", "Narration": "C", "Transaction ID": "T9", "Debit": 10, "Credit": ""},
    ])
    kept, dropped = L.dedupe_lines(lines, existing_external_ids={"T9"}, existing_hashes=set())
    assert dropped == 3 and [k["description"] for k in kept] == ["A", "B"]


def test_statement_totals_and_implied_opening():
    lines, _ = L.normalise_rows(_rows())
    t = L.statement_totals(lines)
    assert t == {"debit": Decimal("12650.00"), "credit": Decimal("85000.00"), "net": Decimal("72350.00")}
    assert L.implied_opening_balance("572350", lines) == Decimal("500000.00")


# ── Scoring & matching ────────────────────────────────────────────

def _stmt(**kw):
    s = dict(id="s1", txn_date=date(2026, 9, 3), description="Customer transfer INV-2026-0042", reference="FT26246ABC",
             debit=Decimal(0), credit=Decimal("85000.00"), external_id=None)
    s.update(kw)
    return s


def _ledger(**kw):
    l = dict(line_id="l1", entry_id="e1", entry_date=date(2026, 9, 3), description="Receipt INV-2026-0042",
             entry_description="Customer payment", reference="FT26246ABC", debit=Decimal("85000.00"), credit=Decimal(0))
    l.update(kw)
    return l


def test_signed_amounts_opposite_views():
    assert L.signed_statement_amount(_stmt()) == Decimal("85000.00")
    assert L.signed_ledger_amount(_ledger()) == Decimal("85000.00")
    assert L.signed_statement_amount(_stmt(credit=0, debit="150")) == Decimal("-150.00")
    assert L.signed_ledger_amount(_ledger(debit=0, credit="150")) == Decimal("-150.00")


def test_score_requires_amount_and_direction():
    assert L.score(_stmt(), _ledger()) >= 90                                       # same day + ref + similar text
    assert L.score(_stmt(), _ledger(description="Customer transfer INV-2026-0042", entry_description="")) == 100
    assert L.score(_stmt(), _ledger(debit=Decimal("85000.01"))) == 0
    assert L.score(_stmt(), _ledger(debit=0, credit=Decimal("85000.00"))) == 0     # wrong direction
    assert L.score(_stmt(credit=0, debit=0), _ledger(debit=0, credit=0)) == 0      # zero lines never match


def test_score_date_bands_and_text():
    plain = _ledger(reference="", description="", entry_description="")
    assert L.score(_stmt(reference="", description=""), plain) == 50                              # same day only
    assert L.score(_stmt(reference="", description="", txn_date=date(2026, 9, 6)), plain) == 40   # within ±3
    assert L.score(_stmt(reference="", description="", txn_date=date(2026, 9, 10)), plain) == 20  # within 7
    assert L.score(_stmt(reference="", description="", txn_date=date(2026, 10, 10)), plain) == 5
    # reference match adds 30; reference inside narration adds 25
    assert L.score(_stmt(description=""), _ledger(description="", entry_description="")) == 80
    assert L.score(_stmt(reference="", description="paid FT26246ABC"), _ledger(description="", entry_description="")) == 75
    # description similarity adds up to 20
    s = L.score(_stmt(reference=""), _ledger(reference="", description="Customer transfer INV-2026-0042", entry_description=""))
    assert 60 <= s <= 70


def test_similarity_bounds():
    assert L.similarity("", "x") == 0.0
    assert L.similarity("Cheque 001234 supplier", "cheque 001234 SUPPLIER") == 1.0
    assert 0 < L.similarity("Service charge Sept", "Bank service charge") < 1


def test_candidates_sorted_and_limited():
    ledger = [_ledger(line_id=f"l{i}", entry_date=date(2026, 9, 1 + i), reference="") for i in range(6)]
    c = L.candidates(_stmt(reference=""), ledger, limit=3)
    assert len(c) == 3 and c[0][1]["line_id"] == "l2" and c[0][0] >= c[1][0] >= c[2][0]


def test_auto_match_one_to_one_and_ambiguity():
    stmts = [_stmt(id="s1"), _stmt(id="s2", description="Service charge", reference="", credit=0, debit=Decimal("150.00"), txn_date=date(2026, 9, 30)),
             _stmt(id="s3", description="Salary", reference="", credit=0, debit=Decimal("5000.00"), txn_date=date(2026, 9, 25))]
    ledger = [_ledger(line_id="l1"),
              _ledger(line_id="l2", description="Bank charges", entry_description="", reference="", debit=0, credit=Decimal("150.00"), entry_date=date(2026, 9, 30)),
              # two identical salary payments on the same day → ambiguous, left for a human
              _ledger(line_id="l3", description="Salary A", entry_description="", reference="", debit=0, credit=Decimal("5000.00"), entry_date=date(2026, 9, 25)),
              _ledger(line_id="l4", description="Salary B", entry_description="", reference="", debit=0, credit=Decimal("5000.00"), entry_date=date(2026, 9, 25))]
    res = L.auto_match(stmts, ledger)
    got = {m["stmt"]["id"]: m["ledger"]["line_id"] for m in res}
    assert got == {"s1": "l1", "s2": "l2"}
    assert all(m["score"] >= L.AUTO_MATCH_MIN_SCORE for m in res)


def test_auto_match_never_reuses_a_ledger_line():
    stmts = [_stmt(id="s1"), _stmt(id="s2", txn_date=date(2026, 9, 4))]
    res = L.auto_match(stmts, [_ledger()])
    assert len(res) == 1 and res[0]["stmt"]["id"] == "s1"
    assert L.auto_match([], []) == []


# ── Reconciliation arithmetic ─────────────────────────────────────

def test_reconcile_textbook_example():
    # bank 10,000; deposits in transit 2,000; outstanding cheques 1,500 → adjusted 10,500 = ledger
    r = L.reconcile("10000", "10500", "2000", "1500")
    assert r["adjusted_bank_balance"] == Decimal("10500.00") and r["difference"] == 0 and r["is_balanced"]
    # bank charge of 150 on the statement, not yet booked → difference −150, unexplained 0
    r = L.reconcile("9850", "10500", "2000", "1500", unmatched_statement_debits="150")
    assert r["difference"] == Decimal("-150.00")
    assert r["adjusted_ledger_balance"] == Decimal("10350.00") and r["unexplained_difference"] == 0
    assert not r["is_balanced"]


def test_outstanding_totals_and_ledger_balance():
    lines = [_ledger(debit="100", credit=0), _ledger(debit=0, credit="40.555")]
    assert L.outstanding_totals(lines) == {"deposits": Decimal("100.00"), "payments": Decimal("40.56")}
    assert L.ledger_balance("1000", lines) == Decimal("1059.44")


def test_ageing():
    assert [L.ageing_bucket(d) for d in (None, 0, 30, 31, 60, 61, 90, 91)] == ["0-30", "0-30", "0-30", "31-60", "31-60", "61-90", "61-90", "90+"]
    items = [dict(txn_date=date(2026, 9, 1), debit="10", credit=0), dict(txn_date=date(2026, 5, 1), debit=0, credit="20"),
             dict(txn_date=None, debit="5", credit=0)]
    aged = L.age_items(items, as_of=date(2026, 10, 1))
    assert aged["0-30"]["count"] == 2 and aged["0-30"]["amount"] == Decimal("15.00")
    assert aged["90+"]["count"] == 1 and aged["90+"]["items"][0]["age_days"] == 153


# ── Adjustments ───────────────────────────────────────────────────

def test_adjustment_direction_and_journal_lines():
    assert L.adjustment_effect("bank_charge", "increase") == "decrease"
    assert L.adjustment_effect("interest") == "increase"
    assert L.adjustment_effect("fx", "inc") == "increase" and L.adjustment_effect("fx", None) == "decrease"
    ch = L.adjustment_journal_lines("bank_charge", "150", "1010", "6210", "Sept charges", bank_name="CBE", other_name="Bank charges")
    assert ch[0]["account_code"] == "6210" and ch[0]["debit_amount"] == Decimal("150.00") and ch[0]["credit_amount"] == 0
    assert ch[1]["account_code"] == "1010" and ch[1]["credit_amount"] == Decimal("150.00")
    it = L.adjustment_journal_lines("interest", "-33.333", "1010", "4900", "Interest")
    assert it[0]["account_code"] == "1010" and it[0]["debit_amount"] == Decimal("33.33")
    assert sum(l["debit_amount"] for l in it) == sum(l["credit_amount"] for l in it)
    assert L.adjustment_journal_lines("other", 0, "1010", "6999", "x") == []
    assert L.signed_adjustment_amount("bank_charge", "150") == Decimal("-150.00")
    assert L.signed_adjustment_amount("interest", "20") == Decimal("20.00")
    assert L.signed_adjustment_amount("correction", "20", "increase") == Decimal("20.00")


# ── i18n coverage ─────────────────────────────────────────────────

_GETTEXT = re.compile(r"""\b_\(\s*(?:'((?:[^'\\]|\\.)*)'|"((?:[^"\\]|\\.)*)")\s*\)""")


def test_i18n_catalogue_covers_every_template_and_python_string():
    cat = importlib.import_module("i18n_catalogue_bank")
    assert isinstance(cat.AM, dict) and len(cat.AM) > 200
    assert all(isinstance(k, str) and isinstance(v, str) and v for k, v in cat.AM.items())
    missing = set()
    for tpl in (_WEB_DIR / "templates" / "bank").glob("*.html"):
        for m in _GETTEXT.finditer(tpl.read_text(encoding="utf-8")):
            key = (m.group(1) if m.group(1) is not None else m.group(2)).replace("\\'", "'")
            if key not in cat.AM:
                missing.add(key)
    for v in list(L.ADJUSTMENT_LABELS.values()) + list(L.TEMPLATE_NOTES.values()):
        if v not in cat.AM:
            missing.add(v)
    for s in L.STATEMENT_STATUSES + L.LINE_STATUSES + L.RECON_STATUSES + L.MATCH_KINDS:
        if s.replace("_", " ") not in cat.AM:
            missing.add(s)
    assert not missing, sorted(missing)


def test_constants_are_consistent():
    assert set(L.ADJUSTMENT_LABELS) == set(L.ADJUSTMENT_KINDS)
    assert "Commercial Bank of Ethiopia" in L.BANK_NAMES and "Awash Bank" in L.BANK_NAMES
    assert L.TEMPLATE_COLUMNS == ("Date", "Description", "Reference", "Debit", "Credit", "Balance")
    assert set(L.TEMPLATE_NOTES) == set(L.TEMPLATE_COLUMNS)
