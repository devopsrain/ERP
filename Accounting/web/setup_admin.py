#!/usr/bin/env python3
"""
One-time script to create/update admin user and demo credentials.
Run this on the server: python setup_admin.py
"""
import os
import sys

# Add web directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from db import get_cursor, get_conn
import bcrypt
import uuid
from datetime import datetime

def hash_password(password: str) -> str:
    """Hash password with bcrypt."""
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def create_or_update_user(username: str, password: str, full_name: str, 
                          email: str, privilege_level: str):
    """Create user if not exists, or update password if exists."""
    try:
        with get_cursor() as cur:
            cur.execute("SELECT user_id FROM users WHERE username=%s", (username,))
            row = cur.fetchone()
            
            if row:
                # User exists - update password and unlock
                user_id = row['user_id']
                cur.execute(
                    """UPDATE users 
                       SET password_hash=%s, 
                           privilege_level=%s,
                           is_active=TRUE,
                           failed_login_count=0,
                           locked_until=''
                       WHERE user_id=%s""",
                    (hash_password(password), privilege_level, user_id)
                )
                print(f"  Updated: {username} (privilege: {privilege_level})")
                return True
            else:
                # Create new user
                user_id = str(uuid.uuid4())
                cur.execute(
                    """INSERT INTO users
                       (user_id, username, password_hash, full_name, email, phone,
                        privilege_level, is_active, created_at, last_login,
                        login_count, failed_login_count, locked_until)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                    (user_id, username, hash_password(password), full_name,
                     email, '', privilege_level, True,
                     datetime.now().isoformat(), '', 0, 0, '')
                )
                print(f"  Created: {username} (privilege: {privilege_level})")
                return True
    except Exception as e:
        print(f"  ERROR for {username}: {e}")
        return False

def main():
    print("=" * 50)
    print("Setting up Admin and Demo Users")
    print("=" * 50)
    
    users = [
        # Super Admin - requested by user
        ('Admin', 'Devopsrain', 'System Administrator', 'admin@system.local', 'super_admin'),
        
        # Demo users with fixed credentials
        ('admin', 'admin123', 'Demo Admin', 'demo.admin@system.et', 'super_admin'),
        ('hr_manager', 'hr123', 'HR Manager Demo', 'hr@demo.et', 'manager'),
        ('accountant', 'acc123', 'Accountant Demo', 'acc@demo.et', 'operator'),
        ('employee1', 'emp123', 'Employee Demo', 'emp@demo.et', 'viewer'),
        ('data_entry', 'data123', 'Data Entry Demo', 'data@demo.et', 'data_entry'),
    ]
    
    print("\nCreating/updating users...")
    for username, password, full_name, email, privilege in users:
        create_or_update_user(username, password, full_name, email, privilege)
    
    print("\n" + "=" * 50)
    print("User Setup Complete!")
    print("=" * 50)
    print("\nCredentials:")
    print("  Admin      : Admin / Devopsrain (super_admin)")
    print("  Demo Admin : admin / admin123 (super_admin)")
    print("  HR Manager : hr_manager / hr123 (manager)")
    print("  Accountant : accountant / acc123 (operator)")
    print("  Employee   : employee1 / emp123 (viewer)")
    print("  Data Entry : data_entry / data123 (data_entry)")
    print("=" * 50)

if __name__ == '__main__':
    main()
