"""
Business Logic Layer for Ethiopian Business Management System
Handles accounting, VAT, and payroll calculations using Parquet storage
"""

import pandas as pd
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Tuple
from decimal import Decimal, ROUND_HALF_UP
from data_store import data_store
from config import (
    VAT_RATES, PROGRESSIVE_TAX, EMPLOYEE_PENSION_RATE, 
    EMPLOYER_PENSION_RATE, STANDARD_ACCOUNTS, DEFAULT_COMPANY
)

class EthiopianBusinessManager:
    """Main business logic manager for Ethiopian business operations"""
    
    def __init__(self, company_id: str = 'default_company'):
        self.company_id = company_id
        self.accounting = AccountingManager(company_id)
        self.vat = VATManager(company_id)
        self.payroll = PayrollManager(company_id)
    
    def initialize_system(self) -> Dict[str, Any]:
        """Initialize the complete business management system"""
        results = {}
        
        # Create default company if it doesn't exist
        company = data_store.get_record('companies', self.company_id)
        if not company:
            company_data = DEFAULT_COMPANY.copy()
            company_data['id'] = self.company_id
            company_data['is_active'] = True
            # Convert string date to datetime for Parquet storage
            if isinstance(company_data.get('created_date'), str):
                company_data['created_date'] = pd.to_datetime(company_data['created_date'])
            else:
                company_data['created_date'] = datetime.now()
            data_store.insert_record('companies', company_data)
            results['company'] = 'created'
        else:
            results['company'] = 'exists'
        
        # Initialize chart of accounts
        results['accounts'] = self.accounting.setup_standard_accounts()
        
        # Initialize sample data
        results['sample_data'] = self._create_sample_data()
        
        return results
    
    def _create_sample_data(self) -> Dict[str, int]:
        """Create sample data for demonstration"""
        results = {'employees': 0, 'vat_records': 0, 'transactions': 0}
        
        # Sample employees
        sample_employees = [
            {
                'employee_number': 'EMP001',
                'first_name': 'Abebe',
                'last_name': 'Kebede',
                'position': 'Manager',
                'department': 'Administration',
                'hire_date': date(2024, 1, 15),
                'basic_salary': 8000.0,
                'allowances': 2000.0,
                'tax_exemption': 600.0,
                'is_active': True,
                'company_id': self.company_id
            },
            {
                'employee_number': 'EMP002',
                'first_name': 'Almaz',
                'last_name': 'Tadesse',
                'position': 'Accountant',
                'department': 'Finance',
                'hire_date': date(2024, 2, 1),
                'basic_salary': 6000.0,
                'allowances': 1500.0,
                'tax_exemption': 600.0,
                'is_active': True,
                'company_id': self.company_id
            },
            {
                'employee_number': 'EMP003',
                'first_name': 'Dawit',
                'last_name': 'Haile',
                'position': 'Sales Representative',
                'department': 'Sales',
                'hire_date': date(2024, 3, 1),
                'basic_salary': 4500.0,
                'allowances': 1000.0,
                'tax_exemption': 600.0,
                'is_active': True,
                'company_id': self.company_id
            }
        ]
        
        # Insert employees if they don't exist
        existing_employees = data_store.query('employees', company_id=self.company_id)
        if existing_employees.empty:
            for emp in sample_employees:
                data_store.insert_record('employees', emp)
                results['employees'] += 1
        
        # Sample VAT records
        sample_vat_income = [
            {
                'date': date(2026, 2, 1),
                'contract_date': date(2026, 1, 25),
                'description': 'Software Development Services',
                'customer_name': 'ABC Trading PLC',
                'invoice_number': 'INV-2026-001',
                'gross_amount': 50000.0,
                'vat_rate': VAT_RATES['standard'],
                'company_id': self.company_id
            },
            {
                'date': date(2026, 2, 5),
                'contract_date': date(2026, 2, 1),
                'description': 'Consulting Services',
                'customer_name': 'XYZ Manufacturing SC',
                'invoice_number': 'INV-2026-002',
                'gross_amount': 25000.0,
                'vat_rate': VAT_RATES['standard'],
                'company_id': self.company_id
            }
        ]
        
        # Insert VAT income if none exists
        existing_vat = data_store.query('vat_income', company_id=self.company_id)
        if existing_vat.empty:
            for vat_rec in sample_vat_income:
                self.vat.add_income_record(
                    vat_rec['date'],
                    vat_rec['contract_date'],
                    vat_rec['description'],
                    vat_rec['customer_name'],
                    vat_rec['invoice_number'],
                    vat_rec['gross_amount'],
                    vat_rec['vat_rate']
                )
                results['vat_records'] += 1
        
        # Sample transactions
        if not existing_vat.empty:  # Only if we have VAT records
            sample_transactions = [
                {
                    'date': date(2026, 2, 1),
                    'description': 'Office Rent Payment',
                    'entries': [
                        {'account_code': '6100', 'debit': 15000.0, 'credit': 0.0},
                        {'account_code': '1000', 'debit': 0.0, 'credit': 15000.0}
                    ]
                },
                {
                    'date': date(2026, 2, 3),
                    'description': 'Equipment Purchase',
                    'entries': [
                        {'account_code': '1500', 'debit': 25000.0, 'credit': 0.0},
                        {'account_code': '1000', 'debit': 0.0, 'credit': 25000.0}
                    ]
                }
            ]
            
            for trans in sample_transactions:
                self.accounting.create_journal_entry(
                    trans['date'],
                    trans['description'],
                    trans['entries']
                )
                results['transactions'] += 1
        
        return results

class AccountingManager:
    """Manages double-entry accounting operations"""
    
    def __init__(self, company_id: str):
        self.company_id = company_id
    
    def setup_standard_accounts(self) -> int:
        """Create standard chart of accounts"""
        existing_accounts = data_store.query('accounts', company_id=self.company_id)
        if not existing_accounts.empty:
            return len(existing_accounts)
        
        count = 0
        for account_data in STANDARD_ACCOUNTS:
            account = account_data.copy()
            account['company_id'] = self.company_id
            account['normal_balance'] = 'debit' if account['type'] in ['Asset', 'Expense'] else 'credit'
            account['is_active'] = True
            
            data_store.insert_record('accounts', account)
            count += 1
        
        return count
    
    def create_journal_entry(self, entry_date: date, description: str, 
                           entries: List[Dict[str, Any]], reference: str = None) -> str:
        """Create a journal entry with multiple account entries"""
        if not self._validate_journal_entry(entries):
            raise ValueError("Journal entry does not balance (debits != credits)")
        
        entry_id = None
        for entry in entries:
            journal_record = {
                'date': entry_date,
                'description': description,
                'reference': reference or f"JE-{datetime.now().strftime('%Y%m%d-%H%M%S')}",
                'account_code': entry['account_code'],
                'debit': float(entry.get('debit', 0.0)),
                'credit': float(entry.get('credit', 0.0)),
                'company_id': self.company_id,
                'entry_type': 'manual',
                'created_by': 'system'
            }
            
            if entry_id is None:
                entry_id = data_store.insert_record('journal_entries', journal_record)
            else:
                data_store.insert_record('journal_entries', journal_record)
        
        return entry_id
    
    def _validate_journal_entry(self, entries: List[Dict[str, Any]]) -> bool:
        """Validate that journal entry balances"""
        total_debits = sum(float(entry.get('debit', 0.0)) for entry in entries)
        total_credits = sum(float(entry.get('credit', 0.0)) for entry in entries)
        return abs(total_debits - total_credits) < 0.01
    
    def get_account_balance(self, account_code: str) -> float:
        """Calculate account balance from journal entries"""
        entries = data_store.query('journal_entries', 
                                 company_id=self.company_id, 
                                 account_code=account_code)
        
        if entries.empty:
            return 0.0
        
        total_debits = entries['debit'].sum()
        total_credits = entries['credit'].sum()
        
        # Get account type to determine normal balance
        account = data_store.query('accounts', 
                                 company_id=self.company_id, 
                                 code=account_code)
        
        if account.empty:
            return total_debits - total_credits
        
        account_type = account.iloc[0]['type']
        if account_type in ['Asset', 'Expense']:
            return total_debits - total_credits
        else:  # Liability, Equity, Revenue
            return total_credits - total_debits
    
    def generate_trial_balance(self) -> pd.DataFrame:
        """Generate trial balance report"""
        accounts = data_store.query('accounts', company_id=self.company_id, is_active=True)
        
        trial_balance = []
        for _, account in accounts.iterrows():
            balance = self.get_account_balance(account['code'])
            
            if balance != 0.0:  # Only include accounts with balances
                trial_balance.append({
                    'Account Code': account['code'],
                    'Account Name': account['name'],
                    'Account Type': account['type'],
                    'Debit': balance if balance > 0 and account['type'] in ['Asset', 'Expense'] else 0.0,
                    'Credit': abs(balance) if balance < 0 or (balance > 0 and account['type'] in ['Liability', 'Equity', 'Revenue']) else 0.0
                })
        
        return pd.DataFrame(trial_balance)
    
    def generate_income_statement(self, start_date: date = None, end_date: date = None) -> Dict[str, Any]:
        """Generate income statement for specified period"""
        if start_date is None:
            start_date = date(2026, 1, 1)
        if end_date is None:
            end_date = date.today()
        
        # Get revenue accounts
        revenue_accounts = data_store.query('accounts', 
                                          company_id=self.company_id,
                                          type='Revenue',
                                          is_active=True)
        
        # Get expense accounts
        expense_accounts = data_store.query('accounts',
                                          company_id=self.company_id,
                                          type='Expense',
                                          is_active=True)
        
        total_revenue = 0.0
        total_expenses = 0.0
        
        # Calculate revenue
        revenue_details = []
        for _, account in revenue_accounts.iterrows():
            balance = abs(self.get_account_balance(account['code']))
            if balance > 0:
                revenue_details.append({
                    'account': account['name'],
                    'amount': balance
                })
                total_revenue += balance
        
        # Calculate expenses
        expense_details = []
        for _, account in expense_accounts.iterrows():
            balance = self.get_account_balance(account['code'])
            if balance > 0:
                expense_details.append({
                    'account': account['name'],
                    'amount': balance
                })
                total_expenses += balance
        
        net_income = total_revenue - total_expenses
        
        return {
            'period': f"{start_date} to {end_date}",
            'revenue': {
                'total': total_revenue,
                'details': revenue_details
            },
            'expenses': {
                'total': total_expenses,
                'details': expense_details
            },
            'net_income': net_income
        }

class VATManager:
    """Manages VAT operations for Ethiopian businesses"""
    
    def __init__(self, company_id: str):
        self.company_id = company_id
    
    def add_income_record(self, transaction_date: date, contract_date: date, 
                         description: str, customer_name: str, invoice_number: str,
                         gross_amount: float, vat_rate: float) -> str:
        """Add VAT income record with automatic calculations"""
        
        vat_amount = gross_amount * vat_rate
        net_amount = gross_amount - vat_amount
        
        income_record = {
            'date': transaction_date,
            'contract_date': contract_date,
            'description': description,
            'customer_name': customer_name,
            'invoice_number': invoice_number,
            'gross_amount': gross_amount,
            'vat_rate': vat_rate,
            'vat_amount': vat_amount,
            'net_amount': net_amount,
            'company_id': self.company_id
        }
        
        return data_store.insert_record('vat_income', income_record)
    
    def add_expense_record(self, transaction_date: date, description: str,
                          supplier_name: str, invoice_number: str,
                          gross_amount: float, vat_rate: float,
                          expense_category: str) -> str:
        """Add VAT expense record"""
        
        vat_amount = gross_amount * vat_rate
        net_amount = gross_amount - vat_amount
        
        expense_record = {
            'date': transaction_date,
            'description': description,
            'supplier_name': supplier_name,
            'invoice_number': invoice_number,
            'gross_amount': gross_amount,
            'vat_rate': vat_rate,
            'vat_amount': vat_amount,
            'net_amount': net_amount,
            'expense_category': expense_category,
            'company_id': self.company_id
        }
        
        return data_store.insert_record('vat_expenses', expense_record)
    
    def add_capital_record(self, transaction_date: date, description: str,
                          asset_type: str, gross_amount: float, 
                          vat_rate: float, depreciation_years: int) -> str:
        """Add VAT capital transaction record"""
        
        vat_amount = gross_amount * vat_rate
        net_amount = gross_amount - vat_amount
        
        capital_record = {
            'date': transaction_date,
            'description': description,
            'asset_type': asset_type,
            'gross_amount': gross_amount,
            'vat_rate': vat_rate,
            'vat_amount': vat_amount,
            'net_amount': net_amount,
            'depreciation_years': depreciation_years,
            'company_id': self.company_id
        }
        
        return data_store.insert_record('vat_capital', capital_record)
    
    def get_vat_summary(self, start_date: date = None, end_date: date = None) -> Dict[str, Any]:
        """Generate comprehensive VAT summary"""
        if start_date is None:
            start_date = date(2026, 1, 1)
        if end_date is None:
            end_date = date.today()
        
        # Get all VAT records for the period
        income_records = data_store.query('vat_income', company_id=self.company_id)
        expense_records = data_store.query('vat_expenses', company_id=self.company_id)
        capital_records = data_store.query('vat_capital', company_id=self.company_id)
        
        # Filter by date range
        if not income_records.empty:
            income_records = income_records[
                (income_records['date'] >= start_date) & 
                (income_records['date'] <= end_date)
            ]
        
        if not expense_records.empty:
            expense_records = expense_records[
                (expense_records['date'] >= start_date) & 
                (expense_records['date'] <= end_date)
            ]
        
        if not capital_records.empty:
            capital_records = capital_records[
                (capital_records['date'] >= start_date) & 
                (capital_records['date'] <= end_date)
            ]
        
        # Calculate totals
        total_income_vat = income_records['vat_amount'].sum() if not income_records.empty else 0.0
        total_expense_vat = expense_records['vat_amount'].sum() if not expense_records.empty else 0.0
        total_capital_vat = capital_records['vat_amount'].sum() if not capital_records.empty else 0.0
        
        total_input_vat = total_expense_vat + total_capital_vat
        net_vat_payable = total_income_vat - total_input_vat
        
        return {
            'period': f"{start_date} to {end_date}",
            'income': {
                'transactions': len(income_records),
                'gross_amount': income_records['gross_amount'].sum() if not income_records.empty else 0.0,
                'vat_amount': total_income_vat,
                'net_amount': income_records['net_amount'].sum() if not income_records.empty else 0.0
            },
            'expenses': {
                'transactions': len(expense_records),
                'gross_amount': expense_records['gross_amount'].sum() if not expense_records.empty else 0.0,
                'vat_amount': total_expense_vat,
                'net_amount': expense_records['net_amount'].sum() if not expense_records.empty else 0.0
            },
            'capital': {
                'transactions': len(capital_records),
                'gross_amount': capital_records['gross_amount'].sum() if not capital_records.empty else 0.0,
                'vat_amount': total_capital_vat,
                'net_amount': capital_records['net_amount'].sum() if not capital_records.empty else 0.0
            },
            'summary': {
                'output_vat': total_income_vat,
                'input_vat': total_input_vat,
                'net_vat_payable': net_vat_payable
            }
        }

class PayrollManager:
    """Manages Ethiopian payroll calculations"""
    
    def __init__(self, company_id: str):
        self.company_id = company_id
    
    def calculate_income_tax(self, taxable_income: float) -> float:
        """Calculate Ethiopian progressive income tax"""
        for bracket in PROGRESSIVE_TAX:
            if bracket['min'] <= taxable_income <= bracket['max']:
                return (taxable_income * bracket['rate']) - bracket['deduction']
        return 0.0
    
    def calculate_employee_payroll(self, employee_id: str, pay_period: str) -> Dict[str, Any]:
        """Calculate complete payroll for an employee"""
        employee = data_store.get_record('employees', employee_id)
        if not employee or not employee['is_active']:
            raise ValueError(f"Employee {employee_id} not found or inactive")
        
        basic_salary = employee['basic_salary']
        allowances = employee['allowances']
        tax_exemption = employee.get('tax_exemption', 600.0)  # Default ETB 600
        
        # Calculate gross pay
        gross_pay = basic_salary + allowances
        
        # Calculate taxable income (gross pay minus exemptions)
        taxable_income = max(0, gross_pay - tax_exemption)
        
        # Calculate income tax
        income_tax = self.calculate_income_tax(taxable_income)
        
        # Calculate pension contributions
        employee_pension = gross_pay * EMPLOYEE_PENSION_RATE
        employer_pension = gross_pay * EMPLOYER_PENSION_RATE
        
        # Calculate net pay
        net_pay = gross_pay - income_tax - employee_pension
        
        # Create payroll record
        payroll_record = {
            'employee_id': employee_id,
            'pay_period': pay_period,
            'basic_salary': basic_salary,
            'allowances': allowances,
            'gross_pay': gross_pay,
            'taxable_income': taxable_income,
            'income_tax': income_tax,
            'employee_pension': employee_pension,
            'employer_pension': employer_pension,
            'net_pay': net_pay,
            'company_id': self.company_id
        }
        
        # Save payroll record
        record_id = data_store.insert_record('payroll_records', payroll_record)
        payroll_record['id'] = record_id
        
        return payroll_record
    
    def process_company_payroll(self, pay_period: str) -> Dict[str, Any]:
        """Process payroll for all active employees"""
        employees = data_store.query('employees', 
                                   company_id=self.company_id, 
                                   is_active=True)
        
        results = {
            'pay_period': pay_period,
            'employees_processed': 0,
            'total_gross_pay': 0.0,
            'total_income_tax': 0.0,
            'total_employee_pension': 0.0,
            'total_employer_pension': 0.0,
            'total_net_pay': 0.0,
            'payroll_records': []
        }
        
        for _, employee in employees.iterrows():
            try:
                payroll = self.calculate_employee_payroll(employee['id'], pay_period)
                results['payroll_records'].append(payroll)
                results['employees_processed'] += 1
                results['total_gross_pay'] += payroll['gross_pay']
                results['total_income_tax'] += payroll['income_tax']
                results['total_employee_pension'] += payroll['employee_pension']
                results['total_employer_pension'] += payroll['employer_pension']
                results['total_net_pay'] += payroll['net_pay']
                
            except Exception as e:
                print(f"Error processing payroll for employee {employee['id']}: {e}")
        
        return results