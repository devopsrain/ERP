#!/usr/bin/env python3
"""
Ethiopian Business Management System - Local MVP
Parquet-based standalone system with complete I/O capabilities

Usage:
    python run.py                    # Start web interface
    python run.py --cli              # Start CLI interface  
    python run.py --initialize       # Initialize system with sample data
    python run.py --demo             # Run comprehensive demonstration
"""

import sys
import argparse
from pathlib import Path

# Add current directory to path
sys.path.insert(0, str(Path(__file__).parent))

def run_web_interface():
    """Start the Flask web interface"""
    print("\n🇪🇹 Ethiopian Business Management System - Web Interface")
    print("=" * 60)
    print("Starting web server...")
    print("Access the system at: http://localhost:5000")
    print("Press Ctrl+C to stop the server")
    print("=" * 60)
    
    from web_interface import app
    app.run(debug=True, host='0.0.0.0', port=5000)

def run_cli_interface():
    """Start the CLI interface"""
    from cli_interface import main
    main()

def initialize_system():
    """Initialize the system with sample data"""
    from business_logic import EthiopianBusinessManager
    
    print("\n🚀 Initializing Ethiopian Business Management System...")
    print("=" * 60)
    
    business_manager = EthiopianBusinessManager()
    results = business_manager.initialize_system()
    
    print(f"✅ Company: {results['company']}")
    print(f"✅ Chart of accounts: {results['accounts']} accounts created")
    print(f"✅ Sample employees: {results['sample_data']['employees']} created")
    print(f"✅ Sample VAT records: {results['sample_data']['vat_records']} created")
    print(f"✅ Sample transactions: {results['sample_data']['transactions']} created")
    
    print("\n🎉 System initialization completed successfully!")
    print("\nYou can now:")
    print("  • Run 'python run.py' to start web interface")
    print("  • Run 'python run.py --cli' to use CLI interface")
    print("  • Run 'python run.py --demo' for a demonstration")
    
def run_demonstration():
    """Run a comprehensive demonstration of all features"""
    from business_logic import EthiopianBusinessManager
    from data_store import data_store
    from datetime import datetime, date
    import pandas as pd
    
    print("\n🎆 Ethiopian Business Management System - Comprehensive Demo")
    print("=" * 70)
    
    # Initialize system
    business_manager = EthiopianBusinessManager()
    results = business_manager.initialize_system()
    
    print(f"\n🏢 1. SYSTEM INITIALIZATION")
    print(f"   • Company created: {results['company']}")
    print(f"   • Chart of accounts: {results['accounts']} accounts")
    print(f"   • Sample data: {results['sample_data']}")
    
    # Demonstrate accounting features
    print(f"\n📊 2. ACCOUNTING OPERATIONS")
    
    # Add a journal entry
    entries = [
        {'account_code': '6200', 'debit': 3500.0, 'credit': 0.0},  # Utilities Expense
        {'account_code': '1000', 'debit': 0.0, 'credit': 3500.0}   # Cash
    ]
    
    entry_id = business_manager.accounting.create_journal_entry(
        date.today(), 
        "Monthly utilities payment", 
        entries
    )
    print(f"   • Created journal entry: {entry_id}")
    
    # Generate trial balance
    trial_balance = business_manager.accounting.generate_trial_balance()
    print(f"   • Trial balance generated: {len(trial_balance)} accounts with balances")
    print(f"   • Total debits: {trial_balance['Debit'].sum():,.2f} ETB")
    print(f"   • Total credits: {trial_balance['Credit'].sum():,.2f} ETB")
    
    # Generate income statement
    income_stmt = business_manager.accounting.generate_income_statement()
    print(f"   • Income statement: Net income = {income_stmt['net_income']:,.2f} ETB")
    
    # Demonstrate VAT operations
    print(f"\n💰 3. VAT PORTAL OPERATIONS")
    
    # Add VAT expense
    expense_id = business_manager.vat.add_expense_record(
        date.today(),
        "Office supplies purchase",
        "ABC Supplies Ltd", 
        "SUP-2026-001",
        5000.0,
        0.15,  # 15% VAT
        "Office Supplies"
    )
    print(f"   • Added VAT expense: {expense_id}")
    
    # Add capital transaction
    capital_id = business_manager.vat.add_capital_record(
        date.today(),
        "Computer equipment purchase",
        "Computer Equipment",
        12000.0,
        0.15,  # 15% VAT
        3  # 3 years depreciation
    )
    print(f"   • Added VAT capital: {capital_id}")
    
    # Generate VAT summary
    vat_summary = business_manager.vat.get_vat_summary()
    print(f"   • VAT Summary:")
    print(f"     - Output VAT: {vat_summary['summary']['output_vat']:,.2f} ETB")
    print(f"     - Input VAT: {vat_summary['summary']['input_vat']:,.2f} ETB")
    print(f"     - Net VAT Payable: {vat_summary['summary']['net_vat_payable']:,.2f} ETB")
    
    # Demonstrate payroll operations
    print(f"\n👥 4. PAYROLL OPERATIONS")
    
    # Get employees
    employees = data_store.query('employees', company_id='default_company', is_active=True)
    print(f"   • Active employees: {len(employees)}")
    
    # Process payroll for all employees
    current_period = datetime.now().strftime('%Y-%m')
    payroll_results = business_manager.payroll.process_company_payroll(current_period)
    
    print(f"   • Payroll processed for period: {current_period}")
    print(f"     - Employees processed: {payroll_results['employees_processed']}")
    print(f"     - Total gross pay: {payroll_results['total_gross_pay']:,.2f} ETB")
    print(f"     - Total income tax: {payroll_results['total_income_tax']:,.2f} ETB")
    print(f"     - Total net pay: {payroll_results['total_net_pay']:,.2f} ETB")
    
    # Show individual payroll records
    print(f"   • Individual payroll records:")
    for record in payroll_results['payroll_records']:
        employee = data_store.get_record('employees', record['employee_id'])
        name = f"{employee['first_name']} {employee['last_name']}" if employee else "Unknown"
        print(f"     - {name}: {record['net_pay']:,.2f} ETB (Tax: {record['income_tax']:,.2f})")
    
    # Demonstrate data statistics
    print(f"\n📈 5. DATA STATISTICS")
    
    stats = data_store.get_table_stats()
    total_records = sum(stat['rows'] for stat in stats.values())
    total_size = sum(stat['file_size_mb'] for stat in stats.values())
    
    print(f"   • Total records: {total_records:,}")
    print(f"   • Total storage: {total_size:.2f} MB")
    print(f"   • Tables with data:")
    
    for table_name, stat in stats.items():
        if stat['rows'] > 0:
            print(f"     - {table_name}: {stat['rows']:,} records ({stat['file_size_mb']:.2f} MB)")
    
    # Export demonstration
    print(f"\n📤 6. DATA EXPORT")
    
    from config import EXPORTS_DIR
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    # Export trial balance to Excel
    trial_balance_file = EXPORTS_DIR / f"trial_balance_{timestamp}.xlsx"
    trial_balance.to_excel(trial_balance_file, index=False)
    print(f"   • Trial balance exported to: {trial_balance_file}")
    
    # Export VAT summary as JSON
    import json
    vat_summary_file = EXPORTS_DIR / f"vat_summary_{timestamp}.json"
    with open(vat_summary_file, 'w') as f:
        json.dump(vat_summary, f, indent=2, default=str)
    print(f"   • VAT summary exported to: {vat_summary_file}")
    
    # Create backup
    backup_dir = Path("backups")
    data_store.backup_data(backup_dir)
    print(f"   • Data backup created in: {backup_dir}")
    
    print(f"\n🎉 DEMONSTRATION COMPLETED SUCCESSFULLY!")
    print("=" * 70)
    print("\n🔗 Next steps:")
    print("   • Start web interface: python run.py")
    print("   • Use CLI interface: python run.py --cli")
    print("   • Check exports folder for generated files")
    print("   • Review data in the 'data' folder (Parquet files)")
    print("\n📚 System Features Demonstrated:")
    print("   ✓ Double-entry accounting with journal entries")
    print("   ✓ Ethiopian VAT compliance (15% standard, 0% zero-rated, 2% withholding)")
    print("   ✓ Payroll with progressive tax and pension calculations")
    print("   ✓ Trial balance and income statement generation")
    print("   ✓ Comprehensive VAT reporting")
    print("   ✓ Data export to Excel and JSON")
    print("   ✓ Automated backup system")
    print("   ✓ Parquet-based high-performance storage")

def show_system_info():
    """Display system information"""
    from data_store import data_store
    
    print("\n🔍 System Information")
    print("=" * 40)
    
    stats = data_store.get_table_stats()
    
    print(f"Data Tables:")
    for table_name, stat in stats.items():
        print(f"  {table_name:.<20} {stat['rows']:>6,} rows ({stat['file_size_mb']:>6.2f} MB)")
    
    total_records = sum(stat['rows'] for stat in stats.values())
    total_size = sum(stat['file_size_mb'] for stat in stats.values())
    
    print(f"\nTotal Records: {total_records:,}")
    print(f"Total Storage: {total_size:.2f} MB")
    
    # Check if system is initialized
    accounts = data_store.query('accounts', company_id='default_company')
    if accounts.empty:
        print("\n⚠️  System not initialized. Run 'python run.py --initialize' first.")
    else:
        print(f"\n✅ System initialized with {len(accounts)} accounts")

def main():
    """Main entry point"""
    parser = argparse.ArgumentParser(
        description='Ethiopian Business Management System - Local MVP',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python run.py                    Start web interface (default)
  python run.py --cli              Start command-line interface
  python run.py --initialize       Initialize system with sample data
  python run.py --demo             Run comprehensive demonstration
  python run.py --info             Show system information
        """
    )
    
    parser.add_argument('--cli', action='store_true', 
                       help='Start CLI interface instead of web interface')
    parser.add_argument('--initialize', action='store_true',
                       help='Initialize system with sample data')
    parser.add_argument('--demo', action='store_true',
                       help='Run comprehensive demonstration')
    parser.add_argument('--info', action='store_true',
                       help='Show system information')
    
    args = parser.parse_args()
    
    try:
        if args.initialize:
            initialize_system()
        elif args.demo:
            run_demonstration()
        elif args.info:
            show_system_info()
        elif args.cli:
            run_cli_interface()
        else:
            # Default to web interface
            run_web_interface()
            
    except KeyboardInterrupt:
        print("\n\n🚑 Interrupted by user. Goodbye!")
    except ImportError as e:
        print(f"\n❌ Import error: {e}")
        print("\nMake sure to install dependencies first:")
        print("pip install -r requirements.txt")
    except Exception as e:
        print(f"\n❌ Error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    
    return 0

if __name__ == '__main__':
    sys.exit(main())