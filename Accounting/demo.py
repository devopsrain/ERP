#!/usr/bin/env python3
"""
Demo script for the Basic Accounting Software

This script demonstrates the core functionality by creating sample transactions
and generating reports.
"""

import sys
from datetime import datetime, timedelta, date
from pathlib import Path

# Add the current directory to path for imports
sys.path.append(str(Path(__file__).parent))

from models.account import Account, AccountType, AccountSubType
from models.journal_entry import JournalEntry, JournalEntryBuilder
from core.ledger import GeneralLedger
from models.ethiopian_payroll import Employee, EmployeeCategory, AllowanceType
from core.ethiopian_payroll_integration import EthiopianPayrollIntegration


def create_demo_data():
    """Create a complete demo with sample transactions"""
    
    print("="*60)
    print("         BASIC ACCOUNTING SOFTWARE - DEMO")
    print("="*60)
    
    # Create the ledger and setup accounts
    ledger = GeneralLedger()
    ledger.company_name = "Demo Company LLC"
    
    print("\n1. Setting up Chart of Accounts...")
    ledger.create_standard_chart_of_accounts()
    print(f"   ✓ Created {len(ledger.accounts)} standard accounts")
    
    # Add some sample transactions
    print("\n2. Recording Sample Transactions...")
    
    # Transaction 1: Initial capital investment
    print("   • Owner investment: $10,000")
    capital_entry = JournalEntry(
        description="Initial capital investment by owner",
        date=datetime.now() - timedelta(days=30)
    )
    capital_entry.add_line("1000", debit_amount=10000)   # Cash
    capital_entry.add_line("3000", credit_amount=10000)  # Owner's Equity
    ledger.post_journal_entry(capital_entry)
    
    # Transaction 2: Equipment purchase
    print("   • Equipment purchase: $3,000")
    equipment_entry = JournalEntryBuilder.asset_purchase(
        "1500", "1000", 3000, "Office computer and equipment",
        date=datetime.now() - timedelta(days=25)
    )
    ledger.post_journal_entry(equipment_entry)
    
    # Transaction 3: Cash sales
    print("   • Cash sales: $2,500")
    for i in range(5):
        sale_amount = 500
        sale_entry = JournalEntryBuilder.cash_sale(
            "1000", "4000", sale_amount, f"Sales - Week {i+1}",
            date=datetime.now() - timedelta(days=20 - i*3)
        )
        ledger.post_journal_entry(sale_entry)
    
    # Transaction 4: Various expenses
    expenses = [
        ("6100", 1200, "Monthly office rent"),
        ("6200", 150, "Utilities - electricity and internet"),
        ("6300", 75, "Office supplies purchase")
    ]
    
    for account, amount, desc in expenses:
        print(f"   • Expense: {desc} - ${amount}")
        expense_entry = JournalEntryBuilder.expense_payment(
            account, "1000", amount, desc,
            date=datetime.now() - timedelta(days=10)
        )
        ledger.post_journal_entry(expense_entry)
    
    # Transaction 4b: Ethiopian Payroll Processing (Sample)
    print("   • Ethiopian Payroll Processing...")
    _demonstrate_ethiopian_payroll_integration(ledger)
    
    # Transaction 5: Accounts receivable sale
    print("   • Credit sale: $1,500")
    credit_sale = JournalEntry(
        description="Credit sale to customer ABC Corp",
        date=datetime.now() - timedelta(days=5)
    )
    credit_sale.add_line("1100", debit_amount=1500)   # Accounts Receivable
    credit_sale.add_line("4000", credit_amount=1500)  # Sales Revenue
    ledger.post_journal_entry(credit_sale)
    
    return ledger


def display_reports(ledger):
    """Display various financial reports"""
    
    print("\n" + "="*60)
    print("                    FINANCIAL REPORTS")
    print("="*60)
    
    # 1. Trial Balance
    print("\n📊 TRIAL BALANCE")
    print("-" * 50)
    trial_balance = ledger.get_trial_balance()
    
    total_debits = 0
    total_credits = 0
    
    for account_id, balance in trial_balance.items():
        account = ledger.get_account(account_id)
        if balance >= 0:
            total_debits += balance
            print(f"{account_id:<6} {account.name:<25} ${balance:>10,.2f}")
        else:
            total_credits += -balance
            print(f"{account_id:<6} {account.name:<25} ${-balance:>10,.2f} CR")
    
    print("-" * 50)
    print(f"{'TOTALS':<31} ${total_debits:>10,.2f}")
    print(f"{'CREDITS':<31} ${total_credits:>10,.2f}")
    print(f"{'DIFFERENCE':<31} ${abs(total_debits - total_credits):>10,.2f}")
    
    # 2. Income Statement
    print("\n📈 INCOME STATEMENT")
    print("-" * 50)
    
    # Get income statement for current year
    now = datetime.now()
    start_of_year = datetime(now.year, 1, 1)
    income_stmt = ledger.get_income_statement(start_of_year, now)
    
    print(f"For the period: {income_stmt['period']}")
    print("\nREVENUE:")
    for account in income_stmt['revenue']['accounts']:
        print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
    print(f"  {'Total Revenue':<30} ${income_stmt['revenue']['total']:>12,.2f}")
    
    print("\nEXPENSES:")
    for account in income_stmt['expenses']['accounts']:
        print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
    print(f"  {'Total Expenses':<30} ${income_stmt['expenses']['total']:>12,.2f}")
    
    print("-" * 50)
    net_income = income_stmt['net_income']
    status = "NET INCOME" if net_income >= 0 else "NET LOSS"
    print(f"  {status:<30} ${abs(net_income):>12,.2f}")
    
    # 3. Balance Sheet
    print("\n🏦 BALANCE SHEET")
    print("-" * 50)
    balance_sheet = ledger.get_balance_sheet()
    print(f"As of: {balance_sheet['as_of_date']}")
    
    print("\nASSETS:")
    for account in balance_sheet['assets']['accounts']:
        print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
    print(f"  {'Total Assets':<30} ${balance_sheet['assets']['total']:>12,.2f}")
    
    print("\nLIABILITIES:")
    for account in balance_sheet['liabilities']['accounts']:
        print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
    print(f"  {'Total Liabilities':<30} ${balance_sheet['liabilities']['total']:>12,.2f}")
    
    print("\nEQUITY:")
    for account in balance_sheet['equity']['accounts']:
        print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
    print(f"  {'Total Equity':<30} ${balance_sheet['equity']['total']:>12,.2f}")
    
    print("-" * 50)
    print(f"  {'Total Liab. + Equity':<30} ${balance_sheet['total_liabilities_and_equity']:>12,.2f}")


def demonstrate_account_ledger(ledger):
    """Show detailed account ledger for Cash account"""
    
    print("\n" + "="*60)
    print("              ACCOUNT LEDGER EXAMPLE")
    print("="*60)
    
    # Show Cash account ledger
    cash_ledger = ledger.get_account_ledger("1000")
    
    print("\nCash Account (1000) - Detailed Transaction History:")
    print("-" * 80)
    print(f"{'Date':<12} {'Description':<30} {'Debit':<12} {'Credit':<12} {'Balance':<12}")
    print("-" * 80)
    
    for entry in cash_ledger:
        print(f"{entry['date']:<12} {entry['description'][:30]:<30} "
              f"${entry['debit']:>10,.2f} ${entry['credit']:>10,.2f} ${entry['balance']:>10,.2f}")

def _demonstrate_ethiopian_payroll_integration(ledger):
    """Demonstrate Ethiopian payroll integration within the main demo"""
    
    # Create payroll integration
    payroll_integration = EthiopianPayrollIntegration(ledger)
    
    # Create a sample employee for demo
    demo_employee = Employee(
        employee_id="DEMO001",
        name="Demo Employee",
        category=EmployeeCategory.REGULAR_EMPLOYEE,
        basic_salary=10000,  # ETB per month
        hire_date=date(2023, 1, 1),
        department="Demo Department",
        position="Demo Position"
    )
    
    # Process payroll for current month
    current_date = datetime.now().date()
    pay_period_start = date(current_date.year, current_date.month, 1)
    pay_period_end = date(current_date.year, current_date.month, 28)
    
    try:
        result = payroll_integration.process_monthly_payroll(
            [demo_employee], pay_period_start, pay_period_end
        )
        
        summary = result['payroll_summary'] 
        print(f"     • Processed Ethiopian payroll: {summary['totals']['net_pay']:,.2f} ETB net")
        print(f"     • Income tax withheld: {summary['totals']['income_tax']:,.2f} ETB")
        print(f"     • Pension contributions: {summary['totals']['employee_pension']:,.2f} ETB (employee)")
        print(f"     • Created {len(result['journal_entries'])} payroll journal entries")
        
    except Exception as e:
        print(f"     • Ethiopian payroll demo skipped: {str(e)[:50]}...")

def main():
    """Main demo function"""
    try:
        # Create demo data
        ledger = create_demo_data()
        
        # Display reports
        display_reports(ledger)
        
        # Show account ledger example
        demonstrate_account_ledger(ledger)
        
        # Show journal entries summary
        print(f"\n📝 SUMMARY:")
        print(f"   • Total Accounts: {len(ledger.accounts)}")
        print(f"   • Total Journal Entries: {len(ledger.journal_entries)}")
        print(f"   • Company: {ledger.company_name}")
        print(f"   • Ethiopian Payroll: Integrated ✓")
        
        # Export data
        print(f"\n💾 Exporting demo data...")
        filename = ledger.export_to_json("demo_export.json")
        print(f"   ✓ Data exported to: {filename}")
        
        print("\n" + "="*60)
        print("Demo completed successfully! 🎉")
        print("\nTo explore further:")
        print("• Run 'python run.py' for interactive interfaces")
        print("• Run 'python ethiopian_payroll_demo.py' for comprehensive payroll demo")
        print("• Check 'demo_export.json' for exported data")
        print("• Review the code to understand the implementation")
        print("="*60)
        
    except Exception as e:
        print(f"\n❌ Error during demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()