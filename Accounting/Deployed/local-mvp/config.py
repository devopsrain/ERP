"""
Configuration settings for Ethiopian Business Management System Local MVP
"""

import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
REPORTS_DIR = BASE_DIR / "reports"
EXPORTS_DIR = BASE_DIR / "exports"
LOGS_DIR = BASE_DIR / "logs"

# Ensure directories exist
for dir_path in [DATA_DIR, REPORTS_DIR, EXPORTS_DIR, LOGS_DIR]:
    dir_path.mkdir(exist_ok=True)

# Parquet file paths
PARQUET_FILES = {
    'accounts': DATA_DIR / 'accounts.parquet',
    'journal_entries': DATA_DIR / 'journal_entries.parquet',
    'vat_income': DATA_DIR / 'vat_income.parquet',
    'vat_expenses': DATA_DIR / 'vat_expenses.parquet',
    'vat_capital': DATA_DIR / 'vat_capital.parquet',
    'employees': DATA_DIR / 'employees.parquet',
    'payroll_records': DATA_DIR / 'payroll_records.parquet',
    'companies': DATA_DIR / 'companies.parquet',
    'audit_log': DATA_DIR / 'audit_log.parquet'
}

# Ethiopian VAT Rates
VAT_RATES = {
    'standard': 0.15,      # 15% standard VAT
    'zero_rated': 0.0,     # 0% for exports, basic necessities
    'exempt': None,        # VAT exempt items
    'withholding': 0.02    # 2% withholding VAT
}

# Ethiopian Progressive Tax Brackets (2026)
PROGRESSIVE_TAX = [
    {'min': 0, 'max': 600, 'rate': 0.0, 'deduction': 0},
    {'min': 601, 'max': 1650, 'rate': 0.10, 'deduction': 60},
    {'min': 1651, 'max': 3200, 'rate': 0.15, 'deduction': 142.5},
    {'min': 3201, 'max': 5250, 'rate': 0.20, 'deduction': 302.5},
    {'min': 5251, 'max': 7800, 'rate': 0.25, 'deduction': 565},
    {'min': 7801, 'max': 10900, 'rate': 0.30, 'deduction': 955},
    {'min': 10901, 'max': float('inf'), 'rate': 0.35, 'deduction': 1500}
]

# Pension rates
EMPLOYEE_PENSION_RATE = 0.07  # 7%
EMPLOYER_PENSION_RATE = 0.11  # 11%

# Default company settings
DEFAULT_COMPANY = {
    'id': 'default_company',
    'name': 'Ethiopian Business Demo',
    'tin_number': 'ETH123456789',
    'vat_number': 'VAT987654321',
    'address': 'Addis Ababa, Ethiopia',
    'currency': 'ETB',
    'fiscal_year_start': '01-01',  # January 1st
    'created_date': '2026-01-01'
}

# Account types and their normal balances
ACCOUNT_TYPES = {
    'Asset': {'normal_balance': 'debit', 'code_range': (1000, 1999)},
    'Liability': {'normal_balance': 'credit', 'code_range': (2000, 2999)},
    'Equity': {'normal_balance': 'credit', 'code_range': (3000, 3999)},
    'Revenue': {'normal_balance': 'credit', 'code_range': (4000, 4999)},
    'Expense': {'normal_balance': 'debit', 'code_range': (5000, 6999)}
}

# Standard Chart of Accounts
STANDARD_ACCOUNTS = [
    {'code': '1000', 'name': 'Cash', 'type': 'Asset', 'description': 'Cash on hand and in bank'},
    {'code': '1100', 'name': 'Accounts Receivable', 'type': 'Asset', 'description': 'Money owed by customers'},
    {'code': '1200', 'name': 'Inventory', 'type': 'Asset', 'description': 'Goods for resale'},
    {'code': '1300', 'name': 'Prepaid Expenses', 'type': 'Asset', 'description': 'Expenses paid in advance'},
    {'code': '1500', 'name': 'Equipment', 'type': 'Asset', 'description': 'Business equipment'},
    {'code': '1600', 'name': 'Accumulated Depreciation', 'type': 'Asset', 'description': 'Equipment depreciation'},
    
    {'code': '2000', 'name': 'Accounts Payable', 'type': 'Liability', 'description': 'Money owed to suppliers'},
    {'code': '2100', 'name': 'Accrued Expenses', 'type': 'Liability', 'description': 'Expenses incurred but not paid'},
    {'code': '2200', 'name': 'VAT Payable', 'type': 'Liability', 'description': 'VAT owed to government'},
    {'code': '2300', 'name': 'Income Tax Payable', 'type': 'Liability', 'description': 'Income tax owed'},
    {'code': '2400', 'name': 'Pension Payable', 'type': 'Liability', 'description': 'Pension contributions payable'},
    
    {'code': '3000', 'name': "Owner's Equity", 'type': 'Equity', 'description': "Owner's investment in business"},
    {'code': '3100', 'name': 'Retained Earnings', 'type': 'Equity', 'description': 'Accumulated profits'},
    
    {'code': '4000', 'name': 'Sales Revenue', 'type': 'Revenue', 'description': 'Revenue from sales'},
    {'code': '4100', 'name': 'Service Revenue', 'type': 'Revenue', 'description': 'Revenue from services'},
    {'code': '4200', 'name': 'Other Income', 'type': 'Revenue', 'description': 'Miscellaneous income'},
    
    {'code': '5000', 'name': 'Cost of Goods Sold', 'type': 'Expense', 'description': 'Direct cost of products sold'},
    {'code': '6000', 'name': 'Salaries Expense', 'type': 'Expense', 'description': 'Employee salaries'},
    {'code': '6100', 'name': 'Rent Expense', 'type': 'Expense', 'description': 'Office rent'},
    {'code': '6200', 'name': 'Utilities Expense', 'type': 'Expense', 'description': 'Electricity, water, etc'},
    {'code': '6300', 'name': 'Office Supplies', 'type': 'Expense', 'description': 'Office supplies and materials'},
    {'code': '6400', 'name': 'Marketing Expense', 'type': 'Expense', 'description': 'Marketing and advertising'},
    {'code': '6500', 'name': 'Professional Fees', 'type': 'Expense', 'description': 'Legal, accounting fees'},
    {'code': '6600', 'name': 'Insurance Expense', 'type': 'Expense', 'description': 'Business insurance'},
    {'code': '6700', 'name': 'Depreciation Expense', 'type': 'Expense', 'description': 'Equipment depreciation'}
]

# Flask configuration
class Config:
    SECRET_KEY = 'ethiopian-business-mvp-local-secret'
    DEBUG = True
    WTF_CSRF_ENABLED = True
    WTF_CSRF_TIME_LIMIT = None