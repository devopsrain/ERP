"""
General Ledger - Core accounting system
"""
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from collections import defaultdict
import json

from models.account import Account, AccountType, AccountSubType, Transaction
from models.journal_entry import JournalEntry, JournalEntryLine


class GeneralLedger:
    """Main accounting system that manages all accounts and transactions"""
    
    def __init__(self):
        self.accounts: Dict[str, Account] = {}
        self.journal_entries: List[JournalEntry] = []
        self.company_name = "My Company"
        
    def add_account(self, account: Account) -> bool:
        """Add a new account to the ledger"""
        if account.account_id in self.accounts:
            return False
        self.accounts[account.account_id] = account
        return True
    
    def get_account(self, account_id: str) -> Optional[Account]:
        """Get account by ID"""
        return self.accounts.get(account_id)
    
    def create_standard_chart_of_accounts(self):
        """Create a standard set of accounts for a small business"""
        standard_accounts = [
            # Assets
            Account("1000", "Cash", AccountType.ASSET, AccountSubType.CURRENT_ASSET),
            Account("1100", "Accounts Receivable", AccountType.ASSET, AccountSubType.CURRENT_ASSET),
            Account("1200", "Inventory", AccountType.ASSET, AccountSubType.CURRENT_ASSET),
            Account("1500", "Equipment", AccountType.ASSET, AccountSubType.FIXED_ASSET),
            Account("1510", "Accumulated Depreciation - Equipment", AccountType.ASSET, AccountSubType.FIXED_ASSET),
            
            # Liabilities
            Account("2000", "Accounts Payable", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            Account("2100", "Accrued Expenses", AccountType.LIABILITY, AccountSubType.CURRENT_LIABILITY),
            Account("2500", "Long-term Debt", AccountType.LIABILITY, AccountSubType.LONG_TERM_LIABILITY),
            
            # Equity
            Account("3000", "Owner's Equity", AccountType.EQUITY, AccountSubType.OWNERS_EQUITY),
            Account("3100", "Retained Earnings", AccountType.EQUITY, AccountSubType.RETAINED_EARNINGS),
            
            # Revenue
            Account("4000", "Sales Revenue", AccountType.REVENUE, AccountSubType.OPERATING_REVENUE),
            Account("4100", "Service Revenue", AccountType.REVENUE, AccountSubType.OPERATING_REVENUE),
            
            # Expenses
            Account("5000", "Cost of Goods Sold", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6000", "Salaries Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6100", "Rent Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6200", "Utilities Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
            Account("6300", "Office Supplies", AccountType.EXPENSE, AccountSubType.ADMINISTRATIVE_EXPENSE),
            Account("6400", "Depreciation Expense", AccountType.EXPENSE, AccountSubType.OPERATING_EXPENSE),
        ]
        
        for account in standard_accounts:
            self.add_account(account)
    
    def post_journal_entry(self, journal_entry: JournalEntry) -> bool:
        """Post a journal entry to the accounts"""
        if not journal_entry.validate():
            return False
            
        # Post to each account
        for line in journal_entry.lines:
            account = self.get_account(line.account_id)
            if account is None:
                return False
                
            # Create transaction for the account
            amount = line.debit_amount if line.debit_amount > 0 else -line.credit_amount
            transaction = Transaction(
                date=journal_entry.date,
                description=f"{journal_entry.description} - {line.description}".strip(" - "),
                reference=journal_entry.reference,
                amount=amount
            )
            account.add_transaction(transaction)
        
        # Mark as posted and add to journal entries
        journal_entry.post()
        self.journal_entries.append(journal_entry)
        return True
    
    def get_trial_balance(self, as_of_date: datetime = None) -> Dict[str, float]:
        """Generate trial balance"""
        if as_of_date is None:
            as_of_date = datetime.now()
            
        trial_balance = {}
        for account_id, account in self.accounts.items():
            balance = account.get_balance_at_date(as_of_date)
            if abs(balance) > 0.01:  # Only include accounts with balances
                trial_balance[account_id] = balance
                
        return trial_balance
    
    def get_income_statement(self, start_date: datetime, end_date: datetime) -> Dict:
        """Generate income statement for a period"""
        revenue_total = 0.0
        expense_total = 0.0
        
        revenue_accounts = []
        expense_accounts = []
        
        for account in self.accounts.values():
            transactions = account.get_transactions_by_period(start_date, end_date)
            period_balance = sum(t.amount for t in transactions)
            
            if abs(period_balance) > 0.01:
                if account.account_type == AccountType.REVENUE:
                    # Revenue accounts have credit balances (negative in our system)
                    actual_revenue = -period_balance
                    revenue_total += actual_revenue
                    revenue_accounts.append({
                        'account_id': account.account_id,
                        'account_name': account.name,
                        'amount': actual_revenue
                    })
                elif account.account_type == AccountType.EXPENSE:
                    # Expense accounts have debit balances (positive in our system)
                    expense_total += period_balance
                    expense_accounts.append({
                        'account_id': account.account_id,
                        'account_name': account.name,
                        'amount': period_balance
                    })
        
        net_income = revenue_total - expense_total
        
        return {
            'period': f"{start_date.strftime('%Y-%m-%d')} to {end_date.strftime('%Y-%m-%d')}",
            'revenue': {
                'accounts': revenue_accounts,
                'total': revenue_total
            },
            'expenses': {
                'accounts': expense_accounts,
                'total': expense_total
            },
            'net_income': net_income
        }
    
    def get_balance_sheet(self, as_of_date: datetime = None) -> Dict:
        """Generate balance sheet as of a specific date"""
        if as_of_date is None:
            as_of_date = datetime.now()
            
        assets = []
        liabilities = []
        equity = []
        
        total_assets = 0.0
        total_liabilities = 0.0
        total_equity = 0.0
        
        for account in self.accounts.values():
            balance = account.get_balance_at_date(as_of_date)
            
            if abs(balance) > 0.01:
                account_info = {
                    'account_id': account.account_id,
                    'account_name': account.name,
                    'amount': balance
                }
                
                if account.account_type == AccountType.ASSET:
                    assets.append(account_info)
                    total_assets += balance
                elif account.account_type == AccountType.LIABILITY:
                    # Liabilities have credit balances (negative in our system)
                    account_info['amount'] = -balance
                    liabilities.append(account_info)
                    total_liabilities += -balance
                elif account.account_type == AccountType.EQUITY:
                    # Equity has credit balances (negative in our system)
                    account_info['amount'] = -balance
                    equity.append(account_info)
                    total_equity += -balance
        
        return {
            'as_of_date': as_of_date.strftime('%Y-%m-%d'),
            'assets': {
                'accounts': assets,
                'total': total_assets
            },
            'liabilities': {
                'accounts': liabilities,
                'total': total_liabilities
            },
            'equity': {
                'accounts': equity,
                'total': total_equity
            },
            'total_liabilities_and_equity': total_liabilities + total_equity
        }
    
    def get_account_ledger(self, account_id: str, start_date: datetime = None, 
                          end_date: datetime = None) -> List[Dict]:
        """Get detailed transaction history for an account"""
        account = self.get_account(account_id)
        if not account:
            return []
            
        transactions = account.transactions
        if start_date:
            transactions = [t for t in transactions if t.date >= start_date]
        if end_date:
            transactions = [t for t in transactions if t.date <= end_date]
            
        # Sort by date
        transactions.sort(key=lambda x: x.date)
        
        ledger_entries = []
        running_balance = 0.0
        
        for transaction in transactions:
            running_balance += transaction.amount
            ledger_entries.append({
                'date': transaction.date.strftime('%Y-%m-%d'),
                'description': transaction.description,
                'reference': transaction.reference,
                'debit': transaction.amount if transaction.amount > 0 else 0,
                'credit': -transaction.amount if transaction.amount < 0 else 0,
                'balance': running_balance
            })
            
        return ledger_entries
    
    def export_to_json(self, filename: str = None) -> str:
        """Export all data to JSON format"""
        if filename is None:
            filename = f"ledger_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            
        export_data = {
            'company_name': self.company_name,
            'export_date': datetime.now().isoformat(),
            'accounts': [],
            'journal_entries': []
        }
        
        # Export accounts
        for account in self.accounts.values():
            account_data = {
                'account_id': account.account_id,
                'name': account.name,
                'account_type': account.account_type.value,
                'subtype': account.subtype.value if account.subtype else None,
                'balance': account.balance,
                'transactions': [
                    {
                        'date': t.date.isoformat(),
                        'description': t.description,
                        'reference': t.reference,
                        'amount': t.amount
                    } for t in account.transactions
                ]
            }
            export_data['accounts'].append(account_data)
        
        # Export journal entries
        for je in self.journal_entries:
            je_data = {
                'entry_id': je.entry_id,
                'date': je.date.isoformat(),
                'description': je.description,
                'reference': je.reference,
                'posted': je.posted,
                'lines': [
                    {
                        'account_id': line.account_id,
                        'debit_amount': line.debit_amount,
                        'credit_amount': line.credit_amount,
                        'description': line.description
                    } for line in je.lines
                ]
            }
            export_data['journal_entries'].append(je_data)
        
        with open(filename, 'w') as f:
            json.dump(export_data, f, indent=2)
            
        return filename