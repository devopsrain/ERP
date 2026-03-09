"""
Account model for the accounting software
"""
from enum import Enum
from typing import List, Dict
from dataclasses import dataclass
from datetime import datetime


class AccountType(Enum):
    ASSET = "Asset"
    LIABILITY = "Liability"
    EQUITY = "Equity"
    REVENUE = "Revenue"
    EXPENSE = "Expense"


class AccountSubType(Enum):
    # Assets
    CURRENT_ASSET = "Current Asset"
    FIXED_ASSET = "Fixed Asset"
    
    # Liabilities
    CURRENT_LIABILITY = "Current Liability"
    LONG_TERM_LIABILITY = "Long-term Liability"
    
    # Equity
    OWNERS_EQUITY = "Owner's Equity"
    RETAINED_EARNINGS = "Retained Earnings"
    
    # Revenue
    OPERATING_REVENUE = "Operating Revenue"
    OTHER_REVENUE = "Other Revenue"
    
    # Expenses
    OPERATING_EXPENSE = "Operating Expense"
    ADMINISTRATIVE_EXPENSE = "Administrative Expense"
    FINANCIAL_EXPENSE = "Financial Expense"


@dataclass
class Transaction:
    date: datetime
    description: str
    reference: str
    amount: float


class Account:
    def __init__(self, account_id: str, name: str, account_type: AccountType, 
                 subtype: AccountSubType = None, parent_account: str = None):
        self.account_id = account_id
        self.name = name
        self.account_type = account_type
        self.subtype = subtype
        self.parent_account = parent_account
        self.transactions: List[Transaction] = []
        self.balance = 0.0
        
    def add_transaction(self, transaction: Transaction):
        """Add a transaction to this account"""
        self.transactions.append(transaction)
        
        # Update balance based on account type
        if self.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
            # Debit increases assets and expenses
            self.balance += transaction.amount
        else:
            # Credit increases liabilities, equity, and revenue
            self.balance -= transaction.amount
    
    def get_balance(self) -> float:
        """Get current account balance"""
        return self.balance
    
    def get_balance_at_date(self, date: datetime) -> float:
        """Get account balance at a specific date"""
        balance = 0.0
        for transaction in self.transactions:
            if transaction.date <= date:
                if self.account_type in [AccountType.ASSET, AccountType.EXPENSE]:
                    balance += transaction.amount
                else:
                    balance -= transaction.amount
        return balance
    
    def get_transactions_by_period(self, start_date: datetime, end_date: datetime) -> List[Transaction]:
        """Get transactions within a date range"""
        return [t for t in self.transactions if start_date <= t.date <= end_date]
    
    def __str__(self):
        return f"{self.account_id}: {self.name} ({self.account_type.value}) - Balance: ${self.balance:,.2f}"
    
    def __repr__(self):
        return f"Account(id='{self.account_id}', name='{self.name}', type='{self.account_type}', balance={self.balance})"