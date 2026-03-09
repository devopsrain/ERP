"""
Ethiopian Payroll Integration with Accounting System

This module integrates Ethiopian payroll calculations with the general ledger,
creating appropriate journal entries for salary expenses, tax liabilities,
and pension contributions.
"""

from datetime import datetime, date
from dateutil.relativedelta import relativedelta
from typing import List, Dict, Optional
from models.ethiopian_payroll import (
    EthiopianPayrollCalculator, PayrollItem, Employee, EmployeeCategory,
    AllowanceType, DeductionType
)
from models.journal_entry import JournalEntry, JournalEntryBuilder
from core.ledger import GeneralLedger
from models.account import Account, AccountType, AccountSubType


class EthiopianPayrollIntegration:
    """Integration class for Ethiopian payroll and accounting system"""
    
    def __init__(self, ledger: GeneralLedger):
        self.ledger = ledger
        self.calculator = EthiopianPayrollCalculator()
        self._setup_payroll_accounts()
    
    def _setup_payroll_accounts(self):
        """Setup payroll-specific accounts in the chart of accounts"""
        payroll_accounts = [
            # Expense Accounts
            Account("6000", "Salary Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6001", "Basic Salary Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6002", "Allowances Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6003", "Employer Pension Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6004", "Overtime Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6005", "Bonus Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            
            # Liability Accounts (amounts owed to employees and government)
            Account("2200", "Salaries Payable", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            Account("2210", "Income Tax Withheld Payable", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            Account("2220", "Employee Pension Payable", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            Account("2230", "Employer Pension Payable", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            Account("2240", "Other Deductions Payable", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            
            # Asset Accounts for advances and loans to employees
            Account("1300", "Employee Advances", AccountType.ASSET, AccountSubType.CURRENT_ASSET),
            Account("1310", "Employee Loans Receivable", AccountType.ASSET, AccountSubType.CURRENT_ASSET),
        ]
        
        for account in payroll_accounts:
            self.ledger.add_account(account)
    
    def _calculate_months_in_period(self, start_date: date, end_date: date) -> float:
        """
        Calculate the number of months in a given period
        
        Args:
            start_date: Start of the period
            end_date: End of the period
            
        Returns:
            Number of months (can be fractional)
        """
        # Calculate total days in period
        total_days = (end_date - start_date).days + 1
        
        # For simplicity, assume 30 days per month for partial months
        # For full month calculations, we can be more precise
        months = total_days / 30.0
        
        # If it's close to whole months, round to nearest month
        if abs(months - round(months)) < 0.1:
            months = round(months)
            
        return max(months, 0.1)  # Minimum 0.1 months
    
    def process_monthly_payroll(self, employees: List[Employee], 
                               pay_period_start: date, pay_period_end: date,
                               payment_date: Optional[datetime] = None) -> Dict:
        """
        Process complete monthly payroll and create accounting entries
        
        Args:
            employees: List of employees to process
            pay_period_start: Start date of pay period
            pay_period_end: End date of pay period  
            payment_date: Date when salaries are paid (defaults to end of pay period)
        
        Returns:
            Dictionary with payroll summary and journal entry references
        """
        if payment_date is None:
            payment_date = datetime.combine(pay_period_end, datetime.min.time())
        
        # Calculate number of months in the period
        months_in_period = self._calculate_months_in_period(pay_period_start, pay_period_end)
        
        # Calculate payroll for all employees with period multiplier
        payroll_items = self.calculator.calculate_monthly_payroll(
            employees, pay_period_start, pay_period_end, months_in_period
        )
        
        if not payroll_items:
            return {'error': 'No active employees found for payroll processing'}
        
        # Create journal entries
        journal_entries = self._create_payroll_journal_entries(payroll_items, payment_date)
        
        # Post entries to ledger
        for entry in journal_entries:
            self.ledger.post_journal_entry(entry)
        
        # Generate summary
        summary = self.calculator.get_payroll_summary(payroll_items)
        summary['journal_entries'] = [entry.entry_id for entry in journal_entries]
        summary['employees_processed'] = len(payroll_items)
        summary['months_in_period'] = months_in_period
        summary['pay_period_start'] = pay_period_start
        summary['pay_period_end'] = pay_period_end
        
        return {
            'payroll_summary': summary,
            'payroll_items': payroll_items,
            'journal_entries': journal_entries
        }
    
    def _create_payroll_journal_entries(self, payroll_items: List[PayrollItem], 
                                       payment_date: datetime) -> List[JournalEntry]:
        """Create journal entries for payroll"""
        entries = []
        
        # 1. Salary Expense Recognition Entry
        expense_entry = self._create_salary_expense_entry(payroll_items, payment_date)
        entries.append(expense_entry)
        
        # 2. Salary Payment Entry (when cash is actually paid)
        payment_entry = self._create_salary_payment_entry(payroll_items, payment_date)
        entries.append(payment_entry)
        
        # 3. Tax and Pension Remittance Entries (separate entries for government payments)
        tax_entry = self._create_tax_remittance_entry(payroll_items, payment_date)
        if tax_entry:
            entries.append(tax_entry)
        
        pension_entry = self._create_pension_remittance_entry(payroll_items, payment_date)
        if pension_entry:
            entries.append(pension_entry)
        
        return entries
    
    def _create_salary_expense_entry(self, payroll_items: List[PayrollItem], 
                                    payment_date: datetime) -> JournalEntry:
        """Create journal entry to recognize salary expenses"""
        
        total_basic_salary = sum(item.basic_salary for item in payroll_items)
        total_allowances = sum(item.total_allowances for item in payroll_items)
        total_employer_pension = sum(item.employer_pension for item in payroll_items)
        total_income_tax = sum(item.income_tax for item in payroll_items)
        total_employee_pension = sum(item.employee_pension for item in payroll_items)
        total_net_payable = sum(item.net_salary for item in payroll_items)
        
        # Create detailed expense recognition entry
        expense_entry = JournalEntry(
            description=f"Monthly payroll expense - {payment_date.strftime('%B %Y')}",
            date=payment_date
        )
        
        # Debit: Salary expenses
        if total_basic_salary > 0:
            expense_entry.add_line("6001", debit_amount=total_basic_salary)  # Basic Salary Expense
        
        if total_allowances > 0:
            expense_entry.add_line("6002", debit_amount=total_allowances)    # Allowances Expense
        
        if total_employer_pension > 0:
            expense_entry.add_line("6003", debit_amount=total_employer_pension)  # Employer Pension Expense
        
        # Credit: Liabilities (amounts owed)
        if total_net_payable > 0:
            expense_entry.add_line("2200", credit_amount=total_net_payable)   # Salaries Payable
        
        if total_income_tax > 0:
            expense_entry.add_line("2210", credit_amount=total_income_tax)    # Income Tax Withheld
        
        if total_employee_pension > 0:
            expense_entry.add_line("2220", credit_amount=total_employee_pension)  # Employee Pension Payable
        
        if total_employer_pension > 0:
            expense_entry.add_line("2230", credit_amount=total_employer_pension)  # Employer Pension Payable
        
        return expense_entry
    
    def _create_salary_payment_entry(self, payroll_items: List[PayrollItem], 
                                    payment_date: datetime) -> JournalEntry:
        """Create journal entry for actual salary payment to employees"""
        
        total_net_payable = sum(item.net_salary for item in payroll_items)
        
        payment_entry = JournalEntry(
            description=f"Salary payment to employees - {payment_date.strftime('%B %Y')}",
            date=payment_date
        )
        
        # Debit: Salaries Payable (reducing liability)
        payment_entry.add_line("2200", debit_amount=total_net_payable)
        
        # Credit: Cash (payment made)
        payment_entry.add_line("1000", credit_amount=total_net_payable)
        
        return payment_entry
    
    def _create_tax_remittance_entry(self, payroll_items: List[PayrollItem], 
                                    payment_date: datetime) -> Optional[JournalEntry]:
        """Create journal entry for tax remittance to government"""
        
        total_income_tax = sum(item.income_tax for item in payroll_items)
        
        if total_income_tax <= 0:
            return None
        
        tax_entry = JournalEntry(
            description=f"Income tax remittance - {payment_date.strftime('%B %Y')}",
            date=payment_date
        )
        
        # Debit: Income Tax Withheld Payable (reducing liability)
        tax_entry.add_line("2210", debit_amount=total_income_tax)
        
        # Credit: Cash (payment to tax authority)
        tax_entry.add_line("1000", credit_amount=total_income_tax)
        
        return tax_entry
    
    def _create_pension_remittance_entry(self, payroll_items: List[PayrollItem], 
                                        payment_date: datetime) -> Optional[JournalEntry]:
        """Create journal entry for pension contributions remittance"""
        
        total_employee_pension = sum(item.employee_pension for item in payroll_items)
        total_employer_pension = sum(item.employer_pension for item in payroll_items)
        total_pension = total_employee_pension + total_employer_pension
        
        if total_pension <= 0:
            return None
        
        pension_entry = JournalEntry(
            description=f"Pension contributions remittance - {payment_date.strftime('%B %Y')}",
            date=payment_date
        )
        
        # Debit: Pension Payable accounts (reducing liabilities)
        if total_employee_pension > 0:
            pension_entry.add_line("2220", debit_amount=total_employee_pension)
        
        if total_employer_pension > 0:
            pension_entry.add_line("2230", debit_amount=total_employer_pension)
        
        # Credit: Cash (payment to pension fund)
        pension_entry.add_line("1000", credit_amount=total_pension)
        
        return pension_entry
    
    def create_employee_advance(self, employee_id: str, amount: float, 
                               description: str, advance_date: Optional[datetime] = None) -> JournalEntry:
        """Create journal entry for employee advance"""
        
        if advance_date is None:
            advance_date = datetime.now()
        
        advance_entry = JournalEntry(
            description=f"Employee advance - {description}",
            date=advance_date
        )
        
        # Debit: Employee Advances (asset)
        advance_entry.add_line("1300", debit_amount=amount)
        
        # Credit: Cash
        advance_entry.add_line("1000", credit_amount=amount)
        
        # Post to ledger
        self.ledger.post_journal_entry(advance_entry)
        
        return advance_entry
    
    def process_advance_recovery(self, employee_id: str, amount: float, 
                                description: str, recovery_date: Optional[datetime] = None) -> JournalEntry:
        """Create journal entry for advance recovery from salary"""
        
        if recovery_date is None:
            recovery_date = datetime.now()
        
        recovery_entry = JournalEntry(
            description=f"Advance recovery - {description}",
            date=recovery_date
        )
        
        # Debit: Salaries Payable (reducing amount to pay employee)
        recovery_entry.add_line("2200", debit_amount=amount)
        
        # Credit: Employee Advances (reducing advance asset)
        recovery_entry.add_line("1300", credit_amount=amount)
        
        # Post to ledger
        self.ledger.post_journal_entry(recovery_entry)
        
        return recovery_entry
    
    def get_payroll_reports(self, start_date: date, end_date: date) -> Dict:
        """Generate payroll reports for a given period"""
        
        # Get all payroll-related journal entries
        payroll_entries = [
            entry for entry in self.ledger.journal_entries
            if entry.date.date() >= start_date and entry.date.date() <= end_date
            and any(line.account_id.startswith(('6000', '6001', '6002', '6003', '6004', '6005', '2200', '2210', '2220', '2230')) 
                   for line in entry.lines)
        ]
        
        # Calculate summary data
        total_salary_expense = 0
        total_tax_withheld = 0
        total_pension_employee = 0
        total_pension_employer = 0
        
        for entry in payroll_entries:
            for line in entry.lines:
                if line.account_id in ['6001', '6002']:  # Salary expenses
                    total_salary_expense += line.debit_amount or 0
                elif line.account_id == '2210':  # Tax withheld
                    total_tax_withheld += line.credit_amount or 0
                elif line.account_id == '2220':  # Employee pension
                    total_pension_employee += line.credit_amount or 0
                elif line.account_id == '6003':  # Employer pension expense
                    total_pension_employer += line.debit_amount or 0
        
        return {
            'period': f"{start_date} to {end_date}",
            'summary': {
                'total_salary_expense': total_salary_expense,
                'total_tax_withheld': total_tax_withheld,
                'total_employee_pension': total_pension_employee,
                'total_employer_pension': total_pension_employer,
                'total_payroll_cost': total_salary_expense + total_pension_employer
            },
            'journal_entries_count': len(payroll_entries)
        }