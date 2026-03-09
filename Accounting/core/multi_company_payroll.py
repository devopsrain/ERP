"""
Multi-Company Payroll Integration

This module integrates the Ethiopian payroll system with multi-company
functionality, providing isolated payroll processing per company.
"""

from typing import Dict, List, Optional, Tuple
from datetime import datetime, date, timedelta
from dataclasses import dataclass, field
import json

from models.multi_company import (
    Company, User, UserRole, MultiCompanyUserManager, 
    SubscriptionPlan, CompanyStatus
)
from models.ethiopian_payroll import (
    EthiopianPayrollCalculator, Employee, PayrollItem, 
    EmployeeCategory
)
from core.ethiopian_payroll_integration import EthiopianPayrollIntegration
from core.ledger import GeneralLedger


@dataclass
class PayrollSummary:
    """Summary of payroll calculation results"""
    month: int
    year: int 
    payslips: List[PayrollItem] = field(default_factory=list)
    total_gross_salary: float = 0
    total_tax: float = 0
    total_pension_employee: float = 0
    total_pension_employer: float = 0
    total_net_salary: float = 0


@dataclass
class CompanyPayrollContext:
    """Container for company-specific payroll data"""
    company_id: str
    company: Company
    payroll_calculator: EthiopianPayrollCalculator
    payroll_integration: EthiopianPayrollIntegration
    employees: Dict[str, Employee] = field(default_factory=dict)
    payroll_history: List[PayrollSummary] = field(default_factory=list)
    
    def get_employee_count(self) -> int:
        return len(self.employees)
    
    def can_add_employee(self) -> bool:
        return self.company.can_add_employee() and self.get_employee_count() < self.company.max_employees


class MultiCompanyPayrollManager:
    """Manages payroll operations across multiple companies"""
    
    def __init__(self, user_manager: MultiCompanyUserManager):
        self.user_manager = user_manager
        self.company_contexts: Dict[str, CompanyPayrollContext] = {}
        self.current_user_id: Optional[str] = None
        self.current_company_id: Optional[str] = None
    
    def initialize_company_payroll(self, company_id: str) -> CompanyPayrollContext:
        """Initialize payroll system for a company"""
        
        if company_id in self.company_contexts:
            return self.company_contexts[company_id]
        
        company = self.user_manager.companies.get(company_id)
        if not company:
            raise ValueError(f"Company {company_id} not found")
        
        # Create company-specific ledger
        company_ledger = GeneralLedger()
        
        # Initialize payroll systems
        payroll_calculator = EthiopianPayrollCalculator()
        payroll_integration = EthiopianPayrollIntegration(company_ledger)
        
        # Load existing employees from user manager
        employees = {}
        company_employees = self.user_manager.get_company_employees(company_id)
        for emp_id, employee in company_employees.items():
            if hasattr(employee, 'employee_id'):
                employees[emp_id] = employee
            else:
                # Convert dict to Employee if needed
                employees[emp_id] = Employee(**employee) if isinstance(employee, dict) else employee
        
        context = CompanyPayrollContext(
            company_id=company_id,
            company=company,
            payroll_calculator=payroll_calculator,
            payroll_integration=payroll_integration,
            employees=employees
        )
        
        self.company_contexts[company_id] = context
        return context
    
    def set_user_context(self, user_id: str, company_id: Optional[str] = None) -> bool:
        """Set current user and company context"""
        
        user = self.user_manager.users.get(user_id)
        if not user:
            return False
        
        # Use provided company or user's default
        target_company = company_id or user.default_company_id
        
        if not target_company or not user.has_access_to_company(target_company):
            return False
        
        self.current_user_id = user_id
        self.current_company_id = target_company
        
        # Initialize company payroll if not exists
        if target_company not in self.company_contexts:
            self.initialize_company_payroll(target_company)
        
        return True
    
    def get_current_context(self) -> Optional[CompanyPayrollContext]:
        """Get current company payroll context"""
        if not self.current_company_id:
            return None
        return self.company_contexts.get(self.current_company_id)
    
    def check_permission(self, required_role: UserRole = UserRole.EMPLOYEE) -> bool:
        """Check if current user has required permission in current company"""
        
        if not self.current_user_id or not self.current_company_id:
            return False
        
        user = self.user_manager.users.get(self.current_user_id)
        if not user:
            return False
        
        user_role = user.get_role_in_company(self.current_company_id)
        if not user_role:
            return False
        
        # Role hierarchy check
        role_levels = {
            UserRole.EMPLOYEE: 1,
            UserRole.PAYROLL_CLERK: 2,
            UserRole.ACCOUNTANT: 3,
            UserRole.HR_MANAGER: 4,
            UserRole.COMPANY_ADMIN: 5,
            UserRole.SUPER_ADMIN: 6
        }
        
        return role_levels.get(user_role, 0) >= role_levels.get(required_role, 0)
    
    def add_employee(self, employee_data: dict) -> Tuple[bool, str]:
        """Add employee to current company"""
        
        if not self.check_permission(UserRole.HR_MANAGER):
            return False, "Permission denied: HR Manager role required"
        
        context = self.get_current_context()
        if not context:
            return False, "No company context selected"
        
        if not context.can_add_employee():
            return False, f"Employee limit reached ({context.company.max_employees})"
        
        # Create employee
        employee = Employee(**employee_data)
        
        # Add to company context
        context.employees[employee.employee_id] = employee
        
        # Add to user manager
        self.user_manager.add_employee_to_company(context.company_id, employee)
        
        return True, f"Employee {employee.full_name} added successfully"
    
    def calculate_payroll(self, month: int, year: int, employee_ids: Optional[List[str]] = None) -> Tuple[bool, PayrollSummary]:
        """Calculate payroll for current company"""
        
        if not self.check_permission(UserRole.PAYROLL_CLERK):
            return False, PayrollSummary(month, year, [], 0, 0, 0, 0, 0)
        
        context = self.get_current_context()
        if not context:
            return False, PayrollSummary(month, year, [], 0, 0, 0, 0, 0)
        
        # Check subscription limits
        if not context.company.is_active():
            return False, PayrollSummary(month, year, [], 0, 0, 0, 0, 0)
        
        # Get employees to process
        employees_to_process = []
        if employee_ids:
            employees_to_process = [context.employees[emp_id] for emp_id in employee_ids 
                                  if emp_id in context.employees]
        else:
            employees_to_process = list(context.employees.values())
        
        # Calculate payroll
        payslips = []
        total_gross = 0
        total_tax = 0
        total_pension_employee = 0
        total_pension_employer = 0
        total_net = 0
        
        for employee in employees_to_process:
            # Create payroll item
            from datetime import date
            payroll_item = PayrollItem(
                employee=employee,
                pay_period_start=date(year, month, 1),
                pay_period_end=date(year, month, 28),  # Simplified
                basic_salary=employee.base_salary
            )
            
            # Calculate payroll
            payslip = context.payroll_calculator.calculate_payroll_item(payroll_item)
            payslips.append(payslip)
            
            total_gross += payslip.gross_taxable_income
            total_tax += payslip.income_tax
            total_pension_employee += payslip.employee_pension
            total_pension_employer += payslip.employer_pension
            total_net += payslip.net_salary
        
        # Create payroll summary
        summary = PayrollSummary(
            month=month,
            year=year,
            payslips=payslips,
            total_gross_salary=total_gross,
            total_tax=total_tax,
            total_pension_employee=total_pension_employee,
            total_pension_employer=total_pension_employer,
            total_net_salary=total_net
        )
        
        # Process through accounting integration
        try:
            context.payroll_integration.process_monthly_payroll(summary, f"{context.company.name}")
        except Exception as e:
            return False, summary
        
        # Store in history
        context.payroll_history.append(summary)
        
        return True, summary
    
    def get_employee_payslip(self, employee_id: str, month: int, year: int) -> Optional[PayrollItem]:
        """Get individual employee payslip"""
        
        context = self.get_current_context()
        if not context:
            return None
        
        employee = context.employees.get(employee_id)
        if not employee:
            return None
        
        # Check permissions - employees can only view their own, others need payroll access
        current_user = self.user_manager.users.get(self.current_user_id)
        if not current_user:
            return None
        
        user_role = current_user.get_role_in_company(self.current_company_id)
        
        # If user is employee, check if viewing own payslip
        if user_role == UserRole.EMPLOYEE:
            # Check if this employee record belongs to current user
            if not (hasattr(employee, 'user_id') and employee.user_id == self.current_user_id):
                return None
        elif not self.check_permission(UserRole.PAYROLL_CLERK):
            return None
        
        # Create payroll item for calculation
        from datetime import date
        payroll_item = PayrollItem(
            employee=employee,
            pay_period_start=date(year, month, 1),
            pay_period_end=date(year, month, 28),  # Simplified
            basic_salary=employee.base_salary
        )
        
        return context.payroll_calculator.calculate_payroll_item(payroll_item)
    
    def get_company_summary(self) -> Dict:
        """Get current company payroll summary"""
        
        if not self.check_permission(UserRole.HR_MANAGER):
            return {"error": "Permission denied"}
        
        context = self.get_current_context()
        if not context:
            return {"error": "No company context"}
        
        stats = self.user_manager.get_company_statistics(context.company_id)
        
        # Add payroll-specific statistics
        recent_payroll = context.payroll_history[-1] if context.payroll_history else None
        
        stats.update({
            'total_employees': len(context.employees),
            'employees_by_category': self._get_employee_categories(context),
            'recent_payroll': {
                'month': recent_payroll.month if recent_payroll else None,
                'year': recent_payroll.year if recent_payroll else None,
                'total_cost': (recent_payroll.total_gross_salary + recent_payroll.total_pension_employer) if recent_payroll else 0,
                'total_net_paid': recent_payroll.total_net_salary if recent_payroll else 0
            } if recent_payroll else None,
            'payroll_runs_count': len(context.payroll_history)
        })
        
        return stats
    
    def _get_employee_categories(self, context: CompanyPayrollContext) -> Dict[str, int]:
        """Get employee count by category"""
        categories = {}
        for employee in context.employees.values():
            category = employee.category.value
            categories[category] = categories.get(category, 0) + 1
        return categories
    
    def get_user_companies_summary(self, user_id: str) -> List[Dict]:
        """Get summary of all companies user has access to"""
        
        user = self.user_manager.users.get(user_id)
        if not user:
            return []
        
        summaries = []
        for company_id in user.get_companies():
            company = self.user_manager.companies.get(company_id)
            if not company:
                continue
            
            role = user.get_role_in_company(company_id)
            stats = self.user_manager.get_company_statistics(company_id)
            
            summary = {
                'company_id': company_id,
                'name': company.name,
                'role': role.value,
                'status': company.status.value,
                'employee_count': stats.get('employee_count', 0),
                'max_employees': stats.get('max_employees', 0),
                'subscription_plan': stats.get('subscription_plan', ''),
                'can_access': company.is_active() and user.has_access_to_company(company_id)
            }
            
            summaries.append(summary)
        
        return summaries
    
    def switch_company_context(self, user_id: str, company_id: str) -> Tuple[bool, str]:
        """Switch user to different company context"""
        
        user = self.user_manager.users.get(user_id)
        if not user:
            return False, "User not found"
        
        if not user.has_access_to_company(company_id):
            return False, "Access denied to company"
        
        company = self.user_manager.companies.get(company_id)
        if not company or not company.is_active():
            return False, "Company is not active"
        
        # Switch context
        success = self.set_user_context(user_id, company_id)
        if success:
            # Update user's default company
            self.user_manager.switch_user_context(user_id, company_id)
            return True, f"Switched to {company.name}"
        
        return False, "Failed to switch context"
    
    def export_company_data(self, format_type: str = "json") -> Optional[str]:
        """Export company payroll data"""
        
        if not self.check_permission(UserRole.COMPANY_ADMIN):
            return None
        
        context = self.get_current_context()
        if not context:
            return None
        
        data = {
            'company': {
                'name': context.company.name,
                'registration_number': context.company.registration_number,
                'tin_number': context.company.tin_number,
            },
            'employees': [
                {
                    'id': emp.employee_id,
                    'name': emp.full_name,
                    'employee_number': emp.employee_number,
                    'category': emp.category.value,
                    'base_salary': emp.base_salary,
                    'department': emp.department
                }
                for emp in context.employees.values()
            ],
            'payroll_history': [
                {
                    'month': summary.month,
                    'year': summary.year,
                    'total_employees': len(summary.payslips),
                    'total_gross': summary.total_gross_salary,
                    'total_net': summary.total_net_salary,
                    'total_tax': summary.total_tax
                }
                for summary in context.payroll_history
            ]
        }
        
        if format_type == "json":
            return json.dumps(data, indent=2, default=str)
        
        return None