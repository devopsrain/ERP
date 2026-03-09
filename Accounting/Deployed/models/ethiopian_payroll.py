"""
Ethiopian Payroll Calculations Module

This module provides salary and pension calculations based on Ethiopian labor law
and tax regulations as of 2026.

Key Features:
- Progressive income tax calculation
- Pension contributions (Employee and Employer)
- Social security contributions
- Allowances and deductions
- Pay slip generation
- Monthly payroll processing
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Tuple
from enum import Enum
import json


class EmployeeCategory(Enum):
    """Employee categories based on Ethiopian labor law"""
    REGULAR_EMPLOYEE = "Regular Employee"
    CONTRACT_EMPLOYEE = "Contract Employee"  
    CASUAL_WORKER = "Casual Worker"
    EXECUTIVE = "Executive"


class AllowanceType(Enum):
    """Types of allowances"""
    TRANSPORT = "Transport Allowance"
    HOUSING = "Housing Allowance"
    MEAL = "Meal Allowance"
    PHONE = "Phone Allowance"
    OVERTIME = "Overtime Pay"
    BONUS = "Performance Bonus"
    COST_OF_LIVING = "Cost of Living Allowance"


class DeductionType(Enum):
    """Types of deductions"""
    INCOME_TAX = "Income Tax"
    PENSION_EMPLOYEE = "Employee Pension (7%)"
    INCOME_TAX_ON_ALLOWANCES = "Tax on Taxable Allowances"
    UNION_DUES = "Union Dues"
    LOAN_REPAYMENT = "Loan Repayment"
    INSURANCE = "Insurance Premium"


@dataclass
class Allowance:
    """Represents an allowance item"""
    type: AllowanceType
    amount: float
    is_taxable: bool = True
    description: str = ""


@dataclass
class Deduction:
    """Represents a deduction item"""
    type: DeductionType
    amount: float
    description: str = ""


@dataclass
class Employee:
    """Employee information"""
    employee_id: str
    name: str
    category: EmployeeCategory
    basic_salary: float
    hire_date: date
    department: str = ""
    position: str = ""
    bank_account: str = ""
    tin_number: str = ""  # Tax Identification Number
    pension_number: str = ""
    is_active: bool = True
    
    # Employment terms
    work_days_per_month: int = 22
    work_hours_per_day: int = 8
    
    def __post_init__(self):
        """Validate employee data"""
        if self.basic_salary < 0:
            raise ValueError("Basic salary cannot be negative")
        if not self.employee_id or not self.name:
            raise ValueError("Employee ID and name are required")


@dataclass
class PayrollItem:
    """Individual payroll calculation item"""
    employee: Employee
    pay_period_start: date
    pay_period_end: date
    
    # Salary components
    basic_salary: float = 0
    allowances: List[Allowance] = field(default_factory=list)
    deductions: List[Deduction] = field(default_factory=list)
    
    # Calculated values (filled by calculation methods)
    gross_taxable_income: float = 0
    total_allowances: float = 0
    taxable_allowances: float = 0
    non_taxable_allowances: float = 0
    
    # Tax calculations
    income_tax: float = 0
    employee_pension: float = 0
    total_deductions: float = 0
    net_salary: float = 0
    
    # Employer contributions
    employer_pension: float = 0
    total_employer_cost: float = 0
    
    def add_allowance(self, allowance_type: AllowanceType, amount: float, 
                     is_taxable: bool = True, description: str = ""):
        """Add an allowance to the payroll item"""
        allowance = Allowance(allowance_type, amount, is_taxable, description)
        self.allowances.append(allowance)
    
    def add_deduction(self, deduction_type: DeductionType, amount: float, description: str = ""):
        """Add a deduction to the payroll item"""
        deduction = Deduction(deduction_type, amount, description)
        self.deductions.append(deduction)


class EthiopianPayrollCalculator:
    """Ethiopian payroll calculation engine"""
    
    # Ethiopian Income Tax Brackets (2026 rates)
    INCOME_TAX_BRACKETS = [
        (600, 0.00),      # First 600 ETB - 0%
        (1650, 0.10),     # Next 1050 ETB (600-1650) - 10%
        (3200, 0.15),     # Next 1550 ETB (1650-3200) - 15%
        (5250, 0.20),     # Next 2050 ETB (3200-5250) - 20%
        (7800, 0.25),     # Next 2550 ETB (5250-7800) - 25%
        (10900, 0.30),    # Next 3100 ETB (7800-10900) - 30%
        (float('inf'), 0.35)  # Above 10900 ETB - 35%
    ]
    
    # Pension rates
    EMPLOYEE_PENSION_RATE = 0.07  # 7%
    EMPLOYER_PENSION_RATE = 0.11  # 11%
    
    # Standard non-taxable allowances limits (monthly)
    NON_TAXABLE_LIMITS = {
        AllowanceType.TRANSPORT: 600,    # Transport allowance up to 600 ETB
        AllowanceType.OVERTIME: float('inf'),  # Overtime is generally taxable but calculated differently
    }
    
    def __init__(self):
        self.payroll_items: List[PayrollItem] = []
    
    def calculate_income_tax(self, taxable_income: float) -> float:
        """Calculate Ethiopian progressive income tax"""
        if taxable_income <= 0:
            return 0
        
        total_tax = 0
        remaining_income = taxable_income
        previous_bracket = 0
        
        for bracket_limit, tax_rate in self.INCOME_TAX_BRACKETS:
            if remaining_income <= 0:
                break
                
            # Calculate taxable amount in this bracket
            bracket_amount = min(remaining_income, bracket_limit - previous_bracket)
            
            # Calculate tax for this bracket
            tax_amount = bracket_amount * tax_rate
            total_tax += tax_amount
            
            # Reduce remaining income
            remaining_income -= bracket_amount
            previous_bracket = bracket_limit
            
            if bracket_limit == float('inf'):
                break
        
        return round(total_tax, 2)
    
    def calculate_pension_contributions(self, basic_salary: float) -> Tuple[float, float]:
        """Calculate employee and employer pension contributions"""
        employee_pension = round(basic_salary * self.EMPLOYEE_PENSION_RATE, 2)
        employer_pension = round(basic_salary * self.EMPLOYER_PENSION_RATE, 2)
        return employee_pension, employer_pension
    
    def process_allowances(self, allowances: List[Allowance]) -> Dict[str, float]:
        """Process allowances and separate taxable from non-taxable"""
        total_allowances = sum(a.amount for a in allowances)
        taxable_allowances = 0
        non_taxable_allowances = 0
        
        for allowance in allowances:
            if allowance.is_taxable:
                # Check if there's a non-taxable limit
                limit = self.NON_TAXABLE_LIMITS.get(allowance.type, 0)
                if limit > 0 and allowance.amount <= limit:
                    non_taxable_allowances += allowance.amount
                else:
                    # Exceeds limit or no limit - fully taxable
                    taxable_allowances += allowance.amount
            else:
                non_taxable_allowances += allowance.amount
        
        return {
            'total': total_allowances,
            'taxable': round(taxable_allowances, 2),
            'non_taxable': round(non_taxable_allowances, 2)
        }
    
    def calculate_payroll_item(self, payroll_item: PayrollItem, 
                              months_multiplier: float = 1.0) -> PayrollItem:
        """Calculate complete payroll for an employee"""
        
        # Set basic salary (multiply by period months)
        monthly_basic_salary = payroll_item.employee.basic_salary
        payroll_item.basic_salary = monthly_basic_salary * months_multiplier
        
        # Process allowances (multiply by period months)
        allowance_summary = self.process_allowances(payroll_item.allowances)
        payroll_item.total_allowances = allowance_summary['total'] * months_multiplier
        payroll_item.taxable_allowances = allowance_summary['taxable'] * months_multiplier
        payroll_item.non_taxable_allowances = allowance_summary['non_taxable'] * months_multiplier
        
        # Calculate gross taxable income
        payroll_item.gross_taxable_income = (
            payroll_item.basic_salary + payroll_item.taxable_allowances
        )
        
        # Calculate pension contributions (based on multiplied salary)
        employee_pension, employer_pension = self.calculate_pension_contributions(
            payroll_item.basic_salary
        )
        payroll_item.employee_pension = employee_pension
        payroll_item.employer_pension = employer_pension
        
        # Calculate taxable income after pension deduction
        taxable_income_after_pension = payroll_item.gross_taxable_income - employee_pension
        
        # Calculate income tax (based on multiplied taxable income)
        payroll_item.income_tax = self.calculate_income_tax(taxable_income_after_pension)
        
        # Add calculated deductions
        payroll_item.add_deduction(DeductionType.INCOME_TAX, payroll_item.income_tax)
        payroll_item.add_deduction(DeductionType.PENSION_EMPLOYEE, payroll_item.employee_pension)
        
        # Calculate total deductions
        payroll_item.total_deductions = sum(d.amount for d in payroll_item.deductions)
        
        # Calculate net salary
        gross_pay = payroll_item.basic_salary + payroll_item.total_allowances
        payroll_item.net_salary = gross_pay - payroll_item.total_deductions
        
        # Calculate total employer cost
        payroll_item.total_employer_cost = gross_pay + payroll_item.employer_pension
        
        return payroll_item
    
    def generate_pay_slip(self, payroll_item: PayrollItem) -> Dict:
        """Generate a detailed pay slip"""
        return {
            'employee_info': {
                'employee_id': payroll_item.employee.employee_id,
                'name': payroll_item.employee.name,
                'department': payroll_item.employee.department,
                'position': payroll_item.employee.position,
                'pay_period': f"{payroll_item.pay_period_start} to {payroll_item.pay_period_end}"
            },
            'earnings': {
                'basic_salary': payroll_item.basic_salary,
                'allowances': [
                    {
                        'type': a.type.value,
                        'amount': a.amount,
                        'taxable': a.is_taxable
                    } for a in payroll_item.allowances
                ],
                'total_allowances': payroll_item.total_allowances,
                'gross_pay': payroll_item.basic_salary + payroll_item.total_allowances
            },
            'deductions': {
                'income_tax': payroll_item.income_tax,
                'employee_pension': payroll_item.employee_pension,
                'other_deductions': [
                    {
                        'type': d.type.value,
                        'amount': d.amount,
                        'description': d.description
                    } for d in payroll_item.deductions 
                    if d.type not in [DeductionType.INCOME_TAX, DeductionType.PENSION_EMPLOYEE]
                ],
                'total_deductions': payroll_item.total_deductions
            },
            'summary': {
                'gross_taxable_income': payroll_item.gross_taxable_income,
                'net_salary': payroll_item.net_salary,
                'employer_pension': payroll_item.employer_pension,
                'total_employer_cost': payroll_item.total_employer_cost
            }
        }
    
    def calculate_monthly_payroll(self, employees: List[Employee], 
                                 pay_period_start: date, pay_period_end: date,
                                 months_multiplier: float = 1.0) -> List[PayrollItem]:
        """Calculate payroll for multiple employees for a given period
        
        Args:
            employees: List of employees to process
            pay_period_start: Start date of pay period
            pay_period_end: End date of pay period 
            months_multiplier: Number of months to multiply salary by (for multi-month periods)
        """
        monthly_payroll = []
        
        for employee in employees:
            if not employee.is_active:
                continue
                
            payroll_item = PayrollItem(
                employee=employee,
                pay_period_start=pay_period_start,
                pay_period_end=pay_period_end
            )
            
            # Calculate payroll with months multiplier
            calculated_item = self.calculate_payroll_item(payroll_item, months_multiplier)
            monthly_payroll.append(calculated_item)
        
        return monthly_payroll
    
    def get_payroll_summary(self, payroll_items: List[PayrollItem]) -> Dict:
        """Generate summary statistics for a payroll run"""
        if not payroll_items:
            return {}
        
        total_employees = len(payroll_items)
        total_basic_salary = sum(item.basic_salary for item in payroll_items)
        total_allowances = sum(item.total_allowances for item in payroll_items)
        total_gross_pay = sum(item.basic_salary + item.total_allowances for item in payroll_items)
        total_deductions = sum(item.total_deductions for item in payroll_items)
        total_net_pay = sum(item.net_salary for item in payroll_items)
        total_income_tax = sum(item.income_tax for item in payroll_items)
        total_employee_pension = sum(item.employee_pension for item in payroll_items)
        total_employer_pension = sum(item.employer_pension for item in payroll_items)
        total_employer_cost = sum(item.total_employer_cost for item in payroll_items)
        
        return {
            'period': f"{payroll_items[0].pay_period_start} to {payroll_items[0].pay_period_end}",
            'total_employees': total_employees,
            'totals': {
                'basic_salary': round(total_basic_salary, 2),
                'allowances': round(total_allowances, 2),
                'gross_pay': round(total_gross_pay, 2),
                'deductions': round(total_deductions, 2),
                'net_pay': round(total_net_pay, 2),
                'income_tax': round(total_income_tax, 2),
                'employee_pension': round(total_employee_pension, 2),
                'employer_pension': round(total_employer_pension, 2),
                'total_employer_cost': round(total_employer_cost, 2)
            },
            'averages': {
                'basic_salary': round(total_basic_salary / total_employees, 2),
                'gross_pay': round(total_gross_pay / total_employees, 2),
                'net_pay': round(total_net_pay / total_employees, 2),
                'employer_cost': round(total_employer_cost / total_employees, 2)
            }
        }