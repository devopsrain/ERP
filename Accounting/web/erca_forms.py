"""
ERCA / Ministry of Revenues tax outputs — pure computations and PDF builders.

Everything in this module is side-effect free (no DB, no request objects) so it
can be unit-tested directly:

  * Ethiopian tax constants (VAT, withholding rates/thresholds, filing rules)
  * A private Ethiopian calendar converter (Julian-day based) + fiscal-year and
    monthly-period helpers
  * TIN normalisation / format validation
  * Withholding rule engine  -> withholding_for(gross, has_tin, transaction_type)
  * VAT return line builder   -> build_vat_return_lines(...)
  * Gapless numbering helpers + tamper-evidence hash chain
  * PDF builders for the VAT declaration, withholding return/receipt and invoices
    (reportlab when installed, otherwise a tiny built-in PDF writer — no new deps)

A shared web/ethiopian_calendar.py will be provided by the localisation work
later; the converter here is deliberately private (underscore-free names are
kept but nothing outside this module should import the calendar from here).
"""
from __future__ import annotations

import calendar
import glob
import hashlib
import io
import json
import os
import re
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP
from typing import Iterable, List, Optional

# ══════════════════════════════════════════════════════════════════════════════
#  TAX CONSTANTS — verify against current proclamation
# ══════════════════════════════════════════════════════════════════════════════
# VAT Proclamation 285/2002 as amended (1157/2019, 1341/2024): standard rate 15%.
VAT_RATE = Decimal("0.15")

# Withholding on domestic payments — Income Tax Proclamation 979/2016 art. 92
# and Directive: 2% of the gross payment when the supplier holds a TIN and the
# payment reaches the per-transaction threshold; 30% when the supplier cannot
# present a TIN. Imports: 3% of the CIF value (art. 91).
WHT_RATE_TIN = Decimal("0.02")
WHT_RATE_NO_TIN = Decimal("0.30")
WHT_RATE_IMPORT = Decimal("0.03")
WHT_THRESHOLD_GOODS = Decimal("10000")     # per transaction, ETB
WHT_THRESHOLD_SERVICES = Decimal("3000")   # per transaction, ETB

# Category A taxpayers file VAT monthly; the return + payment are due by the
# last day of the month following the tax period.
VAT_FILING_FREQUENCY = {"A": "monthly", "B": "monthly", "C": "quarterly"}

TRANSACTION_TYPES = ("goods", "services", "import", "other")
TAXPAYER_CATEGORIES = ("A", "B", "C")
INVOICE_KINDS = ("invoice", "receipt", "credit_note", "withholding_receipt")

# ERCA VAT declaration box numbers (Form VAT-Return). Verify against the
# current form revision before relying on the numbering for e-filing.
VAT_BOXES = [
    ("5",  "Taxable sales / supplies (15%)",        "ግብር የሚከፈልባቸው ሽያጮች",       "output_taxable"),
    ("10", "Output VAT on taxable sales",           "የውጤት ተ.እ.ታ",               "output_vat"),
    ("15", "Zero-rated sales / supplies",           "ዜሮ ተመን ሽያጮች",             "output_zero_rated"),
    ("20", "Exempt sales / supplies",               "ከግብር ነጻ ሽያጮች",            "output_exempt"),
    ("30", "Total sales / supplies",                "ጠቅላላ ሽያጭ",                 "output_total"),
    ("35", "Local purchases with VAT (value)",      "የአገር ውስጥ ግዢዎች",           "input_local"),
    ("40", "Input VAT on local purchases",          "የግብዓት ተ.እ.ታ (አገር ውስጥ)",   "input_vat_local"),
    ("45", "Imports (value)",                       "ከውጭ የገቡ ዕቃዎች",            "input_import"),
    ("50", "Input VAT on imports",                  "የግብዓት ተ.እ.ታ (ውጭ)",        "input_vat_import"),
    ("55", "Capital goods purchases (value)",       "የካፒታል ዕቃ ግዢ",             "input_capital"),
    ("60", "Input VAT on capital goods",            "የግብዓት ተ.እ.ታ (ካፒታል)",     "input_vat_capital"),
    ("65", "Purchases without VAT (exempt/zero)",   "ተ.እ.ታ የሌለባቸው ግዢዎች",      "input_no_vat"),
    ("70", "Total input VAT",                       "ጠቅላላ የግብዓት ተ.እ.ታ",       "input_vat_total"),
    ("75", "VAT credit brought forward",            "የተላለፈ ተ.እ.ታ ክሬዲት",       "credit_brought_forward"),
    ("80", "Total credit (70 + 75)",                "ጠቅላላ ክሬዲት",               "credit_total"),
    ("85", "Net VAT payable (10 - 80)",             "የሚከፈል የተጣራ ተ.እ.ታ",       "net_payable"),
    ("90", "Net VAT credit carried forward",        "የሚተላለፍ ክሬዲት",             "net_creditable"),
]

ETHIOPIAN_MONTHS = ["Meskerem", "Tikimt", "Hidar", "Tahsas", "Tir", "Yekatit",
                    "Megabit", "Miazia", "Ginbot", "Sene", "Hamle", "Nehase", "Pagume"]
ETHIOPIAN_MONTHS_AM = ["መስከረም", "ጥቅምት", "ኅዳር", "ታኅሣሥ", "ጥር", "የካቲት",
                       "መጋቢት", "ሚያዝያ", "ግንቦት", "ሰኔ", "ሐምሌ", "ነሐሴ", "ጳጉሜ"]

TWOPLACES = Decimal("0.01")


def money(value) -> Decimal:
    """Coerce anything numeric-ish to a 2-dp Decimal (None/'' -> 0.00)."""
    if value in (None, ""):
        return Decimal("0.00")
    if isinstance(value, Decimal):
        d = value
    else:
        try:
            d = Decimal(str(value))
        except Exception:
            return Decimal("0.00")
    return d.quantize(TWOPLACES, rounding=ROUND_HALF_UP)


# ══════════════════════════════════════════════════════════════════════════════
#  Ethiopian calendar (private converter — Julian Day based)
# ══════════════════════════════════════════════════════════════════════════════
# JD 1724220.5 is the start of 1 Meskerem 1 Amete Mihret (29 Aug 8 CE Julian),
# i.e. Julian Day Number 1724221. The Beyene/Kudlek algorithm below works with
# the integer JDN and an offset of (epoch JDN - 365).
ETHIOPIC_EPOCH_JD = 1724220.5
_ETHIOPIC_EPOCH_JDN = int(ETHIOPIC_EPOCH_JD + 0.5)          # 1724221
_JDN_OFFSET = _ETHIOPIC_EPOCH_JDN - 365                     # 1723856


def gregorian_to_jdn(d: date) -> int:
    a = (14 - d.month) // 12
    y = d.year + 4800 - a
    m = d.month + 12 * a - 3
    return d.day + (153 * m + 2) // 5 + 365 * y + y // 4 - y // 100 + y // 400 - 32045


def jdn_to_gregorian(jdn: int) -> date:
    a = jdn + 32044
    b = (4 * a + 3) // 146097
    c = a - 146097 * b // 4
    d = (4 * c + 3) // 1461
    e = c - 1461 * d // 4
    m = (5 * e + 2) // 153
    day = e - (153 * m + 2) // 5 + 1
    month = m + 3 - 12 * (m // 10)
    year = 100 * b + d - 4800 + m // 10
    return date(year, month, day)


def ethiopic_to_jdn(year: int, month: int, day: int) -> int:
    return _JDN_OFFSET + 365 + 365 * (year - 1) + year // 4 + 30 * month + day - 31


def jdn_to_ethiopic(jdn: int) -> tuple:
    r = (jdn - _JDN_OFFSET) % 1461
    n = (r % 365) + 365 * (r // 1460)
    year = 4 * ((jdn - _JDN_OFFSET) // 1461) + r // 365 - r // 1460
    month = n // 30 + 1
    day = n % 30 + 1
    return year, month, day


def to_ethiopic(d: date) -> tuple:
    """Gregorian date -> (year, month, day) in the Ethiopian (Amete Mihret) calendar."""
    return jdn_to_ethiopic(gregorian_to_jdn(d))


def to_gregorian(year: int, month: int, day: int) -> date:
    return jdn_to_gregorian(ethiopic_to_jdn(year, month, day))


def ethiopic_month_name(month: int, amharic: bool = False) -> str:
    names = ETHIOPIAN_MONTHS_AM if amharic else ETHIOPIAN_MONTHS
    return names[month - 1] if 1 <= month <= 13 else ""


def format_ethiopic(d: date, amharic: bool = False) -> str:
    y, m, dd = to_ethiopic(d)
    return f"{ethiopic_month_name(m, amharic)} {dd}, {y}"


def ethiopian_fiscal_year(d: date) -> int:
    """Ethiopian fiscal year (EFY) containing d. The EFY runs Hamle 1 – Sene 30
    and is named after the Ethiopian year in which it ENDS: EFY 2018 =
    Hamle 1 2017 – Sene 30 2018 (~ 8 Jul 2025 – 7 Jul 2026)."""
    y, m, _ = to_ethiopic(d)
    return y + 1 if m >= 11 else y


def fiscal_year_bounds(efy: int) -> tuple:
    """Gregorian (start, end) of Ethiopian fiscal year `efy`."""
    return to_gregorian(efy - 1, 11, 1), to_gregorian(efy, 10, 30)


# ══════════════════════════════════════════════════════════════════════════════
#  Monthly VAT periods (Gregorian months as used by the e-filing portal)
# ══════════════════════════════════════════════════════════════════════════════

def period_bounds(year: int, month: int) -> dict:
    """First/last day of the Gregorian tax period, the filing deadline (last day
    of the following month), and the equivalent Ethiopian calendar span."""
    if not 1 <= int(month) <= 12:
        raise ValueError("month must be 1..12")
    year, month = int(year), int(month)
    start = date(year, month, 1)
    end = date(year, month, calendar.monthrange(year, month)[1])
    ny, nm = (year + 1, 1) if month == 12 else (year, month + 1)
    deadline = date(ny, nm, calendar.monthrange(ny, nm)[1])
    ey1, em1, ed1 = to_ethiopic(start)
    ey2, em2, ed2 = to_ethiopic(end)
    return {
        "year": year, "month": month, "start": start, "end": end,
        "deadline": deadline,
        "label": f"{calendar.month_name[month]} {year}",
        "ethiopian_label": (f"{ethiopic_month_name(em1)} {ed1} – "
                            f"{ethiopic_month_name(em2)} {ed2}, {ey2}"),
        "ethiopian_label_am": (f"{ethiopic_month_name(em1, True)} {ed1} – "
                               f"{ethiopic_month_name(em2, True)} {ed2}, {ey2}"),
        "fiscal_year": ethiopian_fiscal_year(end),
    }


def previous_period(year: int, month: int) -> tuple:
    return (year - 1, 12) if month == 1 else (year, month - 1)


def current_period(today: Optional[date] = None) -> tuple:
    """The period whose return is currently due (the previous calendar month)."""
    today = today or date.today()
    return previous_period(today.year, today.month)


def unfiled_periods(filed: Iterable[tuple], today: Optional[date] = None,
                    lookback: int = 12) -> List[dict]:
    """Periods in the last `lookback` months that are not in `filed`
    {(year, month), ...}; each flagged overdue if past the deadline."""
    today = today or date.today()
    filed = set(tuple(x) for x in filed)
    out = []
    y, m = current_period(today)
    for _ in range(lookback):
        if (y, m) not in filed:
            pb = period_bounds(y, m)
            pb["overdue"] = today > pb["deadline"]
            out.append(pb)
        y, m = previous_period(y, m)
    return out


# ══════════════════════════════════════════════════════════════════════════════
#  TIN handling
# ══════════════════════════════════════════════════════════════════════════════
_TIN_RE = re.compile(r"^\d{10}$")
_LEGACY_TIN_RE = re.compile(r"^\d{9}$")


def normalize_tin(tin) -> str:
    """Strip spaces/dashes: '0012-345-678' -> '0012345678'."""
    return re.sub(r"[\s\-]", "", str(tin or ""))


def luhn_check(digits: str) -> bool:
    """Mod-10 (Luhn) check. NOT enforced by validate_tin: the Ministry of
    Revenues has not published a check-digit scheme for TINs — verify against
    current proclamation/directive before switching strict=True on."""
    if not digits.isdigit():
        return False
    total, parity = 0, len(digits) % 2
    for i, ch in enumerate(digits):
        n = int(ch)
        if i % 2 == parity:
            n *= 2
            if n > 9:
                n -= 9
        total += n
    return total % 10 == 0


def validate_tin(tin, strict: bool = False) -> tuple:
    """(ok, message). Current TINs are 10 digits; 9-digit legacy TINs are
    accepted with a warning message. `strict` additionally applies luhn_check."""
    t = normalize_tin(tin)
    if not t:
        return False, "TIN is empty"
    if not t.isdigit():
        return False, "TIN must contain digits only"
    if _TIN_RE.match(t):
        if strict and not luhn_check(t):
            return False, "TIN check digit failed"
        return True, "ok"
    if _LEGACY_TIN_RE.match(t):
        return True, "legacy 9-digit TIN — confirm with the supplier"
    return False, f"TIN must be 10 digits (got {len(t)})"


# ══════════════════════════════════════════════════════════════════════════════
#  Withholding rules
# ══════════════════════════════════════════════════════════════════════════════

def withholding_for(gross, has_tin: bool, transaction_type: str = "goods") -> dict:
    """Withholding tax on a single payment.

    goods    : 2% when gross >= 10,000 ETB (TIN holder); 30% of any amount if no TIN
    services : 2% when gross >=  3,000 ETB (TIN holder); 30% of any amount if no TIN
    import   : 3% of CIF value regardless of TIN
    other    : treated as services threshold
    """
    g = money(gross)
    tt = (transaction_type or "goods").lower()
    if tt not in TRANSACTION_TYPES:
        tt = "other"
    if tt == "import":
        rate, reason = WHT_RATE_IMPORT, "3% withholding on imports"
    elif not has_tin:
        rate, reason = WHT_RATE_NO_TIN, "30% — supplier without TIN"
    else:
        threshold = WHT_THRESHOLD_GOODS if tt == "goods" else WHT_THRESHOLD_SERVICES
        if g >= threshold:
            rate, reason = WHT_RATE_TIN, f"2% — {tt} at or above ETB {threshold:,.0f}"
        else:
            rate, reason = Decimal("0"), f"below ETB {threshold:,.0f} threshold for {tt}"
    amount = money(g * rate)
    return {"gross": g, "rate": rate, "amount": amount, "net_payable": money(g - amount),
            "applies": amount > 0, "reason": reason, "transaction_type": tt}


def withholding_summary(entries: Iterable[dict]) -> dict:
    """Totals for the monthly withholding return, grouped by rate bucket."""
    buckets = {}
    total_gross = total_wht = Decimal("0")
    count = 0
    for e in entries:
        rate = money(e.get("withheld_rate") or 0)
        gross = money(e.get("gross_amount"))
        wht = money(e.get("withheld_amount"))
        key = f"{(rate * 100).normalize():f}%"
        b = buckets.setdefault(key, {"rate": rate, "count": 0, "gross": Decimal("0"), "withheld": Decimal("0")})
        b["count"] += 1
        b["gross"] += gross
        b["withheld"] += wht
        total_gross += gross
        total_wht += wht
        count += 1
    return {"count": count, "total_gross": money(total_gross), "total_withheld": money(total_wht),
            "buckets": dict(sorted(buckets.items()))}


# ══════════════════════════════════════════════════════════════════════════════
#  VAT return computation (pure — rows are plain dicts from vat_* tables)
# ══════════════════════════════════════════════════════════════════════════════

def classify_vat_type(vat_type, vat_rate=None) -> str:
    """Map the free-form vat_type stored by the VAT portal ('Standard VAT (15%)',
    'STANDARD', 'zero_rated', 'Exempt', 'Withholding VAT (2%)' …) to
    standard | zero_rated | exempt."""
    s = str(vat_type or "").strip().lower()
    if "zero" in s:
        return "zero_rated"
    if "exempt" in s:
        return "exempt"
    if not s and vat_rate is not None:
        try:
            return "standard" if Decimal(str(vat_rate)) > 0 else "exempt"
        except Exception:
            pass
    return "standard"


def is_import_row(row: dict) -> bool:
    text = f"{row.get('category', '')} {row.get('description', '')}".lower()
    return "import" in text or "customs" in text


def build_vat_return_lines(income_rows: Iterable[dict], expense_rows: Iterable[dict],
                           capital_rows: Iterable[dict] = (), credit_brought_forward=0) -> dict:
    """Aggregate raw table rows into the declaration lines.

    income rows : gross_amount (incl. VAT), vat_amount, net_amount, vat_type
    expense rows: same shape; rows whose category/description mentions import
                  are treated as imports
    capital rows: vat_capital 'INJECTION' rows carrying VAT are treated as
                  capital-goods purchases (amount, vat_amount)
    """
    z = Decimal("0")
    L = {k: z for _, _, _, k in VAT_BOXES}
    for r in income_rows:
        if r.get("is_active") is False:
            continue
        cls = classify_vat_type(r.get("vat_type"), r.get("vat_rate"))
        gross, vat = money(r.get("gross_amount")), money(r.get("vat_amount"))
        net = money(r.get("net_amount")) if r.get("net_amount") not in (None, "") else money(gross - vat)
        if cls == "standard":
            L["output_taxable"] += net
            L["output_vat"] += vat
        elif cls == "zero_rated":
            L["output_zero_rated"] += net
        else:
            L["output_exempt"] += net
    L["output_total"] = L["output_taxable"] + L["output_zero_rated"] + L["output_exempt"]

    for r in expense_rows:
        if r.get("is_active") is False:
            continue
        cls = classify_vat_type(r.get("vat_type"), r.get("vat_rate"))
        gross, vat = money(r.get("gross_amount")), money(r.get("vat_amount"))
        net = money(r.get("net_amount")) if r.get("net_amount") not in (None, "") else money(gross - vat)
        if cls != "standard" or vat == 0:
            L["input_no_vat"] += net
        elif is_import_row(r):
            L["input_import"] += net
            L["input_vat_import"] += vat
        else:
            L["input_local"] += net
            L["input_vat_local"] += vat

    for r in capital_rows:
        if r.get("is_active") is False:
            continue
        if str(r.get("transaction_type") or "INJECTION").upper() != "INJECTION":
            continue
        vat = money(r.get("vat_amount"))
        if vat <= 0:
            continue
        L["input_capital"] += money(r.get("amount")) - vat
        L["input_vat_capital"] += vat

    L["input_vat_total"] = L["input_vat_local"] + L["input_vat_import"] + L["input_vat_capital"]
    L["credit_brought_forward"] = money(credit_brought_forward)
    L["credit_total"] = L["input_vat_total"] + L["credit_brought_forward"]
    net = L["output_vat"] - L["credit_total"]
    L["net_payable"] = net if net > 0 else z
    L["net_creditable"] = -net if net < 0 else z
    lines = {k: money(v) for k, v in L.items()}
    return {
        "lines": lines,
        "boxes": [{"box": b, "label": en, "label_am": am, "key": k, "amount": lines[k]}
                  for b, en, am, k in VAT_BOXES],
        "totals": {
            "output_vat": lines["output_vat"],
            "input_vat": lines["input_vat_total"],
            "net_payable": lines["net_payable"],
            "net_creditable": lines["net_creditable"],
            "total_sales": lines["output_total"],
            "total_purchases": money(lines["input_local"] + lines["input_import"]
                                     + lines["input_capital"] + lines["input_no_vat"]),
        },
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Invoice maths, gapless numbering, hash chain
# ══════════════════════════════════════════════════════════════════════════════

def format_number(prefix: str, n: int, pad: int = 6) -> str:
    return f"{prefix or ''}{int(n):0{int(pad or 0)}d}"


class SequenceAllocator:
    """In-memory model of the DB allocation (UPDATE … next_number+1 RETURNING
    next_number-1). Voided numbers are recorded, never reused."""

    def __init__(self, prefix="INV-", start=1, pad=6):
        self.prefix, self.next_number, self.pad = prefix, int(start), int(pad)
        self.issued: List[str] = []
        self.voided: List[str] = []

    def allocate(self) -> str:
        n = self.next_number
        self.next_number += 1
        s = format_number(self.prefix, n, self.pad)
        self.issued.append(s)
        return s

    def void(self, number: str) -> None:
        if number not in self.issued:
            raise ValueError("unknown number")
        if number in self.voided:
            raise ValueError("already voided")
        self.voided.append(number)


def sequence_is_gapless(numbers: Iterable[str], prefix: str, pad: int) -> bool:
    ns = sorted(int(str(x)[len(prefix or ""):]) for x in numbers)
    return not ns or ns == list(range(ns[0], ns[0] + len(ns)))


def normalize_items(items: Iterable[dict], default_vat_rate=VAT_RATE) -> List[dict]:
    """Coerce raw line items; drop empty rows; compute line_total (net) & vat."""
    out = []
    for it in items or ():
        desc = str(it.get("description") or "").strip()
        qty = money(it.get("qty") or 0)
        price = money(it.get("unit_price") or 0)
        if not desc and qty == 0 and price == 0:
            continue
        rate_raw = it.get("vat_rate")
        rate = Decimal(str(rate_raw)) if rate_raw not in (None, "") else Decimal(str(default_vat_rate))
        if rate > 1:                      # tolerate '15' meaning 15%
            rate = rate / Decimal(100)
        line_total = money(qty * price)
        out.append({"description": desc, "qty": str(qty), "unit_price": str(price),
                    "vat_rate": str(rate), "line_total": str(line_total),
                    "vat_amount": str(money(line_total * rate))})
    return out


def invoice_totals(items: Iterable[dict]) -> dict:
    sub = vat = Decimal("0")
    for it in items:
        sub += money(it.get("line_total"))
        vat += money(it.get("vat_amount"))
    return {"subtotal": money(sub), "vat_amount": money(vat), "total": money(sub + vat)}


def chain_hash(previous_hash: Optional[str], payload: dict) -> str:
    """sha256(previous_hash + canonical JSON of the invoice content)."""
    canon = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(((previous_hash or "") + canon).encode("utf-8")).hexdigest()


def invoice_hash_payload(inv: dict) -> dict:
    return {
        "number": inv.get("number"), "kind": inv.get("kind"),
        "issued_at": str(inv.get("issued_at") or ""),
        "customer_name": inv.get("customer_name") or "", "customer_tin": inv.get("customer_tin") or "",
        "items": inv.get("items") or [],
        "subtotal": str(money(inv.get("subtotal"))), "vat_amount": str(money(inv.get("vat_amount"))),
        "total": str(money(inv.get("total"))),
    }


def verify_chain(invoices: Iterable[dict]) -> dict:
    """Re-hash invoices in issue order (per series). Returns {ok, checked, broken:[numbers]}."""
    prev_by_series = {}
    broken = []
    checked = 0
    for inv in invoices:
        key = inv.get("series_id")
        expected = chain_hash(prev_by_series.get(key), invoice_hash_payload(inv))
        if expected != inv.get("hash"):
            broken.append(inv.get("number"))
        prev_by_series[key] = inv.get("hash")
        checked += 1
    return {"ok": not broken, "checked": checked, "broken": broken}


# ══════════════════════════════════════════════════════════════════════════════
#  PDF output
# ══════════════════════════════════════════════════════════════════════════════
_PAGE_W, _PAGE_H = 595.28, 841.89          # A4 portrait, points
_ETHIOPIC_RANGE = re.compile(r"[ሀ-፿ᎀ-᎟ⶀ-⷟]")

try:                                        # optional — not a declared dependency
    from reportlab.pdfgen import canvas as _rl_canvas          # type: ignore
    from reportlab.pdfbase import pdfmetrics as _rl_metrics    # type: ignore
    from reportlab.pdfbase.ttfonts import TTFont as _RLTTFont  # type: ignore
    _HAS_REPORTLAB = True
except Exception:                           # pragma: no cover - depends on env
    _rl_canvas = _rl_metrics = _RLTTFont = None
    _HAS_REPORTLAB = False

_FONT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static", "fonts")
_ETHIOPIC_FONT_NAME = "ERCAEthiopic"
_ethiopic_font_state = {"checked": False, "ok": False}


def find_ethiopic_font() -> Optional[str]:
    """First .ttf under web/static/fonts that looks like an Ethiopic face."""
    pats = ("ethiop", "abyssinica", "nyala", "ebrima", "noto", "washra", "geez")
    files = sorted(glob.glob(os.path.join(_FONT_DIR, "*.ttf")))
    for f in files:
        if any(p in os.path.basename(f).lower() for p in pats):
            return f
    return files[0] if files else None


def _ethiopic_font_ready() -> bool:
    if _ethiopic_font_state["checked"]:
        return _ethiopic_font_state["ok"]
    _ethiopic_font_state["checked"] = True
    if not _HAS_REPORTLAB:
        return False
    path = find_ethiopic_font()
    if not path:
        return False
    try:
        _rl_metrics.registerFont(_RLTTFont(_ETHIOPIC_FONT_NAME, path))
        _ethiopic_font_state["ok"] = True
    except Exception:
        _ethiopic_font_state["ok"] = False
    return _ethiopic_font_state["ok"]


def pdf_backend() -> str:
    return "reportlab" if _HAS_REPORTLAB else "builtin"


class _BuiltinPdf:
    """Minimal PDF writer: Helvetica text, lines, rectangles, multiple pages.
    Only needed when reportlab is absent; Latin-1 text only."""

    def __init__(self):
        self.pages: List[List[str]] = [[]]

    @property
    def ops(self):
        return self.pages[-1]

    @staticmethod
    def _esc(s: str) -> str:
        s = s.encode("latin-1", "replace").decode("latin-1")
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    def text(self, x, y, s, size=9, bold=False):
        self.ops.append(f"BT /{'F2' if bold else 'F1'} {size} Tf {x:.2f} {y:.2f} Td ({self._esc(s)}) Tj ET")

    def line(self, x1, y1, x2, y2, w=0.6):
        self.ops.append(f"{w} w {x1:.2f} {y1:.2f} m {x2:.2f} {y2:.2f} l S")

    def rect(self, x, y, w, h, fill=False):
        self.ops.append(f"0.6 w {x:.2f} {y:.2f} {w:.2f} {h:.2f} re {'f' if fill else 'S'}")

    def new_page(self):
        self.pages.append([])

    def render(self) -> bytes:
        objs: List[bytes] = []

        def add(body: str) -> int:
            objs.append(body.encode("latin-1"))
            return len(objs)

        font1 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica /Encoding /WinAnsiEncoding >>")
        font2 = add("<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold /Encoding /WinAnsiEncoding >>")
        pages_idx = len(objs) + 1 + 2 * len(self.pages)      # reserved id for /Pages
        page_ids = []
        for ops in self.pages:
            stream = "\n".join(ops)
            cid = add(f"<< /Length {len(stream.encode('latin-1'))} >>\nstream\n{stream}\nendstream")
            pid = add(f"<< /Type /Page /Parent {pages_idx} 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}] "
                      f"/Contents {cid} 0 R /Resources << /Font << /F1 {font1} 0 R /F2 {font2} 0 R >> >> >>")
            page_ids.append(pid)
        kids = " ".join(f"{p} 0 R" for p in page_ids)
        assert add(f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>") == pages_idx
        catalog = add(f"<< /Type /Catalog /Pages {pages_idx} 0 R >>")
        out = io.BytesIO()
        out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = []
        for i, body in enumerate(objs, start=1):
            offsets.append(out.tell())
            out.write(f"{i} 0 obj\n".encode("latin-1") + body + b"\nendobj\n")
        xref = out.tell()
        out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode("latin-1"))
        for off in offsets:
            out.write(f"{off:010d} 00000 n \n".encode("latin-1"))
        out.write(f"trailer\n<< /Size {len(objs) + 1} /Root {catalog} 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode("latin-1"))
        return out.getvalue()


class PdfDoc:
    """Tiny drawing API shared by every form. Coordinates are measured from the
    TOP-left of an A4 page in points; y grows downwards."""

    def __init__(self):
        self.ethiopic = _ethiopic_font_ready()
        if _HAS_REPORTLAB:
            self._buf = io.BytesIO()
            self._c = _rl_canvas.Canvas(self._buf, pagesize=(_PAGE_W, _PAGE_H))
            self._b = None
        else:
            self._c = None
            self._b = _BuiltinPdf()
        self.y = 40.0
        self.page_no = 1

    # -- primitives -------------------------------------------------------
    def _font_for(self, s: str, bold: bool) -> str:
        if self.ethiopic and _ETHIOPIC_RANGE.search(s):
            return _ETHIOPIC_FONT_NAME
        return "Helvetica-Bold" if bold else "Helvetica"

    def clean(self, s) -> str:
        """Drop Ethiopic text when no Ethiopic font can be embedded."""
        s = "" if s is None else str(s)
        if not self.ethiopic and _ETHIOPIC_RANGE.search(s):
            # drop whole Ethiopic words (incl. their abbreviation dots, e.g. ተ.እ.ታ)
            # and any punctuation-only tokens left dangling next to them
            tokens = [t for t in s.split() if not _ETHIOPIC_RANGE.search(t)]
            while tokens and not re.search(r"[0-9A-Za-z]", tokens[-1]):
                tokens.pop()
            while tokens and not re.search(r"[0-9A-Za-z]", tokens[0]):
                tokens.pop(0)
            s = " ".join(tokens)
        return s

    def width(self, s: str, size: float, bold=False) -> float:
        if _HAS_REPORTLAB:
            try:
                return _rl_metrics.stringWidth(s, self._font_for(s, bold), size)
            except Exception:
                pass
        return len(s) * size * 0.52

    def text(self, x, y, s, size=9, bold=False, align="left"):
        s = self.clean(s)
        if not s:
            return
        if align == "right":
            x -= self.width(s, size, bold)
        elif align == "center":
            x -= self.width(s, size, bold) / 2
        py = _PAGE_H - y
        if self._c is not None:
            self._c.setFont(self._font_for(s, bold), size)
            self._c.drawString(x, py, s)
        else:
            self._b.text(x, py, s, size, bold)

    def line(self, x1, y1, x2, y2, w=0.6):
        if self._c is not None:
            self._c.setLineWidth(w)
            self._c.line(x1, _PAGE_H - y1, x2, _PAGE_H - y2)
        else:
            self._b.line(x1, _PAGE_H - y1, x2, _PAGE_H - y2, w)

    def rect(self, x, y, w, h):
        if self._c is not None:
            self._c.setLineWidth(0.6)
            self._c.rect(x, _PAGE_H - y - h, w, h)
        else:
            self._b.rect(x, _PAGE_H - y - h, w, h)

    def new_page(self):
        if self._c is not None:
            self._c.showPage()
        else:
            self._b.new_page()
        self.page_no += 1
        self.y = 40.0

    def ensure_space(self, needed: float):
        if self.y + needed > _PAGE_H - 50:
            self.new_page()

    def render(self) -> bytes:
        if self._c is not None:
            self._c.save()
            return self._buf.getvalue()
        return self._b.render()

    # -- composites -------------------------------------------------------
    def para(self, s, size=9, bold=False, x=40, gap=13):
        self.ensure_space(gap)
        self.text(x, self.y + size, s, size, bold)
        self.y += gap

    def label_am(self, x, y, en, am, size=9, bold=False):
        """English label with the Amharic label alongside (when a font exists)."""
        self.text(x, y, en, size, bold)
        if self.ethiopic and am:
            self.text(x + self.width(en, size, bold) + 6, y, am, size)

    def header(self, profile: dict, title_en: str, title_am: str = "", subtitle: str = ""):
        p = profile or {}
        self.text(_PAGE_W / 2, 52, "FEDERAL DEMOCRATIC REPUBLIC OF ETHIOPIA — MINISTRY OF REVENUES (ERCA)", 8.5, True, "center")
        self.text(_PAGE_W / 2, 64, "የኢትዮጵያ ፌዴራላዊ ዲሞክራሲያዊ ሪፐብሊክ — የገቢዎች ሚኒስቴር", 8.5, False, "center")
        self.text(_PAGE_W / 2, 86, title_en, 14, True, "center")
        if title_am:
            self.text(_PAGE_W / 2, 102, title_am, 11, False, "center")
        if subtitle:
            self.text(_PAGE_W / 2, 118, subtitle, 9.5, False, "center")
        top = 130
        self.rect(40, top, _PAGE_W - 80, 74)
        rows = [
            ("Taxpayer name", p.get("taxpayer_name") or "", "ስም", p.get("taxpayer_name_am") or ""),
            ("TIN", p.get("tin") or "", "የግብር ከፋይ መለያ ቁጥር", ""),
            ("VAT registration no.", p.get("vat_reg_no") or "", "የተ.እ.ታ ምዝገባ ቁጥር", ""),
            ("Tax centre / branch", p.get("tax_centre") or "", "የግብር ማዕከል", f"Category {p.get('category') or ''}"),
        ]
        yy = top + 15
        for en, val, am, extra in rows:
            self.label_am(46, yy, en + ":", am, 8.5, True)
            self.text(200, yy, val, 9)
            if extra:
                self.text(_PAGE_W - 46, yy, extra, 8.5, False, "right")
            yy += 15
        addr = ", ".join(x for x in (p.get("region"), p.get("city"), p.get("sub_city"),
                                     f"Woreda {p['woreda']}" if p.get("woreda") else "",
                                     f"House {p['house_no']}" if p.get("house_no") else "") if x)
        contact = " · ".join(x for x in (p.get("phone"), p.get("email")) if x)
        self.text(46, top + 84, f"Address: {addr or '—'}", 8)
        self.text(_PAGE_W - 46, top + 84, contact, 8, False, "right")
        self.y = top + 100

    def table(self, columns: List[tuple], rows: Iterable[Iterable], size=8.5, row_h=14):
        """columns: [(title, width, align)]; draws header + rows with page breaks."""
        x0 = 40
        total_w = sum(w for _, w, _ in columns)

        def draw_header():
            self.ensure_space(row_h * 2)
            self.rect(x0, self.y, total_w, row_h)
            x = x0
            for title, w, align in columns:
                tx = x + 3 if align == "left" else (x + w - 3 if align == "right" else x + w / 2)
                self.text(tx, self.y + row_h - 4, title, size, True, align)
                x += w
            self.y += row_h

        draw_header()
        for row in rows:
            if self.y + row_h > _PAGE_H - 50:
                self.new_page()
                draw_header()
            self.rect(x0, self.y, total_w, row_h)
            x = x0
            for (title, w, align), val in zip(columns, row):
                if val is None:
                    val = ""
                s = f"{val:,.2f}" if isinstance(val, (int, float, Decimal)) and not isinstance(val, bool) else str(val)
                tx = x + 3 if align == "left" else (x + w - 3 if align == "right" else x + w / 2)
                max_chars = max(3, int(w / (size * 0.5)))
                if len(s) > max_chars:
                    s = s[:max_chars - 1] + "…"
                self.text(tx, self.y + row_h - 4, s, size, False, align)
                x += w
            self.y += row_h

    def signature_block(self, declaration: str):
        self.ensure_space(110)
        self.y += 14
        self.rect(40, self.y, _PAGE_W - 80, 92)
        self.text(46, self.y + 14, "DECLARATION / መግለጫ", 9, True)
        words, line, yy = declaration.split(), "", self.y + 28
        for w in words:
            if self.width(line + " " + w, 8) > _PAGE_W - 100:
                self.text(46, yy, line, 8)
                yy += 11
                line = w
            else:
                line = (line + " " + w).strip()
        if line:
            self.text(46, yy, line, 8)
        yy = self.y + 72
        for i, lab in enumerate(("Name of declarant / ስም", "Signature / ፊርማ", "Date / ቀን", "Official stamp / ማህተም")):
            x = 46 + i * 128
            self.line(x, yy, x + 115, yy)
            self.text(x, yy + 10, lab, 7.5)
        self.y += 100

    def footer(self, note: str):
        self.text(40, _PAGE_H - 28, note, 7)
        self.text(_PAGE_W - 40, _PAGE_H - 28, f"Page {self.page_no}", 7, False, "right")


def _d(v) -> str:
    if v in (None, ""):
        return ""
    if isinstance(v, datetime):
        return v.strftime("%Y-%m-%d %H:%M")
    if isinstance(v, date):
        return v.isoformat()
    return str(v)[:10]


def build_vat_return_pdf(profile: dict, ret: dict) -> bytes:
    """ERCA VAT declaration: header, period, box table, declaration block."""
    pb = period_bounds(int(ret.get("period_year")), int(ret.get("period_month")))
    lines = ret.get("lines") or {}
    doc = PdfDoc()
    doc.header(profile, "VALUE ADDED TAX DECLARATION", "የተጨማሪ እሴት ታክስ ማስታወቂያ",
               f"Tax period: {pb['label']}  ({pb['ethiopian_label']})   ·   Due: {pb['deadline']}")
    doc.para(f"Status: {str(ret.get('status') or 'draft').upper()}    Computed: {_d(ret.get('computed_at'))}"
             f"    ERCA reference: {ret.get('erca_reference') or '—'}", 8.5)
    doc.y += 4
    rows = []
    for box, en, am, key in VAT_BOXES:
        label = en + (f"   {am}" if doc.ethiopic else "")
        rows.append((box, label, money(lines.get(key, 0))))
    doc.table([("Box", 36, "center"), ("Description", 359, "left"), ("Amount (ETB)", 120, "right")], rows)
    t = ret.get("totals") or {}
    doc.y += 8
    doc.text(_PAGE_W - 40, doc.y + 10, f"NET VAT PAYABLE: ETB {money(t.get('net_payable') or lines.get('net_payable')):,.2f}", 10, True, "right")
    doc.y += 14
    doc.text(_PAGE_W - 40, doc.y + 10, f"Credit carried forward: ETB {money(t.get('net_creditable') or lines.get('net_creditable')):,.2f}", 9, False, "right")
    doc.y += 10
    doc.signature_block("I declare that the information given in this return is true, correct and complete "
                        "to the best of my knowledge, in accordance with the Value Added Tax Proclamation. "
                        "እኔ በዚህ መግለጫ ላይ የሰጠሁት መረጃ ትክክለኛና የተሟላ መሆኑን አረጋግጣለሁ።")
    doc.footer("Generated by EBMS — ERCA tax outputs module. Box numbers: verify against current ERCA VAT declaration form.")
    return doc.render()


def build_withholding_return_pdf(profile: dict, year: int, month: int, entries: List[dict]) -> bytes:
    pb = period_bounds(year, month)
    summary = withholding_summary(entries)
    doc = PdfDoc()
    doc.header(profile, "WITHHOLDING TAX MONTHLY DECLARATION", "የተያዘ ግብር ወርሃዊ መግለጫ",
               f"Tax period: {pb['label']}  ({pb['ethiopian_label']})   ·   Due: {pb['deadline']}")
    doc.para(f"Entries: {summary['count']}    Gross paid: ETB {summary['total_gross']:,.2f}"
             f"    Tax withheld: ETB {summary['total_withheld']:,.2f}", 9, True)
    doc.y += 4
    cols = [("Receipt", 62, "left"), ("Date", 58, "left"), ("Payee", 128, "left"), ("TIN", 68, "left"),
            ("Type", 48, "left"), ("Gross", 70, "right"), ("Rate", 32, "right"), ("Withheld", 49, "right")]
    rows = [(e.get("receipt_no") or "", _d(e.get("payment_date")), e.get("payee_name") or "",
             e.get("payee_tin") or ("NO TIN" if not e.get("has_tin") else ""), e.get("transaction_type") or "",
             money(e.get("gross_amount")), f"{money(e.get('withheld_rate')) * 100:.0f}%", money(e.get("withheld_amount")))
            for e in entries]
    rows.append(("TOTAL", "", "", "", "", summary["total_gross"], "", summary["total_withheld"]))
    doc.table(cols, rows, size=8)
    doc.y += 6
    for key, b in summary["buckets"].items():
        doc.para(f"Rate {key}: {b['count']} payment(s), gross ETB {money(b['gross']):,.2f}, withheld ETB {money(b['withheld']):,.2f}", 8.5)
    doc.signature_block("I declare that the tax withheld from payments listed above has been correctly computed "
                        "under the Federal Income Tax Proclamation No. 979/2016 and will be remitted within 30 days "
                        "of the end of the month. የተያዘው ግብር በሕጉ መሠረት መሰብሰቡንና እንደሚከፈል አረጋግጣለሁ።")
    doc.footer("Generated by EBMS — ERCA tax outputs module. Rates: verify against current proclamation.")
    return doc.render()


def build_withholding_receipt_pdf(profile: dict, entry: dict) -> bytes:
    e = entry or {}
    doc = PdfDoc()
    doc.header(profile, "WITHHOLDING TAX RECEIPT", "የተያዘ ግብር ደረሰኝ",
               f"Receipt No. {e.get('receipt_no') or '—'}   ·   Date: {_d(e.get('payment_date'))}")
    rows = [
        ("Payee / supplier", e.get("payee_name") or ""),
        ("Payee TIN", e.get("payee_tin") or ("No TIN presented — 30% rate applied" if not e.get("has_tin") else "")),
        ("Transaction type", e.get("transaction_type") or ""),
        ("Invoice / reference", e.get("invoice_ref") or ""),
        ("Gross amount (ETB)", f"{money(e.get('gross_amount')):,.2f}"),
        ("Withholding rate", f"{money(e.get('withheld_rate')) * 100:.0f}%"),
        ("Tax withheld (ETB)", f"{money(e.get('withheld_amount')):,.2f}"),
        ("Net paid to payee (ETB)", f"{money(e.get('gross_amount')) - money(e.get('withheld_amount')):,.2f}"),
    ]
    doc.table([("Item", 200, "left"), ("Value", 315, "left")], rows, size=9, row_h=18)
    doc.y += 8
    doc.para("This receipt is issued to the payee as evidence of tax withheld at source. The withheld amount is "
             "creditable against the payee's income tax liability. ይህ ደረሰኝ በምንጭ ላይ ስለተያዘ ግብር ማስረጃ ነው።", 8)
    doc.signature_block("Issued by the withholding agent named above.")
    doc.footer("Generated by EBMS — ERCA tax outputs module.")
    return doc.render()


def build_invoice_pdf(profile: dict, inv: dict, series: Optional[dict] = None) -> bytes:
    i = inv or {}
    kind = (i.get("kind") or "invoice").replace("_", " ").upper()
    titles_am = {"INVOICE": "ደረሰኝ", "RECEIPT": "የክፍያ ደረሰኝ", "CREDIT NOTE": "የክሬዲት ማስታወሻ"}
    doc = PdfDoc()
    sub = f"No. {i.get('number') or '—'}   ·   Issued: {_d(i.get('issued_at'))}"
    if series and series.get("machine_id"):
        sub += f"   ·   Machine: {series['machine_id']}"
    if i.get("status") == "voided":
        sub += "   ·   *** VOIDED ***"
    doc.header(profile, f"VAT {kind}", titles_am.get(kind, ""), sub)
    doc.para(f"Customer: {i.get('customer_name') or '—'}      Customer TIN: {i.get('customer_tin') or '—'}", 9, True)
    doc.y += 4
    items = i.get("items") or []
    if isinstance(items, str):
        try:
            items = json.loads(items)
        except Exception:
            items = []
    rows = [(n, it.get("description", ""), money(it.get("qty")), money(it.get("unit_price")),
             f"{Decimal(str(it.get('vat_rate') or 0)) * 100:.0f}%", money(it.get("line_total")))
            for n, it in enumerate(items, start=1)]
    doc.table([("#", 24, "center"), ("Description", 251, "left"), ("Qty", 60, "right"),
               ("Unit price", 80, "right"), ("VAT", 40, "right"), ("Line total", 60, "right")], rows)
    doc.y += 6
    for lab, key in (("Subtotal (excl. VAT)", "subtotal"), ("VAT 15%", "vat_amount"), ("TOTAL (ETB)", "total")):
        doc.text(_PAGE_W - 170, doc.y + 10, lab, 9, key == "total")
        doc.text(_PAGE_W - 40, doc.y + 10, f"{money(i.get(key)):,.2f}", 9, key == "total", "right")
        doc.y += 13
    if money(i.get("withholding_expected")) > 0:
        doc.text(_PAGE_W - 170, doc.y + 10, "Withholding expected", 8.5)
        doc.text(_PAGE_W - 40, doc.y + 10, f"{money(i.get('withholding_expected')):,.2f}", 8.5, False, "right")
        doc.y += 13
    if i.get("status") == "voided":
        doc.para(f"VOIDED — {i.get('void_reason') or ''}", 9, True)
    doc.y += 6
    doc.para(f"Tamper-evidence hash: {i.get('hash') or ''}", 7)
    doc.footer("Generated by EBMS — ERCA tax outputs module. Numbers are gapless; voided numbers are retained.")
    return doc.render()
