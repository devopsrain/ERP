"""
Multi-Company User Portal Models

This module handles multi-tenancy with company isolation and user management
across different companies in the Ethiopian payroll system.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Set
from enum import Enum
import uuid


class CompanyStatus(Enum):
    ACTIVE = "Active"
    INACTIVE = "Inactive"
    SUSPENDED = "Suspended"
    TRIAL = "Trial"


class UserRole(Enum):
    SUPER_ADMIN = "Super Admin"  # System-wide access
    COMPANY_ADMIN = "Company Admin"  # Full company access
    HR_MANAGER = "HR Manager"  # Employee and payroll management
    PAYROLL_CLERK = "Payroll Clerk"  # Payroll processing only
    ACCOUNTANT = "Accountant"  # Financial reporting access
    EMPLOYEE = "Employee"  # View own payslip only
    AUDITOR = "Auditor"  # Read-only access for compliance


class SubscriptionPlan(Enum):
    FREE = "Free"  # Up to 10 employees
    BASIC = "Basic"  # Up to 50 employees
    PROFESSIONAL = "Professional"  # Up to 200 employees
    ENTERPRISE = "Enterprise"  # Unlimited employees


@dataclass
class Company:
    """Company/Tenant information"""
    company_id: str
    name: str
    registration_number: str
    tin_number: str
    address: str
    city: str = "Addis Ababa"
    country: str = "Ethiopia"
    phone: str = ""
    email: str = ""
    website: str = ""
    
    # Business details
    business_type: str = ""
    employee_count: int = 0
    established_date: Optional[date] = None
    
    # System details
    status: CompanyStatus = CompanyStatus.TRIAL
    subscription_plan: SubscriptionPlan = SubscriptionPlan.FREE
    subscription_start: Optional[date] = None
    subscription_end: Optional[date] = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    
    # System limits based on subscription
    max_employees: int = field(default=10)
    max_payroll_runs_per_month: int = field(default=1)
    features_enabled: Set[str] = field(default_factory=lambda: {"basic_payroll", "reports"})
    
    def __post_init__(self):
        """Set limits based on subscription plan"""
        if not self.company_id:
            self.company_id = str(uuid.uuid4())
            
        # Set limits based on subscription
        limits = {
            SubscriptionPlan.FREE: (10, 1, {"basic_payroll", "reports"}),
            SubscriptionPlan.BASIC: (50, 12, {"basic_payroll", "reports", "advanced_reports"}),
            SubscriptionPlan.PROFESSIONAL: (200, 12, {"basic_payroll", "reports", "advanced_reports", "api_access"}),
            SubscriptionPlan.ENTERPRISE: (999999, 12, {"basic_payroll", "reports", "advanced_reports", "api_access", "custom_fields", "audit_trail"})
        }
        
        if self.subscription_plan in limits:
            max_emp, max_payroll, features = limits[self.subscription_plan]
            self.max_employees = max_emp
            self.max_payroll_runs_per_month = max_payroll
            self.features_enabled = features
    
    def is_active(self) -> bool:
        """Check if company subscription is active"""
        if self.status not in [CompanyStatus.ACTIVE, CompanyStatus.TRIAL]:
            return False
        
        if self.subscription_end and self.subscription_end < date.today():
            return False
            
        return True
    
    def can_add_employee(self) -> bool:
        """Check if company can add more employees"""
        return self.employee_count < self.max_employees
    
    def has_feature(self, feature: str) -> bool:
        """Check if company has access to specific feature"""
        return feature in self.features_enabled


@dataclass
class User:
    """Multi-company user management"""
    user_id: str
    username: str
    email: str
    password_hash: str
    first_name: str
    last_name: str
    
    # Multi-company associations
    company_roles: Dict[str, UserRole] = field(default_factory=dict)  # company_id -> role
    default_company_id: Optional[str] = None
    
    # User settings
    is_active: bool = True
    email_verified: bool = False
    phone: str = ""
    language: str = "en"
    timezone: str = "Africa/Addis_Ababa"
    
    # System metadata
    created_at: datetime = field(default_factory=datetime.now)
    last_login: Optional[datetime] = None
    login_count: int = 0
    
    def __post_init__(self):
        if not self.user_id:
            self.user_id = str(uuid.uuid4())
    
    @property
    def full_name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()
    
    def get_companies(self) -> List[str]:
        """Get list of company IDs user has access to"""
        return list(self.company_roles.keys())
    
    def get_role_in_company(self, company_id: str) -> Optional[UserRole]:
        """Get user's role in specific company"""
        return self.company_roles.get(company_id)
    
    def has_access_to_company(self, company_id: str) -> bool:
        """Check if user has access to company"""
        return company_id in self.company_roles
    
    def is_admin_in_company(self, company_id: str) -> bool:
        """Check if user is admin in company"""
        role = self.get_role_in_company(company_id)
        return role in [UserRole.SUPER_ADMIN, UserRole.COMPANY_ADMIN]
    
    def can_manage_payroll(self, company_id: str) -> bool:
        """Check if user can manage payroll in company"""
        role = self.get_role_in_company(company_id)
        return role in [UserRole.SUPER_ADMIN, UserRole.COMPANY_ADMIN, UserRole.HR_MANAGER, UserRole.PAYROLL_CLERK]
    
    def can_view_reports(self, company_id: str) -> bool:
        """Check if user can view reports in company"""
        role = self.get_role_in_company(company_id)
        return role in [UserRole.SUPER_ADMIN, UserRole.COMPANY_ADMIN, UserRole.HR_MANAGER, UserRole.ACCOUNTANT, UserRole.AUDITOR]
    
    def add_company_role(self, company_id: str, role: UserRole):
        """Add or update user role in company"""
        self.company_roles[company_id] = role
        if not self.default_company_id:
            self.default_company_id = company_id
    
    def remove_company_access(self, company_id: str):
        """Remove user access from company"""
        if company_id in self.company_roles:
            del self.company_roles[company_id]
        
        if self.default_company_id == company_id:
            # Set new default company if available
            if self.company_roles:
                self.default_company_id = list(self.company_roles.keys())[0]
            else:
                self.default_company_id = None


@dataclass
class CompanyInvitation:
    """Invitation system for adding users to companies"""
    invitation_id: str
    company_id: str
    inviter_user_id: str
    email: str
    role: UserRole
    
    # Status tracking
    is_accepted: bool = False
    is_expired: bool = False
    accepted_by_user_id: Optional[str] = None
    accepted_at: Optional[datetime] = None
    
    # Metadata
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime = field(default_factory=lambda: datetime.now() + timedelta(days=7))
    message: str = ""
    
    def __post_init__(self):
        if not self.invitation_id:
            self.invitation_id = str(uuid.uuid4())
    
    def is_valid(self) -> bool:
        """Check if invitation is still valid"""
        return not self.is_accepted and not self.is_expired and datetime.now() < self.expires_at


class MultiCompanyUserManager:
    """Manages multi-company user operations"""
    
    def __init__(self):
        self.companies: Dict[str, Company] = {}
        self.users: Dict[str, User] = {}
        self.invitations: Dict[str, CompanyInvitation] = {}
        self.company_employees: Dict[str, Dict[str, any]] = {}  # company_id -> employees
    
    def create_company(self, company_data: dict, admin_user_id: str) -> Company:
        """Create new company and assign admin"""
        company = Company(**company_data)
        self.companies[company.company_id] = company
        
        # Assign admin role to creator
        if admin_user_id in self.users:
            self.users[admin_user_id].add_company_role(company.company_id, UserRole.COMPANY_ADMIN)
        
        # Initialize company employee storage
        self.company_employees[company.company_id] = {}
        
        return company
    
    def create_user(self, user_data: dict) -> User:
        """Create new user"""
        user = User(**user_data)
        self.users[user.user_id] = user
        return user
    
    def invite_user_to_company(self, company_id: str, inviter_user_id: str, 
                              email: str, role: UserRole, message: str = "") -> CompanyInvitation:
        """Invite user to join company"""
        
        # Check if inviter has permission
        if not self.users[inviter_user_id].is_admin_in_company(company_id):
            raise PermissionError("Only admins can invite users")
        
        invitation = CompanyInvitation(
            invitation_id=str(uuid.uuid4()),
            company_id=company_id,
            inviter_user_id=inviter_user_id,
            email=email,
            role=role,
            message=message
        )
        
        self.invitations[invitation.invitation_id] = invitation
        return invitation
    
    def accept_invitation(self, invitation_id: str, user_id: str) -> bool:
        """Accept company invitation"""
        invitation = self.invitations.get(invitation_id)
        
        if not invitation or not invitation.is_valid():
            return False
        
        user = self.users.get(user_id)
        if not user or user.email != invitation.email:
            return False
        
        # Add user to company
        user.add_company_role(invitation.company_id, invitation.role)
        
        # Mark invitation as accepted
        invitation.is_accepted = True
        invitation.accepted_by_user_id = user_id
        invitation.accepted_at = datetime.now()
        
        return True
    
    def get_user_companies(self, user_id: str) -> List[Company]:
        """Get all companies user has access to"""
        user = self.users.get(user_id)
        if not user:
            return []
        
        return [self.companies[company_id] for company_id in user.get_companies() 
                if company_id in self.companies]
    
    def get_company_users(self, company_id: str) -> List[tuple]:
        """Get all users with access to company"""
        result = []
        for user in self.users.values():
            if user.has_access_to_company(company_id):
                role = user.get_role_in_company(company_id)
                result.append((user, role))
        return result
    
    def switch_user_context(self, user_id: str, company_id: str) -> bool:
        """Switch user's current company context"""
        user = self.users.get(user_id)
        if not user or not user.has_access_to_company(company_id):
            return False
        
        user.default_company_id = company_id
        return True
    
    def get_company_employees(self, company_id: str) -> Dict[str, any]:
        """Get employees for specific company"""
        return self.company_employees.get(company_id, {})
    
    def add_employee_to_company(self, company_id: str, employee) -> bool:
        """Add employee to specific company"""
        company = self.companies.get(company_id)
        if not company or not company.can_add_employee():
            return False
        
        if company_id not in self.company_employees:
            self.company_employees[company_id] = {}
        
        self.company_employees[company_id][employee.employee_id] = employee
        company.employee_count += 1
        return True
    
    def get_company_statistics(self, company_id: str) -> Dict:
        """Get company usage statistics"""
        company = self.companies.get(company_id)
        if not company:
            return {}
        
        employees = self.get_company_employees(company_id)
        users = self.get_company_users(company_id)
        
        return {
            'company_name': company.name,
            'employee_count': len(employees),
            'max_employees': company.max_employees,
            'user_count': len(users),
            'subscription_plan': company.subscription_plan.value,
            'subscription_status': company.status.value,
            'features_enabled': list(company.features_enabled),
            'days_until_expiry': (company.subscription_end - date.today()).days if company.subscription_end else None
        }