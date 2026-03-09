"""
Demo Data Setup for Ethiopian Payroll System
"""

from models.ethiopian_payroll import Employee, EmployeeCategory
from datetime import date

def add_demo_payroll_data(employees_db):
    """Add demo employees to the payroll system"""
    
    demo_employees = [
        Employee(
            employee_id="EMP001",
            name="Bethlehem Girma", 
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            basic_salary=15000.0,
            hire_date=date(2024, 1, 15),
            department="Finance",
            position="Senior Accountant",
            tin_number="1234567890",
            pension_number="P001234567",
            is_active=True
        ),
        Employee(
            employee_id="EMP002",
            name="Samuel Wolde",
            category=EmployeeCategory.CONTRACT_EMPLOYEE,
            basic_salary=12000.0,
            hire_date=date(2024, 3, 10),
            department="IT",
            position="Software Developer",
            tin_number="2345678901",
            pension_number="P002345678",
            is_active=True
        ),
        Employee(
            employee_id="EMP003",
            name="Rahel Kassaye",
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            basic_salary=18000.0,
            hire_date=date(2023, 8, 5),
            department="Operations",
            position="Operations Manager",
            tin_number="3456789012",
            pension_number="P003456789",
            is_active=True
        ),
        Employee(
            employee_id="EMP004",
            name="Mohammed Ibrahim",
            category=EmployeeCategory.EXECUTIVE,
            basic_salary=25000.0,
            hire_date=date(2023, 1, 1),
            department="Management",
            position="General Manager",
            tin_number="4567890123",
            pension_number="P004567890",
            is_active=True
        ),
        Employee(
            employee_id="EMP005",
            name="Tigist Alemu",
            category=EmployeeCategory.CASUAL_WORKER,
            basic_salary=8000.0,
            hire_date=date(2024, 6, 20),
            department="Administration",
            position="Administrative Assistant",
            tin_number="5678901234",
            pension_number="P005678901",
            is_active=True
        )
    ]
    
    for employee in demo_employees:
        employees_db[employee.employee_id] = employee
    
    return len(demo_employees)