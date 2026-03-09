"""
Test login credentials for multi-company portal
"""

import sys
import os

# Add project root to path
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)

from multicompany_demo_setup import setup_demo_data, hash_password

def test_login_credentials():
    """Test if login credentials work correctly"""
    
    print("Testing login credentials...")
    
    # Setup demo data
    try:
        user_manager, payroll_manager = setup_demo_data()
        print("✓ Demo data loaded successfully")
    except Exception as e:
        print(f"✗ Error loading demo data: {e}")
        return
    
    # Test credentials
    test_logins = [
        ('admin', 'admin123'),
        ('hr_manager', 'hr123'),
        ('payroll_clerk', 'payroll123'),
        ('employee1', 'emp123'),
        ('coffee_admin', 'coffee123'),
        ('manufacturing_hr', 'mfg123')
    ]
    
    print("\\nTesting login credentials:")
    print("-" * 40)
    
    for username, password in test_logins:
        # Find user
        user = None
        for u in user_manager.users.values():
            if u.username == username:
                user = u
                break
        
        if not user:
            print(f"✗ User '{username}' not found")
            continue
            
        # Test password
        expected_hash = hash_password(password)
        if user.password_hash == expected_hash:
            print(f"✓ {username} / {password} - Login OK")
            print(f"  - User ID: {user.user_id}")
            print(f"  - Full Name: {user.first_name} {user.last_name}")
            print(f"  - Companies: {len(user.get_companies())}")
        else:
            print(f"✗ {username} / {password} - Password mismatch")
            print(f"  - Expected: {expected_hash}")
            print(f"  - Got: {user.password_hash}")
    
    print("\\nUsers created:")
    print("-" * 15)
    for user in user_manager.users.values():
        companies = ", ".join([user_manager.companies[cid].name for cid in user.get_companies()])
        print(f"• {user.username} ({user.first_name} {user.last_name}) - {companies}")
    
    print("\\nCompanies created:")
    print("-" * 18)
    for company in user_manager.companies.values():
        employee_count = len(user_manager.get_company_employees(company.company_id))
        print(f"• {company.name} - {employee_count} employees")

if __name__ == "__main__":
    test_login_credentials()