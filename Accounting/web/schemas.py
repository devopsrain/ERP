"""
Pydantic request/response schemas for the EBMS REST API.

Used by FastAPI route handlers for automatic validation, type coercion,
and OpenAPI documentation generation.

Import pattern in route handlers:
    from schemas import EmployeeCreate, JournalEntryCreate, ...
"""
from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


# ── Shared / primitives ───────────────────────────────────────────────────────

class _Base(BaseModel):
    model_config = {"str_strip_whitespace": True, "use_enum_values": True}


# ── Employees ─────────────────────────────────────────────────────────────────

class EmployeeCategory(str, Enum):
    permanent   = "permanent"
    contract    = "contract"
    part_time   = "part_time"
    consultant  = "consultant"
    intern      = "intern"


class EmployeeCreate(_Base):
    employee_id:    str               = Field(..., min_length=2, max_length=50, description="Unique employee ID (e.g. EMP-001)")
    name:           str               = Field(..., min_length=2, max_length=200)
    tin_number:     str               = Field(..., pattern=r"^\d{9}$", description="9-digit Ethiopian TIN")
    category:       EmployeeCategory
    department:     Optional[str]     = Field(None, max_length=100)
    position:       Optional[str]     = Field(None, max_length=200)
    basic_salary:   Decimal           = Field(..., gt=0, description="Monthly basic salary in ETB")
    hire_date:      date
    bank_account:   Optional[str]     = Field(None, max_length=50)
    phone_number:   Optional[str]     = Field(None, max_length=20)
    manager:        Optional[str]     = Field(None, max_length=200)
    date_of_birth:  Optional[date]    = None

    @field_validator("basic_salary")
    @classmethod
    def salary_reasonable(cls, v):
        if v > 10_000_000:
            raise ValueError("Basic salary exceeds ETB 10,000,000 — check for input error")
        return v


class EmployeeUpdate(_Base):
    name:           Optional[str]           = Field(None, min_length=2, max_length=200)
    department:     Optional[str]           = Field(None, max_length=100)
    position:       Optional[str]           = Field(None, max_length=200)
    basic_salary:   Optional[Decimal]       = Field(None, gt=0)
    bank_account:   Optional[str]           = Field(None, max_length=50)
    phone_number:   Optional[str]           = Field(None, max_length=20)
    manager:        Optional[str]           = Field(None, max_length=200)
    is_active:      Optional[bool]          = None


# ── Journal Entries ───────────────────────────────────────────────────────────

class JournalLine(_Base):
    account_code:   str     = Field(..., min_length=1, max_length=20)
    description:    str     = Field(..., min_length=1, max_length=500)
    debit:          Decimal = Field(default=Decimal("0.00"), ge=0)
    credit:         Decimal = Field(default=Decimal("0.00"), ge=0)

    @model_validator(mode="after")
    def debit_or_credit(self) -> "JournalLine":
        if self.debit == 0 and self.credit == 0:
            raise ValueError("Each line must have a non-zero debit or credit")
        if self.debit > 0 and self.credit > 0:
            raise ValueError("A line cannot have both debit and credit amounts")
        return self


class JournalEntryCreate(_Base):
    date:           date
    description:    str               = Field(..., min_length=3, max_length=500)
    reference:      Optional[str]     = Field(None, max_length=100)
    lines:          List[JournalLine] = Field(..., min_length=2, description="At least two lines required")

    @model_validator(mode="after")
    def balanced(self) -> "JournalEntryCreate":
        total_debit  = sum(l.debit  for l in self.lines)
        total_credit = sum(l.credit for l in self.lines)
        if abs(total_debit - total_credit) > Decimal("0.005"):
            raise ValueError(
                f"Journal entry is not balanced: debits={total_debit} credits={total_credit}"
            )
        return self


# ── Chart of Accounts ─────────────────────────────────────────────────────────

class AccountType(str, Enum):
    asset     = "Asset"
    liability = "Liability"
    equity    = "Equity"
    revenue   = "Revenue"
    expense   = "Expense"


class AccountCreate(_Base):
    account_code:     str         = Field(..., min_length=1, max_length=20)
    account_name:     str         = Field(..., min_length=2, max_length=200)
    account_type:     AccountType
    account_subtype:  Optional[str] = Field(None, max_length=100)
    parent_account:   Optional[str] = Field(None, max_length=20)
    description:      Optional[str] = Field(None, max_length=500)
    normal_balance:   str           = Field(default="debit", pattern=r"^(debit|credit)$")
    current_balance:  Decimal       = Field(default=Decimal("0.00"), ge=0)


# ── VAT ───────────────────────────────────────────────────────────────────────

class VATType(str, Enum):
    standard = "standard"    # 15%
    exempt   = "exempt"
    zero     = "zero_rated"
    withheld = "withholding"


class VATIncomeCreate(_Base):
    date:         date
    transaction_type: str       = Field(..., max_length=100)
    customer:     Optional[str] = Field(None, max_length=200)
    tin:          Optional[str] = Field(None, pattern=r"^\d{9}$")
    description:  str           = Field(..., min_length=2, max_length=500)
    amount:       Decimal       = Field(..., gt=0)
    vat_type:     VATType       = VATType.standard
    invoice_ref:  Optional[str] = Field(None, max_length=100)


class VATExpenseCreate(_Base):
    date:         date
    supplier:     Optional[str] = Field(None, max_length=200)
    tin:          Optional[str] = Field(None, pattern=r"^\d{9}$")
    description:  str           = Field(..., min_length=2, max_length=500)
    amount:       Decimal       = Field(..., gt=0)
    vat_type:     VATType       = VATType.standard
    invoice_ref:  Optional[str] = Field(None, max_length=100)


# ── Inventory ─────────────────────────────────────────────────────────────────

class ValuationMethod(str, Enum):
    fifo    = "FIFO"
    lifo    = "LIFO"
    average = "Average"


class InventoryItemCreate(_Base):
    item_code:          str             = Field(..., min_length=1, max_length=50)
    item_name:          str             = Field(..., min_length=2, max_length=200)
    category:           Optional[str]  = Field(None, max_length=100)
    quantity:           Decimal         = Field(..., ge=0)
    unit:               Optional[str]  = Field(None, max_length=50)
    unit_price:         Decimal         = Field(..., ge=0)
    reorder_level:      int             = Field(default=0, ge=0)
    valuation_method:   ValuationMethod = ValuationMethod.fifo
    description:        Optional[str]  = Field(None, max_length=500)


# ── CPO ───────────────────────────────────────────────────────────────────────

class CPOCreate(_Base):
    payee_name:     str           = Field(..., min_length=2, max_length=200)
    amount:         Decimal       = Field(..., gt=0)
    date:           date
    purpose:        str           = Field(..., min_length=3, max_length=500)
    bid_reference:  Optional[str] = Field(None, max_length=100)
    bank_name:      Optional[str] = Field(None, max_length=200)
    cpo_number:     Optional[str] = Field(None, max_length=100)


# ── Letters ───────────────────────────────────────────────────────────────────

class LetterCreate(_Base):
    subject:    str           = Field(..., min_length=3, max_length=300)
    to:         str           = Field(..., min_length=2, max_length=300)
    to_address: Optional[str] = Field(None, max_length=500)
    body:       str           = Field(..., min_length=10)
    category:   Optional[str] = Field(None, max_length=100)
    cc:         Optional[str] = Field(None, max_length=500)


# ── Bids ──────────────────────────────────────────────────────────────────────

class BidStatus(str, Enum):
    open        = "Open"
    submitted   = "Submitted"
    won         = "Won"
    lost        = "Lost"
    withdrawn   = "Withdrawn"


class BidCreate(_Base):
    organisation:   str           = Field(..., min_length=2, max_length=300)
    description:    str           = Field(..., min_length=3, max_length=1000)
    deadline:       date
    amount:         Optional[Decimal] = Field(None, ge=0)
    reference:      Optional[str] = Field(None, max_length=100)
    handler:        Optional[str] = Field(None, max_length=200)
    status:         BidStatus     = BidStatus.open


# ── Auth / User Management (admin-only) ───────────────────────────────────────

class UserCreate(_Base):
    username:       str = Field(..., min_length=3, max_length=50, pattern=r"^[a-zA-Z0-9_.-]+$")
    full_name:      str = Field(..., min_length=2, max_length=200)
    email:          str = Field(..., max_length=254)
    password:       str = Field(..., min_length=12, description="Minimum 12 characters")
    privilege_level: str = Field(default="viewer",
                                  pattern=r"^(viewer|data_entry|operator|manager|admin|super_admin)$")
    phone:          Optional[str] = Field(None, max_length=20)


class PasswordChange(_Base):
    current_password: str = Field(..., min_length=1)
    new_password:     str = Field(..., min_length=12, description="Minimum 12 characters (NIST SP 800-63B)")
    confirm_password: str

    @model_validator(mode="after")
    def passwords_match(self) -> "PasswordChange":
        if self.new_password != self.confirm_password:
            raise ValueError("new_password and confirm_password do not match")
        return self
