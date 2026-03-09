"""
Journal Entry system for double-entry bookkeeping
"""
from dataclasses import dataclass, field
from datetime import datetime
from typing import List, Dict, Optional
from uuid import uuid4


@dataclass
class JournalEntryLine:
    """Individual line item in a journal entry"""
    account_id: str
    debit_amount: float = 0.0
    credit_amount: float = 0.0
    description: str = ""
    
    def __post_init__(self):
        # Ensure only debit OR credit is set, not both
        if self.debit_amount > 0 and self.credit_amount > 0:
            raise ValueError("An entry line cannot have both debit and credit amounts")
        if self.debit_amount == 0 and self.credit_amount == 0:
            raise ValueError("An entry line must have either a debit or credit amount")


@dataclass
class JournalEntry:
    """Complete journal entry with multiple lines"""
    entry_id: str = field(default_factory=lambda: str(uuid4()))
    date: datetime = field(default_factory=datetime.now)
    description: str = ""
    reference: str = ""
    lines: List[JournalEntryLine] = field(default_factory=list)
    posted: bool = False
    
    def add_line(self, account_id: str, debit_amount: float = 0.0, 
                 credit_amount: float = 0.0, description: str = ""):
        """Add a line to the journal entry"""
        line = JournalEntryLine(
            account_id=account_id,
            debit_amount=debit_amount,
            credit_amount=credit_amount,
            description=description
        )
        self.lines.append(line)
    
    def validate(self) -> bool:
        """Validate that the journal entry is balanced (debits = credits)"""
        total_debits = sum(line.debit_amount for line in self.lines)
        total_credits = sum(line.credit_amount for line in self.lines)
        return abs(total_debits - total_credits) < 0.01  # Allow for small rounding errors
    
    def get_total_debits(self) -> float:
        """Get total debit amount"""
        return sum(line.debit_amount for line in self.lines)
    
    def get_total_credits(self) -> float:
        """Get total credit amount"""
        return sum(line.credit_amount for line in self.lines)
    
    def post(self) -> bool:
        """Mark entry as posted (final)"""
        if self.validate():
            self.posted = True
            return True
        return False
    
    def __str__(self):
        status = "Posted" if self.posted else "Draft"
        return f"JE {self.entry_id[:8]} - {self.date.strftime('%Y-%m-%d')} - {self.description} [{status}]"


class JournalEntryBuilder:
    """Helper class to build journal entries with common patterns"""
    
    @staticmethod
    def simple_transaction(account_debit: str, account_credit: str, amount: float,
                          description: str, date: datetime = None, reference: str = "") -> JournalEntry:
        """Create a simple two-line journal entry"""
        if date is None:
            date = datetime.now()
            
        entry = JournalEntry(
            date=date,
            description=description,
            reference=reference
        )
        entry.add_line(account_debit, debit_amount=amount)
        entry.add_line(account_credit, credit_amount=amount)
        return entry
    
    @staticmethod
    def cash_sale(cash_account: str, revenue_account: str, amount: float,
                  description: str, date: datetime = None) -> JournalEntry:
        """Create a cash sale journal entry"""
        return JournalEntryBuilder.simple_transaction(
            cash_account, revenue_account, amount, 
            f"Cash Sale: {description}", date
        )
    
    @staticmethod
    def expense_payment(expense_account: str, cash_account: str, amount: float,
                       description: str, date: datetime = None) -> JournalEntry:
        """Create an expense payment journal entry"""
        return JournalEntryBuilder.simple_transaction(
            expense_account, cash_account, amount,
            f"Expense Payment: {description}", date
        )
    
    @staticmethod
    def asset_purchase(asset_account: str, cash_account: str, amount: float,
                      description: str, date: datetime = None) -> JournalEntry:
        """Create an asset purchase journal entry"""
        return JournalEntryBuilder.simple_transaction(
            asset_account, cash_account, amount,
            f"Asset Purchase: {description}", date
        )