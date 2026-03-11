"""
SQLAlchemy ORM Models — Ethiopian Business Management System

These declarative models mirror the PostgreSQL schema defined in
`init_db.sql` / Alembic migrations.  Use them for type-checked queries,
relationship navigation, and as the source of truth for generating new
Alembic migrations via `alembic revision --autogenerate`.

Usage (with async session from async_db.py):
    from orm_models import User, LoginHistory, SiemEvent
    from async_db import get_async_session

    async with get_async_session() as session:
        user = await session.get(User, user_id)
        history = await session.execute(
            select(LoginHistory).where(LoginHistory.user_id == user_id)
        )
"""
from __future__ import annotations

import uuid as _uuid
from datetime import datetime
from typing import List, Optional

from sqlalchemy import (
    BigInteger, Boolean, Column, DateTime, ForeignKey,
    Integer, String, Text, func,
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Shared declarative base — all ORM models inherit from this."""
    pass


# ── Users & Auth ──────────────────────────────────────────────────

class User(Base):
    __tablename__ = "users"

    user_id:         Mapped[str]  = mapped_column(UUID(as_uuid=False), primary_key=True,
                                                   default=lambda: str(_uuid.uuid4()))
    username:        Mapped[str]  = mapped_column(String(80), unique=True, nullable=False, index=True)
    password_hash:   Mapped[str]  = mapped_column(String(255), nullable=False)
    full_name:       Mapped[str]  = mapped_column(String(120), nullable=False)
    email:           Mapped[Optional[str]] = mapped_column(String(120), unique=True)
    privilege_level: Mapped[str]  = mapped_column(String(20), nullable=False, default="viewer")
    company_id:      Mapped[Optional[str]] = mapped_column(String(80), index=True)
    is_active:       Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_logins:   Mapped[int]  = mapped_column(Integer, default=0)
    locked_until:    Mapped[Optional[datetime]] = mapped_column(DateTime)
    created_at:      Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at:      Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                       onupdate=func.now())

    login_history: Mapped[List["LoginHistory"]] = relationship(
        back_populates="user", lazy="select", cascade="all, delete-orphan"
    )


class LoginHistory(Base):
    __tablename__ = "login_history"

    id:          Mapped[int]  = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id:     Mapped[str]  = mapped_column(
        ForeignKey("users.user_id", ondelete="CASCADE"), nullable=False, index=True
    )
    ip_address:  Mapped[str]  = mapped_column(String(45), nullable=False)
    device_name: Mapped[str]  = mapped_column(String(120), nullable=False, default="Unknown")
    user_agent:  Mapped[Optional[str]] = mapped_column(Text)
    success:     Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    logged_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)

    user: Mapped["User"] = relationship(back_populates="login_history")


# ── SIEM ──────────────────────────────────────────────────────────

class SiemEvent(Base):
    __tablename__ = "siem_events"

    id:          Mapped[int]  = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_type:  Mapped[str]  = mapped_column(String(80), nullable=False, index=True)
    severity:    Mapped[str]  = mapped_column(String(20), nullable=False, default="info")
    username:    Mapped[Optional[str]] = mapped_column(String(80), index=True)
    ip_address:  Mapped[Optional[str]] = mapped_column(String(45))
    company_id:  Mapped[Optional[str]] = mapped_column(String(80), index=True)
    message:     Mapped[Optional[str]] = mapped_column(Text)
    details:     Mapped[Optional[str]] = mapped_column(Text)   # JSON payload
    created_at:  Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


# ── Chart of Accounts ─────────────────────────────────────────────

class Account(Base):
    __tablename__ = "accounts"

    account_id:       Mapped[str]  = mapped_column(String(20), primary_key=True)
    company_id:       Mapped[str]  = mapped_column(String(80), nullable=False, index=True)
    account_name:     Mapped[str]  = mapped_column(String(120), nullable=False)
    account_type:     Mapped[str]  = mapped_column(String(30), nullable=False)
    account_sub_type: Mapped[Optional[str]] = mapped_column(String(50))
    parent_id:        Mapped[Optional[str]] = mapped_column(ForeignKey("accounts.account_id"))
    balance:          Mapped[int]  = mapped_column(BigInteger, nullable=False, default=0)
    is_active:        Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at:       Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    children: Mapped[List["Account"]] = relationship(
        "Account", backref="parent", remote_side="Account.account_id", lazy="select"
    )
    debit_entries:  Mapped[List["JournalEntry"]] = relationship(
        "JournalEntry", foreign_keys="JournalEntry.debit_account_id", lazy="select"
    )
    credit_entries: Mapped[List["JournalEntry"]] = relationship(
        "JournalEntry", foreign_keys="JournalEntry.credit_account_id", lazy="select"
    )


# ── Journal Entries ───────────────────────────────────────────────

class JournalEntry(Base):
    __tablename__ = "journal_entries"

    entry_id:          Mapped[str]  = mapped_column(UUID(as_uuid=False), primary_key=True,
                                                     default=lambda: str(_uuid.uuid4()))
    company_id:        Mapped[str]  = mapped_column(String(80), nullable=False, index=True)
    date:              Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    description:       Mapped[str]  = mapped_column(Text, nullable=False)
    reference:         Mapped[Optional[str]] = mapped_column(String(80))
    debit_account_id:  Mapped[str]  = mapped_column(ForeignKey("accounts.account_id"), nullable=False)
    credit_account_id: Mapped[str]  = mapped_column(ForeignKey("accounts.account_id"), nullable=False)
    amount:            Mapped[int]  = mapped_column(BigInteger, nullable=False)  # stored in cents
    created_by:        Mapped[Optional[str]] = mapped_column(String(80))
    created_at:        Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


# ── Inventory ─────────────────────────────────────────────────────

class InventoryItem(Base):
    __tablename__ = "inventory_items"

    item_id:      Mapped[str]  = mapped_column(UUID(as_uuid=False), primary_key=True,
                                                default=lambda: str(_uuid.uuid4()))
    company_id:   Mapped[str]  = mapped_column(String(80), nullable=False, index=True)
    item_name:    Mapped[str]  = mapped_column(String(120), nullable=False)
    sku:          Mapped[Optional[str]] = mapped_column(String(80), index=True)
    category:     Mapped[Optional[str]] = mapped_column(String(80))
    unit:         Mapped[str]  = mapped_column(String(20), nullable=False, default="unit")
    quantity:     Mapped[int]  = mapped_column(Integer, nullable=False, default=0)
    reorder_level:Mapped[int]  = mapped_column(Integer, nullable=False, default=0)
    unit_cost:    Mapped[int]  = mapped_column(BigInteger, nullable=False, default=0)  # cents
    is_active:    Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at:   Mapped[datetime] = mapped_column(DateTime, server_default=func.now(),
                                                    onupdate=func.now())
