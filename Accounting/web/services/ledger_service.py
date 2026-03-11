"""
Ledger Service — business logic layer for double-entry accounting.

Responsibilities:
- Validate journal entries before posting (debits == credits, account exists)
- Post journal entries atomically (debit one account, credit another)
- Calculate account balances and financial statement line items
- Generate trial balance, income statement, balance sheet data structures

Routes should call this service instead of interacting with data stores
directly so that accounting rules (e.g. double-entry balance check) are
always enforced in one place.

Example usage:
    from services.ledger_service import ledger_service

    @router.post("/journal/add")
    async def add_entry(request: Request, form=Depends(...)):
        result = ledger_service.post_entry(
            company_id=request.state.company_id,
            debit_account=form["debit_account"],
            credit_account=form["credit_account"],
            amount=form["amount"],
            description=form["description"],
            reference=form.get("reference"),
            created_by=request.session["username"],
        )
        if result["success"]:
            flash(request, "Journal entry posted", "success")
        else:
            flash(request, result["error"], "danger")
        return RedirectResponse("/journal/", status_code=303)
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)


class LedgerService:
    """
    Business logic for double-entry general ledger.

    Enforces accounting rules:
    - Every journal entry must have equal debits and credits.
    - Accounts must exist and be active before an entry is posted.
    - Account balances are updated atomically with each posting.
    """

    # ── Validation ────────────────────────────────────────────────

    def validate_entry(
        self,
        debit_account_id: str,
        credit_account_id: str,
        amount: float,
        company_id: str,
    ) -> dict:
        """
        Validate a journal entry before posting.

        Returns {"valid": True} or {"valid": False, "errors": [...]}
        """
        errors = []

        if amount <= 0:
            errors.append("Amount must be greater than zero.")

        if debit_account_id == credit_account_id:
            errors.append("Debit and credit accounts must be different.")

        try:
            from chart_of_accounts_data_store import accounts_store
            debit_acct  = accounts_store.get_account(debit_account_id, company_id)
            credit_acct = accounts_store.get_account(credit_account_id, company_id)

            if not debit_acct:
                errors.append(f"Debit account '{debit_account_id}' not found.")
            elif not debit_acct.get("is_active"):
                errors.append(f"Debit account '{debit_account_id}' is inactive.")

            if not credit_acct:
                errors.append(f"Credit account '{credit_account_id}' not found.")
            elif not credit_acct.get("is_active"):
                errors.append(f"Credit account '{credit_account_id}' is inactive.")
        except Exception as e:
            logger.warning("Account lookup failed during validation: %s", e)

        return {"valid": not errors, "errors": errors}

    # ── Posting ───────────────────────────────────────────────────

    def post_entry(
        self,
        company_id: str,
        debit_account_id: str,
        credit_account_id: str,
        amount: float,
        description: str,
        reference: Optional[str] = None,
        created_by: Optional[str] = None,
        entry_date: Optional[str] = None,
    ) -> dict:
        """
        Validate and post a double-entry journal entry.

        On success returns {"success": True, "entry_id": "..."}.
        On failure returns {"success": False, "error": "..."}.
        """
        validation = self.validate_entry(
            debit_account_id, credit_account_id, amount, company_id
        )
        if not validation["valid"]:
            return {"success": False, "error": "; ".join(validation["errors"])}

        try:
            from journal_entry_data_store import journal_store
            entry = journal_store.add_entry(
                company_id=company_id,
                debit_account_id=debit_account_id,
                credit_account_id=credit_account_id,
                amount=amount,
                description=description,
                reference=reference,
                created_by=created_by,
                date=entry_date,
            )
            logger.info(
                "Journal entry posted: %s  DR %s  CR %s  %.2f  by %s",
                entry.get("entry_id"), debit_account_id, credit_account_id,
                amount, created_by,
            )
            return {"success": True, "entry_id": entry.get("entry_id"), "entry": entry}
        except Exception as e:
            logger.error("post_entry failed: %s", e)
            return {"success": False, "error": str(e)}

    # ── Reporting helpers ─────────────────────────────────────────

    def get_trial_balance(self, company_id: str) -> list:
        """
        Return a list of accounts with debit/credit totals for trial balance.
        Each item: {account_id, account_name, account_type, debit_total, credit_total}
        """
        try:
            from chart_of_accounts_data_store import accounts_store
            return accounts_store.get_trial_balance(company_id)
        except Exception as e:
            logger.error("get_trial_balance failed: %s", e)
            return []

    def get_income_statement(
        self, company_id: str, start_date: str, end_date: str
    ) -> dict:
        """
        Return income and expense totals for a date range.
        {"revenue": float, "expenses": float, "net_income": float, "lines": [...]}
        """
        try:
            from income_expense_data_store import income_expense_store
            return income_expense_store.get_income_statement(
                company_id, start_date, end_date
            )
        except Exception as e:
            logger.error("get_income_statement failed: %s", e)
            return {"revenue": 0, "expenses": 0, "net_income": 0, "lines": []}

    def get_balance_sheet(self, company_id: str, as_of_date: str) -> dict:
        """
        Return assets, liabilities, equity as of a given date.
        {"assets": float, "liabilities": float, "equity": float, "lines": [...]}
        """
        try:
            from chart_of_accounts_data_store import accounts_store
            return accounts_store.get_balance_sheet(company_id, as_of_date)
        except Exception as e:
            logger.error("get_balance_sheet failed: %s", e)
            return {"assets": 0, "liabilities": 0, "equity": 0, "lines": []}


# ── Module-level singleton ────────────────────────────────────────
ledger_service = LedgerService()
