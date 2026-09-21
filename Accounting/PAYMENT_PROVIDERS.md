# Mobile-Money Payment Providers (Telebirr, CBE Birr, M-Pesa Ethiopia)

The payments module (`/payments`) records money received and paid through
Ethiopian mobile-money providers plus bank transfers and cash, reconciles it
against VAT income/expense records, and accepts provider notifications
through the generic inbound webhook endpoint.

Files: `web/payment_providers.py` (adapters, pure Python),
`web/payments_data_store.py`, `web/payments_routes.py`, `web/payments_jobs.py`,
`web/templates/payments/*`, tests in `web/tests/test_payment_providers.py` and
`web/tests/test_payments_templates.py`.

> Endpoint URLs, request signing details and sandbox hosts change per
> onboarding package. Nothing in this document or the code hard-codes a
> provider URL as fact — every item marked **confirm with provider onboarding
> docs** must be checked against the material the provider gives you.

## Environment variables (credentials are never stored in the database)

| Provider | Required | Optional |
|---|---|---|
| Telebirr | `TELEBIRR_APP_ID`, `TELEBIRR_APP_KEY` | `TELEBIRR_SHORT_CODE`, `TELEBIRR_MERCHANT_ID`, `TELEBIRR_PUBLIC_KEY`, `TELEBIRR_NOTIFY_SECRET`, `TELEBIRR_ENV` (`sandbox`/`production`) |
| M-Pesa Ethiopia (Safaricom Daraja) | `MPESA_CONSUMER_KEY`, `MPESA_CONSUMER_SECRET`, `MPESA_SHORTCODE`, `MPESA_PASSKEY` | `MPESA_ENV` (`sandbox`/`production`), `MPESA_INITIATOR_NAME`, `MPESA_SECURITY_CREDENTIAL` (B2C only), `MPESA_CALLBACK_SECRET` |
| CBE Birr | `CBEBIRR_MERCHANT_ID`, `CBEBIRR_KEY` | `CBEBIRR_SHORT_CODE`, `CBEBIRR_CALLBACK_SECRET`, `CBEBIRR_ENV` |
| Bank transfer / cash | — | — |
| General | — | `PUBLIC_BASE_URL` (used to display the callback URL on the settings page) |

The settings page (`/payments/settings`) shows which of these are detected in
the running process (green = set) without revealing values. Non-secret
per-company configuration (short code, merchant name, the *name* of the env
var that holds a callback secret, "require signature", enabled flag) is stored
in `payment_provider_settings`.

`initiate(amount, msisdn, reference)` on every adapter returns
`{"status": "not_configured", "missing_env": [...]}` until the required
variables are set. With credentials present it returns
`{"status": "pending_integration", "request": {...}}` — the request body it
would send — but makes **no network call**; wire the HTTP step only after the
endpoint URLs below are confirmed.

## Callback / notification flow

Providers push notifications to the generic receiver
`POST /webhooks/inbound/{source}` with `source` = `telebirr`, `cbebirr` or
`mpesa` (built by the webhooks module, which logs every request into
`webhook_inbound_log`). The payments module never exposes its own public
endpoint. Instead:

1. Every 5 minutes (`payments_jobs.process_inbound_job`) — or on demand via the
   "Process inbound notifications" button on the dashboard/settings page —
   unprocessed `webhook_inbound_log` rows with those sources are read.
   If the table does not exist yet the step is a no-op.
2. The provider adapter parses the body (`parse_notification(headers, body)`)
   into a `NormalizedPayment`.
3. If the provider's settings have *Reject unsigned notifications* enabled, the
   signature is checked with `verify_signature(headers, body, secret)`, where
   `secret` is read from the env var named in *Callback secret env ref*.
4. A payment is created with `source='notification'`; duplicates (same
   provider + provider transaction id) are skipped.
5. The outcome per log row is stored in `payment_inbound_processed`
   (created / duplicate / rejected / invalid / error) and shown on the settings
   page.

Give each provider the URL displayed on the settings page, e.g.
`https://<your-host>/webhooks/inbound/telebirr`.

## Telebirr — SuperApp / H5 web payment (high level)

Flow as described in Ethio Telecom's merchant integration material:

1. **Fabric token** — obtain an application token using `TELEBIRR_APP_ID` /
   `TELEBIRR_APP_KEY` (the "fabric" gateway issues a short-lived token).
   *Endpoint host: confirm with provider onboarding docs.*
2. **Create order (pre-order)** — call the *create order* API with merchant
   short code, `outTradeNo` (your unique reference), `totalAmount`, `subject`,
   `notify_url` (= `/webhooks/inbound/telebirr`) and `timeoutExpress`; the
   request is signed with the merchant private key (RSA / SHA-256 with RSA,
   parameters sorted). The response contains a `prepay_id`.
   *Exact path, header names and signing algorithm: confirm with provider onboarding docs.*
3. **Checkout** — build the H5 / SuperApp payment URL (or raw request) from
   `prepay_id` + `appId` + short code + signature and redirect the customer
   (or hand it to the SuperApp `startPay` bridge).
4. **Asynchronous notify** — Telebirr POSTs the result to `notify_url`
   (`tradeNo` / `transactionNo`, `outTradeNo`, `totalAmount`, `tradeStatus`
   e.g. `TRADE_SUCCESS`, `msisdn`, `transactionTime`, plus a `sign`). Reply
   with the acknowledgement string the docs prescribe (commonly `success`).
   `TelebirrAdapter.parse_notification` reads these fields (tolerant to
   camelCase / snake_case and a `data` / `biz_content` wrapper).
5. **Query order** — optional reconciliation call by `outTradeNo` when a
   notification is missed. *Confirm with provider onboarding docs.*

Signature verification: the official scheme is RSA verification of the sorted
parameters with Telebirr's public key (`TELEBIRR_PUBLIC_KEY`). That needs an
RSA library that is **not** a dependency of this app today, so the adapter
currently supports a shared-secret HMAC-SHA256 header (`X-Signature`) that a
relay/proxy can add, using `TELEBIRR_NOTIFY_SECRET`. Implement RSA verification
in `TelebirrAdapter.verify_signature` once the exact string-to-sign is
confirmed.

## Safaricom Ethiopia — M-Pesa Daraja STK push (Lipa na M-Pesa Online), high level

1. **OAuth token** — `GET .../oauth/v1/generate?grant_type=client_credentials`
   with HTTP Basic auth `MPESA_CONSUMER_KEY:MPESA_CONSUMER_SECRET`; returns an
   `access_token` valid for about an hour.
   *Host (sandbox vs production, Ethiopian Daraja domain): confirm with provider onboarding docs.*
2. **STK push request** — `POST .../mpesa/stkpush/v1/processrequest` (path per
   Daraja docs — *confirm*), bearer token, JSON body:
   - `BusinessShortCode` = `MPESA_SHORTCODE`
   - `Password` = base64(`MPESA_SHORTCODE` + `MPESA_PASSKEY` + `Timestamp`)
   - `Timestamp` = `YYYYMMDDHHmmss`
   - `TransactionType` = `CustomerPayBillOnline` (or `CustomerBuyGoodsOnline` for till numbers)
   - `Amount`, `PartyA` (customer MSISDN as `2517XXXXXXXX`), `PartyB` (short code),
     `PhoneNumber`, `CallBackURL` (= `/webhooks/inbound/mpesa`),
     `AccountReference` (≤ 12 chars), `TransactionDesc` (≤ 13 chars)
   `MPesaAdapter.build_initiate_request` produces this body shape (without
   `Password`/`Timestamp`/`CallBackURL`, which the sending step adds).
3. **Customer PIN prompt** — the customer approves on the handset.
4. **Result callback** — Daraja POSTs
   `{"Body": {"stkCallback": {"MerchantRequestID", "CheckoutRequestID", "ResultCode", "ResultDesc", "CallbackMetadata": {"Item": [{"Name": "Amount"}, {"Name": "MpesaReceiptNumber"}, {"Name": "TransactionDate"}, {"Name": "PhoneNumber"}]}}}}`.
   `ResultCode` 0 = paid; anything else (e.g. 1032 cancelled) = failed.
   `MPesaAdapter.parse_notification` handles this, plus C2B
   validation/confirmation bodies (`TransID`, `TransAmount`, `MSISDN`,
   `BillRefNumber`, `TransTime`) and B2C `Result` bodies (Key/Value parameters
   should be relayed as Name/Value, or extend `_flatten`).
5. **STK query** — `.../mpesa/stkpushquery/v1/query` by `CheckoutRequestID` to
   recover a missed callback. *Confirm with provider onboarding docs.*
6. **C2B register URLs** — register validation/confirmation URLs once per
   short code so Pay-Bill payments made directly by customers also arrive at
   `/webhooks/inbound/mpesa`. *Confirm with provider onboarding docs.*

Daraja callbacks are not signed; restrict by source IP allow-list at the edge
if the provider publishes one, and/or use a relay that adds
`X-Signature: HMAC-SHA256(body, MPESA_CALLBACK_SECRET)`.

## CBE Birr

CBE Birr merchant integration is arranged directly with the Commercial Bank
of Ethiopia; field names vary by integration package. The adapter accepts
statement exports (`transaction_id`, `transaction_date`, `description`,
`credit`, `debit`, `customer_name`, `customer_phone`, `reference`, `status`)
and flat JSON / form-encoded notifications (`transactionId`, `amount`,
`msisdn`, `reference`, `status`, `timestamp`). Signature: shared-secret
HMAC-SHA256 header using `CBEBIRR_CALLBACK_SECRET`.
*Endpoints and signing: confirm with provider onboarding docs.*

## Statement import

`/payments/import` accepts `.xlsx` / `.xls` / `.csv` per provider. Column
headers are matched case-insensitively with common aliases (e.g. "Receipt
No.", "Transaction ID", "Paid In" / "Withdrawn", "Other Party Info"). A
downloadable template per provider lists the canonical columns. Rows whose
provider transaction id already exists (same company + provider) are counted
as duplicates and skipped; every import is logged in
`payment_statement_imports`.

## Reconciliation

`/payments/reconcile` lists unmatched completed payments with candidate
income (`vat_income`, direction *in*) or expense (`vat_expenses`, direction
*out*) records, scored 0–100:

- amount equal → 50, within ±1 % → 35–50, otherwise excluded
- date same day → 30, within 7 days → 10–30
- reference / tender id / invoice number / description / counterparty text
  similarity ≥ 50 % → up to 20

The nightly job (02:30) links only candidates scoring ≥ 85 with no runner-up
within 15 points; everything else waits for a human. Linking sets
`matched_type` / `matched_id` and marks the payment reconciled.

Python API for other modules:

```python
from payments_data_store import record_payment, find_matches, link_payment
p = record_payment("default", provider="telebirr", direction="in", amount="1150",
                   payer_msisdn="0911223344", provider_txn_id="BJK7H2XYZ1", reference="INV-42")
find_matches("default", p)                         # scored candidates
link_payment(p["id"], "income", income_id, by="system", company_id="default")
```

## MSISDN handling

`normalize_msisdn` converts `+2519xxxxxxxx`, `2519xxxxxxxx`, `002519…`,
`09xxxxxxxx`, `9xxxxxxxx` and Safaricom `07xxxxxxxx` (with spaces / dashes /
Excel numeric cells) to `+251XXXXXXXXX`; anything else becomes `None`.
