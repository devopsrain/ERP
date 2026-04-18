#!/usr/bin/env python3
"""
One-time script to create/update admin user and demo credentials.
Run this on the server: python setup_admin.py

This script will:
1. Try to load DATABASE_URL from environment
2. Try to load from .env file in current or parent directory
3. Provide helpful error if database cannot be found
"""
import os
import sys

# Add web directory to path
script_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, script_dir)

def load_env_file():
    """Try to load .env file from current or parent directories."""
    env_locations = [
        os.path.join(script_dir, '.env'),
        os.path.join(script_dir, '..', '.env'),
        os.path.join(script_dir, '..', '..', '.env'),
        '/opt/ethiopian-business/.env',
        '/opt/ethiopian-business/Accounting/.env',
        '/opt/ethiopian-business/Accounting/web/.env',
    ]
    
    for env_path in env_locations:
        if os.path.exists(env_path):
            print(f"  Found .env at: {env_path}")
            with open(env_path, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, _, value = line.partition('=')
                        key = key.strip()
                        value = value.strip().strip('"').strip("'")
                        if key and value and key not in os.environ:
                            os.environ[key] = value
            return True
    return False

def ensure_database_url():
    """Ensure DATABASE_URL is set, trying multiple sources."""
    if os.environ.get('DATABASE_URL'):
        print(f"  DATABASE_URL found in environment")
        return True
    
    print("  DATABASE_URL not in environment, checking .env files...")
    if load_env_file():
        if os.environ.get('DATABASE_URL'):
            print(f"  DATABASE_URL loaded from .env")
            return True
    
    # Try to construct from individual vars
    db_host = os.environ.get('DB_HOST') or os.environ.get('POSTGRES_HOST') or os.environ.get('RDS_HOSTNAME')
    db_port = os.environ.get('DB_PORT') or os.environ.get('POSTGRES_PORT') or os.environ.get('RDS_PORT') or '5432'
    db_name = os.environ.get('DB_NAME') or os.environ.get('POSTGRES_DB') or os.environ.get('RDS_DB_NAME')
    db_user = os.environ.get('DB_USER') or os.environ.get('POSTGRES_USER') or os.environ.get('RDS_USERNAME')
    db_pass = os.environ.get('DB_PASSWORD') or os.environ.get('POSTGRES_PASSWORD') or os.environ.get('RDS_PASSWORD')
    
    if db_host and db_name and db_user and db_pass:
        db_url = f"postgresql://{db_user}:{db_pass}@{db_host}:{db_port}/{db_name}"
        os.environ['DATABASE_URL'] = db_url
        print(f"  DATABASE_URL constructed from individual vars")
        return True
    
    return False

# Check database URL before importing db module
print("\nChecking database configuration...")
if not ensure_database_url():
    print("\n" + "=" * 60)
    print("ERROR: DATABASE_URL not found!")
    print("=" * 60)
    print("""
To fix this, do ONE of the following:

Option 1: Set DATABASE_URL environment variable:
  export DATABASE_URL="postgresql://user:password@host:5432/dbname"
  python setup_admin.py

Option 2: Create a .env file in this directory with:
  DATABASE_URL=postgresql://user:password@host:5432/dbname

Option 3: Find your database URL:
  grep -R "postgresql://" /opt/ethiopian-business
  grep -R "DATABASE_URL" /opt/ethiopian-business

Option 4: Add to Supervisor config:
  sudo nano /etc/supervisor/conf.d/ethiopian-business.conf
  Add: environment=DATABASE_URL="postgresql://..."
  Then: sudo supervisorctl restart ethiopian-business
""")
    sys.exit(1)

import bcrypt
import uuid
from datetime import datetime

# Now import db module (it will use DATABASE_URL from environment)
try:
    from db import get_cursor, get_conn
    print("  Database module loaded successfully")
except Exception as e:
    print(f"\nERROR: Could not load database module: {e}")
    print("\nTrying direct psycopg2 connection...")
    
    import psycopg2
    from psycopg2.extras import RealDictCursor
    from contextlib import contextmanager
    
    @contextmanager
    def get_cursor():
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        conn.autocommit = True
        try:
            with conn.cursor(cursor_factory=RealDictCursor) as cur:
                yield cur
        finally:
            conn.close()
    
    @contextmanager  
    def get_conn():
        conn = psycopg2.connect(os.environ['DATABASE_URL'])
        try:
            yield conn
            conn.commit()
        except:
            conn.rollback()
            raise
        finally:
            conn.close()
    
    print("  Direct psycopg2 connection configured")

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
