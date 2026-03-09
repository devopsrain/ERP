"""
Multi-Tenant & Module Licensing Data Store  PostgreSQL backend
"""

import os
import uuid
import logging
from datetime import datetime, date, timedelta
from typing import Dict, List, Optional

from db import get_cursor, get_conn

logger = logging.getLogger(__name__)

#  Subscription Tiers 
SUBSCRIPTION_TIERS = {
    'starter': {
        'display_name': 'Starter',
        'max_users': 3,
        'max_employees': 25,
        'price_monthly_etb': 0,
        'modules': ['auth', 'accounts', 'journal', 'vat'],
        'description': 'Core accounting  Chart of Accounts, Journal Entries, VAT',
    },
    'professional': {
        'display_name': 'Professional',
        'max_users': 15,
        'max_employees': 200,
        'price_monthly_etb': 2500,
        'modules': ['auth', 'accounts', 'journal', 'vat', 'income_expense',
                    'transactions', 'cpo', 'employees', 'payroll'],
        'description': 'Full financial suite  adds Income/Expense, Transactions, CPO, Payroll',
    },
    'enterprise': {
        'display_name': 'Enterprise',
        'max_users': 999999,
        'max_employees': 999999,
        'price_monthly_etb': 7500,
        'modules': ['auth', 'accounts', 'journal', 'vat', 'income_expense',
                    'transactions', 'cpo', 'employees', 'payroll', 'inventory',
                    'bid', 'siem', 'backup', 'version', 'multicompany'],
        'description': 'Everything  adds Inventory, Bid Tracker, SIEM, Backup, Version Control',
    },
}

BLUEPRINT_TO_MODULE: Dict[str, str] = {
    'auth': 'auth', 'accounts_bp': 'accounts', 'chart_of_accounts': 'accounts',
    'journal_bp': 'journal', 'journal': 'journal', 'vat': 'vat', 'vat_bp': 'vat',
    'income_expense': 'income_expense', 'income_expense_bp': 'income_expense',
    'transaction': 'transactions', 'transaction_bp': 'transactions',
    'transactions': 'transactions', 'cpo': 'cpo', 'cpo_bp': 'cpo',
    'payroll': 'payroll', 'employee': 'employees', 'employees': 'employees',
    'inventory': 'inventory', 'inventory_bp': 'inventory', 'bid': 'bid',
    'bid_bp': 'bid', 'siem': 'siem', 'siem_bp': 'siem', 'backup': 'backup',
    'backup_bp': 'backup', 'version': 'version', 'version_bp': 'version',
    'multicompany': 'multicompany',
}

ALWAYS_ALLOWED_MODULES = frozenset({'auth', 'static'})


class TenantDataStore:
    """Manages company tenants, subscriptions, and module licenses."""

    def __init__(self):
        pass  # Tables created by init_db.sql

    #  Tenant CRUD 

    def create_tenant(self, company_data: dict, created_by: str = 'system') -> dict:
        tier_key = company_data.get('subscription_tier', 'starter')
        tier = SUBSCRIPTION_TIERS.get(tier_key, SUBSCRIPTION_TIERS['starter'])
        now = datetime.now().isoformat()
        company_id = company_data.get('company_id') or str(uuid.uuid4())

        record = {
            'company_id': company_id,
            'company_name': company_data.get('company_name', ''),
            'registration_number': company_data.get('registration_number', ''),
            'tin_number': company_data.get('tin_number', ''),
            'address': company_data.get('address', ''),
            'city': company_data.get('city', 'Addis Ababa'),
            'country': company_data.get('country', 'Ethiopia'),
            'phone': company_data.get('phone', ''),
            'email': company_data.get('email', ''),
            'business_type': company_data.get('business_type', ''),
            'subscription_tier': tier_key,
            'subscription_status': 'active',
            'subscription_start': date.today().isoformat(),
            'subscription_end': (date.today() + timedelta(days=365)).isoformat(),
            'max_users': tier['max_users'],
            'max_employees': tier['max_employees'],
            'license_key': str(uuid.uuid4()),
            'is_active': True,
            'created_at': now,
            'updated_at': now,
            'created_by': created_by,
            'notes': company_data.get('notes', ''),
        }

        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO tenants
                       (company_id,company_name,registration_number,tin_number,
                        address,city,country,phone,email,business_type,
                        subscription_tier,subscription_status,subscription_start,
                        subscription_end,max_users,max_employees,license_key,
                        is_active,created_at,updated_at,created_by,notes)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    tuple(record.values())
                )
        except Exception as e:
            logger.error("create_tenant failed: %s", e)
            raise

        self._provision_tier_licenses(company_id, tier_key, created_by)
        return record

    def get_tenant(self, company_id: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM tenants WHERE company_id=%s", (company_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_tenant failed: %s", e)
            return None

    def get_tenant_by_license_key(self, license_key: str) -> Optional[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM tenants WHERE license_key=%s", (license_key,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_tenant_by_license_key failed: %s", e)
            return None

    def get_all_tenants(self) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM tenants ORDER BY company_name")
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_all_tenants failed: %s", e)
            return []

    def ensure_default_tenant(self) -> str:
        """Return the first active tenant's company_id, creating a default one if none exist."""
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT company_id FROM tenants WHERE is_active=TRUE "
                    "ORDER BY created_at LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    return row['company_id']
        except Exception as e:
            logger.error("ensure_default_tenant query failed: %s", e)

        # No tenant exists — seed a default one
        try:
            self.create_tenant({
                'company_id': 'default',
                'company_name': 'Default Company',
                'subscription_tier': 'enterprise',
            }, created_by='system')
            logger.info("Created default tenant")
        except Exception as e:
            logger.error("ensure_default_tenant create failed: %s", e)
        return 'default'

    def update_tenant(self, company_id: str, updates: dict) -> bool:
        protected = {'company_id', 'created_at', 'created_by', 'license_key'}
        clean = {k: v for k, v in updates.items() if k not in protected}
        if not clean:
            return True
        clean['updated_at'] = datetime.now().isoformat()
        cols = ', '.join(f"{k}=%s" for k in clean)
        vals = list(clean.values()) + [company_id]
        try:
            with get_cursor() as cur:
                cur.execute(f"UPDATE tenants SET {cols} WHERE company_id=%s", vals)
            return True
        except Exception as e:
            logger.error("update_tenant failed: %s", e)
            return False

    def suspend_tenant(self, company_id: str, performed_by: str = 'system') -> bool:
        ok = self.update_tenant(company_id, {'subscription_status': 'suspended', 'is_active': False})
        if ok:
            self._audit_log(company_id, 'all', 'suspend', performed_by, 'Tenant suspended')
        return ok

    def reactivate_tenant(self, company_id: str, performed_by: str = 'system') -> bool:
        ok = self.update_tenant(company_id, {'subscription_status': 'active', 'is_active': True})
        if ok:
            self._audit_log(company_id, 'all', 'reactivate', performed_by, 'Tenant reactivated')
        return ok

    def delete_tenant(self, company_id: str) -> bool:
        try:
            with get_cursor() as cur:
                cur.execute("DELETE FROM tenants WHERE company_id=%s", (company_id,))
                return cur.rowcount > 0
        except Exception as e:
            logger.error("delete_tenant failed: %s", e)
            return False

    def change_subscription_tier(self, company_id: str, new_tier: str,
                                  performed_by: str = 'system') -> bool:
        tier = SUBSCRIPTION_TIERS.get(new_tier)
        if not tier:
            return False
        ok = self.update_tenant(company_id, {
            'subscription_tier': new_tier,
            'max_users': tier['max_users'],
            'max_employees': tier['max_employees'],
        })
        if ok:
            self._provision_tier_licenses(company_id, new_tier, performed_by)
            self._audit_log(company_id, 'all', 'tier_change', performed_by,
                            f'Changed to {new_tier}')
        return ok

    def is_subscription_active(self, company_id: str) -> bool:
        tenant = self.get_tenant(company_id)
        if not tenant:
            return False
        if not tenant.get('is_active'):
            return False
        return tenant.get('subscription_status') == 'active'

    #  License Management 

    def _provision_tier_licenses(self, company_id: str, tier_key: str,
                                  granted_by: str = 'system'):
        tier = SUBSCRIPTION_TIERS.get(tier_key, SUBSCRIPTION_TIERS['starter'])
        now = datetime.now().isoformat()
        expires = (date.today() + timedelta(days=365)).isoformat()
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM licenses WHERE company_id=%s", (company_id,))
                    for module in tier['modules']:
                        cur.execute(
                            """INSERT INTO licenses
                               (license_id,company_id,module_name,is_enabled,
                                granted_at,expires_at,granted_by,notes)
                               VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                            (str(uuid.uuid4()), company_id, module, True,
                             now, expires, granted_by, f'Auto-provisioned for {tier_key}')
                        )
        except Exception as e:
            logger.error("_provision_tier_licenses failed: %s", e)

    def get_company_licenses(self, company_id: str) -> List[dict]:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT * FROM licenses WHERE company_id=%s AND is_enabled=TRUE",
                    (company_id,)
                )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_company_licenses failed: %s", e)
            return []

    def is_module_licensed(self, company_id: str, module_name: str) -> bool:
        if module_name in ALWAYS_ALLOWED_MODULES:
            return True
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM licenses WHERE company_id=%s AND module_name=%s "
                    "AND is_enabled=TRUE",
                    (company_id, module_name)
                )
                return cur.fetchone() is not None
        except Exception as e:
            logger.error("is_module_licensed failed: %s", e)
            return False

    def is_blueprint_licensed(self, company_id: str, blueprint_name: str) -> bool:
        module = BLUEPRINT_TO_MODULE.get(blueprint_name, blueprint_name)
        return self.is_module_licensed(company_id, module)

    def set_module_license(self, company_id: str, module_name: str,
                           is_enabled: bool, performed_by: str = 'system') -> bool:
        try:
            with get_cursor() as cur:
                cur.execute(
                    "SELECT 1 FROM licenses WHERE company_id=%s AND module_name=%s",
                    (company_id, module_name)
                )
                exists = cur.fetchone()
                if exists:
                    cur.execute(
                        "UPDATE licenses SET is_enabled=%s WHERE company_id=%s AND module_name=%s",
                        (is_enabled, company_id, module_name)
                    )
                else:
                    cur.execute(
                        """INSERT INTO licenses
                           (license_id,company_id,module_name,is_enabled,
                            granted_at,expires_at,granted_by,notes)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)""",
                        (str(uuid.uuid4()), company_id, module_name, is_enabled,
                         datetime.now().isoformat(), '', performed_by, '')
                    )
            action = 'enable' if is_enabled else 'disable'
            self._audit_log(company_id, module_name, action, performed_by, '')
            return True
        except Exception as e:
            logger.error("set_module_license failed: %s", e)
            return False

    #  Audit Log 

    def _audit_log(self, company_id: str, module_name: str, action: str,
                   performed_by: str, details: str):
        try:
            with get_cursor() as cur:
                cur.execute(
                    """INSERT INTO license_audit
                       (audit_id,company_id,module_name,action,performed_by,timestamp,details)
                       VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    (str(uuid.uuid4()), company_id, module_name, action,
                     performed_by, datetime.now().isoformat(), details)
                )
        except Exception as e:
            logger.warning("_audit_log failed: %s", e)

    def get_audit_log(self, company_id: str = None, limit: int = 200) -> List[dict]:
        try:
            with get_cursor() as cur:
                if company_id:
                    cur.execute(
                        "SELECT * FROM license_audit WHERE company_id=%s "
                        "ORDER BY timestamp DESC LIMIT %s",
                        (company_id, limit)
                    )
                else:
                    cur.execute(
                        "SELECT * FROM license_audit ORDER BY timestamp DESC LIMIT %s",
                        (limit,)
                    )
                return [dict(r) for r in cur.fetchall()]
        except Exception as e:
            logger.error("get_audit_log failed: %s", e)
            return []

    #  Statistics 

    def get_platform_stats(self) -> dict:
        try:
            with get_cursor() as cur:
                cur.execute("SELECT COUNT(*) AS total FROM tenants")
                total = cur.fetchone()['total']
                cur.execute("SELECT COUNT(*) AS active FROM tenants WHERE is_active=TRUE")
                active = cur.fetchone()['active']
                cur.execute(
                    "SELECT subscription_tier, COUNT(*) AS cnt FROM tenants GROUP BY subscription_tier"
                )
                tier_breakdown = {r['subscription_tier']: r['cnt'] for r in cur.fetchall()}
            return {
                'total_tenants': total,
                'active_tenants': active,
                'tier_breakdown': tier_breakdown,
            }
        except Exception as e:
            logger.error("get_platform_stats failed: %s", e)
            return {'total_tenants': 0, 'active_tenants': 0, 'tier_breakdown': {}}


# Singleton instance
tenant_store = TenantDataStore()
