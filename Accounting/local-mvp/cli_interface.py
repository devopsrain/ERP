"""
Command Line Interface for Ethiopian Business Management System
Provides complete CLI access to all system features
"""

import click
import pandas as pd
from datetime import datetime, date
from tabulate import tabulate
from colorama import init, Fore, Style, Back
import os
from pathlib import Path

# Initialize colorama for Windows
init()

from business_logic import EthiopianBusinessManager
from data_store import data_store
from config import VAT_RATES, EXPORTS_DIR

class CLIInterface:
    """Command Line Interface for the business management system"""
    
    def __init__(self):
        self.business_manager = EthiopianBusinessManager()
        self.current_company = 'default_company'
    
    def print_header(self, title: str):
        """Print formatted header"""
        print(f"\n{Fore.CYAN}{'='*60}{Style.RESET_ALL}")
        print(f"{Fore.YELLOW}{title.center(60)}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}{'='*60}{Style.RESET_ALL}\n")
    
    def print_success(self, message: str):
        """Print success message"""
        print(f"{Fore.GREEN}✅ {message}{Style.RESET_ALL}")
    
    def print_error(self, message: str):
        """Print error message"""
        print(f"{Fore.RED}❌ {message}{Style.RESET_ALL}")
    
    def print_info(self, message: str):
        """Print informational message"""
        print(f"{Fore.BLUE}ℹ️  {message}{Style.RESET_ALL}")
    
    def print_warning(self, message: str):
        """Print warning message"""
        print(f"{Fore.YELLOW}⚠️  {message}{Style.RESET_ALL}")

# Create CLI instance
cli = CLIInterface()

@click.group()
def main():
    """Ethiopian Business Management System - Local MVP with Parquet Storage"""
    cli.print_header("🇪🇹 ETHIOPIAN BUSINESS MANAGEMENT SYSTEM")
    click.echo(f"{Fore.GREEN}📊 Parquet-based Local MVP - Complete I/O System{Style.RESET_ALL}")
    click.echo(f"{Fore.BLUE}💼 Company: {cli.current_company}{Style.RESET_ALL}")

@main.command()
def initialize():
    """Initialize the business management system with sample data"""
    cli.print_header("System Initialization")
    
    try:
        results = cli.business_manager.initialize_system()
        
        cli.print_success(f"Company: {results['company']}")
        cli.print_success(f"Accounts created: {results['accounts']}")
        cli.print_success(f"Sample employees: {results['sample_data']['employees']}")
        cli.print_success(f"Sample VAT records: {results['sample_data']['vat_records']}")
        cli.print_success(f"Sample transactions: {results['sample_data']['transactions']}")
        
        cli.print_info("\nSystem initialization completed successfully!")
        
    except Exception as e:
        cli.print_error(f"Initialization failed: {e}")

@main.group()
def accounting():
    """Accounting operations (chart of accounts, journal entries, reports)"""
    pass

@accounting.command('accounts')
def list_accounts():
    """List all accounts in chart of accounts"""
    cli.print_header("Chart of Accounts")
    
    accounts = data_store.query('accounts', company_id=cli.current_company, is_active=True)
    
    if accounts.empty:
        cli.print_warning("No accounts found. Run 'initialize' first.")
        return
    
    # Format for display
    display_data = []
    for _, account in accounts.iterrows():
        balance = cli.business_manager.accounting.get_account_balance(account['code'])
        display_data.append([
            account['code'],
            account['name'],
            account['type'],
            f"{balance:,.2f} ETB",
            account['description']
        ])
    
    headers = ['Code', 'Account Name', 'Type', 'Balance', 'Description']
    print(tabulate(display_data, headers=headers, tablefmt='grid'))

@accounting.command('add-entry')
@click.option('--date', prompt='Transaction date (YYYY-MM-DD)', help='Transaction date')
@click.option('--description', prompt='Description', help='Transaction description')
def add_journal_entry(date, description):
    """Add a journal entry with multiple account lines"""
    cli.print_header("Add Journal Entry")
    
    try:
        trans_date = datetime.strptime(date, '%Y-%m-%d').date()
    except ValueError:
        cli.print_error("Invalid date format. Use YYYY-MM-DD")
        return
    
    entries = []
    cli.print_info("Enter account entries (press Enter with empty account code to finish):")
    
    while True:
        account_code = click.prompt('Account code', default='', show_default=False)
        if not account_code:
            break
        
        # Validate account exists
        account = data_store.query('accounts', 
                                 company_id=cli.current_company, 
                                 code=account_code, 
                                 is_active=True)
        if account.empty:
            cli.print_error(f"Account {account_code} not found")
            continue
        
        debit = click.prompt('Debit amount', type=float, default=0.0)
        credit = click.prompt('Credit amount', type=float, default=0.0)
        
        if debit == 0.0 and credit == 0.0:
            cli.print_error("Either debit or credit must be greater than 0")
            continue
        
        entries.append({
            'account_code': account_code,
            'debit': debit,
            'credit': credit
        })
        
        account_name = account.iloc[0]['name']
        cli.print_success(f"Added: {account_code} - {account_name} (Dr: {debit}, Cr: {credit})")
    
    if not entries:
        cli.print_warning("No entries added")
        return
    
    # Validate entry balances
    total_debits = sum(entry['debit'] for entry in entries)
    total_credits = sum(entry['credit'] for entry in entries)
    
    if abs(total_debits - total_credits) > 0.01:
        cli.print_error(f"Entry doesn't balance! Debits: {total_debits}, Credits: {total_credits}")
        return
    
    try:
        entry_id = cli.business_manager.accounting.create_journal_entry(
            trans_date, description, entries
        )
        cli.print_success(f"Journal entry created with ID: {entry_id}")
    except Exception as e:
        cli.print_error(f"Failed to create journal entry: {e}")

@accounting.command('trial-balance')
def trial_balance():
    """Generate and display trial balance"""
    cli.print_header("Trial Balance")
    
    try:
        tb = cli.business_manager.accounting.generate_trial_balance()
        
        if tb.empty:
            cli.print_warning("No account balances found")
            return
        
        print(tabulate(tb, headers=tb.columns, tablefmt='grid', floatfmt='.2f'))
        
        total_debits = tb['Debit'].sum()
        total_credits = tb['Credit'].sum()
        
        print(f"\n{Fore.CYAN}Total Debits: {total_debits:,.2f} ETB{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Total Credits: {total_credits:,.2f} ETB{Style.RESET_ALL}")
        
        if abs(total_debits - total_credits) < 0.01:
            cli.print_success("Trial balance is balanced! ✅")
        else:
            cli.print_error("Trial balance is NOT balanced! ❌")
        
    except Exception as e:
        cli.print_error(f"Failed to generate trial balance: {e}")

@accounting.command('income-statement')
def income_statement():
    """Generate income statement"""
    cli.print_header("Income Statement")
    
    try:
        income_stmt = cli.business_manager.accounting.generate_income_statement()
        
        print(f"\n{Fore.YELLOW}Period: {income_stmt['period']}{Style.RESET_ALL}")
        print(f"\n{Fore.GREEN}REVENUE:{Style.RESET_ALL}")
        
        for revenue in income_stmt['revenue']['details']:
            print(f"  {revenue['account']:.<40} {revenue['amount']:>15,.2f} ETB")
        
        print(f"  {'Total Revenue':.<40} {income_stmt['revenue']['total']:>15,.2f} ETB")
        
        print(f"\n{Fore.RED}EXPENSES:{Style.RESET_ALL}")
        
        for expense in income_stmt['expenses']['details']:
            print(f"  {expense['account']:.<40} {expense['amount']:>15,.2f} ETB")
        
        print(f"  {'Total Expenses':.<40} {income_stmt['expenses']['total']:>15,.2f} ETB")
        
        print(f"\n{'-'*60}")
        net_income = income_stmt['net_income']
        color = Fore.GREEN if net_income >= 0 else Fore.RED
        print(f"{color}NET INCOME: {net_income:>15,.2f} ETB{Style.RESET_ALL}")
        
    except Exception as e:
        cli.print_error(f"Failed to generate income statement: {e}")

@main.group()
def vat():
    """VAT management operations"""
    pass

@vat.command('add-income')
@click.option('--date', prompt='Transaction date (YYYY-MM-DD)')
@click.option('--contract-date', prompt='Contract date (YYYY-MM-DD)')
@click.option('--description', prompt='Description')
@click.option('--customer', prompt='Customer name')
@click.option('--invoice', prompt='Invoice number')
@click.option('--amount', prompt='Gross amount', type=float)
@click.option('--vat-rate', prompt='VAT rate', type=click.Choice(['standard', 'zero_rated', 'withholding']), default='standard')
def add_vat_income(date, contract_date, description, customer, invoice, amount, vat_rate):
    """Add VAT income record"""
    cli.print_header("Add VAT Income Record")
    
    try:
        trans_date = datetime.strptime(date, '%Y-%m-%d').date()
        contract_dt = datetime.strptime(contract_date, '%Y-%m-%d').date()
        rate = VAT_RATES[vat_rate]
        
        record_id = cli.business_manager.vat.add_income_record(
            trans_date, contract_dt, description, customer, invoice, amount, rate
        )
        
        vat_amount = amount * rate
        net_amount = amount - vat_amount
        
        cli.print_success(f"VAT income record created: {record_id}")
        cli.print_info(f"Gross Amount: {amount:,.2f} ETB")
        cli.print_info(f"VAT Amount ({vat_rate}): {vat_amount:,.2f} ETB")
        cli.print_info(f"Net Amount: {net_amount:,.2f} ETB")
        
    except ValueError as e:
        cli.print_error(f"Invalid date format: {e}")
    except Exception as e:
        cli.print_error(f"Failed to add VAT income: {e}")

@vat.command('add-expense')
@click.option('--date', prompt='Transaction date (YYYY-MM-DD)')
@click.option('--description', prompt='Description')
@click.option('--supplier', prompt='Supplier name')
@click.option('--invoice', prompt='Invoice number')
@click.option('--amount', prompt='Gross amount', type=float)
@click.option('--category', prompt='Expense category')
@click.option('--vat-rate', prompt='VAT rate', type=click.Choice(['standard', 'zero_rated', 'withholding']), default='standard')
def add_vat_expense(date, description, supplier, invoice, amount, category, vat_rate):
    """Add VAT expense record"""
    cli.print_header("Add VAT Expense Record")
    
    try:
        trans_date = datetime.strptime(date, '%Y-%m-%d').date()
        rate = VAT_RATES[vat_rate]
        
        record_id = cli.business_manager.vat.add_expense_record(
            trans_date, description, supplier, invoice, amount, rate, category
        )
        
        vat_amount = amount * rate
        net_amount = amount - vat_amount
        
        cli.print_success(f"VAT expense record created: {record_id}")
        cli.print_info(f"Gross Amount: {amount:,.2f} ETB")
        cli.print_info(f"VAT Amount ({vat_rate}): {vat_amount:,.2f} ETB")
        cli.print_info(f"Net Amount: {net_amount:,.2f} ETB")
        
    except ValueError as e:
        cli.print_error(f"Invalid date format: {e}")
    except Exception as e:
        cli.print_error(f"Failed to add VAT expense: {e}")

@vat.command('summary')
@click.option('--start-date', help='Start date (YYYY-MM-DD)')
@click.option('--end-date', help='End date (YYYY-MM-DD)')
def vat_summary(start_date, end_date):
    """Generate VAT summary report"""
    cli.print_header("VAT Summary Report")
    
    try:
        start_dt = datetime.strptime(start_date, '%Y-%m-%d').date() if start_date else None
        end_dt = datetime.strptime(end_date, '%Y-%m-%d').date() if end_date else None
        
        summary = cli.business_manager.vat.get_vat_summary(start_dt, end_dt)
        
        print(f"\n{Fore.YELLOW}Period: {summary['period']}{Style.RESET_ALL}")
        
        print(f"\n{Fore.GREEN}INCOME (Output VAT):{Style.RESET_ALL}")
        print(f"  Transactions: {summary['income']['transactions']}")
        print(f"  Gross Amount: {summary['income']['gross_amount']:,.2f} ETB")
        print(f"  VAT Amount: {summary['income']['vat_amount']:,.2f} ETB")
        print(f"  Net Amount: {summary['income']['net_amount']:,.2f} ETB")
        
        print(f"\n{Fore.RED}EXPENSES (Input VAT):{Style.RESET_ALL}")
        print(f"  Transactions: {summary['expenses']['transactions']}")
        print(f"  Gross Amount: {summary['expenses']['gross_amount']:,.2f} ETB")
        print(f"  VAT Amount: {summary['expenses']['vat_amount']:,.2f} ETB")
        print(f"  Net Amount: {summary['expenses']['net_amount']:,.2f} ETB")
        
        print(f"\n{Fore.BLUE}CAPITAL (Input VAT):{Style.RESET_ALL}")
        print(f"  Transactions: {summary['capital']['transactions']}")
        print(f"  Gross Amount: {summary['capital']['gross_amount']:,.2f} ETB")
        print(f"  VAT Amount: {summary['capital']['vat_amount']:,.2f} ETB")
        print(f"  Net Amount: {summary['capital']['net_amount']:,.2f} ETB")
        
        print(f"\n{'-'*60}")
        print(f"{Fore.CYAN}VAT SUMMARY:{Style.RESET_ALL}")
        print(f"  Output VAT (Sales): {summary['summary']['output_vat']:,.2f} ETB")
        print(f"  Input VAT (Purchases): {summary['summary']['input_vat']:,.2f} ETB")
        
        net_vat = summary['summary']['net_vat_payable']
        color = Fore.RED if net_vat > 0 else Fore.GREEN
        status = "Payable" if net_vat > 0 else "Refundable"
        print(f"  {color}Net VAT {status}: {abs(net_vat):,.2f} ETB{Style.RESET_ALL}")
        
    except ValueError as e:
        cli.print_error(f"Invalid date format: {e}")
    except Exception as e:
        cli.print_error(f"Failed to generate VAT summary: {e}")

@main.group()
def payroll():
    """Payroll management operations"""
    pass

@payroll.command('employees')
def list_employees():
    """List all employees"""
    cli.print_header("Employee List")
    
    employees = data_store.query('employees', company_id=cli.current_company, is_active=True)
    
    if employees.empty:
        cli.print_warning("No employees found. Run 'initialize' first.")
        return
    
    display_data = []
    for _, emp in employees.iterrows():
        display_data.append([
            emp['employee_number'],
            f"{emp['first_name']} {emp['last_name']}",
            emp['position'],
            emp['department'],
            f"{emp['basic_salary']:,.2f} ETB",
            f"{emp['allowances']:,.2f} ETB",
            emp['hire_date']
        ])
    
    headers = ['Emp #', 'Name', 'Position', 'Department', 'Base Salary', 'Allowances', 'Hire Date']
    print(tabulate(display_data, headers=headers, tablefmt='grid'))

@payroll.command('calculate')
@click.option('--employee-id', prompt='Employee ID')
@click.option('--period', prompt='Pay period (YYYY-MM)', help='Pay period in YYYY-MM format')
def calculate_payroll(employee_id, period):
    """Calculate payroll for specific employee"""
    cli.print_header(f"Payroll Calculation - {period}")
    
    try:
        payroll = cli.business_manager.payroll.calculate_employee_payroll(employee_id, period)
        
        # Get employee details
        employee = data_store.get_record('employees', employee_id)
        if employee:
            print(f"\n{Fore.YELLOW}Employee: {employee['first_name']} {employee['last_name']} ({employee['employee_number']}){Style.RESET_ALL}")
            print(f"{Fore.YELLOW}Position: {employee['position']} - {employee['department']}{Style.RESET_ALL}")
        
        print(f"\n{Fore.GREEN}EARNINGS:{Style.RESET_ALL}")
        print(f"  Basic Salary: {payroll['basic_salary']:,.2f} ETB")
        print(f"  Allowances: {payroll['allowances']:,.2f} ETB")
        print(f"  Gross Pay: {payroll['gross_pay']:,.2f} ETB")
        
        print(f"\n{Fore.RED}DEDUCTIONS:{Style.RESET_ALL}")
        print(f"  Taxable Income: {payroll['taxable_income']:,.2f} ETB")
        print(f"  Income Tax: {payroll['income_tax']:,.2f} ETB")
        print(f"  Employee Pension (7%): {payroll['employee_pension']:,.2f} ETB")
        
        print(f"\n{Fore.BLUE}EMPLOYER CONTRIBUTIONS:{Style.RESET_ALL}")
        print(f"  Employer Pension (11%): {payroll['employer_pension']:,.2f} ETB")
        
        print(f"\n{'-'*60}")
        print(f"{Fore.CYAN}NET PAY: {payroll['net_pay']:,.2f} ETB{Style.RESET_ALL}")
        
        cli.print_success(f"Payroll record saved with ID: {payroll['id']}")
        
    except ValueError as e:
        cli.print_error(f"Employee not found: {e}")
    except Exception as e:
        cli.print_error(f"Failed to calculate payroll: {e}")

@payroll.command('process-all')
@click.option('--period', prompt='Pay period (YYYY-MM)')
def process_all_payroll(period):
    """Process payroll for all active employees"""
    cli.print_header(f"Company Payroll Processing - {period}")
    
    try:
        results = cli.business_manager.payroll.process_company_payroll(period)
        
        cli.print_success(f"Processed payroll for {results['employees_processed']} employees")
        
        print(f"\n{Fore.GREEN}PAYROLL SUMMARY:{Style.RESET_ALL}")
        print(f"  Total Gross Pay: {results['total_gross_pay']:,.2f} ETB")
        print(f"  Total Income Tax: {results['total_income_tax']:,.2f} ETB")
        print(f"  Total Employee Pension: {results['total_employee_pension']:,.2f} ETB")
        print(f"  Total Employer Pension: {results['total_employer_pension']:,.2f} ETB")
        print(f"  Total Net Pay: {results['total_net_pay']:,.2f} ETB")
        
        # Show individual records
        print(f"\n{Fore.BLUE}INDIVIDUAL PAYROLL RECORDS:{Style.RESET_ALL}")
        for record in results['payroll_records']:
            employee = data_store.get_record('employees', record['employee_id'])
            name = f"{employee['first_name']} {employee['last_name']}" if employee else "Unknown"
            print(f"  {name}: {record['net_pay']:,.2f} ETB (Gross: {record['gross_pay']:,.2f})")
        
    except Exception as e:
        cli.print_error(f"Failed to process payroll: {e}")

@main.group()
def data():
    """Data management operations (export, backup, stats)"""
    pass

@data.command('export')
@click.option('--format', type=click.Choice(['excel', 'csv']), default='excel', help='Export format')
@click.option('--table', help='Specific table to export (optional)')
def export_data(format, table):
    """Export data to Excel or CSV files"""
    cli.print_header("Data Export")
    
    try:
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        if table:
            # Export specific table
            df = data_store.read_table(table)
            if df.empty:
                cli.print_warning(f"Table '{table}' is empty or doesn't exist")
                return
            
            filename = f"{table}_{timestamp}.{format if format == 'csv' else 'xlsx'}"
            filepath = EXPORTS_DIR / filename
            
            if format == 'excel':
                df.to_excel(filepath, index=False)
            else:
                df.to_csv(filepath, index=False)
            
            cli.print_success(f"Exported {table} to {filepath}")
        
        else:
            # Export all tables
            if format == 'excel':
                filename = f"complete_export_{timestamp}.xlsx"
                filepath = EXPORTS_DIR / filename
                
                with pd.ExcelWriter(filepath) as writer:
                    for table_name in data_store.schemas.keys():
                        df = data_store.read_table(table_name)
                        if not df.empty:
                            df.to_excel(writer, sheet_name=table_name, index=False)
                
                cli.print_success(f"Complete export saved to {filepath}")
            
            else:
                # Export each table as separate CSV
                export_count = 0
                for table_name in data_store.schemas.keys():
                    df = data_store.read_table(table_name)
                    if not df.empty:
                        filename = f"{table_name}_{timestamp}.csv"
                        filepath = EXPORTS_DIR / filename
                        df.to_csv(filepath, index=False)
                        export_count += 1
                
                cli.print_success(f"Exported {export_count} tables to {EXPORTS_DIR}")
        
    except Exception as e:
        cli.print_error(f"Export failed: {e}")

@data.command('stats')
def show_data_stats():
    """Show data statistics for all tables"""
    cli.print_header("Data Statistics")
    
    try:
        stats = data_store.get_table_stats()
        
        display_data = []
        total_size = 0
        total_rows = 0
        
        for table_name, table_stats in stats.items():
            display_data.append([
                table_name,
                f"{table_stats['rows']:,}",
                table_stats['columns'],
                f"{table_stats['file_size_mb']:.2f} MB",
                table_stats['last_modified'].strftime('%Y-%m-%d %H:%M') if table_stats['last_modified'] else 'Never'
            ])
            
            total_size += table_stats['file_size_mb']
            total_rows += table_stats['rows']
        
        headers = ['Table', 'Rows', 'Columns', 'File Size', 'Last Modified']
        print(tabulate(display_data, headers=headers, tablefmt='grid'))
        
        print(f"\n{Fore.CYAN}Total Rows: {total_rows:,}{Style.RESET_ALL}")
        print(f"{Fore.CYAN}Total Size: {total_size:.2f} MB{Style.RESET_ALL}")
        
    except Exception as e:
        cli.print_error(f"Failed to get statistics: {e}")

@data.command('backup')
def backup_data():
    """Create backup of all data files"""
    cli.print_header("Data Backup")
    
    try:
        from config import BASE_DIR
        backup_dir = BASE_DIR / "backups"
        data_store.backup_data(backup_dir)
        cli.print_success(f"Backup created in {backup_dir}")
        
    except Exception as e:
        cli.print_error(f"Backup failed: {e}")

if __name__ == '__main__':
    main()