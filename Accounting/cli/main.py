"""
Command Line Interface for the Accounting Software
"""
import sys
from datetime import datetime, timedelta
from typing import Optional
import os

# Add the parent directory to the path so we can import our modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from models.account import Account, AccountType, AccountSubType
from models.journal_entry import JournalEntry, JournalEntryBuilder
from core.ledger import GeneralLedger


class AccountingCLI:
    def __init__(self):
        self.ledger = GeneralLedger()
        self.running = True
        
    def display_menu(self):
        """Display the main menu"""
        print("\n" + "="*50)
        print("    ACCOUNTING SOFTWARE - MAIN MENU")
        print("="*50)
        print("1.  Setup - Create Standard Chart of Accounts")
        print("2.  Accounts - View All Accounts")
        print("3.  Add Account")
        print("4.  Create Journal Entry")
        print("5.  View Account Ledger")
        print("6.  Trial Balance")
        print("7.  Income Statement")
        print("8.  Balance Sheet")
        print("9.  Export Data")
        print("10. Quick Transactions")
        print("0.  Exit")
        print("="*50)
        
    def setup_standard_accounts(self):
        """Setup standard chart of accounts"""
        print("\nSetting up standard chart of accounts...")
        self.ledger.create_standard_chart_of_accounts()
        print("✓ Standard chart of accounts created successfully!")
        print(f"Created {len(self.ledger.accounts)} accounts.")
        
    def view_all_accounts(self):
        """Display all accounts"""
        if not self.ledger.accounts:
            print("No accounts found. Please setup chart of accounts first.")
            return
            
        print("\n" + "="*70)
        print("                    CHART OF ACCOUNTS")
        print("="*70)
        print(f"{'ID':<8} {'Account Name':<30} {'Type':<12} {'Balance':<15}")
        print("-"*70)
        
        # Group by account type for better display
        account_types = [AccountType.ASSET, AccountType.LIABILITY, AccountType.EQUITY, 
                        AccountType.REVENUE, AccountType.EXPENSE]
        
        for acc_type in account_types:
            type_accounts = [acc for acc in self.ledger.accounts.values() 
                           if acc.account_type == acc_type]
            if type_accounts:
                print(f"\n{acc_type.value.upper()}:")
                for account in sorted(type_accounts, key=lambda x: x.account_id):
                    print(f"{account.account_id:<8} {account.name:<30} {account.account_type.value:<12} ${account.balance:>12,.2f}")
    
    def add_account(self):
        """Add a new account"""
        print("\n--- ADD NEW ACCOUNT ---")
        account_id = input("Account ID: ").strip()
        
        if account_id in self.ledger.accounts:
            print("Account ID already exists!")
            return
            
        name = input("Account Name: ").strip()
        
        print("\nAccount Types:")
        for i, acc_type in enumerate(AccountType, 1):
            print(f"{i}. {acc_type.value}")
            
        try:
            type_choice = int(input("Choose account type (1-5): "))
            account_type = list(AccountType)[type_choice - 1]
        except (ValueError, IndexError):
            print("Invalid choice!")
            return
            
        account = Account(account_id, name, account_type)
        
        if self.ledger.add_account(account):
            print(f"✓ Account '{name}' added successfully!")
        else:
            print("Failed to add account.")
    
    def create_journal_entry(self):
        """Create a manual journal entry"""
        print("\n--- CREATE JOURNAL ENTRY ---")
        description = input("Description: ").strip()
        reference = input("Reference (optional): ").strip()
        
        entry = JournalEntry(description=description, reference=reference)
        
        print("\nAdd journal entry lines (enter 'done' when finished):")
        line_num = 1
        
        while True:
            print(f"\nLine {line_num}:")
            account_id = input("Account ID (or 'done' to finish): ").strip()
            
            if account_id.lower() == 'done':
                break
                
            if account_id not in self.ledger.accounts:
                print("Account not found!")
                continue
                
            print("Enter amount as:")
            print("1. Debit")
            print("2. Credit")
            
            try:
                choice = int(input("Choice (1 or 2): "))
                amount = float(input("Amount: "))
                
                if choice == 1:
                    entry.add_line(account_id, debit_amount=amount)
                else:
                    entry.add_line(account_id, credit_amount=amount)
                    
                line_num += 1
                
            except ValueError:
                print("Invalid input!")
                continue
        
        if len(entry.lines) >= 2:
            if entry.validate():
                if self.ledger.post_journal_entry(entry):
                    print(f"✓ Journal entry posted successfully!")
                    print(f"Entry ID: {entry.entry_id}")
                else:
                    print("Failed to post journal entry!")
            else:
                debits = entry.get_total_debits()
                credits = entry.get_total_credits()
                print(f"❌ Entry not balanced!")
                print(f"Total Debits: ${debits:,.2f}")
                print(f"Total Credits: ${credits:,.2f}")
                print(f"Difference: ${abs(debits - credits):,.2f}")
        else:
            print("Journal entry must have at least 2 lines!")
    
    def view_account_ledger(self):
        """View detailed account ledger"""
        account_id = input("Account ID: ").strip()
        account = self.ledger.get_account(account_id)
        
        if not account:
            print("Account not found!")
            return
            
        ledger_entries = self.ledger.get_account_ledger(account_id)
        
        print(f"\n" + "="*80)
        print(f"           ACCOUNT LEDGER - {account.name} ({account_id})")
        print("="*80)
        print(f"{'Date':<12} {'Description':<25} {'Reference':<12} {'Debit':<12} {'Credit':<12} {'Balance':<12}")
        print("-"*80)
        
        for entry in ledger_entries:
            print(f"{entry['date']:<12} {entry['description'][:25]:<25} {entry['reference']:<12} "
                  f"${entry['debit']:>10,.2f} ${entry['credit']:>10,.2f} ${entry['balance']:>10,.2f}")
            
        if not ledger_entries:
            print("No transactions found for this account.")
    
    def show_trial_balance(self):
        """Display trial balance"""
        trial_balance = self.ledger.get_trial_balance()
        
        print("\n" + "="*60)
        print("                 TRIAL BALANCE")
        print(f"              As of {datetime.now().strftime('%B %d, %Y')}")
        print("="*60)
        print(f"{'Account ID':<10} {'Account Name':<30} {'Balance':<15}")
        print("-"*60)
        
        total_debits = 0.0
        total_credits = 0.0
        
        for account_id, balance in trial_balance.items():
            account = self.ledger.get_account(account_id)
            if balance >= 0:
                total_debits += balance
                print(f"{account_id:<10} {account.name:<30} ${balance:>12,.2f}")
            else:
                total_credits += -balance
                print(f"{account_id:<10} {account.name:<30} ${-balance:>12,.2f} CR")
        
        print("-"*60)
        print(f"{'TOTALS':<40} ${total_debits:>12,.2f}")
        print(f"{'Credits':<40} ${total_credits:>12,.2f}")
        print(f"{'Difference':<40} ${abs(total_debits - total_credits):>12,.2f}")
    
    def show_income_statement(self):
        """Display income statement"""
        print("Income Statement Period:")
        print("1. Current Month")
        print("2. Current Year")
        print("3. Custom Period")
        
        try:
            choice = int(input("Choose period (1-3): "))
            
            if choice == 1:
                # Current month
                now = datetime.now()
                start_date = datetime(now.year, now.month, 1)
                end_date = now
            elif choice == 2:
                # Current year
                now = datetime.now()
                start_date = datetime(now.year, 1, 1)
                end_date = now
            else:
                # Custom period
                start_str = input("Start date (YYYY-MM-DD): ")
                end_str = input("End date (YYYY-MM-DD): ")
                start_date = datetime.strptime(start_str, '%Y-%m-%d')
                end_date = datetime.strptime(end_str, '%Y-%m-%d')
                
        except (ValueError, IndexError):
            print("Invalid input!")
            return
        
        income_statement = self.ledger.get_income_statement(start_date, end_date)
        
        print("\n" + "="*50)
        print("              INCOME STATEMENT")
        print(f"        {income_statement['period']}")
        print("="*50)
        
        print("\nREVENUE:")
        for account in income_statement['revenue']['accounts']:
            print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
        print(f"  {'Total Revenue':<30} ${income_statement['revenue']['total']:>12,.2f}")
        
        print("\nEXPENSES:")
        for account in income_statement['expenses']['accounts']:
            print(f"  {account['account_name']:<30} ${account['amount']:>12,.2f}")
        print(f"  {'Total Expenses':<30} ${income_statement['expenses']['total']:>12,.2f}")
        
        print("-"*50)
        net_income = income_statement['net_income']
        status = "NET INCOME" if net_income >= 0 else "NET LOSS"
        print(f"  {status:<30} ${abs(net_income):>12,.2f}")
    
    def show_balance_sheet(self):
        """Display balance sheet"""
        balance_sheet = self.ledger.get_balance_sheet()
        
        print("\n" + "="*50)
        print("               BALANCE SHEET")
        print(f"            As of {balance_sheet['as_of_date']}")
        print("="*50)
        
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
        
        print("-"*50)
        print(f"  {'Total Liab. + Equity':<30} ${balance_sheet['total_liabilities_and_equity']:>12,.2f}")
    
    def quick_transactions(self):
        """Quick transaction templates"""
        print("\n--- QUICK TRANSACTIONS ---")
        print("1. Cash Sale")
        print("2. Cash Purchase/Expense")
        print("3. Asset Purchase")
        print("4. Back to Main Menu")
        
        try:
            choice = int(input("Choose transaction type (1-4): "))
            
            if choice == 1:
                self.quick_cash_sale()
            elif choice == 2:
                self.quick_expense()
            elif choice == 3:
                self.quick_asset_purchase()
            elif choice == 4:
                return
            else:
                print("Invalid choice!")
                
        except ValueError:
            print("Invalid input!")
    
    def quick_cash_sale(self):
        """Quick cash sale entry"""
        print("\n--- CASH SALE ---")
        amount = float(input("Sale Amount: $"))
        description = input("Description: ")
        
        # Use standard accounts
        entry = JournalEntryBuilder.cash_sale("1000", "4000", amount, description)
        
        if self.ledger.post_journal_entry(entry):
            print("✓ Cash sale recorded successfully!")
        else:
            print("Failed to record cash sale. Check if accounts 1000 (Cash) and 4000 (Sales Revenue) exist.")
    
    def quick_expense(self):
        """Quick expense entry"""
        print("\n--- EXPENSE PAYMENT ---")
        amount = float(input("Expense Amount: $"))
        description = input("Description: ")
        
        print("Common Expense Accounts:")
        print("6000 - Salaries Expense")
        print("6100 - Rent Expense")
        print("6200 - Utilities Expense")
        print("6300 - Office Supplies")
        
        expense_account = input("Expense Account ID: ")
        
        entry = JournalEntryBuilder.expense_payment(expense_account, "1000", amount, description)
        
        if self.ledger.post_journal_entry(entry):
            print("✓ Expense recorded successfully!")
        else:
            print("Failed to record expense. Check if accounts exist.")
    
    def quick_asset_purchase(self):
        """Quick asset purchase entry"""
        print("\n--- ASSET PURCHASE ---")
        amount = float(input("Purchase Amount: $"))
        description = input("Description: ")
        asset_account = input("Asset Account ID (e.g., 1500 for Equipment): ")
        
        entry = JournalEntryBuilder.asset_purchase(asset_account, "1000", amount, description)
        
        if self.ledger.post_journal_entry(entry):
            print("✓ Asset purchase recorded successfully!")
        else:
            print("Failed to record asset purchase. Check if accounts exist.")
    
    def export_data(self):
        """Export all data to JSON"""
        filename = self.ledger.export_to_json()
        print(f"✓ Data exported to: {filename}")
    
    def run(self):
        """Main program loop"""
        print("Welcome to Basic Accounting Software!")
        print("This software implements double-entry bookkeeping principles.")
        
        while self.running:
            try:
                self.display_menu()
                choice = input("\nEnter your choice (0-10): ").strip()
                
                if choice == '0':
                    print("Thank you for using Basic Accounting Software!")
                    self.running = False
                elif choice == '1':
                    self.setup_standard_accounts()
                elif choice == '2':
                    self.view_all_accounts()
                elif choice == '3':
                    self.add_account()
                elif choice == '4':
                    self.create_journal_entry()
                elif choice == '5':
                    self.view_account_ledger()
                elif choice == '6':
                    self.show_trial_balance()
                elif choice == '7':
                    self.show_income_statement()
                elif choice == '8':
                    self.show_balance_sheet()
                elif choice == '9':
                    self.export_data()
                elif choice == '10':
                    self.quick_transactions()
                else:
                    print("Invalid choice! Please try again.")
                    
                if choice != '0':
                    input("\nPress Enter to continue...")
                    
            except KeyboardInterrupt:
                print("\n\nGoodbye!")
                break
            except Exception as e:
                print(f"An error occurred: {e}")
                input("Press Enter to continue...")


if __name__ == "__main__":
    cli = AccountingCLI()
    cli.run()