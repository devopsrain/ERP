"""
Template render tests — Bank Reconciliation module.

Renders every bank/ template with route-accurate contexts, both EMPTY and
POPULATED, so undefined variables / wrong field names fail the build instead
of 500ing in production. No database required.
"""
import re
import sys
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import jinja2
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEB_DIR = _REPO_ROOT / "web"
for p in (str(_REPO_ROOT), str(_WEB_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

try:  # `_()`, `|et_date` — installed into jinja2 defaults (conftest does this too)
    import i18n as _i18n
    _i18n.install()
except Exception:  # pragma: no cover
    pass

import bank_recon_logic as L  # noqa: E402
from bank_recon_data_store import BankReconDataStore  # noqa: E402

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))
TODAY = date(2026, 10, 1)


def _base_ctx(path="/bank/x"):
    request = SimpleNamespace(
        url=SimpleNamespace(path=path, query=""),
        query_params=SimpleNamespace(get=lambda k, d=None: d),
        session={}, form=SimpleNamespace(),
    )
    return dict(
        request=request, session={}, url_for=lambda *a, **k: "#",
        csrf_token=lambda: "x", get_flashed_messages=lambda **k: [],
        static_url=lambda p: p, static_cdn_url="", app_version="1.0",
        current_company_id="default", current_tenant=None,
        active_page="dashboard", bank_names=L.BANK_NAMES, adjustment_kinds=L.ADJUSTMENT_KINDS,
        adjustment_labels=L.ADJUSTMENT_LABELS, today=TODAY,
    )


# ── fixtures ──────────────────────────────────────────────────────

def _account(**kw):
    a = dict(id="acc-1", company_id="default", name="CBE Main Operating", bank_name="Commercial Bank of Ethiopia",
             account_number="1000123456789", currency="ETB", gl_account_code="1010", opening_balance=Decimal("500000.00"),
             opening_date=date(2026, 8, 31), is_active=True, notes="", created_by="admin",
             created_at=datetime(2026, 9, 1), updated_at=datetime(2026, 9, 1),
             ledger_balance=Decimal("572350.00"), statement_balance=Decimal("572350.00"), statement_date=date(2026, 9, 30))
    a.update(kw)
    return a


def _statement(**kw):
    s = dict(id="st-1", company_id="default", bank_account_id="acc-1", period_start=date(2026, 9, 1), period_end=date(2026, 9, 30),
             opening_balance=Decimal("500000.00"), closing_balance=Decimal("572350.00"), filename="cbe_sept.xlsx",
             imported_by="admin", imported_at=datetime(2026, 10, 1, 9, 0), status="in_progress", line_count=3, notes="",
             account_name="CBE Main Operating", bank_name="Commercial Bank of Ethiopia", account_number="1000123456789",
             currency="ETB", gl_account_code="1010", account_opening_balance=Decimal("500000.00"),
             account_opening_date=date(2026, 8, 31), recon_status="open", unexplained_difference=Decimal("-150.00"),
             reconciliation_id="rc-1", unmatched_count=1)
    s.update(kw)
    return s


def _line(**kw):
    l = dict(id="ln-1", statement_id="st-1", bank_account_id="acc-1", company_id="default", txn_date=date(2026, 9, 1),
             value_date=None, description="Cheque 001234 — Supplier payment", reference="CHQ001234", debit=Decimal("12500.00"),
             credit=Decimal("0.00"), balance=Decimal("487500.00"), external_id=None, row_hash="h1", status="unmatched",
             matched_kind=None, matched_id=None, matched_at=None, matched_by=None, line_number=1, candidates=[])
    l.update(kw)
    return l


def _ledger(**kw):
    g = dict(line_id="jl-1", entry_id="je-0001", account_code="1010", description="Cheque 001234 supplier",
             debit=Decimal("0.00"), credit=Decimal("12500.00"), entry_date=date(2026, 9, 1),
             entry_description="Supplier payment", reference="CHQ001234", entry_status="posted",
             reconciled_against=None, reconciled_at=None)
    g.update(kw)
    return g


def _payment(**kw):
    p = dict(id="pay-1", provider="telebirr", direction="in", amount=Decimal("85000.00"), currency="ETB",
             paid_at=datetime(2026, 9, 3, 12), reference="FT26246ABC", narration="Customer transfer", payer_name="EEU",
             payee_name="", provider_txn_id="TB123", status="completed", entry_date=date(2026, 9, 3),
             debit=Decimal("85000.00"), credit=Decimal("0.00"), description="Customer transfer EEU")
    p.update(kw)
    return p


def _adjustment(**kw):
    j = dict(id="adj-1", company_id="default", reconciliation_id="rc-1", bank_account_id="acc-1", statement_line_id="ln-3",
             kind="bank_charge", amount=Decimal("-150.00"), gl_account_code="6210", description="Service charge",
             adj_date=date(2026, 9, 30), journal_entry_id="je-9999", created_by="admin", created_at=datetime(2026, 10, 1))
    j.update(kw)
    return j


def _recon(populated=True, **kw):
    figures = L.reconcile("572350", "572500", "0", "0", "0", "150") if populated else L.reconcile(0, 0, 0, 0)
    r = dict(id="rc-1" if populated else None, company_id="default", bank_account_id="acc-1", statement_id="st-1",
             as_of=date(2026, 9, 30), status="open", closed_by=None, closed_at=None, notes="",
             created_at=datetime(2026, 10, 1), updated_at=datetime(2026, 10, 1))
    r.update(figures)
    r["outstanding_lines"] = [_ledger(line_id="jl-5", entry_id="je-0005", debit=Decimal("2000.00"), credit=Decimal("0.00"),
                                      entry_description="Deposit in transit", reference="DEP-5")] if populated else []
    r["unmatched_lines"] = [_line(id="ln-3", txn_date=date(2026, 9, 30), description="Service charge", reference="",
                                  debit=Decimal("150.00"), balance=Decimal("572350.00"), line_number=3)] if populated else []
    r["adjustments"] = [_adjustment(), _adjustment(id="adj-2", kind="interest", amount=Decimal("20.00"), journal_entry_id=None,
                                                   statement_line_id=None, gl_account_code="")] if populated else []
    r.update(kw)
    return r


def _dashboard_stats(populated):
    s = BankReconDataStore.empty_dashboard()
    if populated:
        s.update(account_count=2, statements=3, unmatched=1, open_recons=1, total_ledger=Decimal("672350.00"),
                 total_bank=Decimal("672200.00"), difference=Decimal("-150.00"),
                 accounts=[{"id": "acc-1", "name": "CBE Main Operating", "bank_name": "Commercial Bank of Ethiopia",
                            "account_number": "1000123456789", "currency": "ETB", "gl_account_code": "1010",
                            "ledger_balance": Decimal("572350.00"), "statement_balance": Decimal("572200.00"),
                            "last_statement_date": date(2026, 9, 30), "days_since_statement": 1,
                            "last_reconciled": date(2026, 8, 31), "days_since_reconciled": 31,
                            "unmatched": 1, "difference": Decimal("-150.00"), "open_statement_id": "st-1"},
                           {"id": "acc-2", "name": "Awash Payroll", "bank_name": "Awash Bank", "account_number": "0130",
                            "currency": "ETB", "gl_account_code": "", "ledger_balance": Decimal("100000.00"),
                            "statement_balance": None, "last_statement_date": None, "days_since_statement": None,
                            "last_reconciled": None, "days_since_reconciled": None, "unmatched": 0, "difference": None,
                            "open_statement_id": None}],
                 recent_statements=[_statement(), _statement(id="st-0", status="reconciled", recon_status="closed",
                                                             unexplained_difference=Decimal("0.00"), unmatched_count=0,
                                                             period_start=date(2026, 8, 1), period_end=date(2026, 8, 31))],
                 payments_available=True)
    return s


def _workbench_ctx(populated, closed=False):
    if not populated:
        return dict(statement=_statement(gl_account_code="", status="imported"), recon=_recon(False), unmatched=[], matched=[],
                    ledger=[], payments=[], ledger_totals=L.outstanding_totals([]), unmatched_totals=L.statement_totals([]),
                    gl_accounts=[], ledger_available=False)
    ledger = [_ledger(), _ledger(line_id="jl-2", entry_id="je-0002", debit=Decimal("85000.00"), credit=Decimal("0.00"),
                                 entry_description="Customer receipt", reference="FT26246ABC", entry_date=date(2026, 9, 3))]
    pays = [_payment()]
    unmatched = [_line(candidates=[{"score": 100, "kind": "journal", "line": ledger[0]}]),
                 _line(id="ln-2", txn_date=date(2026, 9, 3), description="Customer transfer INV-42", reference="FT26246ABC",
                       debit=Decimal("0.00"), credit=Decimal("85000.00"), external_id="FT26246ABC", line_number=2,
                       candidates=[{"score": 100, "kind": "journal", "line": ledger[1]}, {"score": 75, "kind": "payment", "line": pays[0]}]),
                 _line(id="ln-3", txn_date=date(2026, 9, 30), description="Service charge", reference="",
                       debit=Decimal("150.00"), line_number=3)]
    matched = [_line(id="ln-4", status="matched", matched_kind="journal", matched_id="jl-9", matched_by="admin",
                     matched_at=datetime(2026, 10, 1), ledger=_ledger(line_id="jl-9")),
               _line(id="ln-5", status="excluded", matched_by="admin", ledger=None),
               _line(id="ln-6", status="matched", matched_kind="payment", matched_id="pay-7", ledger=None),
               _line(id="ln-7", status="matched", matched_kind="adjustment", matched_id="adj-1", ledger=None)]
    recon = _recon(True, status="closed" if closed else "open", closed_by="manager" if closed else None,
                   closed_at=datetime(2026, 10, 2, 10, 5) if closed else None, notes="Closed with note" if closed else "")
    return dict(statement=_statement(status="reconciled" if closed else "in_progress"), recon=recon, unmatched=[] if closed else unmatched,
                matched=matched, ledger=ledger, payments=pays, ledger_totals=L.outstanding_totals(ledger),
                unmatched_totals=L.statement_totals(unmatched),
                gl_accounts=[{"account_code": "1010", "account_name": "CBE Bank", "account_type": "Asset"},
                             {"account_code": "6210", "account_name": "Bank charges", "account_type": "Expense"}],
                ledger_available=True)


_SUMMARY_LABELS = ["Balance per bank statement", "Add: deposits in transit (ledger debits not on statement)",
                   "Less: outstanding payments (ledger credits not on statement)", "ADJUSTED BANK BALANCE",
                   "Balance per general ledger", "DIFFERENCE", "Unmatched statement credits (not yet booked)",
                   "Unmatched statement debits (not yet booked)", "UNEXPLAINED DIFFERENCE"]


def _recon_statement_ctx(populated):
    recon = _recon(populated, status="closed" if populated else "open", closed_by="manager" if populated else None,
                   closed_at=datetime(2026, 10, 2) if populated else None, notes="ok" if populated else "")
    keys = ["statement_balance", "outstanding_deposits", "outstanding_payments", "adjusted_bank_balance", "ledger_balance",
            "difference", "unmatched_statement_credits", "unmatched_statement_debits", "unexplained_difference"]
    return dict(statement=_statement(), recon=recon, summary=[(lab, recon[k]) for lab, k in zip(_SUMMARY_LABELS, keys)],
                generated=TODAY)


def _ageing_ctx(populated):
    stmt_items = [dict(_line(), account_name="CBE Main Operating", bank_name="CBE"),
                  dict(_line(id="ln-x", txn_date=date(2026, 5, 1)), account_name="CBE Main Operating", bank_name="CBE")] if populated else []
    ledger_items = [dict(_ledger(), account_name="CBE Main Operating", bank_name="CBE", txn_date=date(2026, 7, 15))] if populated else []
    return dict(report={"as_of": TODAY, "statement": L.age_items(stmt_items, TODAY), "ledger": L.age_items(ledger_items, TODAY),
                        "statement_total": L.statement_totals(stmt_items), "ledger_total": L.outstanding_totals(ledger_items),
                        "buckets": L.AGEING_BUCKETS},
                accounts=[_account()] if populated else [], account_filter="acc-1" if populated else "", as_of=TODAY)


def _charges_ctx(populated):
    rows = [dict(_adjustment(), account_name="CBE Main Operating", bank_name="CBE"),
            dict(_adjustment(id="adj-2", kind="interest", amount=Decimal("20.00"), journal_entry_id=None), account_name="CBE Main Operating", bank_name="CBE"),
            dict(_adjustment(id="adj-3", kind="fx", amount=Decimal("-5.00")), account_name="CBE Main Operating", bank_name="CBE")] if populated else []
    rep = {"rows": rows, "count": len(rows), "total_charges": Decimal("150.00") if populated else Decimal(0),
           "total_interest": Decimal("20.00") if populated else Decimal(0), "total_other": Decimal("-5.00") if populated else Decimal(0),
           "by_kind": {"bank_charge": {"kind": "bank_charge", "label": "Bank charge", "count": 1, "amount": Decimal("-150.00")},
                       "interest": {"kind": "interest", "label": "Interest", "count": 1, "amount": Decimal("20.00")},
                       "fx": {"kind": "fx", "label": "FX difference", "count": 1, "amount": Decimal("-5.00")}} if populated else {},
           "by_month": [{"month": "2026-09", "charges": Decimal("150.00"), "interest": Decimal("20.00"), "other": Decimal("-5.00")}] if populated else []}
    return dict(report=rep, date_from=date(2026, 1, 1), date_to=TODAY)


def _cash_ctx(populated):
    rows = [{"account": _account(), "ledger_balance": Decimal("572350.00"), "statement_balance": Decimal("572200.00"),
             "statement_date": date(2026, 9, 30), "statement_status": "in_progress", "difference": Decimal("-150.00"),
             "unmatched": 1, "days_since": 1},
            {"account": _account(id="acc-2", name="Awash Payroll", bank_name="Awash Bank", gl_account_code=""),
             "ledger_balance": Decimal("100000.00"), "statement_balance": None, "statement_date": None, "statement_status": None,
             "difference": None, "unmatched": 0, "days_since": None}] if populated else []
    return dict(report={"rows": rows, "total_ledger": Decimal("672350.00") if populated else Decimal(0),
                        "total_bank": Decimal("572200.00") if populated else Decimal(0),
                        "difference": Decimal("-100150.00") if populated else Decimal(0), "as_of": TODAY})


def _import_ctx(populated, report=None):
    return dict(accounts=[_account()] if populated else [], template_columns=L.TEMPLATE_COLUMNS, template_notes=L.TEMPLATE_NOTES,
                header_aliases={k: v[:6] for k, v in L.HEADER_ALIASES.items()}, report=report,
                selected_account="acc-1" if populated else "")


def _detail_ctx(populated):
    lines = [_line(), _line(id="ln-4", status="matched", matched_kind="journal", matched_id="jl-9", matched_by="admin", line_number=2),
             _line(id="ln-5", status="excluded", line_number=3, balance=None)] if populated else []
    return dict(statement=_statement(), lines=lines, counts={s: sum(1 for l in lines if l["status"] == s) for s in L.LINE_STATUSES},
                totals=L.statement_totals(lines), recon=_recon(True) if populated else None)


CASES = [
    ("bank/dashboard.html", lambda: dict(stats=_dashboard_stats(True))),
    ("bank/dashboard.html", lambda: dict(stats=_dashboard_stats(False))),
    ("bank/dashboard.html", lambda: dict(stats=dict(_dashboard_stats(False), ledger_available=False))),
    ("bank/accounts.html", lambda: dict(accounts=[_account(), _account(id="acc-2", is_active=False, statement_date=None,
                                                                        statement_balance=None, ledger_balance=None, opening_date=None)])),
    ("bank/accounts.html", lambda: dict(accounts=[])),
    ("bank/account_form.html", lambda: dict(account={"currency": "ETB", "is_active": True}, is_edit=False,
                                            gl_accounts=[{"account_code": "1010", "account_name": "CBE Bank", "account_type": "Asset"}])),
    ("bank/account_form.html", lambda: dict(account=_account(), is_edit=True, gl_accounts=[])),
    ("bank/account_form.html", lambda: dict(account={}, is_edit=False, gl_accounts=[])),
    ("bank/statements.html", lambda: dict(statements=[_statement(), _statement(id="st-2", recon_status=None, unexplained_difference=None,
                                                                                imported_at=None, period_start=None, period_end=None)],
                                          accounts=[_account()], account_filter="acc-1")),
    ("bank/statements.html", lambda: dict(statements=[], accounts=[], account_filter="")),
    ("bank/statement_import.html", lambda: _import_ctx(True)),
    ("bank/statement_import.html", lambda: _import_ctx(False)),
    ("bank/statement_import.html", lambda: _import_ctx(True, report={"total": 3, "imported": 0, "duplicates": 3,
                                                                     "errors": ["Row 4: unreadable date 'x'"],
                                                                     "columns": {"txn_date": "Txn Date", "debit": "Debit"},
                                                                     "statement_id": None, "filename": "x.xlsx"})),
    ("bank/statement_detail.html", lambda: _detail_ctx(True)),
    ("bank/statement_detail.html", lambda: _detail_ctx(False)),
    ("bank/statement_detail.html", lambda: dict(_detail_ctx(True), recon=_recon(True, status="closed"))),
    ("bank/workbench.html", lambda: _workbench_ctx(True)),
    ("bank/workbench.html", lambda: _workbench_ctx(True, closed=True)),
    ("bank/workbench.html", lambda: _workbench_ctx(False)),
    ("bank/recon_statement.html", lambda: _recon_statement_ctx(True)),
    ("bank/recon_statement.html", lambda: _recon_statement_ctx(False)),
    ("bank/report_ageing.html", lambda: _ageing_ctx(True)),
    ("bank/report_ageing.html", lambda: _ageing_ctx(False)),
    ("bank/report_charges.html", lambda: _charges_ctx(True)),
    ("bank/report_charges.html", lambda: _charges_ctx(False)),
    ("bank/report_cash_position.html", lambda: _cash_ctx(True)),
    ("bank/report_cash_position.html", lambda: _cash_ctx(False)),
]


@pytest.mark.parametrize("template,ctx_fn", CASES, ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_bank_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000
    assert "module-sidebar" in html


def test_workbench_shows_candidates_and_figures():
    html = env.get_template("bank/workbench.html").render(**_base_ctx(), **_workbench_ctx(True))
    assert 'value="journal:jl-1:je-0001"' in html
    assert 'value="payment:pay-1"' in html
    assert "100% ·" in html
    assert "-150.00" in html            # difference
    assert 'id="adjModal"' in html and "Auto-match" in html


def test_workbench_closed_hides_actions():
    html = env.get_template("bank/workbench.html").render(**_base_ctx(), **_workbench_ctx(True, closed=True))
    assert "Reopen" in html and "Closed with note" in html
    assert 'data-bs-target="#adjModal"' not in html


def test_recon_statement_lists_summary_rows():
    html = env.get_template("bank/recon_statement.html").render(**_base_ctx(), **_recon_statement_ctx(True))
    for lab in _SUMMARY_LABELS:
        assert lab in html
    assert "Deposit in transit" in html and "Service charge" in html


def test_templates_do_not_use_top_level_jquery_ready():
    for tpl in (_WEB_DIR / "templates" / "bank").glob("*.html"):
        assert "$(document).ready" not in tpl.read_text(encoding="utf-8"), tpl.name


def test_all_template_url_for_endpoints_exist():
    import bank_recon_routes as R
    names = {r.name for r in R.router.routes}
    for tpl in (_WEB_DIR / "templates" / "bank").glob("*.html"):
        for ep in re.findall(r"url_for\('bank\.([a-z_]+)'", tpl.read_text(encoding="utf-8")):
            assert f"bank_{ep}" in names, f"{tpl.name}: bank.{ep}"


def test_router_prefix_and_static_before_param():
    import bank_recon_routes as R
    paths = [r.path for r in R.router.routes]
    assert all(p.startswith("/bank") for p in paths)
    assert paths.index("/bank/statements/import") < paths.index("/bank/statements/{statement_id}")
    assert paths.index("/bank/statements/template") < paths.index("/bank/statements/{statement_id}")
    assert paths.index("/bank/accounts/new") < paths.index("/bank/accounts/{account_id}/edit")
    assert paths.index("/bank/reports/ageing") < paths.index("/bank/reconcile/{statement_id}")


def test_manager_guard_on_close_and_adjustments():
    import bank_recon_routes as R
    guarded = {"bank_adjust", "bank_adjust_delete", "bank_recon_status", "bank_statement_delete", "bank_account_toggle"}
    for r in R.router.routes:
        if r.name in guarded:
            deps = [d.call for d in r.dependant.dependencies]
            assert R.manager_required in deps, r.name


def test_statement_template_and_reader_roundtrip(tmp_path):
    """The downloadable template imports through the same reader the upload uses."""
    import bank_recon_routes as R
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["Commercial Bank of Ethiopia"])          # title block above the table
    ws.append(["Account statement 1000123456789"])
    ws.append([])
    ws.append(["Txn Date", "Value Date", "Narration", "Transaction ID", "Debit", "Credit", "Balance"])
    ws.append([date(2026, 9, 1), date(2026, 9, 1), "Cheque 001234", "T001", 12500, None, 487500])
    ws.append([date(2026, 9, 3), date(2026, 9, 3), "Transfer in", "T002", None, 85000, 572500])
    p = tmp_path / "cbe.xlsx"
    wb.save(p)
    rows = R._read_rows("cbe.xlsx", p.read_bytes())
    assert len(rows) == 2 and "Txn Date" in rows[0]
    lines, errors = L.normalise_rows(rows)
    assert not errors and lines[0]["debit"] == Decimal("12500.00") and lines[1]["external_id"] == "T002"
    csv_rows = R._read_rows("x.csv", b"Date,Description,Reference,Debit,Credit,Balance\n2026-09-01,A,R1,10,,990\n")
    assert csv_rows[0]["Debit"] == "10"


def test_pdf_and_xlsx_helpers_produce_files():
    import os
    import bank_recon_routes as R
    pdf = R._pdf_bytes("Bank Reconciliation Statement", ["CBE — Main"], [("Date", 1, "left"), ("Amount", 1, "right")],
                       [["2026-09-01", "12,500.00"]], [("DIFFERENCE", "0.00")])
    assert pdf.startswith(b"%PDF") and len(pdf) > 500
    plain = R._plain_pdf("T", ["m"], ["a", "b"], [[1, 2]], [("x", "1")])
    assert plain.startswith(b"%PDF") and b"%%EOF" in plain
    path = R._xlsx_file("Cash position", ["Account", "Ledger balance"], [["CBE", Decimal("10.50")]],
                        totals=["TOTAL", Decimal("10.50")], meta=["As of 2026-10-01"], money_cols=[1])
    try:
        from openpyxl import load_workbook
        ws = load_workbook(path).active
        assert ws["A1"].value == "Cash position" and ws["B5"].value == 10.5
    finally:
        os.unlink(path)


def test_public_api_and_schema_are_importable_and_side_effect_free():
    import bank_recon_data_store as S
    assert callable(S.ensure_schema) and callable(S.bank_balances) and callable(S.unreconciled_count)
    # No DATABASE_URL in the test env: ensure_schema must swallow the failure, never raise.
    S.ensure_schema()
    assert S.unreconciled_count("default") == 0
    assert S.bank_balances("default") == []
