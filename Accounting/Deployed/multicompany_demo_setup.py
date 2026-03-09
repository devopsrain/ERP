"""
Demo data setup for Multi-Company User Portal

This module sets up demo companies, users, and employees for testing
the multi-company payroll system.
"""

from datetime import datetime, date, timedelta
import hashlib
import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from models.multi_company import (
    Company, User, UserRole, MultiCompanyUserManager, 
    SubscriptionPlan, CompanyStatus
)
from models.ethiopian_payroll import Employee, EmployeeCategory
from core.multi_company_payroll import MultiCompanyPayrollManager

def hash_password(password: str) -> str:
    """Simple password hashing"""
    return hashlib.sha256(password.encode()).hexdigest()

def setup_demo_data():
    """Set up demo companies, users, and employees"""
    
    # Create user manager and payroll manager
    user_manager = MultiCompanyUserManager()
    payroll_manager = MultiCompanyPayrollManager(user_manager)
    
    print("Setting up demo companies and users...")
    
    # Create demo companies
    companies_data = [
        {
            'name': 'Addis Tech Solutions',
            'registration_number': 'REG-001-2024',
            'tin_number': 'TIN-1001234567',
            'address': 'Bole Sub-city, Addis Ababa',
            'city': 'Addis Ababa',
            'phone': '+251-11-555-0001',
            'email': 'info@addistech.et',
            'business_type': 'Information Technology',
            'subscription_plan': SubscriptionPlan.PROFESSIONAL,
            'status': CompanyStatus.ACTIVE,
            'subscription_start': date.today() - timedelta(days=30),
            'subscription_end': date.today() + timedelta(days=335)
        },
        {
            'name': 'Ethiopian Coffee Exports',
            'registration_number': 'REG-002-2024', 
            'tin_number': 'TIN-1001234568',
            'address': 'Merkato, Addis Ababa',
            'city': 'Addis Ababa',
            'phone': '+251-11-555-0002',
            'email': 'info@cofee-exports.et',
            'business_type': 'Export/Import',
            'subscription_plan': SubscriptionPlan.BASIC,
            'status': CompanyStatus.ACTIVE,
            'subscription_start': date.today() - timedelta(days=60),
            'subscription_end': date.today() + timedelta(days=305)
        },
        {
            'name': 'Dire Dawa Manufacturing',
            'registration_number': 'REG-003-2024',
            'tin_number': 'TIN-1001234569', 
            'address': 'Industrial Zone, Dire Dawa',
            'city': 'Dire Dawa',
            'phone': '+251-25-555-0003',
            'email': 'hr@dd-manufacturing.et',
            'business_type': 'Manufacturing',
            'subscription_plan': SubscriptionPlan.ENTERPRISE,
            'status': CompanyStatus.ACTIVE,
            'subscription_start': date.today() - timedelta(days=90),
            'subscription_end': date.today() + timedelta(days=275)
        }
    ]
    
    # Create demo users
    users_data = [
        {
            'username': 'admin',
            'email': 'admin@system.et',
            'password_hash': hash_password('admin123'),
            'first_name': 'System',
            'last_name': 'Administrator',
            'phone': '+251-11-999-0001'
        },
        {
            'username': 'hr_manager',
            'email': 'hr.manager@addistech.et',
            'password_hash': hash_password('hr123'),
            'first_name': 'Almaz',
            'last_name': 'Tadesse', 
            'phone': '+251-11-555-1001'
        },
        {
            'username': 'payroll_clerk',
            'email': 'payroll@addistech.et',
            'password_hash': hash_password('payroll123'),
            'first_name': 'Dawit',
            'last_name': 'Mengistu',
            'phone': '+251-11-555-1002'
        },
        {
            'username': 'employee1',
            'email': 'employee1@addistech.et', 
            'password_hash': hash_password('emp123'),
            'first_name': 'Meron',
            'last_name': 'Haile',
            'phone': '+251-11-555-2001'
        },
        {
            'username': 'coffee_admin',
            'email': 'admin@coffee-exports.et',
            'password_hash': hash_password('coffee123'),
            'first_name': 'Hanan',
            'last_name': 'Ahmed',
            'phone': '+251-11-555-3001'
        },
        {
            'username': 'manufacturing_hr',
            'email': 'hr@dd-manufacturing.et',
            'password_hash': hash_password('mfg123'),
            'first_name': 'Tesfaye',
            'last_name': 'Bekele',
            'phone': '+251-25-555-4001'
        }
    ]
    
    # Create companies
    created_companies = []
    for company_data in companies_data:
        # Add company_id if not present
        if 'company_id' not in company_data:
            import uuid
            company_data['company_id'] = str(uuid.uuid4())
        
        company = user_manager.create_company(company_data, 'temp_admin')  # Will assign proper admin later
        created_companies.append(company)
        print(f"✓ Created company: {company.name}")
    
    # Create users
    created_users = []
    for user_data in users_data:
        # Add user_id if not present
        if 'user_id' not in user_data:
            import uuid
            user_data['user_id'] = str(uuid.uuid4())
            
        user = user_manager.create_user(user_data)
        created_users.append(user)
        print(f"✓ Created user: {user.username}")
    
    # Assign user roles to companies
    role_assignments = [
        # Super Admin - access to all companies
        (created_users[0].user_id, created_companies[0].company_id, UserRole.SUPER_ADMIN),
        (created_users[0].user_id, created_companies[1].company_id, UserRole.SUPER_ADMIN),
        (created_users[0].user_id, created_companies[2].company_id, UserRole.SUPER_ADMIN),
        
        # Addis Tech Solutions staff
        (created_users[1].user_id, created_companies[0].company_id, UserRole.HR_MANAGER),
        (created_users[2].user_id, created_companies[0].company_id, UserRole.PAYROLL_CLERK),
        (created_users[3].user_id, created_companies[0].company_id, UserRole.EMPLOYEE),
        
        # Coffee Exports staff
        (created_users[4].user_id, created_companies[1].company_id, UserRole.COMPANY_ADMIN),
        
        # Manufacturing staff
        (created_users[5].user_id, created_companies[2].company_id, UserRole.HR_MANAGER),
    ]
    
    print("\\nAssigning user roles to companies...")
    for user_id, company_id, role in role_assignments:
        user = user_manager.users[user_id]
        user.add_company_role(company_id, role)
        company = user_manager.companies[company_id]
        print(f"✓ Assigned {user.username} as {role.value} to {company.name}")
    
    # Create demo employees for each company
    demo_employees = {
        created_companies[0].company_id: [  # Addis Tech Solutions
            {
                'employee_id': 'EMP-001',
                'name': 'Bethlehem Girma',
                'category': EmployeeCategory.REGULAR_EMPLOYEE,
                'basic_salary': 15000,
                'department': 'Software Development',
                'position': 'Senior Developer',
                'hire_date': date(2024, 1, 15),
                'bank_account': 'CBE-1001234567',
                'tin_number': 'TIN-2001001001'
            },
            {
                'employee_id': 'EMP-002',
                'name': 'Samuel Wolde',
                'category': EmployeeCategory.REGULAR_EMPLOYEE,
                'basic_salary': 12000,
                'department': 'Quality Assurance',
                'position': 'QA Engineer',
                'hire_date': date(2024, 3, 1),
                'bank_account': 'CBE-1001234568',
                'tin_number': 'TIN-2001001002'
            },
            {
                'employee_id': 'EMP-003',
                'name': 'Rahel Kassaye',
                'category': EmployeeCategory.CONTRACT_EMPLOYEE,
                'basic_salary': 8000,
                'department': 'Design',
                'position': 'UI/UX Designer',
                'hire_date': date(2024, 6, 1),
                'bank_account': 'CBE-1001234569',
                'tin_number': 'TIN-2001001003'
            }
        ],
        created_companies[1].company_id: [  # Ethiopian Coffee Exports
            {
                'employee_id': 'COF-001',
                'name': 'Mohammed Ibrahim',
                'category': EmployeeCategory.REGULAR_EMPLOYEE,
                'basic_salary': 18000,
                'department': 'Export Operations',
                'position': 'Export Manager',
                'hire_date': date(2023, 9, 1),
                'bank_account': 'CBE-2001234567',
                'tin_number': 'TIN-2002001001'
            },
            {
                'employee_id': 'COF-002',
                'name': 'Tigist Alemu',
                'category': EmployeeCategory.REGULAR_EMPLOYEE,
                'basic_salary': 10000,
                'department': 'Quality Control',
                'position': 'Quality Inspector',
                'hire_date': date(2024, 2, 15),
                'bank_account': 'CBE-2001234568',
                'tin_number': 'TIN-2002001002'
            }
        ],
        created_companies[2].company_id: [  # Dire Dawa Manufacturing
            {
                'employee_id': 'MFG-001',
                'name': 'Getachew Desta',
                'category': EmployeeCategory.REGULAR_EMPLOYEE,
                'basic_salary': 20000,
                'department': 'Production',
                'position': 'Production Manager',
                'hire_date': date(2023, 5, 1),
                'bank_account': 'CBE-3001234567',
                'tin_number': 'TIN-2003001001'
            },
            {
                'employee_id': 'MFG-002',
                'name': 'Selamawit Tekle',
                'category': EmployeeCategory.REGULAR_EMPLOYEE,
                'basic_salary': 16000,
                'department': 'Engineering',
                'position': 'Process Engineer',
                'hire_date': date(2023, 8, 15),
                'bank_account': 'CBE-3001234568',
                'tin_number': 'TIN-2003001002'
            },
            {
                'employee_id': 'MFG-003',
                'name': 'Yohannes Mulugeta',
                'category': EmployeeCategory.CASUAL_WORKER,
                'basic_salary': 7500,
                'department': 'Assembly',
                'position': 'Assembly Technician',
                'hire_date': date(2025, 12, 1),
                'bank_account': 'CBE-3001234569',
                'tin_number': 'TIN-2003001003'
            }
        ]
    }
    
    print("\\nCreating demo employees...")
    for company_id, employees_list in demo_employees.items():
        for emp_data in employees_list:
            employee = Employee(**emp_data)
            user_manager.add_employee_to_company(company_id, employee)
            company = user_manager.companies[company_id]
            print(f"✓ Added {employee.name} to {company.name}")
    
    print("\\n" + "="*50)
    print("DEMO SETUP COMPLETE!")
    print("="*50)
    print("\\nDemo Login Credentials:")
    print("-" * 25)
    print("Super Admin:     admin / admin123")
    print("HR Manager:      hr_manager / hr123") 
    print("Payroll Clerk:   payroll_clerk / payroll123")
    print("Employee:        employee1 / emp123")
    print("Coffee Admin:    coffee_admin / coffee123")
    print("Manufacturing:   manufacturing_hr / mfg123")
    print()
    print("Companies Created:")
    print("-" * 18)
    for company in created_companies:
        print(f"• {company.name} ({company.subscription_plan.value})")
    print()
    print("Access the portal at: http://localhost:5000/company/login")
    print("="*50)
    
    return user_manager, payroll_manager

if __name__ == "__main__":
    setup_demo_data()
