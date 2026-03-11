"""
Payroll Service — business logic layer for Ethiopian payroll calculations.

All business rules (tax brackets, pension, overtime, allowances) live here.
Routes call this service; the service calls data stores for DB access.
This separation means payroll logic can be unit-tested without HTTP context.

Example usage from a route:
    from services.payroll_service import payroll_service

    @router.post("/calculate")
    async def calculate(request: Request, form=Depends(...)):
        result = payroll_service.calculate(
            employee_id=form["employee_id"],
            month=form["month"],
            year=form["year"],
            company_id=request.state.company_id,
        )
        return templates.TemplateResponse("payroll/result.html", {"result": result, ...})
"""
from __future__ import annotations

import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── Ethiopian income tax brackets (Proclamation 1263/2023) ───────
# (monthly_income_upper_limit, rate, deduction)
_TAX_BRACKETS = [
    (600,      0.00,  0.00),
    (1_650,    0.10,  60.00),
    (3_200,    0.15,  142.50),
    (5_250,    0.20,  302.50),
    (7_800,    0.25,  565.00),
    (10_900,   0.30,  955.00),
    (float("inf"), 0.35, 1_500.00),
]

# Pension contribution rates
_EMPLOYEE_PENSION_RATE = 0.07   # 7% employee contribution
_EMPLOYER_PENSION_RATE = 0.11   # 11% employer contribution


class PayrollService:
    """
    Business logic for Ethiopian payroll.

    Wraps the raw payroll_data_store for DB access and adds:
    - Tax bracket calculations
    - Pension deductions
    - Net pay / gross pay derivation
    - Payslip generation helpers
    - Payroll run orchestration (calculate all employees in a company)
    """

    # ── Tax calculation ───────────────────────────────────────────

    def calculate_income_tax(self, gross_salary: float) -> float:
        """Return the monthly income tax amount for a given gross salary (ETB)."""
        for upper, rate, deduction in _TAX_BRACKETS:
            if gross_salary <= upper:
                return round(gross_salary * rate - deduction, 2)
        return 0.0

    def calculate_pension(self, gross_salary: float) -> dict:
        """Return employee and employer pension contribution amounts."""
        return {
            "employee": round(gross_salary * _EMPLOYEE_PENSION_RATE, 2),
            "employer": round(gross_salary * _EMPLOYER_PENSION_RATE, 2),
        }

    def calculate_net_pay(
        self,
        gross_salary: float,
        allowances: float = 0.0,
        deductions: float = 0.0,
    ) -> dict:
        """
        Full net-pay calculation including tax and pension.

        Returns a dict with all components so routes/templates can display
        an itemised payslip.
        """
        taxable = gross_salary + allowances
        income_tax = self.calculate_income_tax(taxable)
        pension = self.calculate_pension(gross_salary)

        total_deductions = income_tax + pension["employee"] + deductions
        net_pay = round(taxable - total_deductions, 2)

        return {
            "gross_salary":       gross_salary,
            "allowances":         allowances,
            "taxable_income":     taxable,
            "income_tax":         income_tax,
            "employee_pension":   pension["employee"],
            "employer_pension":   pension["employer"],
            "other_deductions":   deductions,
            "total_deductions":   round(total_deductions, 2),
            "net_pay":            net_pay,
        }

    # ── Data store delegation ─────────────────────────────────────

    def get_employee(self, employee_id: str, company_id: str) -> Optional[dict]:
        try:
            from payroll_data_store import payroll_store
            return payroll_store.get_employee(employee_id)
        except Exception as e:
            logger.error("get_employee failed: %s", e)
            return None

    def list_employees(self, company_id: str) -> list:
        try:
            from payroll_data_store import payroll_store
            return payroll_store.get_employees(company_id=company_id)
        except Exception as e:
            logger.error("list_employees failed: %s", e)
            return []

    def calculate_employee_payroll(
        self,
        employee_id: str,
        company_id: str,
        month: int,
        year: int,
    ) -> dict:
        """
        Calculate payroll for one employee for a given month/year.
        Fetches employee data from the DB, runs tax/pension calc, returns full breakdown.
        """
        employee = self.get_employee(employee_id, company_id)
        if not employee:
            return {"error": f"Employee {employee_id} not found"}

        gross = float(employee.get("basic_salary", 0))
        allowances = float(employee.get("allowances", 0))
        other_deductions = float(employee.get("other_deductions", 0))

        breakdown = self.calculate_net_pay(gross, allowances, other_deductions)
        breakdown.update({
            "employee_id":   employee_id,
            "employee_name": employee.get("full_name", ""),
            "month":         month,
            "year":          year,
            "company_id":    company_id,
        })
        return breakdown

    def run_payroll(self, company_id: str, month: int, year: int) -> list:
        """
        Process payroll for ALL employees in a company for a given period.
        Returns a list of per-employee breakdowns.
        """
        employees = self.list_employees(company_id)
        results = []
        for emp in employees:
            result = self.calculate_employee_payroll(
                emp["employee_id"], company_id, month, year
            )
            results.append(result)
        logger.info(
            "Payroll run: company=%s month=%d/%d employees=%d",
            company_id, month, year, len(results),
        )
        return results


# ── Module-level singleton ────────────────────────────────────────
payroll_service = PayrollService()
