"""
Bank Reconciliation — pure logic (no database, no FastAPI).

Statement parsing / normalisation, candidate scoring for matching statement
lines to ledger lines, auto-match selection and the reconciliation arithmetic.
Everything here is deterministic and unit-tested in tests/test_bank_recon_logic.py.

Conventions
-----------
* A statement line is a dict with keys txn_date, value_date, description,
  reference, debit, credit, balance, external_id. ``credit`` is money INTO the
  bank account (deposit), ``debit`` is money OUT (payment, charge). That is the
  bank's view and mirrors how every Ethiopian bank prints its statements.
* A ledger line is a dict with entry_date, debit, credit (the company's view of
  the bank GL account: debit = cash in, credit = cash out), reference,
  description. So a statement CREDIT matches a ledger DEBIT and vice versa.
* Money is Decimal quantised to 2 dp via ``q2``.
"""
from __future__ import annotations

import hashlib
import re
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

TWO = Decimal("0.01")
ZERO = Decimal("0")

DATE_TOLERANCE_DAYS = 3
AUTO_MATCH_MIN_SCORE = 60
AUTO_MATCH_MIN_GAP = 10          # best candidate must beat the runner-up by this much

BANK_NAMES = (
    "Commercial Bank of Ethiopia", "Awash Bank", "Dashen Bank", "Bank of Abyssinia",
    "Zemen Bank", "Wegagen Bank", "Hibret Bank", "NIB International Bank",
    "Cooperative Bank of Oromia", "Abay Bank", "Berhan Bank", "Bunna Bank",
    "Oromia Bank", "Lion International Bank", "Enat Bank", "Global Bank Ethiopia",
    "Siinqee Bank", "Tsehay Bank", "Amhara Bank", "ZamZam Bank", "Hijra Bank",
    "Development Bank of Ethiopia", "Other",
)

STATEMENT_STATUSES = ("imported", "in_progress", "reconciled")
LINE_STATUSES = ("unmatched", "matched", "excluded")
MATCH_KINDS = ("journal", "payment", "adjustment")
RECON_STATUSES = ("open", "balanced", "closed")
ADJUSTMENT_KINDS = ("bank_charge", "interest", "fx", "correction", "other")
ADJUSTMENT_LABELS = {
    "bank_charge": "Bank charge",
    "interest": "Interest",
    "fx": "FX difference",
    "correction": "Correction",
    "other": "Other",
}
AGEING_BUCKETS = ("0-30", "31-60", "61-90", "90+")

# Downloadable import template — canonical column order
TEMPLATE_COLUMNS = ("Date", "Description", "Reference", "Debit", "Credit", "Balance")
TEMPLATE_SAMPLE = {
    "Date": "2026-09-01", "Description": "Cheque 001234 — Supplier payment", "Reference": "CHQ001234",
    "Debit": 12500.00, "Credit": "", "Balance": 487500.00,
}
TEMPLATE_NOTES = {
    "Date": "Transaction date (YYYY-MM-DD, DD/MM/YYYY or DD-Mon-YYYY). CBE/Awash 'Txn Date' is accepted.",
    "Description": "Narration / particulars as printed by the bank.",
    "Reference": "Cheque number, transfer reference or transaction id (used for matching).",
    "Debit": "Money OUT of the account (payments, charges). Leave blank if none.",
    "Credit": "Money INTO the account (deposits, interest). Leave blank if none.",
    "Balance": "Running balance after the transaction (optional).",
}

# Header auto-detection — canonical field → lower-case aliases (compared after
# stripping punctuation). Order matters: first canonical field claiming a
# header wins, so put the most specific ones first.
HEADER_ALIASES: Dict[str, Tuple[str, ...]] = {
    "value_date": ("value date", "valuedate", "val date", "effective date"),
    "txn_date": ("txn date", "transaction date", "tran date", "trans date", "posting date", "post date",
                 "booking date", "book date", "date", "entry date", "txndate"),
    "external_id": ("transaction id", "txn id", "trans id", "tran id", "transaction no", "txn no",
                    "bank reference", "bank ref", "external id", "unique id", "ft number", "ft no",
                    "trace number", "trace no", "receipt no", "receipt number"),
    "reference": ("reference", "ref", "ref no", "reference no", "reference number", "cheque no",
                  "cheque number", "check no", "chq no", "document no", "doc no", "voucher no",
                  "instrument no", "narrative ref", "customer reference"),
    "description": ("description", "narration", "narrative", "particulars", "details", "transaction details",
                    "remarks", "memo", "transaction description", "payee", "payee details", "detail"),
    "debit": ("debit", "debit amount", "withdrawal", "withdrawals", "dr", "paid out", "money out",
              "payments", "outflow", "debits", "withdrawal amt", "debit amt"),
    "credit": ("credit", "credit amount", "deposit", "deposits", "cr", "paid in", "money in", "receipts",
               "inflow", "credits", "deposit amt", "credit amt", "lodgement", "lodgements"),
    "balance": ("balance", "running balance", "closing balance", "ledger balance", "available balance",
                "book balance", "bal"),
    "amount": ("amount", "transaction amount", "txn amount", "net amount", "signed amount", "value"),
}

_PUNCT = re.compile(r"[^a-z0-9 ]+")
_WS = re.compile(r"\s+")
_TOKEN = re.compile(r"[a-z0-9]{3,}")


# ── Decimal helpers ───────────────────────────────────────────────

def D(value: Any) -> Decimal:
    """Tolerant Decimal: '', None, NaN, '1,234.50', '(500)', '500 DR' → Decimal."""
    if value is None:
        return ZERO
    if isinstance(value, Decimal):
        return ZERO if value.is_nan() else value
    if isinstance(value, bool):
        return Decimal(int(value))
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:  # NaN
            return ZERO
        return Decimal(str(value))
    s = str(value).strip()
    if not s or s in ("-", "—", "–", "nan", "NaN", "None"):
        return ZERO
    neg = False
    if s.startswith("(") and s.endswith(")"):
        neg, s = True, s[1:-1]
    s = re.sub(r"(?i)\b(etb|birr|usd|eur|gbp)\b", "", s)
    tag = re.search(r"(?i)\b(dr|cr)\b", s)
    if tag:
        s = s.replace(tag.group(0), "")
        if tag.group(1).lower() == "dr":
            neg = not neg
    s = s.replace(",", "").replace(" ", "").replace("'", "")
    if s.endswith("-"):
        neg, s = not neg, s[:-1]
    if s.startswith("-"):
        neg, s = not neg, s[1:]
    s = s.lstrip("+")
    try:
        d = Decimal(s)
    except (InvalidOperation, ValueError):
        return ZERO
    return -d if neg else d


def q2(value: Any) -> Decimal:
    return D(value).quantize(TWO, rounding=ROUND_HALF_UP)


# ── Dates ─────────────────────────────────────────────────────────

_DATE_FORMATS = (
    "%Y-%m-%d", "%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%d-%b-%Y", "%d-%b-%y", "%d %b %Y",
    "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d/%m/%y", "%d-%m-%y", "%Y%m%d", "%m/%d/%Y",
)
_EXCEL_EPOCH = date(1899, 12, 30)


def parse_date(value: Any) -> Optional[date]:
    """Tolerant date parser. Returns None when the value is blank or unreadable."""
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != value:  # NaN
            return None
        if 20000 < float(value) < 80000:  # Excel serial (1954..2119)
            return _EXCEL_EPOCH + timedelta(days=int(value))
        return None
    s = str(value).strip()
    if not s:
        return None
    s = s.split("T")[0] if "T" in s and len(s) >= 10 else s
    s = s[:19] if re.match(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}", s) else s
    if " " in s and re.match(r"\d{1,4}[-/.]\d{1,2}[-/.]\d{1,4} ", s):
        s = s.split(" ")[0]
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s).date()
    except Exception:
        return None


def days_between(a: Any, b: Any) -> Optional[int]:
    da, db = parse_date(a), parse_date(b)
    if da is None or db is None:
        return None
    return abs((da - db).days)


# ── Header detection & row normalisation ──────────────────────────

def _norm_header(h: Any) -> str:
    s = _PUNCT.sub(" ", str(h or "").strip().lower().replace("_", " ").replace("/", " "))
    return _WS.sub(" ", s).strip()


def detect_columns(headers: Sequence[Any]) -> Dict[str, Any]:
    """Map canonical field names → the actual header found in the file.

    Exact alias match first, then a 'contains' pass, so "Debit (ETB)" still
    becomes ``debit`` and "Txn Date" becomes ``txn_date``.
    """
    normed = [(_norm_header(h), h) for h in headers]
    found: Dict[str, Any] = {}
    taken = set()
    for field, aliases in HEADER_ALIASES.items():
        for n, raw in normed:
            if raw in taken or not n:
                continue
            if n in aliases:
                found[field] = raw; taken.add(raw); break
    for field, aliases in HEADER_ALIASES.items():
        if field in found:
            continue
        for n, raw in normed:
            if raw in taken or not n:
                continue
            if any(n.startswith(a + " ") or n.endswith(" " + a) or (" " + a + " ") in (" " + n + " ")
                   for a in aliases if len(a) > 2):
                found[field] = raw; taken.add(raw); break
    if "txn_date" not in found and "value_date" in found:
        found["txn_date"] = found["value_date"]
    return found


def missing_columns(mapping: Dict[str, Any]) -> List[str]:
    missing = []
    if "txn_date" not in mapping:
        missing.append("date")
    if "debit" not in mapping and "credit" not in mapping and "amount" not in mapping:
        missing.append("debit/credit or amount")
    return missing


def line_hash(txn_date: Any, debit: Any, credit: Any, description: Any) -> str:
    """Stable dedupe key for statement lines without a bank transaction id."""
    d = parse_date(txn_date)
    key = "|".join([
        d.isoformat() if d else "",
        str(q2(debit)), str(q2(credit)),
        _WS.sub(" ", str(description or "").strip().lower()),
    ])
    return hashlib.sha1(key.encode("utf-8")).hexdigest()


def _text(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if v != v:
            return ""
        if v.is_integer():
            return str(int(v))
    return str(v).strip()


def normalise_rows(rows: Iterable[Dict[str, Any]], mapping: Optional[Dict[str, Any]] = None
                   ) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Turn raw spreadsheet rows (header → cell) into clean statement lines.

    Returns (lines, errors). Blank rows are skipped silently; rows without a
    readable date or without any amount are reported in ``errors`` with their
    1-based data row number (header is row 1, so data starts at 2).
    """
    rows = list(rows)
    if not rows:
        return [], []
    if mapping is None:
        mapping = detect_columns(list(rows[0].keys()))
    miss = missing_columns(mapping)
    if miss:
        return [], [f"Could not find required column(s): {', '.join(miss)}"]
    out: List[Dict[str, Any]] = []
    errors: List[str] = []
    for idx, row in enumerate(rows):
        rownum = idx + 2
        if not any(_text(v) not in ("", "nan") for v in row.values()):
            continue
        g = lambda f: row.get(mapping[f]) if f in mapping else None  # noqa: E731
        txn_date = parse_date(g("txn_date"))
        if txn_date is None:
            raw = _text(g("txn_date"))
            if raw.lower() in ("total", "totals", "closing balance", "opening balance", "balance b/f", "balance c/f"):
                continue
            errors.append(f"Row {rownum}: unreadable date '{raw}'")
            continue
        debit = q2(g("debit")) if "debit" in mapping else ZERO
        credit = q2(g("credit")) if "credit" in mapping else ZERO
        if debit == ZERO and credit == ZERO and "amount" in mapping:
            amt = q2(g("amount"))
            if amt < 0:
                debit = -amt
            else:
                credit = amt
        if debit < 0 and credit == ZERO:      # some exports print withdrawals as negatives
            debit = -debit
        if credit < 0 and debit == ZERO:
            debit, credit = -credit, ZERO
        if debit == ZERO and credit == ZERO:
            errors.append(f"Row {rownum}: no amount")
            continue
        if debit != ZERO and credit != ZERO:
            # a row with both — net it to the larger side (rare; e.g. reversal lines)
            net = credit - debit
            debit, credit = (-net, ZERO) if net < 0 else (ZERO, net)
        description = _text(g("description"))
        reference = _text(g("reference"))
        external_id = _text(g("external_id")) or None
        balance = g("balance")
        line = {
            "txn_date": txn_date,
            "value_date": parse_date(g("value_date")) if "value_date" in mapping else None,
            "description": description,
            "reference": reference,
            "debit": debit,
            "credit": credit,
            "balance": q2(balance) if _text(balance) not in ("", "nan") else None,
            "external_id": external_id,
        }
        line["row_hash"] = line_hash(txn_date, debit, credit, description)
        out.append(line)
    return out, errors


def dedupe_lines(lines: List[Dict[str, Any]], existing_external_ids: Iterable[str] = (),
                 existing_hashes: Iterable[str] = ()) -> Tuple[List[Dict[str, Any]], int]:
    """Drop lines already imported (by bank txn id, else by row hash) and
    duplicates within the same file. Returns (kept, dropped_count)."""
    seen_ext = set(x for x in existing_external_ids if x)
    seen_hash = set(existing_hashes)
    kept, dropped = [], 0
    for ln in lines:
        ext = ln.get("external_id")
        if ext:
            if ext in seen_ext:
                dropped += 1; continue
            seen_ext.add(ext)
        else:
            if ln["row_hash"] in seen_hash:
                dropped += 1; continue
        seen_hash.add(ln["row_hash"])
        kept.append(ln)
    return kept, dropped


def statement_totals(lines: Iterable[Dict[str, Any]]) -> Dict[str, Decimal]:
    debit = credit = ZERO
    for ln in lines:
        debit += q2(ln.get("debit"))
        credit += q2(ln.get("credit"))
    return {"debit": q2(debit), "credit": q2(credit), "net": q2(credit - debit)}


def implied_opening_balance(closing_balance: Any, lines: Iterable[Dict[str, Any]]) -> Decimal:
    """closing − Σcredits + Σdebits."""
    t = statement_totals(lines)
    return q2(D(closing_balance) - t["net"])


# ── Matching ──────────────────────────────────────────────────────

def signed_statement_amount(line: Dict[str, Any]) -> Decimal:
    """+ for money into the bank account, − for money out (bank view)."""
    return q2(D(line.get("credit")) - D(line.get("debit")))


def signed_ledger_amount(line: Dict[str, Any]) -> Decimal:
    """+ for debits on the bank GL account (cash in), − for credits (cash out)."""
    return q2(D(line.get("debit")) - D(line.get("credit")))


def similarity(a: Any, b: Any) -> float:
    """0..1 text similarity: token overlap blended with a sequence ratio."""
    sa = _WS.sub(" ", _PUNCT.sub(" ", str(a or "").lower())).strip()
    sb = _WS.sub(" ", _PUNCT.sub(" ", str(b or "").lower())).strip()
    if not sa or not sb:
        return 0.0
    ta, tb = set(_TOKEN.findall(sa)), set(_TOKEN.findall(sb))
    jacc = len(ta & tb) / len(ta | tb) if (ta or tb) else 0.0
    ratio = SequenceMatcher(None, sa, sb).ratio()
    return round(max(jacc, ratio * 0.9), 3)


def _refs(line: Dict[str, Any]) -> set:
    vals = " ".join(str(line.get(k) or "") for k in ("reference", "external_id", "reference_number"))
    return set(t for t in _TOKEN.findall(vals.lower()) if any(ch.isdigit() for ch in t))


def score(stmt: Dict[str, Any], ledger: Dict[str, Any], date_tolerance: int = DATE_TOLERANCE_DAYS) -> int:
    """0..100 confidence that a statement line and a ledger line are the same
    transaction. 0 means 'not a candidate' (amount or direction differs).

    amount+direction exact    → required (else 0)
    same day / ±tolerance / ±7 / later → 50 / 40 / 20 / 5
    reference token in common → +30
    description similarity    → up to +20
    """
    if signed_statement_amount(stmt) != signed_ledger_amount(ledger) or signed_statement_amount(stmt) == ZERO:
        return 0
    s = 0
    gap = days_between(stmt.get("txn_date"), ledger.get("entry_date"))
    if gap is None:
        s += 10
    elif gap == 0:
        s += 50
    elif gap <= date_tolerance:
        s += 40
    elif gap <= 7:
        s += 20
    else:
        s += 5
    ra, rb = _refs(stmt), _refs(ledger)
    if ra and rb and (ra & rb):
        s += 30
    else:
        # reference printed inside the other side's narration
        joined = (str(stmt.get("description") or "") + " " + str(stmt.get("reference") or "")).lower()
        if any(r in joined for r in rb if len(r) >= 4):
            s += 25
    sim = similarity(stmt.get("description"), (str(ledger.get("description") or "") + " " +
                                               str(ledger.get("entry_description") or "")))
    s += int(round(sim * 20))
    return min(100, s)


def candidates(stmt: Dict[str, Any], ledger_lines: Iterable[Dict[str, Any]], limit: int = 5,
               date_tolerance: int = DATE_TOLERANCE_DAYS) -> List[Tuple[int, Dict[str, Any]]]:
    """Ledger lines that could match ``stmt``, best first."""
    scored = []
    for ln in ledger_lines:
        sc = score(stmt, ln, date_tolerance)
        if sc > 0:
            scored.append((sc, ln))
    scored.sort(key=lambda t: (-t[0], str(t[1].get("entry_date") or ""), str(t[1].get("line_id") or "")))
    return scored[:limit]


def auto_match(stmt_lines: Iterable[Dict[str, Any]], ledger_lines: Iterable[Dict[str, Any]],
               min_score: int = AUTO_MATCH_MIN_SCORE, min_gap: int = AUTO_MATCH_MIN_GAP,
               date_tolerance: int = DATE_TOLERANCE_DAYS) -> List[Dict[str, Any]]:
    """Greedy one-to-one matching.

    Pairs are ranked by score; a pair is accepted when the score is at least
    ``min_score`` and the statement line's best candidate beats its runner-up
    by ``min_gap`` (or the runner-up is already taken) — so two identical
    payments on the same day are left for a human. Returns
    [{"stmt": line, "ledger": line, "score": n}, ...].
    """
    stmt_lines = list(stmt_lines)
    ledger_lines = list(ledger_lines)
    pairs: List[Tuple[int, int, int]] = []
    per_stmt: Dict[int, List[Tuple[int, int]]] = {}
    for i, s in enumerate(stmt_lines):
        cands = []
        for j, l in enumerate(ledger_lines):
            sc = score(s, l, date_tolerance)
            if sc >= min_score:
                cands.append((sc, j))
                pairs.append((sc, i, j))
        cands.sort(reverse=True)
        per_stmt[i] = cands
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))
    used_s, used_l = set(), set()
    out = []
    for sc, i, j in pairs:
        if i in used_s or j in used_l:
            continue
        others = [c for c in per_stmt[i] if c[1] != j and c[1] not in used_l]
        if others and sc - others[0][0] < min_gap:
            continue  # ambiguous — leave for manual review
        used_s.add(i); used_l.add(j)
        out.append({"stmt": stmt_lines[i], "ledger": ledger_lines[j], "score": sc})
    return out


# ── Reconciliation arithmetic ─────────────────────────────────────

def reconcile(statement_balance: Any, ledger_balance: Any, outstanding_deposits: Any,
              outstanding_payments: Any, unmatched_statement_credits: Any = 0,
              unmatched_statement_debits: Any = 0) -> Dict[str, Decimal]:
    """Classic bank reconciliation statement.

    adjusted_bank   = statement_balance + deposits in transit − outstanding payments
    adjusted_ledger = ledger_balance + unmatched statement credits (interest etc. not yet booked)
                                     − unmatched statement debits (bank charges not yet booked)
    difference      = adjusted_bank − ledger_balance   (what still has to be explained)
    unexplained     = adjusted_bank − adjusted_ledger  (what remains after booking the bank-only items)
    """
    sb, lb = q2(statement_balance), q2(ledger_balance)
    od, op = q2(outstanding_deposits), q2(outstanding_payments)
    uc, ud = q2(unmatched_statement_credits), q2(unmatched_statement_debits)
    adjusted_bank = q2(sb + od - op)
    adjusted_ledger = q2(lb + uc - ud)
    return {
        "statement_balance": sb,
        "ledger_balance": lb,
        "outstanding_deposits": od,
        "outstanding_payments": op,
        "unmatched_statement_credits": uc,
        "unmatched_statement_debits": ud,
        "adjusted_bank_balance": adjusted_bank,
        "adjusted_ledger_balance": adjusted_ledger,
        "difference": q2(adjusted_bank - lb),
        "unexplained_difference": q2(adjusted_bank - adjusted_ledger),
        "is_balanced": q2(adjusted_bank - lb) == ZERO,
    }


def outstanding_totals(ledger_lines: Iterable[Dict[str, Any]]) -> Dict[str, Decimal]:
    """Σ unreconciled ledger debits (deposits in transit) and credits (outstanding payments)."""
    dep = pay = ZERO
    for ln in ledger_lines:
        dep += q2(ln.get("debit"))
        pay += q2(ln.get("credit"))
    return {"deposits": q2(dep), "payments": q2(pay)}


def ledger_balance(opening_balance: Any, ledger_lines: Iterable[Dict[str, Any]]) -> Decimal:
    bal = q2(opening_balance)
    for ln in ledger_lines:
        bal += q2(ln.get("debit")) - q2(ln.get("credit"))
    return q2(bal)


def ageing_bucket(days: Optional[int]) -> str:
    if days is None or days <= 30:
        return "0-30"
    if days <= 60:
        return "31-60"
    if days <= 90:
        return "61-90"
    return "90+"


def age_items(items: Iterable[Dict[str, Any]], as_of: Optional[date] = None, date_key: str = "txn_date",
              amount_fn=None) -> Dict[str, Dict[str, Any]]:
    """Group items into ageing buckets → {bucket: {count, amount, items}}."""
    as_of = as_of or date.today()
    out = {b: {"count": 0, "amount": ZERO, "items": []} for b in AGEING_BUCKETS}
    for it in items:
        d = parse_date(it.get(date_key))
        days = (as_of - d).days if d else None
        b = ageing_bucket(days)
        amt = q2(amount_fn(it)) if amount_fn else q2(D(it.get("debit")) + D(it.get("credit")))
        it = dict(it, age_days=days, bucket=b)
        out[b]["count"] += 1
        out[b]["amount"] = q2(out[b]["amount"] + amt)
        out[b]["items"].append(it)
    return out


# ── Adjustments → journal lines ───────────────────────────────────

def adjustment_effect(kind: str, effect: Optional[str] = None) -> str:
    """'increase' (bank goes up: Dr bank) or 'decrease' (bank goes down: Cr bank)."""
    if kind == "bank_charge":
        return "decrease"
    if kind == "interest":
        return "increase"
    return "increase" if (effect or "").lower().startswith("inc") else "decrease"


def adjustment_journal_lines(kind: str, amount: Any, bank_gl: str, other_gl: str, description: str,
                             effect: Optional[str] = None, bank_name: str = "", other_name: str = "") -> List[Dict[str, Any]]:
    """Balanced pair of journal lines for a reconciliation adjustment.

    bank_charge → Dr expense  / Cr bank
    interest    → Dr bank     / Cr income
    fx/correction/other → direction from ``effect`` ('increase' | 'decrease').
    """
    amt = abs(q2(amount))
    if amt == ZERO:
        return []
    bank = {"account_code": bank_gl, "account_name": bank_name, "description": description}
    other = {"account_code": other_gl, "account_name": other_name, "description": description}
    if adjustment_effect(kind, effect) == "increase":
        return [dict(bank, debit_amount=amt, credit_amount=ZERO), dict(other, debit_amount=ZERO, credit_amount=amt)]
    return [dict(other, debit_amount=amt, credit_amount=ZERO), dict(bank, debit_amount=ZERO, credit_amount=amt)]


def signed_adjustment_amount(kind: str, amount: Any, effect: Optional[str] = None) -> Decimal:
    """Amount as stored on bank_adjustments: + raises the bank balance, − lowers it."""
    amt = abs(q2(amount))
    return amt if adjustment_effect(kind, effect) == "increase" else -amt


def days_since(d: Any, today: Optional[date] = None) -> Optional[int]:
    dd = parse_date(d)
    if dd is None:
        return None
    return ((today or date.today()) - dd).days
