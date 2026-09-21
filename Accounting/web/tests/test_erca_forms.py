"""
Pure unit tests for the ERCA tax outputs computations (web/erca_forms.py).
No database required.
"""
import re
import sys
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import erca_forms as f  # noqa: E402

D = Decimal


# ── Ethiopian calendar ────────────────────────────────────────────────────────
@pytest.mark.parametrize("greg,eth", [
    (date(2026, 9, 11), (2019, 1, 1)),    # Enkutatash 2019
    (date(2026, 9, 12), (2019, 1, 2)),
    (date(2023, 9, 12), (2016, 1, 1)),    # year before a Gregorian leap year -> 12 Sept
    (date(2027, 9, 12), (2020, 1, 1)),
    (date(2026, 7, 8), (2018, 11, 1)),    # Hamle 1 — fiscal year start
    (date(2026, 7, 7), (2018, 10, 30)),   # Sene 30 — fiscal year end
    (date(2023, 9, 11), (2015, 13, 6)),   # Pagume 6 in an Ethiopian leap year
    (date(2000, 1, 1), (1992, 4, 22)),
])
def test_gregorian_to_ethiopic(greg, eth):
    assert f.to_ethiopic(greg) == eth
    assert f.to_gregorian(*eth) == greg


def test_epoch_constant_matches_algorithm():
    # JD 1724220.5 = start of Meskerem 1, 1 AM (JDN 1724221)
    assert f.ethiopic_to_jdn(1, 1, 1) == 1724221
    assert f.jdn_to_ethiopic(1724221) == (1, 1, 1)


def test_round_trip_many_days():
    d = date(1990, 1, 1)
    for i in range(0, 20000, 7):
        g = date.fromordinal(d.toordinal() + i)
        assert f.to_gregorian(*f.to_ethiopic(g)) == g


def test_fiscal_year_helpers():
    assert f.ethiopian_fiscal_year(date(2026, 7, 8)) == 2019
    assert f.ethiopian_fiscal_year(date(2026, 7, 7)) == 2018
    assert f.ethiopian_fiscal_year(date(2026, 1, 15)) == 2018
    assert f.fiscal_year_bounds(2018) == (date(2025, 7, 8), date(2026, 7, 7))


def test_month_names():
    assert f.ethiopic_month_name(1) == "Meskerem"
    assert f.ethiopic_month_name(13) == "Pagume"
    assert f.ethiopic_month_name(11, amharic=True) == "ሐምሌ"
    assert f.format_ethiopic(date(2026, 9, 11)) == "Meskerem 1, 2019"


# ── periods ───────────────────────────────────────────────────────────────────
def test_period_bounds():
    pb = f.period_bounds(2026, 8)
    assert pb["start"] == date(2026, 8, 1) and pb["end"] == date(2026, 8, 31)
    assert pb["deadline"] == date(2026, 9, 30)          # last day of following month
    assert pb["label"] == "August 2026"
    assert pb["ethiopian_label"].startswith("Hamle 25") and pb["fiscal_year"] == 2019
    dec = f.period_bounds(2026, 12)
    assert dec["deadline"] == date(2027, 1, 31)
    feb = f.period_bounds(2028, 2)
    assert feb["end"] == date(2028, 2, 29)


def test_period_bounds_rejects_bad_month():
    with pytest.raises(ValueError):
        f.period_bounds(2026, 13)


def test_current_and_previous_period():
    assert f.current_period(date(2026, 9, 12)) == (2026, 8)
    assert f.current_period(date(2026, 1, 3)) == (2025, 12)
    assert f.previous_period(2026, 1) == (2025, 12)


def test_unfiled_periods_flags_overdue():
    today = date(2026, 10, 5)                       # August return (due 30 Sep) is overdue, September is not
    out = f.unfiled_periods({(2026, 7)}, today, lookback=3)
    labels = [(p["year"], p["month"], p["overdue"]) for p in out]
    assert labels == [(2026, 9, False), (2026, 8, True)]


# ── TIN ───────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("tin,ok", [
    ("0012345678", True), ("0012-345-678", True), ("123456789", True),   # legacy 9-digit accepted
    ("", False), ("12345", False), ("00123456789", False), ("00A2345678", False),
])
def test_validate_tin(tin, ok):
    assert f.validate_tin(tin)[0] is ok


def test_tin_messages_and_normalisation():
    assert f.normalize_tin(" 0012 345 678 ") == "0012345678"
    assert f.validate_tin("123456789")[1].startswith("legacy")
    assert f.validate_tin("0012345678")[1] == "ok"


def test_luhn_strict_mode():
    assert f.luhn_check("79927398713")
    assert not f.luhn_check("79927398710")
    assert f.validate_tin("7992739871", strict=True)[0] is False or f.luhn_check("7992739871")


# ── withholding ───────────────────────────────────────────────────────────────
@pytest.mark.parametrize("gross,has_tin,tt,rate,amount", [
    (10000, True, "goods", "0.02", "200.00"),         # at threshold
    (9999.99, True, "goods", "0", "0.00"),            # below threshold
    (3000, True, "services", "0.02", "60.00"),
    (2999, True, "services", "0", "0.00"),
    (500, False, "goods", "0.30", "150.00"),          # no TIN: 30% of any amount
    (2000, False, "services", "0.30", "600.00"),
    (50000, True, "import", "0.03", "1500.00"),
    (50000, False, "import", "0.03", "1500.00"),      # import rate regardless of TIN
    (5000, True, "other", "0.02", "100.00"),          # other -> services threshold
    (1000, True, "weird", "0", "0.00"),
])
def test_withholding_for(gross, has_tin, tt, rate, amount):
    r = f.withholding_for(gross, has_tin, tt)
    assert r["rate"] == D(rate)
    assert r["amount"] == D(amount)
    assert r["net_payable"] == f.money(D(str(gross)) - D(amount))
    assert r["applies"] is (D(amount) > 0)


def test_withholding_constants():
    assert f.VAT_RATE == D("0.15")
    assert f.WHT_RATE_TIN == D("0.02") and f.WHT_RATE_NO_TIN == D("0.30") and f.WHT_RATE_IMPORT == D("0.03")
    assert f.WHT_THRESHOLD_GOODS == D("10000") and f.WHT_THRESHOLD_SERVICES == D("3000")


def test_withholding_summary_buckets():
    s = f.withholding_summary([
        {"gross_amount": 10000, "withheld_rate": "0.02", "withheld_amount": 200},
        {"gross_amount": 1000, "withheld_rate": "0.30", "withheld_amount": 300},
        {"gross_amount": 20000, "withheld_rate": "0.02", "withheld_amount": 400},
    ])
    assert s["count"] == 3 and s["total_gross"] == D("31000.00") and s["total_withheld"] == D("900.00")
    assert s["buckets"]["2%"]["count"] == 2 and s["buckets"]["30%"]["withheld"] == D("300")
    assert f.withholding_summary([])["total_withheld"] == D("0.00")


# ── VAT return lines ──────────────────────────────────────────────────────────
def test_classify_vat_type():
    assert f.classify_vat_type("Standard VAT (15%)") == "standard"
    assert f.classify_vat_type("STANDARD") == "standard"
    assert f.classify_vat_type("Zero Rated (0%)") == "zero_rated"
    assert f.classify_vat_type("zero_rated") == "zero_rated"
    assert f.classify_vat_type("Exempt") == "exempt"
    assert f.classify_vat_type("Withholding VAT (2%)") == "standard"
    assert f.classify_vat_type("", 0) == "exempt"
    assert f.classify_vat_type(None, 15) == "standard"


def test_build_vat_return_lines_math():
    income = [
        {"gross_amount": 115000, "vat_amount": 15000, "net_amount": 100000, "vat_type": "Standard VAT (15%)"},
        {"gross_amount": 40000, "vat_amount": 0, "net_amount": 40000, "vat_type": "Zero Rated (0%)"},
        {"gross_amount": 5000, "vat_amount": 0, "net_amount": 5000, "vat_type": "Exempt"},
        {"gross_amount": 999, "vat_amount": 130, "net_amount": 869, "vat_type": "STANDARD", "is_active": False},
    ]
    expenses = [
        {"gross_amount": 23000, "vat_amount": 3000, "net_amount": 20000, "vat_type": "STANDARD", "category": "RENT"},
        {"gross_amount": 11500, "vat_amount": 1500, "net_amount": 10000, "vat_type": "STANDARD", "category": "Import duty"},
        {"gross_amount": 2000, "vat_amount": 0, "net_amount": 2000, "vat_type": "Exempt", "category": "BANK"},
    ]
    capital = [
        {"amount": 57500, "vat_amount": 7500, "transaction_type": "INJECTION"},
        {"amount": 1000, "vat_amount": 100, "transaction_type": "WITHDRAWAL"},
    ]
    r = f.build_vat_return_lines(income, expenses, capital, credit_brought_forward=500)
    L = r["lines"]
    assert L["output_taxable"] == D("100000.00") and L["output_vat"] == D("15000.00")
    assert L["output_zero_rated"] == D("40000.00") and L["output_exempt"] == D("5000.00")
    assert L["output_total"] == D("145000.00")
    assert L["input_local"] == D("20000.00") and L["input_vat_local"] == D("3000.00")
    assert L["input_import"] == D("10000.00") and L["input_vat_import"] == D("1500.00")
    assert L["input_capital"] == D("50000.00") and L["input_vat_capital"] == D("7500.00")
    assert L["input_no_vat"] == D("2000.00")
    assert L["input_vat_total"] == D("12000.00")
    assert L["credit_brought_forward"] == D("500.00") and L["credit_total"] == D("12500.00")
    assert L["net_payable"] == D("2500.00") and L["net_creditable"] == D("0.00")
    assert r["totals"]["net_payable"] == D("2500.00")
    assert [b["box"] for b in r["boxes"]][:3] == ["5", "10", "15"]


def test_build_vat_return_creditable_and_empty():
    r = f.build_vat_return_lines([], [{"gross_amount": 1150, "vat_amount": 150, "vat_type": "STANDARD"}])
    assert r["lines"]["net_creditable"] == D("150.00") and r["lines"]["net_payable"] == D("0.00")
    assert r["lines"]["input_local"] == D("1000.00")           # net derived from gross - vat
    empty = f.build_vat_return_lines([], [])
    assert all(v == 0 for v in empty["lines"].values())
    assert len(empty["boxes"]) == len(f.VAT_BOXES)


# ── numbering / hash chain ────────────────────────────────────────────────────
def test_format_number():
    assert f.format_number("INV-", 7, 6) == "INV-000007"
    assert f.format_number("", 123, 0) == "123"


def test_sequence_allocator_gapless_and_void_never_reused():
    s = f.SequenceAllocator("INV-", 1, 4)
    nums = [s.allocate() for _ in range(5)]
    assert nums == ["INV-0001", "INV-0002", "INV-0003", "INV-0004", "INV-0005"]
    s.void("INV-0003")
    assert s.allocate() == "INV-0006"                 # voided number is not handed out again
    assert "INV-0003" in s.voided and s.next_number == 7
    assert f.sequence_is_gapless(s.issued, "INV-", 4)
    assert not f.sequence_is_gapless(["INV-0001", "INV-0003"], "INV-", 4)
    with pytest.raises(ValueError):
        s.void("INV-0003")


def test_normalize_items_and_totals():
    items = f.normalize_items([
        {"description": "Router", "qty": 2, "unit_price": "1000", "vat_rate": ""},
        {"description": "Install", "qty": 1, "unit_price": 500, "vat_rate": "15"},   # '15' -> 15%
        {"description": "Exempt thing", "qty": 1, "unit_price": 100, "vat_rate": 0},
        {"description": "", "qty": "", "unit_price": ""},                              # dropped
    ])
    assert len(items) == 3
    assert items[0]["line_total"] == "2000.00" and items[0]["vat_amount"] == "300.00"
    assert items[1]["vat_rate"] == "0.15" and items[1]["vat_amount"] == "75.00"
    t = f.invoice_totals(items)
    assert t == {"subtotal": D("2600.00"), "vat_amount": D("375.00"), "total": D("2975.00")}


def test_chain_hash_and_verify():
    a = {"series_id": "s", "number": "INV-000001", "kind": "invoice", "issued_at": "2026-08-01",
         "customer_name": "Acme", "customer_tin": "", "items": [], "subtotal": 100, "vat_amount": 15, "total": 115}
    a["hash"] = f.chain_hash(None, f.invoice_hash_payload(a))
    b = dict(a, number="INV-000002", total=230, subtotal=200, vat_amount=30)
    b["hash"] = f.chain_hash(a["hash"], f.invoice_hash_payload(b))
    assert re.fullmatch(r"[0-9a-f]{64}", a["hash"]) and a["hash"] != b["hash"]
    assert f.verify_chain([a, b]) == {"ok": True, "checked": 2, "broken": []}
    tampered = dict(b, total=999)
    assert f.verify_chain([a, tampered])["broken"] == ["INV-000002"]


# ── PDF builders ──────────────────────────────────────────────────────────────
def _assert_valid_pdf(data: bytes):
    assert data.startswith(b"%PDF-") and data.rstrip().endswith(b"%%EOF")
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF", data)
    assert m, "missing startxref"
    xref = int(m.group(1))
    assert data[xref:xref + 4] == b"xref"


def _profile():
    return {"taxpayer_name": "Abyssinia Networks PLC", "taxpayer_name_am": "አቢሲኒያ ኔትዎርክስ", "tin": "0012345678",
            "vat_reg_no": "VAT-9988", "tax_centre": "Bole Branch", "region": "Addis Ababa", "city": "Addis Ababa",
            "sub_city": "Bole", "woreda": "03", "house_no": "1234", "phone": "+251 11 000 0000", "category": "A"}


def _ret():
    r = f.build_vat_return_lines([{"gross_amount": 1150, "vat_amount": 150, "vat_type": "STANDARD"}], [])
    return {"period_year": 2026, "period_month": 8, "status": "draft", "lines": r["lines"], "totals": r["totals"]}


def _wht():
    return {"receipt_no": "WHT-000001", "payment_date": date(2026, 8, 3), "payee_name": "Supplier", "payee_tin": "0098765432",
            "has_tin": True, "transaction_type": "goods", "gross_amount": 10000, "withheld_rate": "0.02", "withheld_amount": 200,
            "invoice_ref": "S-1"}


def _inv():
    items = f.normalize_items([{"description": "Firewall", "qty": 1, "unit_price": 20000}])
    return {"number": "INV-000001", "kind": "invoice", "issued_at": date(2026, 8, 5), "customer_name": "Awash Bank",
            "customer_tin": "0011111111", "items": items, "status": "issued", "hash": "ab" * 32,
            "withholding_expected": 400, **f.invoice_totals(items)}


@pytest.mark.parametrize("builtin", [True, False])
def test_all_pdfs_render(monkeypatch, builtin):
    if builtin:
        monkeypatch.setattr(f, "_HAS_REPORTLAB", False)
    elif not f._HAS_REPORTLAB:
        pytest.skip("reportlab not installed")
    for data in (f.build_vat_return_pdf(_profile(), _ret()),
                 f.build_withholding_return_pdf(_profile(), 2026, 8, [_wht()] * 60),   # forces a page break
                 f.build_withholding_return_pdf(_profile(), 2026, 8, []),
                 f.build_withholding_receipt_pdf(_profile(), _wht()),
                 f.build_invoice_pdf(_profile(), _inv(), {"machine_id": "MRC-1"}),
                 f.build_invoice_pdf({}, {"items": [], "status": "voided", "void_reason": "test"})):
        _assert_valid_pdf(data)


def test_builtin_pdf_xref_offsets_are_exact(monkeypatch):
    monkeypatch.setattr(f, "_HAS_REPORTLAB", False)
    data = f.build_invoice_pdf(_profile(), _inv())
    xref = int(re.search(rb"startxref\s+(\d+)", data).group(1))
    body = data[xref:].decode("latin-1").splitlines()
    n = int(body[1].split()[1])
    for i, line in enumerate(body[3:3 + n - 1], start=1):
        off = int(line.split()[0])
        assert data[off:].startswith(f"{i} 0 obj".encode()), f"object {i} offset wrong"


def test_builtin_pdf_strips_ethiopic_when_no_font(monkeypatch):
    monkeypatch.setattr(f, "_HAS_REPORTLAB", False)
    d = f.PdfDoc()
    assert d.ethiopic is False
    assert d.clean("VAT ተ.እ.ታ") == "VAT"
    assert f.pdf_backend() in ("reportlab", "builtin")
