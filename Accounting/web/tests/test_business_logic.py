"""
Business Logic Tests — Ethiopian Accounting System
====================================================
Tests core computational logic: payroll tax calculations, VAT computation,
journal entry balancing, inventory valuation, etc.

Run:  cd web && pytest tests/test_business_logic.py -v
"""
import pytest
import sys
import os

# Ensure models/ is importable
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)


# ══════════════════════════════════════════════════════════════════
#  Ethiopian Payroll Tax Calculation
# ══════════════════════════════════════════════════════════════════

class TestEthiopianPayrollCalculation:
    """
    Verify Ethiopian income tax brackets per Proclamation 1263/2023.
    Tax brackets (monthly):
        0   -   600     →  0%
        601 -  1,650    →  10%
      1,651 -  3,200    →  15%
      3,201 -  5,250    →  20%
      5,251 -  7,800    →  25%
      7,801 - 10,900    →  30%
     10,901+            →  35%
    """

    @pytest.fixture(autouse=True)
    def setup_calculator(self):
        from models.ethiopian_payroll import EthiopianPayrollCalculator
        self.calc = EthiopianPayrollCalculator()

    @pytest.mark.unit
    def test_zero_salary_no_tax(self):
        result = self.calc.calculate_income_tax(0)
        assert result == 0

    @pytest.mark.unit
    def test_below_threshold_no_tax(self):
        result = self.calc.calculate_income_tax(600)
        assert result == 0

    @pytest.mark.unit
    def test_first_bracket_10pct(self):
        # 1000 ETB: first 600 exempt, next 400 at 10% = 40
        result = self.calc.calculate_income_tax(1000)
        assert result == 40.0

    @pytest.mark.unit
    def test_second_bracket_boundary(self):
        # 1650 ETB: 600 exempt + 1050 at 10% = 105
        result = self.calc.calculate_income_tax(1650)
        assert result == 105.0

    @pytest.mark.unit
    def test_third_bracket(self):
        # 3200 ETB tax = 0 + 105 + (3200-1650)*15% = 0 + 105 + 232.5 = 337.5
        result = self.calc.calculate_income_tax(3200)
        assert abs(result - 337.5) < 0.01

    @pytest.mark.unit
    def test_high_salary_top_bracket(self):
        # 15000 ETB should use the 35% top bracket
        result = self.calc.calculate_income_tax(15000)
        assert result > 0
        # Tax should be significant on 15k
        assert result > 2000

    @pytest.mark.unit
    def test_pension_contribution_7pct(self):
        # Employee pension = 7% of basic salary
        emp_pension, emp_pension_employer = self.calc.calculate_pension_contributions(10000)
        assert emp_pension == 700.0

    @pytest.mark.unit
    def test_employer_pension_11pct(self):
        emp_pension, employer_pension = self.calc.calculate_pension_contributions(10000)
        assert employer_pension == 1100.0

    @pytest.mark.unit
    def test_payroll_item_calculation(self):
        """Net salary should always be > 0 for any positive gross salary."""
        from models.ethiopian_payroll import Employee, PayrollItem, EmployeeCategory
        from datetime import date
        employee = Employee(
            employee_id='TEST-001',
            name='Test Employee',
            basic_salary=5000.0,
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            hire_date=date(2025, 1, 1),
        )
        payroll_item = PayrollItem(
            employee=employee,
            pay_period_start=date(2026, 1, 1),
            pay_period_end=date(2026, 1, 31),
        )
        result = self.calc.calculate_payroll_item(payroll_item)
        assert result.net_salary > 0

    @pytest.mark.unit
    def test_payroll_deductions_sum(self):
        """Total deductions should equal gross - net."""
        from models.ethiopian_payroll import Employee, PayrollItem, EmployeeCategory
        from datetime import date
        employee = Employee(
            employee_id='TEST-002',
            name='Test Employee 2',
            basic_salary=8000.0,
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            hire_date=date(2025, 1, 1),
        )
        payroll_item = PayrollItem(
            employee=employee,
            pay_period_start=date(2026, 1, 1),
            pay_period_end=date(2026, 1, 31),
        )
        result = self.calc.calculate_payroll_item(payroll_item)
        expected_net = result.gross_taxable_income - result.income_tax - result.employee_pension
        assert abs(result.net_salary - expected_net) < 1.0, \
            f"Net {result.net_salary} != Gross({result.gross_taxable_income}) - Tax({result.income_tax}) - Pension({result.employee_pension}) = {expected_net}"


# ══════════════════════════════════════════════════════════════════
#  Journal Entry Balancing
# ══════════════════════════════════════════════════════════════════

class TestJournalEntryBalancing:
    """Journal entries must have balanced debits and credits."""

    @pytest.mark.unit
    def test_balanced_entry_is_valid(self):
        from models.journal_entry import JournalEntry, JournalEntryLine
        lines = [
            JournalEntryLine(account_id='1000', debit_amount=5000, credit_amount=0),
            JournalEntryLine(account_id='4000', debit_amount=0, credit_amount=5000),
        ]
        entry = JournalEntry(
            description='Test balanced entry',
            lines=lines,
        )
        assert entry.validate()

    @pytest.mark.unit
    def test_unbalanced_entry_is_invalid(self):
        from models.journal_entry import JournalEntry, JournalEntryLine
        lines = [
            JournalEntryLine(account_id='1000', debit_amount=5000, credit_amount=0),
            JournalEntryLine(account_id='4000', debit_amount=0, credit_amount=3000),
        ]
        entry = JournalEntry(
            description='Test unbalanced entry',
            lines=lines,
        )
        assert not entry.validate()

    @pytest.mark.unit
    def test_journal_entry_builder_simple_transaction(self):
        from models.journal_entry import JournalEntryBuilder
        entry = JournalEntryBuilder.simple_transaction(
            account_debit='1000',
            account_credit='4000',
            amount=10000,
            description='Builder test',
        )
        assert entry is not None
        assert entry.validate()


# ══════════════════════════════════════════════════════════════════
#  Account Model
# ══════════════════════════════════════════════════════════════════

class TestAccountModel:
    """Account type enums and basic operations."""

    @pytest.mark.unit
    def test_account_types_exist(self):
        from models.account import AccountType
        assert hasattr(AccountType, 'ASSET')
        assert hasattr(AccountType, 'LIABILITY')
        assert hasattr(AccountType, 'EQUITY')
        assert hasattr(AccountType, 'REVENUE')
        assert hasattr(AccountType, 'EXPENSE')

    @pytest.mark.unit
    def test_create_account(self):
        from models.account import Account, AccountType
        acct = Account(
            account_id='1000',
            name='Cash',
            account_type=AccountType.ASSET,
        )
        assert acct.account_id == '1000'
        assert acct.name == 'Cash'
        assert acct.account_type == AccountType.ASSET

    @pytest.mark.unit
    def test_account_balance_starts_at_zero(self):
        from models.account import Account, AccountType
        acct = Account(
            account_id='2000',
            name='Test Account',
            account_type=AccountType.LIABILITY,
        )
        assert acct.balance == 0


# ══════════════════════════════════════════════════════════════════
#  VAT Calculation
# ══════════════════════════════════════════════════════════════════

class TestVATCalculation:
    """Ethiopian VAT at 15%."""

    @pytest.mark.unit
    def test_vat_model_enums(self):
        from models.vat_portal import VATType
        assert hasattr(VATType, 'STANDARD') or len(list(VATType)) > 0

    @pytest.mark.unit
    def test_standard_vat_rate(self):
        """Standard Ethiopian VAT is 15%."""
        from models.vat_portal import VATConfiguration, VATType
        config = VATConfiguration(vat_id='STD', vat_type=VATType.STANDARD)
        assert float(config.rate) == 0.15 or config.rate == 15


# ══════════════════════════════════════════════════════════════════
#  Multi-Company Models
# ══════════════════════════════════════════════════════════════════

class TestMultiCompanyModels:
    """Company and user role models."""

    @pytest.mark.unit
    def test_user_roles_exist(self):
        from models.multi_company import UserRole
        roles = list(UserRole)
        assert len(roles) >= 3  # At least admin, HR, employee

    @pytest.mark.unit
    def test_company_status_enum(self):
        from models.multi_company import CompanyStatus
        statuses = list(CompanyStatus)
        assert len(statuses) >= 1

    @pytest.mark.unit
    def test_subscription_plans(self):
        from models.multi_company import SubscriptionPlan
        plans = list(SubscriptionPlan)
        assert len(plans) >= 2  # Basic, Professional, Enterprise
