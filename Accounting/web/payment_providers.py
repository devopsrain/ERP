"""
Ethiopian mobile-money provider adapters — Telebirr, CBE Birr, M-Pesa
(Safaricom Ethiopia) plus a generic bank-transfer/cash adapter.

Pure Python: no database, no network. Everything here is unit-testable
without the app running. The data store (payments_data_store.py) and the
routes call into this module for:

  * NormalizedPayment        — provider-agnostic payment record
  * normalize_msisdn()       — +2519xxxxxxxx / 09xxxxxxxx / 07xxxxxxxx → E.164
  * <Provider>Adapter        — parse_statement_row / parse_notification /
                               verify_signature / initiate
  * score_match()            — auto-reconciliation scoring against an
                               income/expense candidate
  * find_duplicates()        — duplicate detection by provider + txn id

Credentials are read from environment variables only (never stored in the
DB). The exact names are listed per adapter in ``ENV_REQUIRED`` /
``ENV_OPTIONAL`` and documented in PAYMENT_PROVIDERS.md at the repo root.
Endpoint URLs are deliberately NOT hard-coded — confirm them with the
provider onboarding docs before wiring ``initiate()`` to the network.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from difflib import SequenceMatcher
from typing import Any, Dict, Iterable, List, Optional, Tuple
from urllib.parse import parse_qs

PROVIDERS = ("telebirr", "cbebirr", "mpesa", "bank", "cash")
PROVIDER_LABELS = {
    "telebirr": "Telebirr",
    "cbebirr": "CBE Birr",
    "mpesa": "M-Pesa Ethiopia",
    "bank": "Bank transfer",
    "cash": "Cash",
}
DIRECTIONS = ("in", "out")
STATUSES = ("pending", "completed", "failed", "reversed")
SOURCES = ("manual", "statement_import", "notification", "api")

_TWO_PLACES = Decimal("0.01")


# ── MSISDN normalisation ──────────────────────────────────────────

def normalize_msisdn(value: Any) -> Optional[str]:
    """
    Normalise an Ethiopian mobile number to E.164 (+2519xxxxxxxx / +2517xxxxxxxx).

    Accepts: +251 9xx xxx xxx, 2519xxxxxxxx, 002519xxxxxxxx, 09xxxxxxxx,
    07xxxxxxxx (Safaricom Ethiopia), 9xxxxxxxx, with spaces, dashes,
    dots or parentheses. Returns None when the input is not a plausible
    Ethiopian mobile number (never raises).
    """
    if value is None:
        return None
    s = str(value).strip()
    if not s:
        return None
    # Excel often hands us floats like 912345678.0
    if re.fullmatch(r"\d+\.0+", s):
        s = s.split(".")[0]
    digits = re.sub(r"\D", "", s)
    if not digits:
        return None
    if digits.startswith("00"):
        digits = digits[2:]
    if digits.startswith("251"):
        national = digits[3:]
    elif digits.startswith("0") and len(digits) == 10:
        national = digits[1:]
    elif len(digits) == 9:
        national = digits
    else:
        return None
    if len(national) != 9 or national[0] not in ("9", "7"):
        return None
    return "+251" + national


def msisdn_local(msisdn: Optional[str]) -> str:
    """+251912345678 → 0912345678 (display form). Empty string when None."""
    n = normalize_msisdn(msisdn)
    return "0" + n[4:] if n else ""


def is_safaricom_msisdn(msisdn: Any) -> bool:
    """Safaricom Ethiopia numbers are in the 07 range (+2517…)."""
    n = normalize_msisdn(msisdn)
    return bool(n and n.startswith("+2517"))


# ── Value parsing helpers ─────────────────────────────────────────

def parse_amount(value: Any) -> Optional[Decimal]:
    """'ETB 1,234.50' / '(500)' / 1234.5 / '' → Decimal('1234.50') / None."""
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float, Decimal)):
        try:
            if isinstance(value, float) and value != value:  # NaN
                return None
            return Decimal(str(value)).quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)
        except (InvalidOperation, ValueError):
            return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null", "-"):
        return None
    negative = s.startswith("(") and s.endswith(")")
    s = re.sub(r"[^\d.\-]", "", s.replace(",", ""))
    if not s or s in ("-", "."):
        return None
    try:
        amt = Decimal(s)
    except InvalidOperation:
        return None
    if negative:
        amt = -amt
    return amt.quantize(_TWO_PLACES, rounding=ROUND_HALF_UP)


_DT_FORMATS = (
    "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d",
    "%Y/%m/%d %H:%M:%S", "%Y/%m/%d",
    "%d/%m/%Y %H:%M:%S", "%d/%m/%Y %H:%M", "%d/%m/%Y",
    "%d-%m-%Y %H:%M:%S", "%d-%m-%Y %H:%M", "%d-%m-%Y",
    "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%d-%b-%Y", "%d %b %Y %H:%M:%S", "%d %b %Y",
    "%Y%m%d%H%M%S", "%Y%m%d",
)


def parse_datetime(value: Any) -> Optional[datetime]:
    """Tolerant timestamp parser (ISO, dd/mm/yyyy, M-Pesa yyyymmddHHMMSS, epoch)."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.replace(tzinfo=None) if value.tzinfo else value
    if isinstance(value, date):
        return datetime(value.year, value.month, value.day)
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value != value:  # NaN
            return None
        try:
            v = float(value)
            if 19000101000000 <= v <= 99991231235959:   # yyyymmddHHMMSS (14 digits, M-Pesa)
                return datetime.strptime(str(int(v)), "%Y%m%d%H%M%S")
            if v > 1e12:      # epoch millis (13 digits)
                return datetime.fromtimestamp(v / 1000.0, tz=timezone.utc).replace(tzinfo=None)
            if v > 1e9:       # epoch seconds (10 digits)
                return datetime.fromtimestamp(v, tz=timezone.utc).replace(tzinfo=None)
            if 19000101 <= v <= 99991231:  # yyyymmdd as number
                return datetime.strptime(str(int(v)), "%Y%m%d")
        except (ValueError, OverflowError, OSError):
            return None
        return None
    s = str(value).strip()
    if not s or s.lower() in ("nan", "none", "null"):
        return None
    if s.isdigit():
        if len(s) == 14:
            try:
                return datetime.strptime(s, "%Y%m%d%H%M%S")
            except ValueError:
                return None
        if len(s) == 8:
            try:
                return datetime.strptime(s, "%Y%m%d")
            except ValueError:
                return None
        if len(s) in (10, 13):
            return parse_datetime(int(s))
    iso = s.replace("Z", "")
    if "T" in iso and ("+" in iso[10:] or iso.count("-") > 2):
        try:
            return datetime.fromisoformat(s.replace("Z", "+00:00")).replace(tzinfo=None)
        except ValueError:
            pass
    for fmt in _DT_FORMATS:
        try:
            return datetime.strptime(s[:26], fmt)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s[:19])
    except ValueError:
        return None


def _norm_key(key: Any) -> str:
    """'Transaction No.' → 'transaction_no' (header normalisation)."""
    k = re.sub(r"[^0-9a-zA-Z]+", "_", str(key or "").strip().lower())
    return k.strip("_")


def normalize_row(row: Dict[str, Any]) -> Dict[str, Any]:
    """Normalise dict keys; drop NaN/None/'' values so aliases fall through."""
    out: Dict[str, Any] = {}
    for k, v in (row or {}).items():
        if v is None:
            continue
        if isinstance(v, float) and v != v:
            continue
        if isinstance(v, str) and not v.strip():
            continue
        out[_norm_key(k)] = v
    return out


def _pick(row: Dict[str, Any], *aliases: str, default=None):
    for a in aliases:
        if a in row and row[a] not in (None, ""):
            return row[a]
    return default


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value == int(value):
        return str(int(value))
    return str(value).strip()


def _direction_from_word(word: Any, default: str = "in") -> str:
    w = _text(word).lower()
    if not w:
        return default
    if w in ("in", "credit", "cr", "received", "receive", "incoming", "paid_in", "paid in",
             "deposit", "collection", "c2b", "inflow"):
        return "in"
    if w in ("out", "debit", "dr", "paid", "sent", "outgoing", "withdrawn", "withdrawal",
             "payment", "b2c", "transfer_out", "outflow"):
        return "out"
    if "credit" in w or "receiv" in w or "paid in" in w:
        return "in"
    if "debit" in w or "withdraw" in w or "sent" in w:
        return "out"
    return default


def _status_from_word(word: Any, default: str = "completed") -> str:
    w = _text(word).lower()
    if not w:
        return default
    if any(t in w for t in ("success", "complete", "settled", "paid", "ok", "approved")):
        return "completed"
    if any(t in w for t in ("revers", "refund", "cancel", "charge_back", "chargeback")):
        return "reversed"
    if any(t in w for t in ("fail", "declin", "reject", "error", "timeout", "expired")):
        return "failed"
    if any(t in w for t in ("pending", "processing", "initiated", "wait")):
        return "pending"
    return default


# ── Normalised payment ────────────────────────────────────────────

@dataclass
class NormalizedPayment:
    provider: str
    direction: str = "in"                      # in | out
    amount: Optional[Decimal] = None
    currency: str = "ETB"
    fee: Decimal = Decimal("0")
    payer_name: str = ""
    payer_msisdn: Optional[str] = None
    payee_name: str = ""
    payee_msisdn: Optional[str] = None
    provider_txn_id: Optional[str] = None
    reference: str = ""
    narration: str = ""
    paid_at: Optional[datetime] = None
    status: str = "completed"
    raw: Dict[str, Any] = field(default_factory=dict)

    def is_valid(self) -> bool:
        return self.amount is not None and self.amount > 0 and self.direction in DIRECTIONS

    def duplicate_key(self) -> Optional[Tuple[str, str]]:
        if self.provider_txn_id:
            return (self.provider, str(self.provider_txn_id).strip().upper())
        return None

    def to_record(self) -> Dict[str, Any]:
        """Dict shaped for payments_data_store.record_payment(**fields)."""
        d = asdict(self)
        d["provider_txn_id"] = (str(self.provider_txn_id).strip() or None) if self.provider_txn_id else None
        d["fee"] = self.fee or Decimal("0")
        return d


def _json_safe(obj: Any) -> Any:
    """Make a parsed body JSON-serialisable (Decimal/datetime → str)."""
    if isinstance(obj, dict):
        return {str(k): _json_safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_json_safe(v) for v in obj]
    if isinstance(obj, (Decimal, datetime, date)):
        return str(obj)
    if isinstance(obj, bytes):
        return obj.decode("utf-8", "replace")
    return obj


def decode_body(body: Any) -> Any:
    """bytes/str JSON or form-encoded → dict (or the original value)."""
    if body is None:
        return {}
    if isinstance(body, (dict, list)):
        return body
    if isinstance(body, bytes):
        body = body.decode("utf-8", "replace")
    s = str(body).strip()
    if not s:
        return {}
    try:
        return json.loads(s)
    except ValueError:
        pass
    if "=" in s and "\n" not in s:
        qs = parse_qs(s, keep_blank_values=True)
        return {k: (v[0] if len(v) == 1 else v) for k, v in qs.items()}
    return {"_raw": s}


def _flatten(obj: Any, prefix: str = "", out: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Flatten nested JSON so aliases can be matched on leaf keys."""
    if out is None:
        out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            nk = _norm_key(k)
            if isinstance(v, (dict, list)):
                _flatten(v, nk, out)
            else:
                out.setdefault(nk, v)
                if prefix:
                    out.setdefault(f"{prefix}_{nk}", v)
    elif isinstance(obj, list):
        for item in obj:
            # Daraja style [{"Name": "Amount", "Value": 100}] (STK) / [{"Key": ..., "Value": ...}] (B2C)
            if isinstance(item, dict) and ("Name" in item or "Key" in item) and "Value" in item:
                out.setdefault(_norm_key(item.get("Name") or item.get("Key")), item.get("Value"))
            else:
                _flatten(item, prefix, out)
    return out


def _header_get(headers: Any, *names: str) -> Optional[str]:
    if not headers:
        return None
    lower = {str(k).lower(): v for k, v in dict(headers).items()}
    for n in names:
        v = lower.get(n.lower())
        if v:
            return str(v)
    return None


# ── Adapter base ─────────────────────────────────────────────────

class PaymentProviderAdapter:
    """Shared behaviour; providers override the parsing/aliases."""

    provider: str = "bank"
    label: str = "Bank transfer"
    ENV_REQUIRED: Tuple[str, ...] = ()
    ENV_OPTIONAL: Tuple[str, ...] = ()
    # Downloadable statement template (column -> sample value / note)
    STATEMENT_COLUMNS: Tuple[str, ...] = ()
    STATEMENT_SAMPLE: Dict[str, Any] = {}
    STATEMENT_NOTES: Dict[str, str] = {}
    # Header names the provider (or our proxy) may put an HMAC signature in
    SIGNATURE_HEADERS: Tuple[str, ...] = ("X-Signature", "X-Hub-Signature-256", "X-Signature-256", "Signature")

    # -- credentials ------------------------------------------------
    def env_status(self) -> Dict[str, bool]:
        """{ENV_NAME: configured?} for every env var this provider understands."""
        return {k: bool(os.environ.get(k)) for k in (*self.ENV_REQUIRED, *self.ENV_OPTIONAL)}

    def missing_env(self) -> List[str]:
        return [k for k in self.ENV_REQUIRED if not os.environ.get(k)]

    def is_configured(self) -> bool:
        return bool(self.ENV_REQUIRED) and not self.missing_env()

    # -- parsing ----------------------------------------------------
    def parse_statement_row(self, row: Dict[str, Any]) -> NormalizedPayment:
        """Generic bank/cash statement: date, reference, description, debit, credit, counterparty."""
        r = normalize_row(row)
        credit = parse_amount(_pick(r, "credit", "paid_in", "amount_in", "deposit", "inflow"))
        debit = parse_amount(_pick(r, "debit", "withdrawn", "amount_out", "withdrawal", "outflow"))
        direction = "in"
        amount = credit
        if (credit is None or credit == 0) and debit:
            direction, amount = "out", abs(debit)
        if amount is None:
            amount = parse_amount(_pick(r, "amount", "value", "total"))
            if amount is not None and amount < 0:
                direction, amount = "out", abs(amount)
            direction = _direction_from_word(_pick(r, "direction", "type", "dr_cr"), direction)
        counterparty = _text(_pick(r, "counterparty", "other_party", "name", "customer", "payer", "payee", "beneficiary"))
        msisdn = normalize_msisdn(_pick(r, "msisdn", "phone", "mobile", "phone_number"))
        np_ = NormalizedPayment(
            provider=self.provider, direction=direction, amount=amount,
            currency=_text(_pick(r, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(r, "fee", "charge", "commission")) or Decimal("0"),
            provider_txn_id=_text(_pick(r, "bank_txn_id", "transaction_id", "txn_id", "reference_no", "ref_no", "receipt_no")) or None,
            reference=_text(_pick(r, "reference", "ref", "narrative_reference", "invoice", "invoice_number")),
            narration=_text(_pick(r, "description", "narration", "details", "memo", "particulars")),
            paid_at=parse_datetime(_pick(r, "transaction_date", "date", "value_date", "posted_at", "paid_at", "completion_time")),
            status=_status_from_word(_pick(r, "status", "transaction_status")),
            raw=_json_safe(dict(row)),
        )
        if direction == "in":
            np_.payer_name, np_.payer_msisdn = counterparty, msisdn
        else:
            np_.payee_name, np_.payee_msisdn = counterparty, msisdn
        return np_

    def parse_notification(self, headers: Dict[str, Any], body: Any) -> Optional[NormalizedPayment]:
        """Generic JSON notification (flat aliases). Providers override."""
        data = decode_body(body)
        if not isinstance(data, (dict, list)):
            return None
        flat = _flatten(data)
        txn = _text(_pick(flat, "transaction_id", "transactionid", "txn_id", "txnid", "trans_id", "transid", "receipt", "reference_id", "id"))
        amount = parse_amount(_pick(flat, "amount", "total_amount", "totalamount", "trans_amount", "transamount", "value"))
        if not txn and amount is None:
            return None
        np_ = NormalizedPayment(
            provider=self.provider,
            direction=_direction_from_word(_pick(flat, "direction", "type", "transaction_type"), "in"),
            amount=amount,
            currency=_text(_pick(flat, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(flat, "fee", "charge")) or Decimal("0"),
            payer_name=_text(_pick(flat, "payer_name", "customer_name", "name", "first_name")),
            payer_msisdn=normalize_msisdn(_pick(flat, "msisdn", "phone", "phone_number", "payer_msisdn", "mobile")),
            provider_txn_id=txn or None,
            reference=_text(_pick(flat, "reference", "bill_ref", "account_reference", "order_id", "out_trade_no")),
            narration=_text(_pick(flat, "narration", "description", "remark", "subject")),
            paid_at=parse_datetime(_pick(flat, "paid_at", "transaction_time", "timestamp", "time", "date")),
            status=_status_from_word(_pick(flat, "status", "result", "result_code", "trade_status")),
            raw=_json_safe(data),
        )
        return np_

    # -- security ---------------------------------------------------
    def verify_signature(self, headers: Dict[str, Any], body: Any, secret: Optional[str]) -> bool:
        """
        Default scheme: hex HMAC-SHA256 of the raw body using ``secret``,
        supplied in one of SIGNATURE_HEADERS (optionally prefixed ``sha256=``).
        Providers with their own scheme (Telebirr RSA) override and document it.
        Returns False when no secret or no signature header is present.
        """
        if not secret:
            return False
        sig = _header_get(headers, *self.SIGNATURE_HEADERS)
        if not sig:
            return False
        sig = sig.strip()
        if "=" in sig and sig.lower().startswith("sha256="):
            sig = sig.split("=", 1)[1]
        raw = body if isinstance(body, bytes) else (
            json.dumps(body, separators=(",", ":"), sort_keys=True) if isinstance(body, (dict, list)) else str(body or "")
        ).encode("utf-8")
        expected = hmac.new(secret.encode("utf-8"), raw, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected.lower(), sig.lower())

    # -- outbound ---------------------------------------------------
    def initiate(self, amount: Any, msisdn: Any, reference: str = "") -> Dict[str, Any]:
        """
        Start a customer-initiated collection (STK push / H5 checkout).
        Returns a ``not_configured`` stub unless the provider's env
        credentials are present. Even then no network call is made here:
        confirm endpoint URLs with the provider onboarding docs
        (see PAYMENT_PROVIDERS.md) before enabling the HTTP step.
        """
        amt = parse_amount(amount)
        num = normalize_msisdn(msisdn)
        base = {"provider": self.provider, "amount": str(amt) if amt is not None else None,
                "msisdn": num, "reference": reference or ""}
        if not self.ENV_REQUIRED:
            return {**base, "status": "not_supported",
                    "message": f"{self.label} has no online initiation API"}
        missing = self.missing_env()
        if missing:
            return {**base, "status": "not_configured", "missing_env": missing}
        if amt is None or amt <= 0 or not num:
            return {**base, "status": "invalid_request",
                    "message": "amount must be > 0 and msisdn a valid Ethiopian mobile number"}
        return {**base, "status": "pending_integration",
                "message": "Credentials detected. Outbound API call is not enabled — "
                           "confirm endpoint URLs with provider onboarding docs (PAYMENT_PROVIDERS.md).",
                "request": self.build_initiate_request(amt, num, reference or "")}

    def build_initiate_request(self, amount: Decimal, msisdn: str, reference: str) -> Dict[str, Any]:
        return {"amount": str(amount), "msisdn": msisdn, "reference": reference}


# ── Telebirr ─────────────────────────────────────────────────────

class TelebirrAdapter(PaymentProviderAdapter):
    provider = "telebirr"
    label = "Telebirr"
    ENV_REQUIRED = ("TELEBIRR_APP_ID", "TELEBIRR_APP_KEY")
    ENV_OPTIONAL = ("TELEBIRR_SHORT_CODE", "TELEBIRR_MERCHANT_ID", "TELEBIRR_PUBLIC_KEY",
                    "TELEBIRR_NOTIFY_SECRET", "TELEBIRR_ENV")
    STATEMENT_COLUMNS = ("transaction_no", "transaction_time", "type", "amount", "fee",
                         "payer_name", "payer_msisdn", "payee_name", "payee_msisdn",
                         "reference", "status", "narration")
    STATEMENT_SAMPLE = {
        "transaction_no": "BJK7H2XYZ1", "transaction_time": "2026-09-01 10:15:32",
        "type": "Credit", "amount": 1150.00, "fee": 0, "payer_name": "Abebe Kebede",
        "payer_msisdn": "0911223344", "payee_name": "My Company PLC", "payee_msisdn": "",
        "reference": "INV-2026-042", "status": "Completed", "narration": "Invoice payment",
    }
    STATEMENT_NOTES = {
        "transaction_no": "Telebirr transaction / trade number (unique — used for de-duplication)",
        "transaction_time": "YYYY-MM-DD HH:MM:SS",
        "type": "Credit (money received) or Debit (money sent)",
        "amount": "Amount in ETB", "fee": "Provider fee in ETB (optional)",
        "payer_name": "Sender name", "payer_msisdn": "Sender phone (09.., +2519..)",
        "payee_name": "Receiver name", "payee_msisdn": "Receiver phone",
        "reference": "Your invoice / order / tender reference",
        "status": "Completed | Pending | Failed | Reversed",
        "narration": "Free text",
    }

    def parse_statement_row(self, row: Dict[str, Any]) -> NormalizedPayment:
        r = normalize_row(row)
        credit = parse_amount(_pick(r, "credit", "amount_in", "received"))
        debit = parse_amount(_pick(r, "debit", "amount_out", "sent"))
        amount = parse_amount(_pick(r, "amount", "total_amount", "trade_amount"))
        direction = _direction_from_word(_pick(r, "type", "transaction_type", "direction", "dr_cr"), "in")
        if amount is None:
            if credit:
                direction, amount = "in", credit
            elif debit:
                direction, amount = "out", abs(debit)
        elif amount < 0:
            direction, amount = "out", abs(amount)
        payer_msisdn = normalize_msisdn(_pick(r, "payer_msisdn", "payer_phone", "sender_msisdn", "sender_phone", "from_msisdn", "msisdn", "phone"))
        payee_msisdn = normalize_msisdn(_pick(r, "payee_msisdn", "payee_phone", "receiver_msisdn", "receiver_phone", "to_msisdn"))
        return NormalizedPayment(
            provider=self.provider, direction=direction, amount=amount,
            currency=_text(_pick(r, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(r, "fee", "service_fee", "charge")) or Decimal("0"),
            payer_name=_text(_pick(r, "payer_name", "payer", "sender_name", "sender", "from_name")),
            payer_msisdn=payer_msisdn,
            payee_name=_text(_pick(r, "payee_name", "payee", "receiver_name", "receiver", "to_name")),
            payee_msisdn=payee_msisdn,
            provider_txn_id=_text(_pick(r, "transaction_no", "transaction_number", "trade_no", "transaction_id", "txn_id", "receipt_no", "reference_no")) or None,
            reference=_text(_pick(r, "reference", "out_trade_no", "order_no", "invoice", "invoice_number", "remark")),
            narration=_text(_pick(r, "narration", "description", "details", "subject", "memo")),
            paid_at=parse_datetime(_pick(r, "transaction_time", "transaction_date", "date", "time", "paid_at", "trade_time")),
            status=_status_from_word(_pick(r, "status", "trade_status", "transaction_status")),
            raw=_json_safe(dict(row)),
        )

    def parse_notification(self, headers: Dict[str, Any], body: Any) -> Optional[NormalizedPayment]:
        """
        Telebirr SuperApp / H5 payment notification (server-to-server
        notify_url callback). Field names follow the public integration
        samples (tradeNo / transactionNo / outTradeNo / totalAmount /
        tradeStatus / msisdn / transactionTime); confirm against your
        onboarding package — aliases below tolerate the common variants.
        """
        data = decode_body(body)
        if not isinstance(data, dict):
            return None
        # Some gateways wrap the notification in {"data": {...}} or {"biz_content": {...}}
        inner = data.get("data") if isinstance(data.get("data"), dict) else None
        inner = inner or (data.get("biz_content") if isinstance(data.get("biz_content"), dict) else None)
        flat = _flatten(inner or data)
        txn = _text(_pick(flat, "trade_no", "tradeno", "transaction_no", "transactionno", "transaction_id",
                          "transactionid", "payment_order_id", "txn_id"))
        out_trade = _text(_pick(flat, "out_trade_no", "outtradeno", "merch_order_id", "merchant_order_id", "order_id"))
        amount = parse_amount(_pick(flat, "total_amount", "totalamount", "amount", "trade_amount", "trans_amount"))
        if not txn and amount is None:
            return None
        status_word = _pick(flat, "trade_status", "tradestatus", "status", "result", "code")
        status = _status_from_word(status_word, "completed")
        if _text(status_word).upper() in ("TRADE_SUCCESS", "SUCCESS", "COMPLETED", "PAID", "0"):
            status = "completed"
        return NormalizedPayment(
            provider=self.provider, direction="in", amount=amount,
            currency=_text(_pick(flat, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(flat, "fee", "service_fee")) or Decimal("0"),
            payer_name=_text(_pick(flat, "payer_name", "customer_name", "buyer_name", "name")),
            payer_msisdn=normalize_msisdn(_pick(flat, "msisdn", "payer_msisdn", "phone", "mobile", "payer_phone")),
            payee_name=_text(_pick(flat, "receive_name", "receivename", "merchant_name", "payee_name")),
            provider_txn_id=txn or None,
            reference=out_trade,
            narration=_text(_pick(flat, "subject", "remark", "description", "title")),
            paid_at=parse_datetime(_pick(flat, "transaction_time", "transactiontime", "trade_time", "timestamp", "pay_time", "gmt_payment")),
            status=status,
            raw=_json_safe(data),
        )

    def verify_signature(self, headers: Dict[str, Any], body: Any, secret: Optional[str]) -> bool:
        """
        Telebirr's official notify signature is an RSA signature over the
        sorted request parameters, verified with the Telebirr public key
        (TELEBIRR_PUBLIC_KEY) — that scheme must be confirmed with the
        onboarding docs and needs an RSA library (not a dependency of this
        app today). Until then we support the shared-secret HMAC scheme
        (TELEBIRR_NOTIFY_SECRET, header X-Signature) that a proxy / relay can
        add, and reject everything else.
        """
        return super().verify_signature(headers, body, secret)

    def build_initiate_request(self, amount: Decimal, msisdn: str, reference: str) -> Dict[str, Any]:
        return {
            "appId": os.environ.get("TELEBIRR_APP_ID", ""),
            "shortCode": os.environ.get("TELEBIRR_SHORT_CODE", ""),
            "outTradeNo": reference,
            "totalAmount": str(amount),
            "msisdn": msisdn,
            "subject": reference or "Payment",
            "timeoutExpress": "30",
        }


# ── CBE Birr ─────────────────────────────────────────────────────

class CBEBirrAdapter(PaymentProviderAdapter):
    provider = "cbebirr"
    label = "CBE Birr"
    ENV_REQUIRED = ("CBEBIRR_MERCHANT_ID", "CBEBIRR_KEY")
    ENV_OPTIONAL = ("CBEBIRR_SHORT_CODE", "CBEBIRR_CALLBACK_SECRET", "CBEBIRR_ENV")
    STATEMENT_COLUMNS = ("transaction_id", "transaction_date", "description", "credit", "debit",
                         "customer_name", "customer_phone", "reference", "status")
    STATEMENT_SAMPLE = {
        "transaction_id": "CBE250901A1B2C3", "transaction_date": "01/09/2026 14:22:10",
        "description": "Merchant payment", "credit": 2300.00, "debit": "",
        "customer_name": "Sara Tesfaye", "customer_phone": "+251922334455",
        "reference": "TENDER-2026-07", "status": "Success",
    }
    STATEMENT_NOTES = {
        "transaction_id": "CBE Birr transaction id (unique — used for de-duplication)",
        "transaction_date": "DD/MM/YYYY HH:MM:SS or YYYY-MM-DD",
        "description": "Narration from the statement",
        "credit": "Amount received (ETB) — leave blank for debits",
        "debit": "Amount paid out (ETB) — leave blank for credits",
        "customer_name": "Counterparty name", "customer_phone": "Counterparty phone",
        "reference": "Your invoice / tender reference", "status": "Success | Pending | Failed | Reversed",
    }

    def parse_statement_row(self, row: Dict[str, Any]) -> NormalizedPayment:
        r = normalize_row(row)
        credit = parse_amount(_pick(r, "credit", "credit_amount", "amount_in", "paid_in"))
        debit = parse_amount(_pick(r, "debit", "debit_amount", "amount_out", "withdrawn"))
        if credit and credit > 0:
            direction, amount = "in", credit
        elif debit and debit != 0:
            direction, amount = "out", abs(debit)
        else:
            amount = parse_amount(_pick(r, "amount", "transaction_amount"))
            direction = _direction_from_word(_pick(r, "type", "transaction_type", "dr_cr"), "in")
            if amount is not None and amount < 0:
                direction, amount = "out", abs(amount)
        name = _text(_pick(r, "customer_name", "customer", "counterparty", "name", "payer_name", "payee_name"))
        msisdn = normalize_msisdn(_pick(r, "customer_phone", "phone", "msisdn", "mobile", "customer_msisdn", "phone_number"))
        np_ = NormalizedPayment(
            provider=self.provider, direction=direction, amount=amount,
            currency=_text(_pick(r, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(r, "fee", "charge", "service_charge")) or Decimal("0"),
            provider_txn_id=_text(_pick(r, "transaction_id", "txn_id", "transaction_ref", "reference_no", "ft_reference", "receipt_no")) or None,
            reference=_text(_pick(r, "reference", "bill_reference", "invoice", "invoice_number", "remark")),
            narration=_text(_pick(r, "description", "narration", "details", "particulars")),
            paid_at=parse_datetime(_pick(r, "transaction_date", "date", "value_date", "time", "transaction_time")),
            status=_status_from_word(_pick(r, "status", "transaction_status")),
            raw=_json_safe(dict(row)),
        )
        if direction == "in":
            np_.payer_name, np_.payer_msisdn = name, msisdn
        else:
            np_.payee_name, np_.payee_msisdn = name, msisdn
        return np_

    def parse_notification(self, headers: Dict[str, Any], body: Any) -> Optional[NormalizedPayment]:
        data = decode_body(body)
        if not isinstance(data, dict):
            return None
        flat = _flatten(data)
        txn = _text(_pick(flat, "transaction_id", "transactionid", "txn_id", "trans_id", "ft_reference", "reference_id", "receipt_no"))
        amount = parse_amount(_pick(flat, "amount", "transaction_amount", "total_amount", "credit"))
        if not txn and amount is None:
            return None
        return NormalizedPayment(
            provider=self.provider,
            direction=_direction_from_word(_pick(flat, "direction", "type", "transaction_type"), "in"),
            amount=amount,
            currency=_text(_pick(flat, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(flat, "fee", "charge")) or Decimal("0"),
            payer_name=_text(_pick(flat, "customer_name", "payer_name", "name", "sender_name")),
            payer_msisdn=normalize_msisdn(_pick(flat, "msisdn", "customer_phone", "phone", "mobile", "payer_msisdn")),
            payee_name=_text(_pick(flat, "merchant_name", "payee_name")),
            provider_txn_id=txn or None,
            reference=_text(_pick(flat, "reference", "bill_reference", "bill_ref", "merchant_reference", "order_id", "invoice")),
            narration=_text(_pick(flat, "narration", "description", "remark")),
            paid_at=parse_datetime(_pick(flat, "transaction_date", "transaction_time", "timestamp", "date", "time")),
            status=_status_from_word(_pick(flat, "status", "result", "result_code", "transaction_status")),
            raw=_json_safe(data),
        )

    def build_initiate_request(self, amount: Decimal, msisdn: str, reference: str) -> Dict[str, Any]:
        return {
            "merchantId": os.environ.get("CBEBIRR_MERCHANT_ID", ""),
            "shortCode": os.environ.get("CBEBIRR_SHORT_CODE", ""),
            "amount": str(amount), "msisdn": msisdn, "reference": reference,
        }


# ── M-Pesa (Safaricom Ethiopia) ──────────────────────────────────

_MPESA_PARTY_RE = re.compile(r"^\s*(?P<msisdn>\+?\d[\d\s\-*]{6,}\d)\s*[-–:]?\s*(?P<name>.*)$")


class MPesaAdapter(PaymentProviderAdapter):
    provider = "mpesa"
    label = "M-Pesa Ethiopia"
    ENV_REQUIRED = ("MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_SHORTCODE", "MPESA_PASSKEY")
    ENV_OPTIONAL = ("MPESA_ENV", "MPESA_INITIATOR_NAME", "MPESA_SECURITY_CREDENTIAL", "MPESA_CALLBACK_SECRET")
    STATEMENT_COLUMNS = ("receipt_no", "completion_time", "details", "transaction_status",
                         "paid_in", "withdrawn", "balance", "other_party_info", "reference")
    STATEMENT_SAMPLE = {
        "receipt_no": "SI91K4M2ZQ", "completion_time": "2026-09-01 09:05:11",
        "details": "Customer payment to till", "transaction_status": "Completed",
        "paid_in": 500.00, "withdrawn": "", "balance": 12500.00,
        "other_party_info": "0712345678 - HANNA GIRMA", "reference": "INV-2026-043",
    }
    STATEMENT_NOTES = {
        "receipt_no": "M-Pesa receipt number (unique — used for de-duplication)",
        "completion_time": "YYYY-MM-DD HH:MM:SS",
        "details": "Statement narration",
        "transaction_status": "Completed | Pending | Failed | Reversed",
        "paid_in": "Amount received (ETB) — blank for outgoing",
        "withdrawn": "Amount sent (ETB) — blank for incoming",
        "balance": "Running balance (informational)",
        "other_party_info": "'07xxxxxxxx - NAME' as printed on the statement",
        "reference": "Account/bill reference (optional)",
    }

    @staticmethod
    def split_party(info: Any) -> Tuple[Optional[str], str]:
        """'0712345678 - HANNA GIRMA' → ('+251712345678', 'HANNA GIRMA')."""
        s = _text(info)
        if not s:
            return None, ""
        m = _MPESA_PARTY_RE.match(s)
        if m:
            return normalize_msisdn(m.group("msisdn")), m.group("name").strip(" -–:")
        # 'NAME - 0712345678'
        parts = re.split(r"\s[-–:]\s", s, maxsplit=1)
        if len(parts) == 2:
            a, b = parts
            if normalize_msisdn(b):
                return normalize_msisdn(b), a.strip()
        return normalize_msisdn(s), ("" if normalize_msisdn(s) else s)

    def parse_statement_row(self, row: Dict[str, Any]) -> NormalizedPayment:
        r = normalize_row(row)
        paid_in = parse_amount(_pick(r, "paid_in", "credit", "amount_in", "received"))
        withdrawn = parse_amount(_pick(r, "withdrawn", "debit", "amount_out", "sent"))
        if paid_in and paid_in > 0:
            direction, amount = "in", paid_in
        elif withdrawn and withdrawn != 0:
            direction, amount = "out", abs(withdrawn)
        else:
            amount = parse_amount(_pick(r, "amount", "trans_amount", "transaction_amount"))
            direction = _direction_from_word(_pick(r, "type", "transaction_type", "direction"), "in")
            if amount is not None and amount < 0:
                direction, amount = "out", abs(amount)
        msisdn, name = self.split_party(_pick(r, "other_party_info", "other_party", "counterparty", "party"))
        if not msisdn:
            msisdn = normalize_msisdn(_pick(r, "msisdn", "phone", "phone_number", "mobile"))
        if not name:
            name = _text(_pick(r, "name", "customer_name", "first_name"))
        np_ = NormalizedPayment(
            provider=self.provider, direction=direction, amount=amount,
            currency=_text(_pick(r, "currency", default="ETB")).upper() or "ETB",
            fee=parse_amount(_pick(r, "fee", "transaction_cost", "charge")) or Decimal("0"),
            provider_txn_id=_text(_pick(r, "receipt_no", "receipt_number", "mpesa_receipt_number", "trans_id", "transaction_id", "receipt")) or None,
            reference=_text(_pick(r, "reference", "account_reference", "bill_ref_number", "bill_reference", "invoice")),
            narration=_text(_pick(r, "details", "description", "narration", "transaction_type")),
            paid_at=parse_datetime(_pick(r, "completion_time", "initiation_time", "transaction_date", "trans_time", "date", "time")),
            status=_status_from_word(_pick(r, "transaction_status", "status")),
            raw=_json_safe(dict(row)),
        )
        if direction == "in":
            np_.payer_name, np_.payer_msisdn = name, msisdn
        else:
            np_.payee_name, np_.payee_msisdn = name, msisdn
        return np_

    def parse_notification(self, headers: Dict[str, Any], body: Any) -> Optional[NormalizedPayment]:
        """
        Daraja-style callbacks:
          * STK push result   {"Body": {"stkCallback": {..., "CallbackMetadata": {"Item": [...]}}}}
          * C2B confirmation  {"TransID", "TransAmount", "MSISDN", "BillRefNumber", "TransTime", ...}
          * B2C result        {"Result": {"ResultCode", "ResultParameters": {"ResultParameter": [...]}}}
        """
        data = decode_body(body)
        if not isinstance(data, dict):
            return None
        stk = (data.get("Body") or {}).get("stkCallback") if isinstance(data.get("Body"), dict) else None
        if isinstance(stk, dict):
            meta = _flatten((stk.get("CallbackMetadata") or {}).get("Item") or [])
            code = stk.get("ResultCode")
            status = "completed" if str(code) == "0" else "failed"
            receipt = _text(meta.get("mpesareceiptnumber") or meta.get("mpesa_receipt_number"))
            amount = parse_amount(meta.get("amount"))
            if status == "failed" and not receipt and amount is None:
                # Failed/cancelled STK — nothing to book, but keep a traceable pending/failed record
                receipt = _text(stk.get("CheckoutRequestID")) or None
            return NormalizedPayment(
                provider=self.provider, direction="in", amount=amount,
                payer_msisdn=normalize_msisdn(meta.get("phonenumber") or meta.get("phone_number")),
                provider_txn_id=receipt or None,
                reference=_text(stk.get("CheckoutRequestID") or stk.get("MerchantRequestID")),
                narration=_text(stk.get("ResultDesc")),
                paid_at=parse_datetime(meta.get("transactiondate") or meta.get("transaction_date")),
                status=status, raw=_json_safe(data),
            )
        result = data.get("Result") if isinstance(data.get("Result"), dict) else None
        if result:
            params = _flatten(((result.get("ResultParameters") or {}).get("ResultParameter")) or [])
            code = result.get("ResultCode")
            amount = parse_amount(params.get("transactionamount") or params.get("amount"))
            receipt = _text(params.get("transactionreceipt") or result.get("TransactionID"))
            payee_msisdn, payee_name = self.split_party(params.get("receiverpartypublicname"))
            return NormalizedPayment(
                provider=self.provider, direction="out", amount=amount,
                payee_name=payee_name, payee_msisdn=payee_msisdn,
                provider_txn_id=receipt or None,
                reference=_text(result.get("ConversationID") or result.get("OriginatorConversationID")),
                narration=_text(result.get("ResultDesc")),
                paid_at=parse_datetime(params.get("transactioncompleteddatetime")),
                status="completed" if str(code) == "0" else "failed", raw=_json_safe(data),
            )
        flat = _flatten(data)
        txn = _text(_pick(flat, "trans_id", "transid", "transaction_id", "mpesa_receipt_number", "receipt"))
        amount = parse_amount(_pick(flat, "trans_amount", "transamount", "amount"))
        if not txn and amount is None:
            return None
        first = _text(_pick(flat, "first_name", "firstname"))
        middle = _text(_pick(flat, "middle_name", "middlename"))
        last = _text(_pick(flat, "last_name", "lastname"))
        name = " ".join(p for p in (first, middle, last) if p) or _text(_pick(flat, "name", "customer_name"))
        return NormalizedPayment(
            provider=self.provider,
            direction=_direction_from_word(_pick(flat, "transaction_type", "transactiontype", "direction"), "in"),
            amount=amount,
            payer_name=name,
            payer_msisdn=normalize_msisdn(_pick(flat, "msisdn", "phone_number", "phonenumber", "phone")),
            provider_txn_id=txn or None,
            reference=_text(_pick(flat, "bill_ref_number", "billrefnumber", "account_reference", "reference")),
            narration=_text(_pick(flat, "transaction_type", "transactiontype", "description")),
            paid_at=parse_datetime(_pick(flat, "trans_time", "transtime", "transaction_date", "timestamp")),
            status=_status_from_word(_pick(flat, "status", "result_code", "resultcode"), "completed"),
            raw=_json_safe(data),
        )

    def build_initiate_request(self, amount: Decimal, msisdn: str, reference: str) -> Dict[str, Any]:
        """STK-push (Lipa na M-Pesa Online) request body shape. Password =
        base64(shortcode + passkey + timestamp) is computed by the caller
        that actually sends the request (see PAYMENT_PROVIDERS.md)."""
        shortcode = os.environ.get("MPESA_SHORTCODE", "")
        return {
            "BusinessShortCode": shortcode,
            "TransactionType": "CustomerPayBillOnline",
            "Amount": str(amount.quantize(Decimal("1")) if amount == amount.to_integral() else amount),
            "PartyA": msisdn.lstrip("+"),
            "PartyB": shortcode,
            "PhoneNumber": msisdn.lstrip("+"),
            "AccountReference": (reference or "Payment")[:12],
            "TransactionDesc": (reference or "Payment")[:13],
        }


# ── Bank transfer / cash ─────────────────────────────────────────

class BankTransferAdapter(PaymentProviderAdapter):
    provider = "bank"
    label = "Bank transfer"
    STATEMENT_COLUMNS = ("transaction_date", "bank_txn_id", "reference", "description",
                         "debit", "credit", "counterparty")
    STATEMENT_SAMPLE = {
        "transaction_date": "2026-09-01", "bank_txn_id": "FT26244ABCD1", "reference": "INV-2026-044",
        "description": "Transfer from Awash Bank", "debit": "", "credit": 25000.00,
        "counterparty": "Awash Bank / Customer PLC",
    }
    STATEMENT_NOTES = {
        "transaction_date": "YYYY-MM-DD or DD/MM/YYYY",
        "bank_txn_id": "Bank FT / reference number (unique — used for de-duplication)",
        "reference": "Your invoice / tender reference",
        "description": "Bank narration",
        "debit": "Amount paid out (ETB)", "credit": "Amount received (ETB)",
        "counterparty": "Other party name",
    }


class CashAdapter(BankTransferAdapter):
    provider = "cash"
    label = "Cash"
    STATEMENT_COLUMNS = ("transaction_date", "reference", "description", "debit", "credit", "counterparty")
    STATEMENT_SAMPLE = {
        "transaction_date": "2026-09-01", "reference": "RCPT-0091", "description": "Cash sale",
        "debit": "", "credit": 1200.00, "counterparty": "Walk-in customer",
    }


ADAPTERS: Dict[str, PaymentProviderAdapter] = {
    "telebirr": TelebirrAdapter(),
    "cbebirr": CBEBirrAdapter(),
    "mpesa": MPesaAdapter(),
    "bank": BankTransferAdapter(),
    "cash": CashAdapter(),
}
# Providers that can push notifications to /webhooks/inbound/<source>
NOTIFYING_PROVIDERS = ("telebirr", "cbebirr", "mpesa")


def get_adapter(provider: str) -> PaymentProviderAdapter:
    key = (provider or "").strip().lower().replace("-", "").replace("_", "").replace(" ", "")
    key = {"cbe": "cbebirr", "safaricom": "mpesa", "mpesaethiopia": "mpesa",
           "banktransfer": "bank", "telebir": "telebirr"}.get(key, key)
    if key not in ADAPTERS:
        raise KeyError(f"Unknown payment provider: {provider!r}")
    return ADAPTERS[key]


def provider_choices() -> List[Tuple[str, str]]:
    return [(p, PROVIDER_LABELS[p]) for p in PROVIDERS]


# ── Duplicate detection ──────────────────────────────────────────

def duplicate_key(provider: str, provider_txn_id: Any) -> Optional[Tuple[str, str]]:
    txn = _text(provider_txn_id)
    if not txn:
        return None
    return ((provider or "").strip().lower(), txn.upper())


def find_duplicates(items: Iterable[Any],
                    existing_keys: Optional[Iterable[Tuple[str, str]]] = None
                    ) -> Tuple[List[Any], List[Any]]:
    """
    Split ``items`` (NormalizedPayment or dicts with provider/provider_txn_id)
    into (unique, duplicates). A row is a duplicate when its
    (provider, txn id) key was already seen in ``existing_keys`` or earlier
    in the same batch. Rows without a txn id are never treated as duplicates.
    """
    seen = set(existing_keys or ())
    unique, dups = [], []
    for it in items:
        if isinstance(it, NormalizedPayment):
            key = it.duplicate_key()
        else:
            key = duplicate_key(it.get("provider"), it.get("provider_txn_id"))
        if key and key in seen:
            dups.append(it)
            continue
        if key:
            seen.add(key)
        unique.append(it)
    return unique, dups


# ── Auto-match scoring ───────────────────────────────────────────

AMOUNT_TOLERANCE = Decimal("0.01")     # ±1 %
DATE_WINDOW_DAYS = 7
AUTO_MATCH_THRESHOLD = 85              # score needed for unattended linking
AUTO_MATCH_MARGIN = 15                 # top candidate must beat runner-up by this


def _tokens(s: str) -> set:
    return {t for t in re.split(r"[^0-9a-z]+", (s or "").lower()) if len(t) >= 3}


def text_similarity(a: Any, b: Any) -> float:
    """0..1 — max of sequence ratio and token overlap; substring hits score high."""
    a, b = _text(a).lower(), _text(b).lower()
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    if a in b or b in a:
        return 0.9
    ratio = SequenceMatcher(None, a, b).ratio()
    ta, tb = _tokens(a), _tokens(b)
    overlap = len(ta & tb) / len(ta | tb) if ta and tb else 0.0
    return max(ratio, overlap)


def _as_date(v: Any) -> Optional[date]:
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    dt = parse_datetime(v)
    return dt.date() if dt else None


def score_match(payment: Dict[str, Any], candidate: Dict[str, Any]) -> Tuple[int, List[str]]:
    """
    Score how well an income/expense ``candidate`` explains a ``payment``.

    payment:   amount, paid_at, reference, narration, payer_name/payee_name, payer_msisdn
    candidate: amount, date, reference, description, counterparty

    Returns (score 0..100, reasons). Weights: amount 50 (exact 50, within
    ±1 % scaled down to 35), date 30 (same day 30 → 7 days 10; outside
    window 0), text 20 (reference / tender id / description / counterparty).
    """
    reasons: List[str] = []
    score = 0
    p_amt = parse_amount(payment.get("amount"))
    c_amt = parse_amount(candidate.get("amount"))
    if p_amt is not None and c_amt is not None and c_amt > 0:
        diff = abs(p_amt - c_amt)
        if diff == 0:
            score += 50; reasons.append("exact amount")
        elif diff <= c_amt * AMOUNT_TOLERANCE:
            pct = float(diff / c_amt) * 100
            score += int(round(35 + 15 * (1 - pct)))  # 1 % off → 35, ~0 % → 50
            reasons.append(f"amount within {pct:.2f}%")
        else:
            return 0, ["amount differs"]
    else:
        return 0, ["no amount"]

    p_date = _as_date(payment.get("paid_at"))
    c_date = _as_date(candidate.get("date"))
    if p_date and c_date:
        days = abs((p_date - c_date).days)
        if days == 0:
            score += 30; reasons.append("same day")
        elif days <= DATE_WINDOW_DAYS:
            pts = int(round(30 - (20 * days / DATE_WINDOW_DAYS)))
            score += pts; reasons.append(f"{days} day(s) apart")
        else:
            reasons.append(f"{days} days apart")
    else:
        reasons.append("no date")

    p_texts = [payment.get("reference"), payment.get("narration"),
               payment.get("payer_name") or payment.get("payee_name")]
    c_texts = [candidate.get("reference"), candidate.get("description"), candidate.get("counterparty")]
    best = 0.0
    for a in p_texts:
        for b in c_texts:
            best = max(best, text_similarity(a, b))
    if best >= 0.5:
        pts = int(round(20 * best))
        score += pts; reasons.append(f"text match {int(best * 100)}%")
    return min(score, 100), reasons


def rank_candidates(payment: Dict[str, Any], candidates: Iterable[Dict[str, Any]],
                    min_score: int = 40) -> List[Dict[str, Any]]:
    """Attach score/reasons to each candidate and sort best-first."""
    ranked = []
    for c in candidates:
        s, why = score_match(payment, c)
        if s >= min_score:
            ranked.append({**c, "score": s, "reasons": why})
    ranked.sort(key=lambda c: c["score"], reverse=True)
    return ranked


def pick_auto_match(ranked: List[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """The candidate safe to link without a human: high score and a clear margin."""
    if not ranked:
        return None
    top = ranked[0]
    if top["score"] < AUTO_MATCH_THRESHOLD:
        return None
    if len(ranked) > 1 and ranked[1]["score"] >= top["score"] - AUTO_MATCH_MARGIN:
        return None
    return top


def date_window(paid_at: Any, days: int = DATE_WINDOW_DAYS) -> Tuple[Optional[date], Optional[date]]:
    d = _as_date(paid_at)
    if not d:
        return None, None
    return d - timedelta(days=days), d + timedelta(days=days)


def amount_window(amount: Any, tolerance: Decimal = AMOUNT_TOLERANCE) -> Tuple[Optional[Decimal], Optional[Decimal]]:
    a = parse_amount(amount)
    if a is None:
        return None, None
    return (a * (1 - tolerance)).quantize(_TWO_PLACES), (a * (1 + tolerance)).quantize(_TWO_PLACES)
