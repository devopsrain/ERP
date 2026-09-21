"""
Pure unit tests for payment_providers — no database, no network.

Covers: MSISDN normalisation, statement-row parsing per provider,
notification parsing (Telebirr H5, Daraja STK / C2B / B2C), signature
verification, initiate() stubs, duplicate detection and auto-match scoring.
"""
import hashlib
import hmac
import json
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

from payment_providers import (  # noqa: E402
    ADAPTERS, AUTO_MATCH_THRESHOLD, BankTransferAdapter, CBEBirrAdapter, MPesaAdapter,
    NormalizedPayment, TelebirrAdapter, amount_window, date_window, find_duplicates,
    get_adapter, is_safaricom_msisdn, msisdn_local, normalize_msisdn, parse_amount,
    parse_datetime, pick_auto_match, rank_candidates, score_match, text_similarity,
)


# ── MSISDN ────────────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("+251912345678", "+251912345678"),
    ("251912345678", "+251912345678"),
    ("00251912345678", "+251912345678"),
    ("0912345678", "+251912345678"),
    ("912345678", "+251912345678"),
    ("0712345678", "+251712345678"),          # Safaricom Ethiopia
    ("+251 71 234 5678", "+251712345678"),
    ("(0)91-234-5678", "+251912345678"),
    (912345678, "+251912345678"),             # Excel numeric cell
    (912345678.0, "+251912345678"),           # Excel float cell
    ("0812345678", None),                     # not an Ethiopian mobile range
    ("12345", None),
    ("", None),
    (None, None),
    ("+254712345678", None),                  # Kenyan number is not Ethiopian
])
def test_normalize_msisdn(raw, expected):
    assert normalize_msisdn(raw) == expected


def test_msisdn_helpers():
    assert msisdn_local("+251912345678") == "0912345678"
    assert msisdn_local(None) == ""
    assert is_safaricom_msisdn("0712345678") is True
    assert is_safaricom_msisdn("0912345678") is False


# ── Value parsing ─────────────────────────────────────────────────

@pytest.mark.parametrize("raw,expected", [
    ("1,234.50", Decimal("1234.50")), ("ETB 1,234.5", Decimal("1234.50")),
    ("(500)", Decimal("-500.00")), (1234.5, Decimal("1234.50")), (100, Decimal("100.00")),
    ("", None), (None, None), ("abc", None), (float("nan"), None),
])
def test_parse_amount(raw, expected):
    assert parse_amount(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("2026-09-01 10:15:32", datetime(2026, 9, 1, 10, 15, 32)),
    ("2026-09-01T10:15:32Z", datetime(2026, 9, 1, 10, 15, 32)),
    ("01/09/2026 14:22:10", datetime(2026, 9, 1, 14, 22, 10)),
    ("20260901101532", datetime(2026, 9, 1, 10, 15, 32)),        # Daraja TransTime
    (20260901101532, datetime(2026, 9, 1, 10, 15, 32)),
    ("2026-09-01", datetime(2026, 9, 1)),
    ("2026-09-01T10:15", datetime(2026, 9, 1, 10, 15)),           # <input type=datetime-local>
    (date(2026, 9, 1), datetime(2026, 9, 1)),
    ("", None), (None, None), ("not a date", None),
])
def test_parse_datetime(raw, expected):
    assert parse_datetime(raw) == expected


# ── Statement rows ────────────────────────────────────────────────

def test_telebirr_statement_row():
    row = {"Transaction No": "BJK7H2XYZ1", "Transaction Time": "2026-09-01 10:15:32", "Type": "Credit",
           "Amount": "1,150.00", "Fee": "0", "Payer Name": "Abebe Kebede", "Payer MSISDN": "0911223344",
           "Reference": "INV-2026-042", "Status": "Completed", "Narration": "Invoice payment"}
    p = TelebirrAdapter().parse_statement_row(row)
    assert p.provider == "telebirr" and p.direction == "in"
    assert p.amount == Decimal("1150.00") and p.fee == Decimal("0")
    assert p.payer_msisdn == "+251911223344" and p.payer_name == "Abebe Kebede"
    assert p.provider_txn_id == "BJK7H2XYZ1" and p.reference == "INV-2026-042"
    assert p.paid_at == datetime(2026, 9, 1, 10, 15, 32) and p.status == "completed"
    assert p.is_valid() and p.duplicate_key() == ("telebirr", "BJK7H2XYZ1")
    assert p.raw["Transaction No"] == "BJK7H2XYZ1"


def test_telebirr_statement_debit_row():
    p = TelebirrAdapter().parse_statement_row({"transaction_no": "X1", "type": "Debit", "amount": 200,
                                               "payee_name": "Supplier", "payee_msisdn": "+251922334455"})
    assert p.direction == "out" and p.payee_msisdn == "+251922334455" and p.amount == Decimal("200.00")


def test_mpesa_statement_row():
    row = {"Receipt No.": "SI91K4M2ZQ", "Completion Time": "2026-09-01 09:05:11", "Details": "Customer payment",
           "Transaction Status": "Completed", "Paid In": "500.00", "Withdrawn": "", "Balance": "12,500.00",
           "Other Party Info": "0712345678 - HANNA GIRMA"}
    p = MPesaAdapter().parse_statement_row(row)
    assert p.provider == "mpesa" and p.direction == "in" and p.amount == Decimal("500.00")
    assert p.provider_txn_id == "SI91K4M2ZQ"
    assert p.payer_msisdn == "+251712345678" and p.payer_name == "HANNA GIRMA"
    assert p.status == "completed"


def test_mpesa_statement_withdrawal_row():
    p = MPesaAdapter().parse_statement_row({"Receipt No.": "SI91K4M2ZR", "Completion Time": "2026-09-02 09:05:11",
                                            "Paid In": "", "Withdrawn": "-750.00",
                                            "Other Party Info": "SUPPLIER PLC - 0733445566"})
    assert p.direction == "out" and p.amount == Decimal("750.00")
    assert p.payee_msisdn == "+251733445566" and p.payee_name == "SUPPLIER PLC"


def test_cbebirr_statement_row():
    row = {"Transaction ID": "CBE250901A1B2C3", "Transaction Date": "01/09/2026 14:22:10",
           "Description": "Merchant payment", "Credit": 2300, "Debit": None,
           "Customer Name": "Sara Tesfaye", "Customer Phone": "+251922334455",
           "Reference": "TENDER-2026-07", "Status": "Success"}
    p = CBEBirrAdapter().parse_statement_row(row)
    assert p.provider == "cbebirr" and p.direction == "in" and p.amount == Decimal("2300.00")
    assert p.payer_msisdn == "+251922334455" and p.payer_name == "Sara Tesfaye"
    assert p.paid_at == datetime(2026, 9, 1, 14, 22, 10) and p.reference == "TENDER-2026-07"


def test_cbebirr_statement_debit_row():
    p = CBEBirrAdapter().parse_statement_row({"transaction_id": "D1", "transaction_date": "2026-09-03",
                                              "credit": "", "debit": "1,000", "customer_name": "Vendor"})
    assert p.direction == "out" and p.amount == Decimal("1000.00") and p.payee_name == "Vendor"


def test_bank_statement_row():
    p = BankTransferAdapter().parse_statement_row({"transaction_date": "2026-09-01", "bank_txn_id": "FT26244ABCD1",
                                                   "reference": "INV-2026-044", "description": "Transfer",
                                                   "debit": "", "credit": "25,000.00", "counterparty": "Customer PLC"})
    assert p.provider == "bank" and p.direction == "in" and p.amount == Decimal("25000.00")
    assert p.provider_txn_id == "FT26244ABCD1" and p.payer_name == "Customer PLC"


def test_blank_row_is_invalid():
    p = TelebirrAdapter().parse_statement_row({"Transaction No": "", "Amount": ""})
    assert not p.is_valid() and p.duplicate_key() is None


# ── Notifications ─────────────────────────────────────────────────

def test_telebirr_h5_notification():
    body = json.dumps({"appId": "app", "outTradeNo": "ORDER-77", "tradeNo": "TB20260901X",
                       "transactionNo": "TB20260901X", "totalAmount": "345.00", "tradeStatus": "TRADE_SUCCESS",
                       "msisdn": "251911223344", "transactionTime": "2026-09-01 12:00:00",
                       "subject": "Order 77", "receiveName": "My Company", "sign": "..."})
    p = TelebirrAdapter().parse_notification({"content-type": "application/json"}, body.encode())
    assert p is not None and p.provider == "telebirr" and p.direction == "in"
    assert p.amount == Decimal("345.00") and p.provider_txn_id == "TB20260901X"
    assert p.reference == "ORDER-77" and p.payer_msisdn == "+251911223344"
    assert p.status == "completed" and p.paid_at == datetime(2026, 9, 1, 12, 0, 0)
    assert p.raw["outTradeNo"] == "ORDER-77"


def test_mpesa_stk_callback():
    body = {"Body": {"stkCallback": {"MerchantRequestID": "m-1", "CheckoutRequestID": "ws_CO_1",
                                     "ResultCode": 0, "ResultDesc": "The service request is processed successfully.",
                                     "CallbackMetadata": {"Item": [
                                         {"Name": "Amount", "Value": 250.0},
                                         {"Name": "MpesaReceiptNumber", "Value": "SI91ABCDEF"},
                                         {"Name": "TransactionDate", "Value": 20260901101532},
                                         {"Name": "PhoneNumber", "Value": 251712345678}]}}}}
    p = MPesaAdapter().parse_notification({}, body)
    assert p.direction == "in" and p.amount == Decimal("250.00")
    assert p.provider_txn_id == "SI91ABCDEF" and p.payer_msisdn == "+251712345678"
    assert p.paid_at == datetime(2026, 9, 1, 10, 15, 32) and p.status == "completed"
    assert p.reference == "ws_CO_1"


def test_mpesa_stk_callback_failed():
    body = {"Body": {"stkCallback": {"MerchantRequestID": "m-2", "CheckoutRequestID": "ws_CO_2",
                                     "ResultCode": 1032, "ResultDesc": "Request cancelled by user"}}}
    p = MPesaAdapter().parse_notification({}, body)
    assert p.status == "failed" and p.amount is None and not p.is_valid()


def test_mpesa_c2b_confirmation():
    body = {"TransactionType": "Pay Bill", "TransID": "SI92QWERTY", "TransTime": "20260902091500",
            "TransAmount": "1200.00", "BusinessShortCode": "600000", "BillRefNumber": "INV-9",
            "MSISDN": "251712345678", "FirstName": "HANNA", "MiddleName": "", "LastName": "GIRMA"}
    p = MPesaAdapter().parse_notification({}, json.dumps(body))
    assert p.provider_txn_id == "SI92QWERTY" and p.amount == Decimal("1200.00")
    assert p.payer_name == "HANNA GIRMA" and p.payer_msisdn == "+251712345678"
    assert p.reference == "INV-9" and p.paid_at == datetime(2026, 9, 2, 9, 15, 0)


def test_mpesa_b2c_result():
    body = {"Result": {"ResultType": 0, "ResultCode": 0, "ResultDesc": "ok", "ConversationID": "AG_1",
                       "TransactionID": "SI93B2CXYZ",
                       "ResultParameters": {"ResultParameter": [
                           {"Key": "TransactionAmount", "Value": 900},
                           {"Key": "TransactionReceipt", "Value": "SI93B2CXYZ"},
                           {"Key": "ReceiverPartyPublicName", "Value": "0722334455 - KEBEDE ALEMU"},
                           {"Key": "TransactionCompletedDateTime", "Value": "03.09.2026 10:00:00"}]}}}
    p = MPesaAdapter().parse_notification({}, body)  # B2C uses Key/Value pairs (STK uses Name/Value)
    assert p.direction == "out" and p.amount == Decimal("900.00") and p.provider_txn_id == "SI93B2CXYZ"
    assert p.payee_msisdn == "+251722334455" and p.payee_name == "KEBEDE ALEMU"


def test_cbebirr_notification_and_form_encoded():
    p = CBEBirrAdapter().parse_notification({}, "transactionId=CBE1&amount=99.5&msisdn=0911000000&status=SUCCESS&reference=R1")
    assert p.provider_txn_id == "CBE1" and p.amount == Decimal("99.50")
    assert p.payer_msisdn == "+251911000000" and p.status == "completed" and p.reference == "R1"


def test_notification_garbage_returns_none():
    assert TelebirrAdapter().parse_notification({}, b"") is None
    assert MPesaAdapter().parse_notification({}, "hello world") is None
    assert CBEBirrAdapter().parse_notification({}, "[1,2,3]") is None


# ── Signatures + initiate ─────────────────────────────────────────

def test_hmac_signature_verification():
    body = b'{"transactionId":"X","amount":"10"}'
    secret = "s3cret"
    sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    a = CBEBirrAdapter()
    assert a.verify_signature({"X-Signature": sig}, body, secret) is True
    assert a.verify_signature({"X-Hub-Signature-256": "sha256=" + sig}, body, secret) is True
    assert a.verify_signature({"X-Signature": "deadbeef"}, body, secret) is False
    assert a.verify_signature({}, body, secret) is False
    assert a.verify_signature({"X-Signature": sig}, body, None) is False


def test_initiate_not_configured(monkeypatch):
    for k in ("TELEBIRR_APP_ID", "TELEBIRR_APP_KEY", "MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET",
              "MPESA_SHORTCODE", "MPESA_PASSKEY", "CBEBIRR_MERCHANT_ID", "CBEBIRR_KEY"):
        monkeypatch.delenv(k, raising=False)
    for name in ("telebirr", "mpesa", "cbebirr"):
        r = get_adapter(name).initiate(100, "0911223344", "REF")
        assert r["status"] == "not_configured" and r["missing_env"]
        assert not get_adapter(name).is_configured()
    assert get_adapter("bank").initiate(100, "0911223344", "REF")["status"] == "not_supported"


def test_initiate_with_credentials_builds_request(monkeypatch):
    monkeypatch.setenv("MPESA_CONSUMER_KEY", "k"); monkeypatch.setenv("MPESA_CONSUMER_SECRET", "s")
    monkeypatch.setenv("MPESA_SHORTCODE", "600000"); monkeypatch.setenv("MPESA_PASSKEY", "p")
    a = MPesaAdapter()
    assert a.is_configured() and a.env_status()["MPESA_SHORTCODE"] is True
    r = a.initiate("250", "0712345678", "INV-1")
    assert r["status"] == "pending_integration"
    req = r["request"]
    assert req["BusinessShortCode"] == "600000" and req["PartyA"] == "251712345678"
    assert req["Amount"] == "250" and req["TransactionType"] == "CustomerPayBillOnline"
    assert a.initiate("0", "0712345678")["status"] == "invalid_request"
    assert a.initiate("10", "12345")["status"] == "invalid_request"


def test_get_adapter_aliases():
    assert get_adapter("CBE-Birr") is ADAPTERS["cbebirr"]
    assert get_adapter("Safaricom") is ADAPTERS["mpesa"]
    assert get_adapter("bank_transfer") is ADAPTERS["bank"]
    with pytest.raises(KeyError):
        get_adapter("paypal")


# ── Duplicates ────────────────────────────────────────────────────

def test_find_duplicates_by_provider_and_txn_id():
    a = TelebirrAdapter().parse_statement_row({"transaction_no": "T1", "type": "Credit", "amount": 10})
    b = TelebirrAdapter().parse_statement_row({"transaction_no": "t1", "type": "Credit", "amount": 10})  # case-insensitive
    c = MPesaAdapter().parse_statement_row({"receipt_no": "T1", "paid_in": 10})  # same id, other provider → distinct
    d = TelebirrAdapter().parse_statement_row({"type": "Credit", "amount": 10})  # no txn id → never a duplicate
    e = TelebirrAdapter().parse_statement_row({"type": "Credit", "amount": 10})
    unique, dups = find_duplicates([a, b, c, d, e])
    assert unique == [a, c, d, e] and dups == [b]
    unique2, dups2 = find_duplicates([a, c], existing_keys={("telebirr", "T1")})
    assert unique2 == [c] and dups2 == [a]
    # dict form
    u3, d3 = find_duplicates([{"provider": "mpesa", "provider_txn_id": "R1"}, {"provider": "mpesa", "provider_txn_id": "R1 "}])
    assert len(u3) == 1 and len(d3) == 1


# ── Auto-match scoring ────────────────────────────────────────────

def _pay(**kw):
    base = {"amount": Decimal("1150.00"), "paid_at": datetime(2026, 9, 1, 10, 0), "reference": "INV-2026-042",
            "narration": "Invoice payment", "payer_name": "Abebe Kebede"}
    base.update(kw)
    return base


def _cand(**kw):
    base = {"type": "income", "id": "i1", "amount": 1150.0, "date": date(2026, 9, 1),
            "reference": "INV-2026-042", "description": "Consulting invoice", "counterparty": "Abebe Kebede"}
    base.update(kw)
    return base


def test_score_perfect_match():
    s, why = score_match(_pay(), _cand())
    assert s == 100 and "exact amount" in why and "same day" in why


def test_score_amount_tolerance():
    s_in, _ = score_match(_pay(amount=Decimal("1160.00")), _cand())      # 0.87 % off → within ±1 %
    s_out, why = score_match(_pay(amount=Decimal("1200.00")), _cand())   # 4.3 % off → rejected
    assert 80 <= s_in < 100 and s_out == 0 and why == ["amount differs"]


def test_score_date_window():
    near, _ = score_match(_pay(paid_at=datetime(2026, 9, 5)), _cand())
    far, why = score_match(_pay(paid_at=datetime(2026, 9, 20)), _cand())
    assert near > far and "19 days apart" in why
    assert far == 70  # amount 50 + text 20, no date points


def test_score_text_similarity_uses_tender_id_and_reference():
    assert text_similarity("INV-2026-042", "INV-2026-042") == 1.0
    assert text_similarity("BID-2026-014", "Payment for BID-2026-014 milestone") >= 0.9
    assert text_similarity("", "x") == 0.0
    s_with, _ = score_match(_pay(reference="BID-2026-014", narration=""), _cand(reference="BID-2026-014", counterparty="", description=""))
    s_without, _ = score_match(_pay(reference="", narration="", payer_name=""), _cand(reference="", counterparty="", description=""))
    assert s_with == 100 and s_without == 80


def test_rank_and_pick_auto_match():
    cands = [_cand(id="a"), _cand(id="b", date=date(2026, 9, 6), reference="", counterparty="", description="Other")]
    ranked = rank_candidates(_pay(), cands)
    assert [c["id"] for c in ranked] == ["a", "b"] and ranked[0]["score"] >= AUTO_MATCH_THRESHOLD
    assert pick_auto_match(ranked)["id"] == "a"
    # two equally good candidates → ambiguous → no auto link
    tie = rank_candidates(_pay(), [_cand(id="a"), _cand(id="b")])
    assert pick_auto_match(tie) is None
    assert pick_auto_match([]) is None
    # a lone mediocre candidate is not auto-linked
    weak = rank_candidates(_pay(paid_at=datetime(2026, 9, 20), reference="", narration="", payer_name=""),
                           [_cand(reference="", counterparty="", description="")])
    assert weak and pick_auto_match(weak) is None


def test_windows():
    lo, hi = amount_window("1000")
    assert (lo, hi) == (Decimal("990.00"), Decimal("1010.00"))
    d_lo, d_hi = date_window(datetime(2026, 9, 8))
    assert (d_lo, d_hi) == (date(2026, 9, 1), date(2026, 9, 15))
    assert amount_window("") == (None, None) and date_window(None) == (None, None)


def test_normalized_to_record():
    p = NormalizedPayment(provider="telebirr", amount=Decimal("5"), provider_txn_id=" X9 ")
    rec = p.to_record()
    assert rec["provider_txn_id"] == "X9" and rec["fee"] == Decimal("0") and rec["currency"] == "ETB"
