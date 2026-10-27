# Bank Reconciliation Module (`/bank`)

Reconciles bank statements (CBE, Awash, Dashen, Abyssinia, … any Excel/CSV export)
against the general ledger's bank/cash accounts and against mobile-money or
transfer payments recorded in the Payments module. Built entirely in new files;
the journal and payments tables are read but never altered.

## Files

| File | Purpose |
|------|---------|
| `web/bank_recon_logic.py` | Pure logic: Decimal/date parsing, header auto-detection, row normalisation, dedupe hashing, candidate scoring, one-to-one auto-match, reconciliation arithmetic, ageing buckets, adjustment → journal lines |
| `web/bank_recon_data_store.py` | PostgreSQL store (`bank_store`), `ensure_schema()`, ledger/payments read access, public API `bank_balances()` / `unreconciled_count()` |
| `web/bank_recon_routes.py` | FastAPI router, prefix `/bank`, route names `bank_*` |
| `web/templates/bank/*.html` | `_sidebar`, `_macros`, dashboard, accounts, account_form, statements, statement_import, statement_detail, workbench, recon_statement, report_ageing, report_charges, report_cash_position |
| `web/i18n_catalogue_bank.py` | Amharic catalogue (`AM`) — every template string and Python-side label |
| `web/tests/test_bank_recon_logic.py` | 57 pure-logic tests incl. i18n coverage |
| `web/tests/test_bank_recon_templates.py` | 37 render tests (empty + populated), route-order, manager guards, file reader, PDF/XLSX helpers |

Wiring (already present centrally): `bank_recon_routes.router` is included by `app.py`;
`bank_recon_data_store.ensure_schema()` runs at startup.

## Tables (all `company_id`-scoped, created by `ensure_schema()`)

- **bank_accounts** — name, bank_name (free text, datalist of Ethiopian banks), account_number, currency (ETB), `gl_account_code` → chart-of-accounts asset account, opening_balance, opening_date, is_active, notes.
- **bank_statements** — bank_account_id, period_start/end, opening/closing_balance, filename, imported_by/at, status `imported|in_progress|reconciled`, line_count.
- **bank_statement_lines** — txn_date, value_date, description, reference, debit (money out), credit (money in), balance, external_id (bank txn id), row_hash (sha1 of date|debit|credit|narration), status `unmatched|matched|excluded`, matched_kind `journal|payment|adjustment`, matched_id, matched_at/by. `UNIQUE(bank_account_id, external_id) WHERE external_id IS NOT NULL`.
- **bank_reconciliations** — one per statement: as_of, statement_balance, ledger_balance, outstanding_deposits, outstanding_payments, unexplained_difference (= adjusted bank − ledger), status `open|balanced|closed`, closed_by/at, notes.
- **bank_adjustments** — reconciliation_id, statement_line_id, kind `bank_charge|interest|fx|correction|other`, amount (signed: + raises the bank balance), gl_account_code (contra), description, adj_date, journal_entry_id, created_by.
- **bank_reconciled_ledger_lines** — (bank_account_id, entry_id, line_id, statement_line_id, reconciled_at/by), `UNIQUE(line_id)`. This is how journal lines are marked reconciled without touching `journal_entry_lines`.

## Ledger integration

- Ledger lines = `journal_entry_lines` ⨝ `journal_entries` filtered by `account_code = bank_accounts.gl_account_code`, active, status ≠ void, `entry_date ≤ period_end` (ISO text comparison — works for the TEXT column in `init_db.sql` and for a DATE column), minus rows present in `bank_reconciled_ledger_lines`.
- Ledger balance = `opening_balance` + Σ(debit − credit) on the GL account for `entry_date > opening_date` and `≤ as_of`.
- Direction: a statement **credit** (money in) matches a ledger **debit** on the bank account, and vice versa.
- Adjustments post a balanced entry through `journal_entry_data_store.journal_store.save_journal_entry()` when a contra GL account is chosen and the GL tables have the expected columns (`information_schema` guard). The bank-side line of that entry is immediately recorded in `bank_reconciled_ledger_lines` so it does not appear as outstanding. If posting fails or no contra account is given, the adjustment is recorded alone and the UI flashes a warning.
- Payments: `payments` rows with `reconciled = FALSE` and `status = 'completed'` are offered as match targets (lazy, guarded by `payments_available()`); matching/unmatching flags them via `payments_store.set_reconciled()`.

## Reconciliation arithmetic (`bank_recon_logic.reconcile`)

```
adjusted_bank   = statement_balance + deposits_in_transit − outstanding_payments
difference      = adjusted_bank − ledger_balance                      (what must still be explained)
adjusted_ledger = ledger_balance + unmatched_stmt_credits − unmatched_stmt_debits
unexplained     = adjusted_bank − adjusted_ledger                     (what remains after booking bank-only items)
```
`balanced` is set automatically when `difference == 0`; `closed` needs a manager and, if the difference
is not zero, a closing note. Closing sets the statement to `reconciled`; reopening reverts it.

## Matching (`bank_recon_logic.score`)

Amount + direction must be identical (else 0). Then: same day 50 / within ±3 days 40 / within 7 days 20 / later 5;
shared reference token +30 (reference found inside the other side's narration +25); narration similarity up to +20.
`auto_match` is greedy one-to-one, accepts pairs ≥ 60 and skips a statement line whose best candidate does not
beat the runner-up by ≥ 10 (two identical salary payments on one day are left for a human). It runs journal
candidates first, then payments for whatever is still unmatched.

## Statement import

- Reader (`bank_recon_routes._read_rows`): `.xlsx/.xls` via openpyxl, `.csv` via csv; the header row is located
  within the first 15 rows (bank exports have a title block), so CBE/Awash files import as-is.
- Header detection (`HEADER_ALIASES`): Date / Txn Date / Transaction Date / Posting Date; Value Date; Narration /
  Particulars / Details / Description; Reference / Cheque No / Ref No; Transaction ID / FT No / Trace No (→ `external_id`);
  Debit / Withdrawal / Dr; Credit / Deposit / Cr; Balance / Running Balance; a single signed Amount column.
- Dedupe: by `external_id` when present, otherwise by row hash, against all lines already imported for the account and within the file.
- Opening/closing balances: taken from the form, else closing from the last Balance cell and opening implied (closing − credits + debits).
- Template: `GET /bank/statements/template` (Date, Description, Reference, Debit, Credit, Balance + notes sheet).

## Routes

| Method | Path | Name | Guard |
|--------|------|------|-------|
| GET | `/bank/` | bank_dashboard | login |
| GET | `/bank/accounts` | bank_accounts | login |
| GET/POST | `/bank/accounts/new` | bank_account_new_get / _post | login |
| GET/POST | `/bank/accounts/{account_id}/edit` | bank_account_edit_get / _post | login |
| POST | `/bank/accounts/{account_id}/toggle` | bank_account_toggle | manager |
| GET | `/bank/statements` | bank_statements | login |
| GET | `/bank/statements/import` | bank_statement_import_get | login |
| GET | `/bank/statements/template` | bank_statement_template | login |
| POST | `/bank/statements/import` | bank_statement_import_post | login |
| GET | `/bank/statements/{statement_id}` | bank_statement_detail | login |
| POST | `/bank/statements/{statement_id}/balances` | bank_statement_balances | login |
| POST | `/bank/statements/{statement_id}/delete` | bank_statement_delete | manager |
| GET | `/bank/reports/ageing` (`?format=xlsx`) | bank_report_ageing | login |
| GET | `/bank/reports/charges` (`?from&to&format=xlsx`) | bank_report_charges | login |
| GET | `/bank/reports/cash-position` (`?format=xlsx`) | bank_report_cash_position | login |
| GET | `/bank/reconcile/{statement_id}` | bank_workbench | login |
| POST | `/bank/reconcile/{statement_id}/auto` | bank_auto_match | login |
| POST | `/bank/reconcile/{statement_id}/match` | bank_match (`target=journal:<line_id>:<entry_id>` or `payment:<id>`) | login |
| POST | `/bank/reconcile/{statement_id}/unmatch` | bank_unmatch | login |
| POST | `/bank/reconcile/{statement_id}/exclude` | bank_exclude | login |
| POST | `/bank/reconcile/{statement_id}/adjust` | bank_adjust | manager |
| POST | `/bank/reconcile/{statement_id}/adjust/{adjustment_id}/delete` | bank_adjust_delete | manager |
| POST | `/bank/reconcile/{statement_id}/status` | bank_recon_status (`balanced|closed|open`) | manager |
| GET | `/bank/reconcile/{statement_id}/report` (`?format=html|xlsx|pdf`) | bank_recon_report | login |

PDF uses `erca_forms.PdfDoc` (reportlab when installed, built-in fallback otherwise); a dependency-free
`_plain_pdf` exists as a last resort. Excel via openpyxl + `tempfile.mkstemp` + `FileResponse`. No new pip dependencies.

## Public Python API

```python
from bank_recon_data_store import bank_balances, unreconciled_count
bank_balances("default")      # [{account_id, name, bank_name, account_number, currency,
                              #   ledger_balance, statement_balance, statement_date, difference}, ...]
unreconciled_count("default") # number of unmatched statement lines across all accounts
```
Both are guarded and return `[]` / `0` when the database is unavailable.

## Conventions honoured

- `''` → `None` for DATE/NUMERIC form fields; money is `Decimal` quantised to 2 dp (`q2`).
- Static routes are registered before `/{param}` routes (tested).
- Every template renders with empty data (tested) and extends `multicompany/base.html` with `.module-sidebar` + `.content-with-sidebar`.
- All UI text is wrapped in `{{ _('…') }}`; `test_i18n_catalogue_covers_every_template_and_python_string` fails the build if a string lacks an Amharic entry.
- Dates in tables use `<small class="text-muted d-block">{{ x|et_date }}</small>` (via the `dt()` macro).

## Not done / known limits

- No scheduled jobs (`bank_recon_jobs`) — nothing in the requirement asked for one; the wiring test lists none.
- Deleting a *posted* adjustment is refused; a reversing journal entry must be made in the Journal module.
- Multi-currency: amounts are stored as given; no FX revaluation beyond the `fx` adjustment kind.
- Statement lines matched to a payment are not cross-linked to the payment's own journal entry (if the Payments module posts one, that ledger line may still appear as outstanding until matched to a journal line manually).
