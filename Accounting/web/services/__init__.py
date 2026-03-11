"""
Service Layer — Ethiopian Business Management System

This package contains business-logic services that sit between
FastAPI routes and raw data stores.

Architecture:
    routes/  →  services/  →  *_data_store.py  →  db.py (PostgreSQL)

Why services?
- Routes handle HTTP I/O only (parse request, return response).
- Data stores handle DB queries only.
- Services own all business logic, validations, and cross-module
  orchestration — independently testable without HTTP context.

Available services:
    payroll_service   PayrollService    — payroll calculation & processing
    ledger_service    LedgerService     — double-entry ledger operations
"""
from services.payroll_service import PayrollService, payroll_service
from services.ledger_service import LedgerService, ledger_service

__all__ = ["PayrollService", "payroll_service", "LedgerService", "ledger_service"]
