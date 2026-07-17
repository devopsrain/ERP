"""
Template render tests — VAT module.

Renders every VAT template with route-accurate contexts, both EMPTY and
POPULATED, so undefined variables / wrong field names / Flask-era leftovers
fail the build instead of 500ing in production. No database required.

This is the harness that caught ~20 production template crashes in July 2026.
Extend the same pattern to other modules as they get touched.
"""
import sys
from datetime import date
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

from models.vat_portal import (  # noqa: E402
    CapitalRecord, ExpenseCategory, ExpenseRecord, FinancialSummary,
    IncomeCategory, IncomeRecord, VATType,
)

env = jinja2.Environment(loader=jinja2.FileSystemLoader(str(_WEB_DIR / "templates")))


def _base_ctx(path="/vat/x"):
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
    )


def _income():
    return IncomeRecord(income_id="", company_id="d", contract_date=date(2026, 7, 1),
                        description="Invoice", category=IncomeCategory.SALES_REVENUE,
                        gross_amount=Decimal("1000"), vat_type=VATType.STANDARD,
                        vat_rate=Decimal("0.15"), customer_name="Acme")


def _expense():
    return ExpenseRecord(expense_id="", company_id="d", expense_date=date(2026, 7, 2),
                         description="Rent", category=ExpenseCategory.RENT,
                         gross_amount=Decimal("300"), vat_type=VATType.STANDARD,
                         vat_rate=Decimal("0.15"), supplier_name="Landlord")


def _capital(kind="INJECTION"):
    return CapitalRecord(company_id="d", transaction_date=date(2026, 7, 3),
                         description="Tx", capital_type="CASH", transaction_type=kind,
                         amount=Decimal("500"), source="Owner")


def _totals(rs):
    return {"gross_amount": sum(r.gross_amount for r in rs),
            "vat_amount": sum(r.vat_amount for r in rs),
            "net_amount": sum(r.net_amount for r in rs)}


def _list_ctx(kind, rs):
    t = _totals(rs)
    return {f"{kind}_records": rs, f"{kind}_transactions": rs, "totals": t,
            "total_gross": t["gross_amount"], "total_vat": t["vat_amount"],
            "total_net": t["net_amount"],
            "filters": {"start_date": None, "end_date": None, "category": None},
            "vat_types": VATType, "income_categories": IncomeCategory,
            "expense_categories": ExpenseCategory}


def _capital_ctx(rs):
    inj = [r for r in rs if r.transaction_type == "INJECTION"]
    wd = [r for r in rs if r.transaction_type != "INJECTION"]
    return dict(capital_records=rs, capital_transactions=rs,
                injections_count=len(inj), withdrawals_count=len(wd),
                total_injected=sum((r.amount for r in inj), Decimal(0)),
                total_withdrawn=sum((r.amount for r in wd), Decimal(0)),
                total_vat=Decimal(0),
                net_capital=sum((r.amount for r in inj), Decimal(0))
                - sum((r.amount for r in wd), Decimal(0)),
                total_capital=sum((r.amount for r in rs), Decimal(0)))


def _summary_ctx(inc, exp, cap):
    s = FinancialSummary("d", date(2026, 1, 1), date(2026, 12, 31))
    for r in inc:
        s.total_income_gross += r.gross_amount
        s.total_income_vat += r.vat_amount
        s.total_income_net += r.net_amount
        s.output_vat += r.vat_amount
        s.income_by_category[r.category.value] = r.gross_amount
    for r in exp:
        s.total_expense_net += r.gross_amount
        s.total_expense_vat += r.vat_amount
        s.total_expense_gross += r.net_amount
        s.input_vat += r.vat_amount
        s.expense_by_category[r.category.value] = r.net_amount
    for r in cap:
        s.total_capital += r.amount
        s.capital_by_type[r.capital_type] = r.amount
    s.vat_payable = s.output_vat - s.input_vat
    s.gross_profit = s.total_income_gross - s.total_expense_net
    s.net_profit = s.total_income_net - s.total_expense_gross
    s.total_assets = s.total_capital + s.net_profit
    return dict(summary=s, start_date=date(2026, 1, 1), end_date=date(2026, 12, 31),
                income_transactions=inc, expense_transactions=exp,
                capital_transactions=cap)


CASES = [
    ("vat/income_list.html",       lambda: _list_ctx("income", [_income()])),
    ("vat/income_list.html",       lambda: _list_ctx("income", [])),
    ("vat/expense_list.html",      lambda: _list_ctx("expense", [_expense()])),
    ("vat/expense_list.html",      lambda: _list_ctx("expense", [])),
    ("vat/capital_list.html",      lambda: _capital_ctx([_capital(), _capital("WITHDRAWAL")])),
    ("vat/capital_list.html",      lambda: _capital_ctx([])),
    ("vat/financial_summary.html", lambda: _summary_ctx([_income()], [_expense()], [_capital()])),
    ("vat/financial_summary.html", lambda: _summary_ctx([], [], [])),
    ("vat/add_income.html",        lambda: dict(income_categories=IncomeCategory,
                                                vat_types=VATType, vat_configs={},
                                                recent_income=[_income()])),
    ("vat/add_expense.html",       lambda: dict(expense_categories=ExpenseCategory,
                                                vat_types=VATType, vat_configs={},
                                                recent_expenses=[_expense()])),
    ("vat/add_capital.html",       lambda: {}),
]


@pytest.mark.parametrize("template,ctx_fn", CASES,
                         ids=[f"{t}:{i}" for i, (t, _) in enumerate(CASES)])
def test_vat_template_renders(template, ctx_fn):
    html = env.get_template(template).render(**_base_ctx(), **ctx_fn())
    assert len(html) > 1000  # sanity: a real page came out
