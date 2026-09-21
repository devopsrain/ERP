"""
Report Builder — data-source catalogue + safe query compiler.

Reports are built ONLY over the whitelisted catalogue below. Users never
supply SQL: every table, column, operator, aggregate function and sort
direction is validated against the catalogue and quoted by this module, and
every user VALUE travels as a psycopg2 parameter.

Public API
    SOURCES                          – {source_key: Source}
    catalog_json(available=None)     – catalogue as plain dicts (for /reports/api/catalog)
    validate_definition(defn)        – normalise + validate a report definition dict
    compile_query(defn, company_id, today=None, available=None)
                                     – → CompiledQuery(sql, params, columns, date_range)
    date_range(preset, today)        – (start, end) inclusive dates or None
    DATE_PRESETS                     – ordered list of (key, label)
    CatalogError                     – raised for anything not whitelisted
"""
from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Sequence, Set, Tuple

MAX_LIMIT = 50_000
DEFAULT_LIMIT = 5_000

TEXT, NUMBER, DATE, DATETIME, DATETEXT, BOOL = "text", "number", "date", "datetime", "datetext", "bool"
DATE_LIKE = (DATE, DATETIME, DATETEXT)

OPERATORS = ("eq", "ne", "gt", "gte", "lt", "lte", "contains", "in", "between", "is_null")
AGG_FUNCS = ("sum", "avg", "min", "max", "count")
GRANULARITIES = ("day", "month", "quarter", "year")
CHART_TYPES = ("bar", "line", "pie")
_IDENT_RE = re.compile(r"^[a-z_][a-z0-9_]*$")


class CatalogError(ValueError):
    """Anything that is not whitelisted / not well-formed."""


# ── Catalogue model ─────────────────────────────────────────────────

@dataclass(frozen=True)
class Col:
    name: str
    label: str
    type: str = TEXT
    expr: Optional[str] = None            # computed column (catalogue-defined SQL, never user SQL)
    requires: Tuple[str, ...] = ()        # physical columns the expr depends on

    @property
    def physical(self) -> bool:
        return self.expr is None


@dataclass(frozen=True)
class Source:
    key: str
    table: str
    label: str
    module: str
    columns: Tuple[Col, ...]
    date_column: Optional[str] = None
    company_column: str = "company_id"

    def col(self, name: str) -> Optional[Col]:
        for c in self.columns:
            if c.name == name:
                return c
        return None

    def date_columns(self) -> List[Col]:
        return [c for c in self.columns if c.type in DATE_LIKE]


def _c(name, label, type_=TEXT):
    return Col(name, label, type_)


_MONEY_TRIO = (
    _c("gross_amount", "Gross amount", NUMBER),
    _c("vat_amount", "VAT amount", NUMBER),
    _c("net_amount", "Net amount", NUMBER),
)

_SOURCE_LIST: List[Source] = [
    Source("vat_income", "vat_income", "VAT Income", "VAT", (
        _c("income_date", "Income date", DATE), _c("contract_date", "Contract date", DATE),
        _c("description", "Description"), _c("category", "Category"),
        _c("customer_name", "Customer"), _c("customer_tin", "Customer TIN"),
        _c("invoice_number", "Invoice no."), _c("income_type", "Income type"),
        _c("brand", "Brand"), _c("payment_mode", "Payment mode"),
        _c("penalty", "Penalty"), _c("penalty_fee", "Penalty fee", NUMBER),
        *_MONEY_TRIO, _c("vat_type", "VAT type"), _c("vat_rate", "VAT rate", NUMBER),
        _c("tender_id", "Tender"), _c("created_by", "Created by"),
        _c("created_date", "Created", DATETIME), _c("is_active", "Active", BOOL),
    ), "income_date"),
    Source("vat_expenses", "vat_expenses", "VAT Expenses", "VAT", (
        _c("expense_date", "Expense date", DATE), _c("description", "Description"),
        _c("category", "Category"), _c("supplier_name", "Supplier"),
        _c("supplier_tin", "Supplier TIN"), _c("receipt_number", "Receipt no."),
        *_MONEY_TRIO, _c("vat_type", "VAT type"), _c("vat_rate", "VAT rate", NUMBER),
        _c("tender_id", "Tender"), _c("created_by", "Created by"),
        _c("created_date", "Created", DATETIME), _c("is_active", "Active", BOOL),
    ), "expense_date"),
    Source("vat_capital", "vat_capital", "VAT Capital", "VAT", (
        _c("investment_date", "Investment date", DATE), _c("description", "Description"),
        _c("capital_type", "Capital type"), _c("transaction_type", "Transaction type"),
        _c("amount", "Amount", NUMBER), _c("vat_type", "VAT type"),
        _c("vat_rate", "VAT rate", NUMBER), _c("vat_amount", "VAT amount", NUMBER),
        _c("investor_name", "Investor"), _c("investor_tin", "Investor TIN"),
        _c("created_by", "Created by"), _c("created_date", "Created", DATETIME),
        _c("is_active", "Active", BOOL),
    ), "investment_date"),
    Source("income_records", "income_records", "Income Records", "Income & Expense", (
        _c("date", "Date", DATETEXT), _c("description", "Description"), _c("category", "Category"),
        _c("client_name", "Client"), _c("client_tin", "Client TIN"),
        _c("gross_amount", "Gross amount", NUMBER), _c("tax_rate", "Tax rate", NUMBER),
        _c("tax_amount", "Tax amount", NUMBER), _c("net_amount", "Net amount", NUMBER),
        _c("payment_method", "Payment method"), _c("reference_number", "Reference"),
    ), "date"),
    Source("expense_records", "expense_records", "Expense Records", "Income & Expense", (
        _c("date", "Date", DATETEXT), _c("description", "Description"), _c("category", "Category"),
        _c("supplier_name", "Supplier"), _c("supplier_tin", "Supplier TIN"),
        _c("gross_amount", "Gross amount", NUMBER), _c("tax_rate", "Tax rate", NUMBER),
        _c("tax_amount", "Tax amount", NUMBER), _c("net_amount", "Net amount", NUMBER),
        _c("payment_method", "Payment method"), _c("receipt_number", "Receipt no."),
        _c("is_deductible", "Deductible", BOOL),
    ), "date"),
    Source("journal_entries", "journal_entries", "Journal Entries", "Accounting", (
        _c("entry_date", "Entry date", DATETEXT), _c("description", "Description"),
        _c("reference_number", "Reference"), _c("total_debit", "Total debit", NUMBER),
        _c("total_credit", "Total credit", NUMBER), _c("status", "Status"),
        _c("created_by", "Created by"), _c("is_active", "Active", BOOL),
    ), "entry_date"),
    Source("transactions", "transactions", "Bank Transactions", "Accounting", (
        _c("date", "Date", DATETEXT), _c("account_code", "Account code"),
        _c("account_name", "Account name"), _c("description", "Description"),
        _c("reference", "Reference"), _c("counterparty", "Counterparty"),
        _c("debit", "Debit", NUMBER), _c("credit", "Credit", NUMBER),
        _c("balance", "Balance", NUMBER), _c("currency", "Currency"),
        _c("is_flagged", "Flagged", BOOL), _c("flag_reason", "Flag reason"),
        _c("review_status", "Review status"), _c("import_batch_id", "Import batch"),
    ), "date"),
    Source("chart_of_accounts", "chart_of_accounts", "Chart of Accounts", "Accounting", (
        _c("account_code", "Account code"), _c("account_name", "Account name"),
        _c("account_type", "Type"), _c("account_subtype", "Subtype"),
        _c("parent_account", "Parent"), _c("normal_balance", "Normal balance"),
        _c("current_balance", "Current balance", NUMBER), _c("is_active", "Active", BOOL),
    )),
    Source("bid_records", "bid_records", "Bids / Tenders", "Bids", (
        _c("title", "Title"), _c("reference_number", "Reference"),
        _c("organization", "Organization"), _c("category", "Category"),
        _c("status", "Status"), _c("deadline", "Deadline", DATETEXT),
        _c("submission_date", "Submission date", DATETEXT),
        _c("bid_amount", "Bid amount", NUMBER), _c("currency", "Currency"),
        _c("case_handler_name", "Case handler"), _c("contract_date", "Contract date", DATE),
        _c("delivery_days", "Delivery days", NUMBER), _c("reminder_sent", "Reminder sent", BOOL),
        _c("created_at", "Created", DATETEXT),
    ), "deadline"),
    Source("cpo_records", "cpo_records", "CPO Records", "Bids", (
        _c("name", "Name"), _c("date", "Date", DATETEXT), _c("amount", "Amount", NUMBER),
        _c("bid_name", "Bid"), _c("is_returned", "Returned"),
        _c("returned_date", "Returned date", DATETEXT), _c("import_batch_id", "Import batch"),
        _c("created_at", "Created", DATETEXT),
    ), "date"),
    Source("employees", "employees", "Employees", "HR & Payroll", (
        _c("employee_id", "Employee ID"), _c("name", "Name"), _c("category", "Category"),
        _c("department", "Department"), _c("position", "Position"),
        _c("basic_salary", "Basic salary", NUMBER), _c("hire_date", "Hire date", DATE),
        _c("tin_number", "TIN"), _c("pension_number", "Pension no."),
        _c("work_days_per_month", "Work days/month", NUMBER),
        _c("work_hours_per_day", "Work hours/day", NUMBER),
        _c("manager", "Manager"), _c("is_active", "Active", BOOL),
        _c("created_date", "Created", DATETIME),
    ), "hire_date"),
    Source("hrm_payroll_runs", "hrm_payroll_runs", "Payroll Runs", "HR & Payroll", (
        _c("payroll_month", "Payroll month"), _c("contract_type", "Contract type"),
        _c("grade", "Grade"), _c("gross_pay", "Gross pay", NUMBER),
        _c("allowances", "Allowances", NUMBER), _c("deductions", "Deductions", NUMBER),
        _c("overtime_pay", "Overtime pay", NUMBER), _c("tax_amount", "Income tax", NUMBER),
        _c("pension_amount", "Pension", NUMBER), _c("net_pay", "Net pay", NUMBER),
        _c("status", "Status"), _c("approved_by", "Approved by"),
        _c("created_by", "Created by"), _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("payroll_data", "payroll_data", "Payroll Data (legacy)", "HR & Payroll", (
        _c("employee_id", "Employee ID"), _c("month", "Month", NUMBER), _c("year", "Year", NUMBER),
        _c("gross_salary", "Gross salary", NUMBER), _c("net_salary", "Net salary", NUMBER),
        _c("pension", "Pension", NUMBER), _c("income_tax", "Income tax", NUMBER),
        _c("total_deductions", "Total deductions", NUMBER),
    )),
    Source("hrm_leave_requests", "hrm_leave_requests", "Leave Requests", "HR & Payroll", (
        _c("employee_id", "Employee ID"), _c("leave_type", "Leave type"),
        _c("start_date", "Start", DATE), _c("end_date", "End", DATE),
        _c("days_requested", "Days", NUMBER), _c("status", "Status"),
        _c("created_at", "Created", DATETIME),
    ), "start_date"),
    Source("inventory_items", "inventory_items", "Inventory Items", "Inventory", (
        _c("sku", "SKU"), _c("name", "Name"), _c("category", "Category"), _c("unit", "Unit"),
        _c("unit_price", "Unit price", NUMBER), _c("cost_price", "Cost price", NUMBER),
        _c("current_stock", "Current stock", NUMBER),
        Col("stock_value", "Stock value (stock × cost)", NUMBER,
            expr='("current_stock" * "cost_price")', requires=("current_stock", "cost_price")),
        _c("min_stock_level", "Min stock", NUMBER), _c("reorder_point", "Reorder point", NUMBER),
        _c("reorder_quantity", "Reorder qty", NUMBER), _c("location", "Location"),
        _c("status", "Status"), _c("valuation_method", "Valuation method"),
        _c("serial_number", "Serial no."), _c("batch_number", "Batch no."),
        _c("created_at", "Created", DATETEXT),
    ), "created_at"),
    Source("inventory_movements", "inventory_movements", "Inventory Movements", "Inventory", (
        _c("item_name", "Item"), _c("movement_type", "Movement type"),
        _c("quantity", "Quantity", NUMBER), _c("unit_cost", "Unit cost", NUMBER),
        _c("total_cost", "Total cost", NUMBER), _c("from_location", "From"),
        _c("to_location", "To"), _c("reference_number", "Reference"), _c("reason", "Reason"),
        _c("approved_by", "Approved by"), _c("approval_status", "Approval status"),
        _c("date", "Date", DATETEXT), _c("created_at", "Created", DATETEXT),
    ), "date"),
    Source("inventory_requisitions", "inventory_requisitions", "Inventory Requisitions", "Inventory", (
        _c("item_name", "Item"), _c("quantity_needed", "Qty needed", NUMBER),
        _c("current_stock", "Current stock", NUMBER), _c("reorder_point", "Reorder point", NUMBER),
        _c("estimated_cost", "Estimated cost", NUMBER), _c("priority", "Priority"),
        _c("status", "Status"), _c("requested_by", "Requested by"),
        _c("approved_by", "Approved by"), _c("supplier", "Supplier"), _c("date", "Date", DATETEXT),
    ), "date"),
    Source("proc_vendors", "proc_vendors", "Vendors", "Procurement", (
        _c("name", "Name"), _c("category", "Category"), _c("tin_number", "TIN"),
        _c("rating", "Rating", NUMBER), _c("status", "Status"), _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("proc_purchase_requisitions", "proc_purchase_requisitions", "Purchase Requisitions", "Procurement", (
        _c("department", "Department"), _c("title", "Title"),
        _c("total_amount", "Total amount", NUMBER), _c("status", "Status"),
        _c("requested_by", "Requested by"), _c("approved_by", "Approved by"),
        _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("proc_purchase_orders", "proc_purchase_orders", "Purchase Orders", "Procurement", (
        _c("title", "Title"), _c("vendor_id", "Vendor ID"), _c("delivery_date", "Delivery date", DATE),
        _c("payment_terms", "Payment terms"), _c("total_amount", "Total amount", NUMBER),
        _c("status", "Status"), _c("grn_received", "GRN received", BOOL),
        _c("invoice_matched", "Invoice matched", BOOL), _c("created_by", "Created by"),
        _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("proc_grn", "proc_grn", "Goods Received Notes", "Procurement", (
        _c("po_id", "PO ID"), _c("received_date", "Received date", DATE),
        _c("received_by", "Received by"), _c("status", "Status"), _c("created_at", "Created", DATETIME),
    ), "received_date"),
    Source("proc_invoices", "proc_invoices", "Supplier Invoices", "Procurement", (
        _c("po_id", "PO ID"), _c("invoice_number", "Invoice no."),
        _c("invoice_date", "Invoice date", DATE), _c("amount", "Amount", NUMBER),
        _c("status", "Status"), _c("created_at", "Created", DATETIME),
    ), "invoice_date"),
    Source("proc_tenders", "proc_tenders", "Procurement Tenders", "Procurement", (
        _c("title", "Title"), _c("rfq_deadline", "RFQ deadline", DATETIME), _c("status", "Status"),
        _c("awarded_to", "Awarded to"), _c("created_by", "Created by"), _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("proc_plans", "proc_plans", "Procurement Plans", "Procurement", (
        _c("fiscal_year", "Fiscal year", NUMBER), _c("department", "Department"), _c("title", "Title"),
        _c("estimated_amount", "Estimated amount", NUMBER), _c("planned_quarter", "Quarter", NUMBER),
        _c("status", "Status"), _c("submitted_by", "Submitted by"), _c("approved_by", "Approved by"),
        _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("pm_projects", "pm_projects", "Projects", "Projects", (
        _c("name", "Name"), _c("classification", "Classification"), _c("status", "Status"),
        _c("start_date", "Start", DATE), _c("end_date", "End", DATE),
        _c("total_budget", "Total budget", NUMBER), _c("material_costs", "Material costs", NUMBER),
        _c("consultant_fees", "Consultant fees", NUMBER), _c("internal_labor", "Internal labour", NUMBER),
        _c("created_by", "Created by"), _c("created_at", "Created", DATETIME),
    ), "start_date"),
    Source("pm_contractors", "pm_contractors", "Contractors & Consultants", "Projects", (
        _c("name", "Name"), _c("type", "Type"), _c("specialty", "Specialty"), _c("tin", "TIN"),
        _c("rating", "Rating", NUMBER), _c("is_active", "Active", BOOL), _c("created_at", "Created", DATETIME),
    ), "created_at"),
    Source("pm_payments", "pm_payments", "Project Payments", "Projects", (
        _c("project_id", "Project ID"), _c("payment_date", "Payment date", DATE),
        _c("amount", "Amount", NUMBER), _c("payee", "Payee"), _c("payment_type", "Payment type"),
        _c("reference", "Reference"), _c("created_at", "Created", DATETIME),
    ), "payment_date"),
    Source("contracts", "contracts", "Contracts", "Contracts", (
        _c("title", "Title"), _c("party_type", "Party type"), _c("party_name", "Party"),
        _c("party_reference", "Party reference"), _c("contract_type", "Contract type"),
        _c("value", "Value", NUMBER), _c("currency", "Currency"),
        _c("start_date", "Start", DATE), _c("end_date", "End", DATE), _c("status", "Status"),
        _c("created_by", "Created by"), _c("created_at", "Created", DATETIME),
    ), "start_date"),
]

SOURCES: Dict[str, Source] = {s.key: s for s in _SOURCE_LIST}


def _check_catalogue():
    """Catalogue self-check at import: every identifier must be a plain snake_case name."""
    for s in _SOURCE_LIST:
        for ident in (s.key, s.table, s.company_column):
            if not _IDENT_RE.match(ident):
                raise RuntimeError(f"bad catalogue identifier {ident!r}")
        names = set()
        for c in s.columns:
            if not _IDENT_RE.match(c.name) or c.name in names:
                raise RuntimeError(f"bad/duplicate catalogue column {s.key}.{c.name}")
            names.add(c.name)
        if s.date_column and (s.col(s.date_column) is None or s.col(s.date_column).type not in DATE_LIKE):
            raise RuntimeError(f"bad date_column for {s.key}")


_check_catalogue()


# ── Date presets ────────────────────────────────────────────────────

DATE_PRESETS: List[Tuple[str, str]] = [
    ("all", "All time"),
    ("today", "Today"),
    ("yesterday", "Yesterday"),
    ("this_week", "This week (Mon–Sun)"),
    ("last_week", "Last week"),
    ("this_month", "This month"),
    ("last_month", "Last month"),
    ("this_quarter", "This quarter"),
    ("this_year", "This calendar year"),
    ("last_year", "Last calendar year"),
    ("fiscal_year", "Ethiopian fiscal year (8 Jul – 7 Jul)"),
    ("last_fiscal_year", "Last Ethiopian fiscal year"),
    ("last_7_days", "Last 7 days"),
    ("last_30_days", "Last 30 days"),
    ("last_90_days", "Last 90 days"),
    ("last_365_days", "Last 365 days"),
]
_LAST_N_RE = re.compile(r"^last_(\d{1,4})_days$")


def _fiscal_year_start(d: date) -> date:
    """Ethiopian fiscal year runs 8 July – 7 July (Hamle 1 – Sene 30)."""
    start = date(d.year, 7, 8)
    return start if d >= start else date(d.year - 1, 7, 8)


def date_range(preset: Optional[str], today: Optional[date] = None) -> Optional[Tuple[date, date]]:
    """Inclusive (start, end) for a preset, or None for 'all'/blank. Raises CatalogError on unknown."""
    today = today or date.today()
    p = (preset or "all").strip().lower()
    if p in ("", "all", "none"):
        return None
    if p == "today":
        return today, today
    if p == "yesterday":
        y = today - timedelta(days=1)
        return y, y
    if p == "this_week":
        start = today - timedelta(days=today.weekday())
        return start, start + timedelta(days=6)
    if p == "last_week":
        start = today - timedelta(days=today.weekday() + 7)
        return start, start + timedelta(days=6)
    if p == "this_month":
        return today.replace(day=1), today.replace(day=calendar.monthrange(today.year, today.month)[1])
    if p == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return end.replace(day=1), end
    if p == "this_quarter":
        qm = 3 * ((today.month - 1) // 3) + 1
        start = date(today.year, qm, 1)
        end_month = qm + 2
        return start, date(today.year, end_month, calendar.monthrange(today.year, end_month)[1])
    if p == "this_year":
        return date(today.year, 1, 1), date(today.year, 12, 31)
    if p == "last_year":
        return date(today.year - 1, 1, 1), date(today.year - 1, 12, 31)
    if p == "fiscal_year":
        start = _fiscal_year_start(today)
        return start, date(start.year + 1, 7, 7)
    if p == "last_fiscal_year":
        start = _fiscal_year_start(today)
        return date(start.year - 1, 7, 8), start - timedelta(days=1)
    m = _LAST_N_RE.match(p)
    if m:
        n = int(m.group(1))
        if n < 1 or n > 3660:
            raise CatalogError("last_N_days: N must be 1..3660")
        return today - timedelta(days=n - 1), today
    raise CatalogError(f"unknown date preset {preset!r}")


def preset_label(preset: Optional[str]) -> str:
    p = (preset or "all").strip().lower()
    for k, lbl in DATE_PRESETS:
        if k == p:
            return lbl
    m = _LAST_N_RE.match(p)
    return f"Last {m.group(1)} days" if m else p


# ── Definition validation ───────────────────────────────────────────

def _as_list(v) -> list:
    if v is None or v == "":
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return [v]


def _parse_group(entry) -> Tuple[str, Optional[str]]:
    """'col' or 'col:month' or {'column':..,'granularity':..} → (col, granularity|None)."""
    if isinstance(entry, dict):
        col, gran = entry.get("column", ""), entry.get("granularity") or None
    else:
        s = str(entry)
        col, _, gran = s.partition(":")
        gran = gran or None
    col = (col or "").strip()
    if gran is not None:
        gran = str(gran).strip().lower()
        if gran not in GRANULARITIES:
            raise CatalogError(f"unknown granularity {gran!r}")
    return col, gran


def _require_col(source: Source, name: str, what: str) -> Col:
    col = source.col((name or "").strip())
    if col is None:
        raise CatalogError(f"{what}: unknown column {name!r} for source {source.key!r}")
    return col


def validate_definition(defn: dict) -> dict:
    """Return a normalised copy of a report definition; raise CatalogError when invalid.

    Normalised shape:
      source_key, columns[str], filters[{column, op, value}], group_by[str 'col' | 'col:gran'],
      aggregates[{column, fn}], sort[{column, dir}], date_column|None, date_preset, limit, chart|None
    """
    if not isinstance(defn, dict):
        raise CatalogError("definition must be an object")
    source_key = str(defn.get("source_key") or "").strip()
    source = SOURCES.get(source_key)
    if source is None:
        raise CatalogError(f"unknown source {source_key!r}")

    out: dict = {"source_key": source_key}
    out["name"] = str(defn.get("name") or "").strip()[:200]
    out["description"] = str(defn.get("description") or "").strip()[:2000]

    cols = []
    for name in _as_list(defn.get("columns")):
        _require_col(source, str(name), "columns")
        if name not in cols:
            cols.append(str(name).strip())
    out["columns"] = cols

    filters = []
    for f in _as_list(defn.get("filters")):
        if not isinstance(f, dict):
            raise CatalogError("filters: each filter must be an object")
        col = _require_col(source, str(f.get("column", "")), "filters")
        op = str(f.get("op") or "").strip().lower()
        if op not in OPERATORS:
            raise CatalogError(f"filters: unknown operator {op!r}")
        value = f.get("value")
        if op == "between":
            vals = _as_list(value)
            if len(vals) != 2:
                raise CatalogError("filters: 'between' needs exactly two values")
            value = [_coerce(col, vals[0]), _coerce(col, vals[1])]
        elif op == "in":
            vals = [_coerce(col, v) for v in _as_list(value)]
            if not vals:
                raise CatalogError("filters: 'in' needs at least one value")
            value = vals
        elif op == "is_null":
            value = _truthy(value)
        elif op == "contains":
            value = "" if value is None else str(value)
        else:
            if value in (None, "") and col.type != TEXT:
                raise CatalogError(f"filters: value required for {op} on {col.name}")
            value = _coerce(col, value)
        filters.append({"column": col.name, "op": op, "value": value})
    out["filters"] = filters

    groups = []
    for g in _as_list(defn.get("group_by")):
        col_name, gran = _parse_group(g)
        col = _require_col(source, col_name, "group_by")
        if gran and col.type not in DATE_LIKE:
            raise CatalogError(f"group_by: granularity only applies to date columns ({col.name})")
        key = f"{col.name}:{gran}" if gran else col.name
        if key not in groups:
            groups.append(key)
    out["group_by"] = groups

    aggs = []
    for a in _as_list(defn.get("aggregates")):
        if not isinstance(a, dict):
            raise CatalogError("aggregates: each aggregate must be an object")
        fn = str(a.get("fn") or "").strip().lower()
        if fn not in AGG_FUNCS:
            raise CatalogError(f"aggregates: unknown function {fn!r}")
        col_name = str(a.get("column") or "*").strip()
        if col_name in ("*", ""):
            if fn != "count":
                raise CatalogError("aggregates: only count() may use '*'")
            col_name = "*"
        else:
            col = _require_col(source, col_name, "aggregates")
            if fn in ("sum", "avg") and col.type != NUMBER:
                raise CatalogError(f"aggregates: {fn}() requires a numeric column ({col.name})")
        entry = {"column": col_name, "fn": fn}
        if entry not in aggs:
            aggs.append(entry)
    out["aggregates"] = aggs
    if aggs and not groups:
        raise CatalogError("aggregates require at least one group_by column")

    out_keys = _output_keys(source, out)
    sort = []
    for s in _as_list(defn.get("sort")):
        if isinstance(s, dict):
            col_name, direction = str(s.get("column") or ""), str(s.get("dir") or "asc")
        else:
            col_name, _, direction = str(s).partition(":")
            direction = direction or "asc"
        col_name, direction = col_name.strip(), direction.strip().lower()
        if direction not in ("asc", "desc"):
            raise CatalogError(f"sort: direction must be asc|desc, got {direction!r}")
        if col_name not in out_keys:
            # plain (non-grouped) reports may sort on any catalogue column
            if groups or source.col(col_name) is None:
                raise CatalogError(f"sort: {col_name!r} is not an output column")
        sort.append({"column": col_name, "dir": direction})
    out["sort"] = sort

    date_column = (defn.get("date_column") or "").strip() or None
    if date_column:
        dc = _require_col(source, date_column, "date_column")
        if dc.type not in DATE_LIKE:
            raise CatalogError(f"date_column: {dc.name} is not a date column")
    out["date_column"] = date_column or source.date_column

    preset = (defn.get("date_preset") or "all").strip().lower()
    date_range(preset, date(2026, 1, 1))  # validates
    out["date_preset"] = preset

    try:
        limit = int(defn.get("limit") or DEFAULT_LIMIT)
    except (TypeError, ValueError):
        raise CatalogError("limit must be an integer")
    out["limit"] = max(1, min(limit, MAX_LIMIT))

    chart = defn.get("chart")
    if chart and isinstance(chart, dict) and chart.get("type"):
        ctype = str(chart.get("type")).strip().lower()
        if ctype not in CHART_TYPES:
            raise CatalogError(f"chart: unknown type {ctype!r}")
        x, y = str(chart.get("x") or "").strip(), str(chart.get("y") or "").strip()
        if x not in out_keys or y not in out_keys:
            raise CatalogError("chart: x and y must be output columns")
        out["chart"] = {"type": ctype, "x": x, "y": y}
    else:
        out["chart"] = None
    return out


def _truthy(v) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("1", "true", "yes", "y", "on")


def _coerce(col: Col, value):
    """Coerce a filter value to a sane Python type for its column."""
    if value is None:
        return None
    if col.type == NUMBER:
        try:
            return float(value)
        except (TypeError, ValueError):
            raise CatalogError(f"filters: {col.name} needs a numeric value, got {value!r}")
    if col.type == BOOL:
        return _truthy(value)
    if col.type in DATE_LIKE:
        s = str(value).strip()
        try:
            date.fromisoformat(s[:10])
        except ValueError:
            raise CatalogError(f"filters: {col.name} needs an ISO date (YYYY-MM-DD), got {value!r}")
        return s[:10] if col.type != DATETIME else s
    return str(value)


# ── Query compilation ───────────────────────────────────────────────

@dataclass
class OutCol:
    key: str          # SQL alias / row dict key
    label: str
    type: str
    source_col: Optional[str] = None
    agg: Optional[str] = None


@dataclass
class CompiledQuery:
    sql: str
    params: List
    columns: List[OutCol]
    date_range: Optional[Tuple[date, date]] = None
    dropped: List[str] = field(default_factory=list)   # identifiers dropped because missing at runtime
    filters_summary: str = ""


def _q(ident: str) -> str:
    if not _IDENT_RE.match(ident):
        raise CatalogError(f"invalid identifier {ident!r}")
    return '"' + ident + '"'


def _date_expr(col: Col) -> str:
    """SQL expression yielding a DATE for date-like columns (TEXT dates are parsed defensively)."""
    if col.type == DATETEXT:
        return (f"(CASE WHEN {_q(col.name)} ~ '^[0-9]{{4}}-[0-9]{{2}}-[0-9]{{2}}' "
                f"THEN substr({_q(col.name)}, 1, 10)::date ELSE NULL END)")
    if col.type == DATETIME:
        return f"({_q(col.name)}::date)"
    return _q(col.name)


def _col_expr(col: Col) -> str:
    return col.expr if col.expr else _q(col.name)


_GRAN_FMT = {"day": "YYYY-MM-DD", "month": "YYYY-MM", "quarter": 'YYYY-"Q"Q', "year": "YYYY"}
_GRAN_LABEL = {"day": "Day", "month": "Month", "quarter": "Quarter", "year": "Year"}


def _group_expr(col: Col, gran: Optional[str]) -> str:
    if not gran:
        return _col_expr(col)
    return f"to_char(date_trunc('{gran}', {_date_expr(col)}), '{_GRAN_FMT[gran]}')"


def _output_keys(source: Source, d: dict) -> Set[str]:
    keys: Set[str] = set()
    if d.get("group_by"):
        for g in d["group_by"]:
            col, gran = _parse_group(g)
            keys.add(f"{col}__{gran}" if gran else col)
        for a in d.get("aggregates") or []:
            keys.add(_agg_key(a))
        if not d.get("aggregates"):
            keys.add("count__all")
    else:
        keys.update(d.get("columns") or [c.name for c in source.columns])
    return keys


def _agg_key(a: dict) -> str:
    return f"{a['fn']}__{'all' if a['column'] == '*' else a['column']}"


def _col_available(col: Col, available: Optional[Set[str]]) -> bool:
    if available is None:
        return True
    need = col.requires if col.expr else (col.name,)
    return all(n in available for n in need)


def compile_query(defn: dict, company_id: str, today: Optional[date] = None,
                  available: Optional[Set[str]] = None, limit: Optional[int] = None) -> CompiledQuery:
    """Compile a (validated or raw) definition into parameterised SQL.

    `available` – the physical columns that exist at runtime (information_schema);
    catalogue columns missing from it are dropped and listed in `dropped`.
    """
    d = validate_definition(defn)
    source = SOURCES[d["source_key"]]
    if available is not None and not available:
        raise CatalogError(f"source table {source.table!r} is not available in this database")
    if available is not None and source.company_column not in available:
        raise CatalogError(f"source table {source.table!r} has no {source.company_column} column")

    dropped: List[str] = []
    params: List = []
    select: List[str] = []
    out_cols: List[OutCol] = []
    group_sql: List[str] = []

    if d["group_by"]:
        for g in d["group_by"]:
            name, gran = _parse_group(g)
            col = source.col(name)
            if not _col_available(col, available):
                dropped.append(name); continue
            key = f"{name}__{gran}" if gran else name
            expr = _group_expr(col, gran)
            select.append(f"{expr} AS {_q(key)}")
            group_sql.append(expr)
            out_cols.append(OutCol(key, f"{_GRAN_LABEL[gran]} of {col.label}" if gran else col.label,
                                   TEXT if gran else col.type, name))
        if not group_sql:
            raise CatalogError("none of the group_by columns exist in this database")
        aggs = d["aggregates"] or [{"column": "*", "fn": "count"}]
        for a in aggs:
            key = _agg_key(a)
            if a["column"] == "*":
                select.append(f"COUNT(*) AS {_q(key)}")
                out_cols.append(OutCol(key, "Count", NUMBER, None, "count"))
                continue
            col = source.col(a["column"])
            if not _col_available(col, available):
                dropped.append(a["column"]); continue
            fn = a["fn"].upper()
            select.append(f"{fn}({_col_expr(col)}) AS {_q(key)}")
            out_type = NUMBER if a["fn"] in ("sum", "avg", "count") else col.type
            out_cols.append(OutCol(key, f"{a['fn'].capitalize()} of {col.label}",
                                   out_type, col.name, a["fn"]))
    else:
        names = d["columns"] or [c.name for c in source.columns]
        for name in names:
            col = source.col(name)
            if not _col_available(col, available):
                dropped.append(name); continue
            select.append(f"{_col_expr(col)} AS {_q(col.name)}")
            out_cols.append(OutCol(col.name, col.label, col.type, col.name))
        if not select:
            raise CatalogError("none of the selected columns exist in this database")

    where = [f"{_q(source.company_column)} = %s"]
    params.append(company_id)
    summary_parts: List[str] = []

    rng = None
    if d["date_column"] and d["date_preset"] not in ("all", ""):
        dc = source.col(d["date_column"])
        if _col_available(dc, available):
            rng = date_range(d["date_preset"], today)
            if rng:
                start, end = rng
                if dc.type == DATETIME:
                    where.append(f"{_q(dc.name)} >= %s AND {_q(dc.name)} < %s")
                    params.extend([start.isoformat(), (end + timedelta(days=1)).isoformat()])
                else:
                    where.append(f"{_date_expr(dc)} BETWEEN %s AND %s")
                    params.extend([start.isoformat(), end.isoformat()])
                summary_parts.append(f"{dc.label}: {preset_label(d['date_preset'])} ({start} – {end})")
        else:
            dropped.append(d["date_column"])

    for f in d["filters"]:
        col = source.col(f["column"])
        if not _col_available(col, available):
            dropped.append(f["column"]); continue
        clause, vals, human = _filter_sql(col, f["op"], f["value"])
        where.append(clause)
        params.extend(vals)
        summary_parts.append(human)

    sql = f"SELECT {', '.join(select)} FROM {_q(source.table)} WHERE {' AND '.join(where)}"
    if group_sql:
        sql += " GROUP BY " + ", ".join(group_sql)

    out_keys = {c.key for c in out_cols}
    order = []
    for s in d["sort"]:
        if s["column"] in out_keys:
            order.append(f"{_q(s['column'])} {s['dir'].upper()}")
        elif not d["group_by"] and source.col(s["column"]) is not None:
            col = source.col(s["column"])
            if _col_available(col, available):
                order.append(f"{_col_expr(col)} {s['dir'].upper()}")
    if order:
        sql += " ORDER BY " + ", ".join(order)
    elif group_sql:
        sql += " ORDER BY 1"

    eff_limit = d["limit"] if limit is None else max(1, min(int(limit), MAX_LIMIT))
    sql += " LIMIT %s"
    params.append(eff_limit)

    return CompiledQuery(sql=sql, params=params, columns=out_cols, date_range=rng,
                         dropped=dropped, filters_summary="; ".join(summary_parts))


_OP_SQL = {"eq": "=", "ne": "<>", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}
_OP_HUMAN = {"eq": "=", "ne": "≠", "gt": ">", "gte": "≥", "lt": "<", "lte": "≤"}


def _filter_sql(col: Col, op: str, value) -> Tuple[str, list, str]:
    lhs = _date_expr(col) if col.type in (DATETEXT,) else _col_expr(col)
    if col.type == DATETIME and op in _OP_SQL and isinstance(value, str) and len(value) == 10:
        lhs = _date_expr(col)
    if op in _OP_SQL:
        return f"{lhs} {_OP_SQL[op]} %s", [value], f"{col.label} {_OP_HUMAN[op]} {value}"
    if op == "contains":
        return f"{_col_expr(col)}::text ILIKE %s", [f"%{value}%"], f"{col.label} contains '{value}'"
    if op == "in":
        ph = ", ".join(["%s"] * len(value))
        return f"{lhs} IN ({ph})", list(value), f"{col.label} in ({', '.join(str(v) for v in value)})"
    if op == "between":
        return f"{lhs} BETWEEN %s AND %s", [value[0], value[1]], f"{col.label} between {value[0]} and {value[1]}"
    if op == "is_null":
        if value:
            return f"({_col_expr(col)} IS NULL)", [], f"{col.label} is empty"
        return f"({_col_expr(col)} IS NOT NULL)", [], f"{col.label} is not empty"
    raise CatalogError(f"unknown operator {op!r}")


# ── Catalogue as JSON ───────────────────────────────────────────────

def source_json(source: Source, available: Optional[Set[str]] = None) -> dict:
    cols = [c for c in source.columns if _col_available(c, available)]
    return {
        "key": source.key, "label": source.label, "module": source.module, "table": source.table,
        "date_column": source.date_column if (source.date_column and
                                              _col_available(source.col(source.date_column), available)) else None,
        "available": available is None or bool(available),
        "columns": [{"name": c.name, "label": c.label, "type": c.type} for c in cols],
    }


def catalog_json(available_fn=None) -> dict:
    """Whole catalogue. `available_fn(table) -> set|None` supplies runtime columns."""
    sources = []
    for s in _SOURCE_LIST:
        avail = available_fn(s.table) if available_fn else None
        sources.append(source_json(s, avail))
    return {
        "sources": sources,
        "operators": list(OPERATORS),
        "aggregates": list(AGG_FUNCS),
        "granularities": list(GRANULARITIES),
        "date_presets": [{"key": k, "label": v} for k, v in DATE_PRESETS],
        "chart_types": list(CHART_TYPES),
        "max_limit": MAX_LIMIT,
        "default_limit": DEFAULT_LIMIT,
    }


# ── Prebuilt templates (seeded lazily per company) ──────────────────

PREBUILT_TEMPLATES: List[dict] = [
    {"name": "Income by month", "description": "VAT income totals per month for the current Ethiopian fiscal year.",
     "source_key": "vat_income", "group_by": ["income_date:month"],
     "aggregates": [{"column": "gross_amount", "fn": "sum"}, {"column": "vat_amount", "fn": "sum"},
                    {"column": "net_amount", "fn": "sum"}, {"column": "*", "fn": "count"}],
     "sort": [{"column": "income_date__month", "dir": "asc"}],
     "date_column": "income_date", "date_preset": "fiscal_year",
     "chart": {"type": "bar", "x": "income_date__month", "y": "sum__gross_amount"}},
    {"name": "Expenses by category", "description": "VAT expense totals grouped by category, current fiscal year.",
     "source_key": "vat_expenses", "group_by": ["category"],
     "aggregates": [{"column": "gross_amount", "fn": "sum"}, {"column": "vat_amount", "fn": "sum"},
                    {"column": "*", "fn": "count"}],
     "sort": [{"column": "sum__gross_amount", "dir": "desc"}],
     "date_column": "expense_date", "date_preset": "fiscal_year",
     "chart": {"type": "pie", "x": "category", "y": "sum__gross_amount"}},
    {"name": "VAT summary by month", "description": "Output VAT collected on income per month (current fiscal year).",
     "source_key": "vat_income", "group_by": ["income_date:month", "vat_type"],
     "aggregates": [{"column": "net_amount", "fn": "sum"}, {"column": "vat_amount", "fn": "sum"},
                    {"column": "gross_amount", "fn": "sum"}],
     "sort": [{"column": "income_date__month", "dir": "asc"}],
     "date_column": "income_date", "date_preset": "fiscal_year",
     "chart": {"type": "line", "x": "income_date__month", "y": "sum__vat_amount"}},
    {"name": "Input VAT by month", "description": "Input VAT paid on expenses per month (current fiscal year).",
     "source_key": "vat_expenses", "group_by": ["expense_date:month"],
     "aggregates": [{"column": "net_amount", "fn": "sum"}, {"column": "vat_amount", "fn": "sum"}],
     "sort": [{"column": "expense_date__month", "dir": "asc"}],
     "date_column": "expense_date", "date_preset": "fiscal_year",
     "chart": {"type": "line", "x": "expense_date__month", "y": "sum__vat_amount"}},
    {"name": "Bids by status", "description": "Number and value of bids per status.",
     "source_key": "bid_records", "group_by": ["status"],
     "aggregates": [{"column": "*", "fn": "count"}, {"column": "bid_amount", "fn": "sum"}],
     "sort": [{"column": "count__all", "dir": "desc"}], "date_preset": "all",
     "chart": {"type": "pie", "x": "status", "y": "count__all"}},
    {"name": "Payroll cost by department", "description": "Active headcount and monthly basic salary per department.",
     "source_key": "employees", "group_by": ["department"],
     "aggregates": [{"column": "*", "fn": "count"}, {"column": "basic_salary", "fn": "sum"},
                    {"column": "basic_salary", "fn": "avg"}],
     "filters": [{"column": "is_active", "op": "eq", "value": True}],
     "sort": [{"column": "sum__basic_salary", "dir": "desc"}], "date_preset": "all",
     "chart": {"type": "bar", "x": "department", "y": "sum__basic_salary"}},
    {"name": "Inventory stock valuation", "description": "Stock on hand valued at cost price.",
     "source_key": "inventory_items",
     "columns": ["sku", "name", "category", "location", "current_stock", "cost_price", "stock_value", "unit_price"],
     "filters": [{"column": "status", "op": "eq", "value": "active"}],
     "sort": [{"column": "stock_value", "dir": "desc"}], "date_preset": "all"},
    {"name": "Outstanding CPOs", "description": "CPOs that have not yet been returned.",
     "source_key": "cpo_records", "columns": ["date", "name", "bid_name", "amount", "is_returned"],
     "filters": [{"column": "is_returned", "op": "ne", "value": "true"}],
     "sort": [{"column": "date", "dir": "desc"}], "date_preset": "all"},
]


def template_fits(tpl: dict, available: Optional[Set[str]]) -> bool:
    """True when every column a prebuilt template touches exists at runtime."""
    source = SOURCES.get(tpl["source_key"])
    if source is None:
        return False
    if available is None:
        return True
    if not available or source.company_column not in available:
        return False
    names: List[str] = list(tpl.get("columns") or [])
    names += [_parse_group(g)[0] for g in tpl.get("group_by") or []]
    names += [a["column"] for a in tpl.get("aggregates") or [] if a["column"] != "*"]
    names += [f["column"] for f in tpl.get("filters") or []]
    if tpl.get("date_column"):
        names.append(tpl["date_column"])
    for n in names:
        col = source.col(n)
        if col is None or not _col_available(col, available):
            return False
    return True
