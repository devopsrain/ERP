"""
Machinery & Equipment Data Store - PostgreSQL Backend

Full CRUD operations for construction equipment management with:
- Asset registry and tracking
- Site-to-site transfers
- Maintenance scheduling
- Operator shift logs
- HR/LMS integration for certification verification
"""
import json
import logging
import uuid
from datetime import datetime, date, timedelta
from decimal import Decimal
from typing import Optional, List, Dict, Any
from contextlib import contextmanager

from db import get_cursor, get_conn, get_tenant_cursor

logger = logging.getLogger(__name__)


class MachineryDataStore:
    """PostgreSQL-backed machinery/equipment data store."""

    def __init__(self):
        self._ensure_tables()

    def _ensure_tables(self):
        """Create machinery tables if they don't exist."""
        try:
            with get_conn() as conn:
                with conn.cursor() as cur:
                    cur.execute("""
                        -- Main Asset Registry
                        CREATE TABLE IF NOT EXISTS machinery_assets (
                            asset_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            internal_code VARCHAR(50) UNIQUE,
                            qr_code VARCHAR(100),
                            barcode VARCHAR(100),
                            serial_number VARCHAR(100),
                            vin_chassis_number VARCHAR(100),
                            
                            name VARCHAR(255) NOT NULL,
                            description TEXT,
                            category VARCHAR(50) DEFAULT 'other',
                            asset_type VARCHAR(50) DEFAULT 'other',
                            product_class VARCHAR(255),
                            manufacturer VARCHAR(100),
                            model VARCHAR(100),
                            year_manufactured INT DEFAULT 0,
                            
                            status VARCHAR(50) DEFAULT 'available',
                            ownership_type VARCHAR(50) DEFAULT 'owned',
                            fuel_type VARCHAR(50) DEFAULT 'diesel',
                            
                            current_site_id VARCHAR(64),
                            current_site_name VARCHAR(255),
                            current_project_id VARCHAR(64),
                            current_project_name VARCHAR(255),
                            home_yard_id VARCHAR(64),
                            home_yard_name VARCHAR(255),
                            
                            primary_operator_id VARCHAR(64),
                            primary_operator_name VARCHAR(255),
                            backup_operator_id VARCHAR(64),
                            backup_operator_name VARCHAR(255),
                            
                            technical_specs JSONB DEFAULT '{}',
                            gps_location JSONB DEFAULT '{}',
                            utilization JSONB DEFAULT '{}',
                            financial JSONB DEFAULT '{}',
                            
                            service_interval_hours DECIMAL(10,2) DEFAULT 250,
                            next_service_due_hours DECIMAL(10,2) DEFAULT 250,
                            next_service_due_date DATE,
                            last_service_date DATE,
                            maintenance_blocked BOOLEAN DEFAULT FALSE,
                            maintenance_block_reason TEXT,
                            
                            documents JSONB DEFAULT '[]',
                            images JSONB DEFAULT '[]',
                            required_licenses JSONB DEFAULT '[]',
                            required_training_courses JSONB DEFAULT '[]',
                            
                            tags JSONB DEFAULT '[]',
                            notes TEXT,
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_by VARCHAR(100)
                        );
                        
                        -- Sites (Project Sites, Yards, Workshops)
                        CREATE TABLE IF NOT EXISTS machinery_sites (
                            site_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            name VARCHAR(255) NOT NULL,
                            site_type VARCHAR(50) DEFAULT 'project',
                            address TEXT,
                            city VARCHAR(100),
                            region VARCHAR(100),
                            country VARCHAR(100),
                            latitude DECIMAL(10,7),
                            longitude DECIMAL(10,7),
                            geofence_radius_m DECIMAL(10,2) DEFAULT 500,
                            project_id VARCHAR(64),
                            project_name VARCHAR(255),
                            site_manager_id VARCHAR(64),
                            site_manager_name VARCHAR(255),
                            contact_phone VARCHAR(50),
                            is_active BOOLEAN DEFAULT TRUE,
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        
                        -- Transfer Orders
                        CREATE TABLE IF NOT EXISTS machinery_transfers (
                            transfer_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            asset_id VARCHAR(64) NOT NULL,
                            asset_name VARCHAR(255),
                            asset_internal_code VARCHAR(50),
                            
                            from_site_id VARCHAR(64),
                            from_site_name VARCHAR(255),
                            to_site_id VARCHAR(64),
                            to_site_name VARCHAR(255),
                            
                            status VARCHAR(50) DEFAULT 'requested',
                            requested_date DATE,
                            requested_by_id VARCHAR(64),
                            requested_by_name VARCHAR(255),
                            approved_date DATE,
                            approved_by_id VARCHAR(64),
                            approved_by_name VARCHAR(255),
                            departure_date DATE,
                            arrival_date DATE,
                            completed_date DATE,
                            
                            transport_method VARCHAR(100),
                            transport_vehicle_id VARCHAR(64),
                            transport_vehicle_name VARCHAR(255),
                            driver_id VARCHAR(64),
                            driver_name VARCHAR(255),
                            estimated_duration_hours DECIMAL(10,2) DEFAULT 0,
                            actual_duration_hours DECIMAL(10,2) DEFAULT 0,
                            transport_cost DECIMAL(15,2) DEFAULT 0,
                            
                            reason TEXT,
                            priority VARCHAR(20) DEFAULT 'normal',
                            notes TEXT,
                            rejection_reason TEXT,
                            
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        
                        -- Maintenance Work Orders
                        CREATE TABLE IF NOT EXISTS machinery_maintenance (
                            work_order_id VARCHAR(64) PRIMARY KEY,
                            work_order_number VARCHAR(50),
                            company_id VARCHAR(64) DEFAULT 'default',
                            asset_id VARCHAR(64) NOT NULL,
                            asset_name VARCHAR(255),
                            asset_internal_code VARCHAR(50),
                            
                            maintenance_type VARCHAR(50) DEFAULT 'preventive',
                            status VARCHAR(50) DEFAULT 'scheduled',
                            priority VARCHAR(20) DEFAULT 'normal',
                            
                            scheduled_date DATE,
                            due_date DATE,
                            started_at TIMESTAMP,
                            completed_at TIMESTAMP,
                            engine_hours_at_service DECIMAL(10,2) DEFAULT 0,
                            
                            title VARCHAR(255),
                            description TEXT,
                            work_performed TEXT,
                            findings TEXT,
                            recommendations TEXT,
                            
                            assigned_technician_id VARCHAR(64),
                            assigned_technician_name VARCHAR(255),
                            workshop_id VARCHAR(64),
                            workshop_name VARCHAR(255),
                            
                            parts_used JSONB DEFAULT '[]',
                            labor_hours DECIMAL(10,2) DEFAULT 0,
                            labor_cost DECIMAL(15,2) DEFAULT 0,
                            parts_cost DECIMAL(15,2) DEFAULT 0,
                            external_service_cost DECIMAL(15,2) DEFAULT 0,
                            total_cost DECIMAL(15,2) DEFAULT 0,
                            
                            attachments JSONB DEFAULT '[]',
                            requires_approval BOOLEAN DEFAULT FALSE,
                            approved_by_id VARCHAR(64),
                            approved_by_name VARCHAR(255),
                            approved_at TIMESTAMP,
                            
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            created_by VARCHAR(100),
                            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        
                        -- Operator Shift Logs
                        CREATE TABLE IF NOT EXISTS machinery_shift_logs (
                            log_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            asset_id VARCHAR(64) NOT NULL,
                            asset_name VARCHAR(255),
                            operator_id VARCHAR(64) NOT NULL,
                            operator_name VARCHAR(255),
                            
                            shift_date DATE,
                            shift_start TIMESTAMP,
                            shift_end TIMESTAMP,
                            shift_duration_hours DECIMAL(10,2) DEFAULT 0,
                            
                            engine_hours_start DECIMAL(10,2) DEFAULT 0,
                            engine_hours_end DECIMAL(10,2) DEFAULT 0,
                            engine_hours_worked DECIMAL(10,2) DEFAULT 0,
                            idle_hours DECIMAL(10,2) DEFAULT 0,
                            
                            fuel_start_liters DECIMAL(10,2) DEFAULT 0,
                            fuel_end_liters DECIMAL(10,2) DEFAULT 0,
                            fuel_consumed_liters DECIMAL(10,2) DEFAULT 0,
                            fuel_added_liters DECIMAL(10,2) DEFAULT 0,
                            
                            site_id VARCHAR(64),
                            site_name VARCHAR(255),
                            project_id VARCHAR(64),
                            project_name VARCHAR(255),
                            
                            pre_shift_inspection_done BOOLEAN DEFAULT FALSE,
                            pre_shift_issues TEXT,
                            post_shift_inspection_done BOOLEAN DEFAULT FALSE,
                            post_shift_issues TEXT,
                            
                            work_description TEXT,
                            tasks_completed JSONB DEFAULT '[]',
                            
                            incidents_reported BOOLEAN DEFAULT FALSE,
                            incident_description TEXT,
                            
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        
                        -- Fuel Logs
                        CREATE TABLE IF NOT EXISTS machinery_fuel_logs (
                            fuel_log_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            asset_id VARCHAR(64) NOT NULL,
                            asset_name VARCHAR(255),
                            
                            fuel_type VARCHAR(50) DEFAULT 'diesel',
                            quantity_liters DECIMAL(10,2) DEFAULT 0,
                            unit_price DECIMAL(10,4) DEFAULT 0,
                            total_cost DECIMAL(15,2) DEFAULT 0,
                            odometer_reading DECIMAL(15,2) DEFAULT 0,
                            engine_hours DECIMAL(10,2) DEFAULT 0,
                            
                            site_id VARCHAR(64),
                            site_name VARCHAR(255),
                            fuel_station VARCHAR(255),
                            receipt_number VARCHAR(100),
                            
                            fueled_by_id VARCHAR(64),
                            fueled_by_name VARCHAR(255),
                            fueled_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                            
                            notes TEXT
                        );
                        
                        -- Operator Certifications Requirements
                        CREATE TABLE IF NOT EXISTS machinery_certifications (
                            certification_id VARCHAR(64) PRIMARY KEY,
                            company_id VARCHAR(64) DEFAULT 'default',
                            name VARCHAR(255) NOT NULL,
                            description TEXT,
                            certification_type VARCHAR(50),
                            issuing_authority VARCHAR(255),
                            validity_period_months INT DEFAULT 12,
                            required_for_categories JSONB DEFAULT '[]',
                            required_for_types JSONB DEFAULT '[]',
                            is_mandatory BOOLEAN DEFAULT TRUE,
                            lms_course_id VARCHAR(64),
                            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                        );
                        
                        -- Indexes
                        CREATE INDEX IF NOT EXISTS idx_machinery_assets_company ON machinery_assets(company_id);
                        CREATE INDEX IF NOT EXISTS idx_machinery_assets_status ON machinery_assets(status);
                        CREATE INDEX IF NOT EXISTS idx_machinery_assets_category ON machinery_assets(category);
                        CREATE INDEX IF NOT EXISTS idx_machinery_assets_site ON machinery_assets(current_site_id);
                        CREATE INDEX IF NOT EXISTS idx_machinery_transfers_status ON machinery_transfers(status);
                        CREATE INDEX IF NOT EXISTS idx_machinery_maintenance_status ON machinery_maintenance(status);
                        CREATE INDEX IF NOT EXISTS idx_machinery_maintenance_asset ON machinery_maintenance(asset_id);
                        CREATE INDEX IF NOT EXISTS idx_machinery_shift_logs_asset ON machinery_shift_logs(asset_id);
                        CREATE INDEX IF NOT EXISTS idx_machinery_shift_logs_operator ON machinery_shift_logs(operator_id);
                    """)
                    conn.commit()
        except Exception as e:
            logger.warning("Machinery tables check: %s", e)

    # ══════════════════════════════════════════════════════════════════════════
    # ASSETS - CRUD Operations
    # ══════════════════════════════════════════════════════════════════════════

    def create_asset(self, data: dict) -> Optional[str]:
        """Create a new asset in the registry."""
        asset_id = data.get('asset_id') or str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        
        # Generate internal code if not provided
        internal_code = data.get('internal_code', '')
        if not internal_code:
            internal_code = self._generate_internal_code(
                data.get('category', 'other'),
                data.get('asset_type', 'other'),
                cid
            )
        
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO machinery_assets (
                        asset_id, company_id, internal_code, qr_code, barcode,
                        serial_number, vin_chassis_number, name, description,
                        category, asset_type, product_class, manufacturer, model,
                        year_manufactured, status, ownership_type, fuel_type,
                        current_site_id, current_site_name, home_yard_id, home_yard_name,
                        service_interval_hours, next_service_due_hours,
                        technical_specs, financial, required_licenses,
                        required_training_courses, tags, notes, created_by
                    ) VALUES (
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                        %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                    ) RETURNING asset_id
                """, (
                    asset_id, cid, internal_code,
                    data.get('qr_code', internal_code),
                    data.get('barcode', ''),
                    data.get('serial_number', ''),
                    data.get('vin_chassis_number', ''),
                    data.get('name', ''),
                    data.get('description', ''),
                    data.get('category', 'other'),
                    data.get('asset_type', 'other'),
                    data.get('product_class', ''),
                    data.get('manufacturer', ''),
                    data.get('model', ''),
                    int(data.get('year_manufactured', 0)),
                    data.get('status', 'available'),
                    data.get('ownership_type', 'owned'),
                    data.get('fuel_type', 'diesel'),
                    data.get('current_site_id', ''),
                    data.get('current_site_name', ''),
                    data.get('home_yard_id', ''),
                    data.get('home_yard_name', ''),
                    float(data.get('service_interval_hours', 250)),
                    float(data.get('next_service_due_hours', 250)),
                    json.dumps(data.get('technical_specs', {})),
                    json.dumps(data.get('financial', {})),
                    json.dumps(data.get('required_licenses', [])),
                    json.dumps(data.get('required_training_courses', [])),
                    json.dumps(data.get('tags', [])),
                    data.get('notes', ''),
                    data.get('created_by', ''),
                ))
                row = cur.fetchone()
                return row['asset_id'] if row else asset_id
        except Exception as e:
            logger.error("create_asset failed: %s", e)
            return None

    def _generate_internal_code(self, category: str, asset_type: str, company_id: str) -> str:
        """Generate unique internal code like EXC-001, BLD-002."""
        prefix_map = {
            'excavator': 'EXC', 'bulldozer': 'BLD', 'wheel_loader': 'WHL',
            'tower_crane': 'TCR', 'mobile_crane': 'MCR', 'forklift': 'FLT',
            'generator': 'GEN', 'dump_truck': 'DMT', 'concrete_mixer': 'CMX',
            'roller': 'ROL', 'backhoe': 'BCH', 'grader': 'GRD',
        }
        prefix = prefix_map.get(asset_type, category[:3].upper())
        
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT COUNT(*) as cnt FROM machinery_assets 
                    WHERE company_id = %s AND internal_code LIKE %s
                """, (company_id, f"{prefix}-%"))
                row = cur.fetchone()
                count = (row['cnt'] or 0) + 1
                return f"{prefix}-{count:03d}"
        except:
            return f"{prefix}-{uuid.uuid4().hex[:6].upper()}"

    def get_asset(self, asset_id: str, company_id: str = None) -> Optional[dict]:
        """Get a single asset by ID."""
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_assets WHERE asset_id = %s"
                params = [asset_id]
                if company_id:
                    sql += " AND company_id = %s"
                    params.append(company_id)
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_asset_dict(row)
                return None
        except Exception as e:
            logger.error("get_asset failed: %s", e)
            return None

    def get_asset_by_code(self, internal_code: str, company_id: str = None) -> Optional[dict]:
        """Get asset by internal code."""
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_assets WHERE internal_code = %s"
                params = [internal_code]
                if company_id:
                    sql += " AND company_id = %s"
                    params.append(company_id)
                cur.execute(sql, params)
                row = cur.fetchone()
                if row:
                    return self._row_to_asset_dict(row)
                return None
        except Exception as e:
            logger.error("get_asset_by_code failed: %s", e)
            return None

    def _row_to_asset_dict(self, row) -> dict:
        """Convert database row to asset dictionary."""
        d = dict(row)
        d['technical_specs'] = d.get('technical_specs') or {}
        d['gps_location'] = d.get('gps_location') or {}
        d['utilization'] = d.get('utilization') or {}
        d['financial'] = d.get('financial') or {}
        d['documents'] = d.get('documents') or []
        d['images'] = d.get('images') or []
        d['required_licenses'] = d.get('required_licenses') or []
        d['required_training_courses'] = d.get('required_training_courses') or []
        d['tags'] = d.get('tags') or []
        return d

    def get_assets(self, company_id: str = None, status: str = None,
                   category: str = None, site_id: str = None,
                   is_active: bool = True) -> List[dict]:
        """Get all assets with optional filters."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_assets WHERE company_id = %s"
                params = [cid]
                
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                if category:
                    sql += " AND category = %s"
                    params.append(category)
                if site_id:
                    sql += " AND current_site_id = %s"
                    params.append(site_id)
                if is_active is not None:
                    sql += " AND is_active = %s"
                    params.append(is_active)
                
                sql += " ORDER BY internal_code ASC"
                cur.execute(sql, params)
                
                return [self._row_to_asset_dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_assets failed: %s", e)
            return []

    def get_available_assets(self, company_id: str = None, category: str = None,
                             asset_type: str = None) -> List[dict]:
        """Get assets that are available for deployment."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = """
                    SELECT * FROM machinery_assets 
                    WHERE company_id = %s 
                      AND status = 'available' 
                      AND is_active = TRUE
                      AND maintenance_blocked = FALSE
                """
                params = [cid]
                
                if category:
                    sql += " AND category = %s"
                    params.append(category)
                if asset_type:
                    sql += " AND asset_type = %s"
                    params.append(asset_type)
                
                sql += " ORDER BY internal_code ASC"
                cur.execute(sql, params)
                return [self._row_to_asset_dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_available_assets failed: %s", e)
            return []

    def get_underutilized_assets(self, company_id: str = None, 
                                  idle_days_threshold: int = 3) -> List[dict]:
        """Get assets that are underutilized (idle for too long)."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT * FROM machinery_assets 
                    WHERE company_id = %s 
                      AND status = 'in_use'
                      AND is_active = TRUE
                      AND (utilization->>'last_operation_date')::date < CURRENT_DATE - INTERVAL '%s days'
                """, (cid, idle_days_threshold))
                return [self._row_to_asset_dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_underutilized_assets failed: %s", e)
            return []

    def update_asset(self, asset_id: str, data: dict) -> bool:
        """Update an existing asset."""
        try:
            with get_cursor() as cur:
                updates = []
                params = []
                
                # Simple fields
                for field in ['name', 'description', 'category', 'asset_type',
                              'product_class', 'manufacturer', 'model', 'year_manufactured',
                              'status', 'ownership_type', 'fuel_type', 'serial_number',
                              'vin_chassis_number', 'current_site_id', 'current_site_name',
                              'current_project_id', 'current_project_name',
                              'home_yard_id', 'home_yard_name',
                              'primary_operator_id', 'primary_operator_name',
                              'backup_operator_id', 'backup_operator_name',
                              'service_interval_hours', 'next_service_due_hours',
                              'next_service_due_date', 'last_service_date',
                              'maintenance_blocked', 'maintenance_block_reason',
                              'notes', 'is_active']:
                    if field in data:
                        updates.append(f"{field} = %s")
                        params.append(data[field])
                
                # JSON fields
                for field in ['technical_specs', 'gps_location', 'utilization',
                              'financial', 'documents', 'images', 'required_licenses',
                              'required_training_courses', 'tags']:
                    if field in data:
                        updates.append(f"{field} = %s")
                        params.append(json.dumps(data[field]))
                
                updates.append("updated_at = CURRENT_TIMESTAMP")
                
                if not updates:
                    return True
                
                params.append(asset_id)
                cur.execute(f"""
                    UPDATE machinery_assets SET {', '.join(updates)}
                    WHERE asset_id = %s
                """, params)
                return True
        except Exception as e:
            logger.error("update_asset failed: %s", e)
            return False

    def update_asset_status(self, asset_id: str, status: str, 
                            site_id: str = None, site_name: str = None) -> bool:
        """Update asset status and optionally location."""
        try:
            with get_cursor() as cur:
                sql = """
                    UPDATE machinery_assets 
                    SET status = %s, updated_at = CURRENT_TIMESTAMP
                """
                params = [status]
                
                if site_id is not None:
                    sql += ", current_site_id = %s, current_site_name = %s"
                    params.extend([site_id, site_name or ''])
                
                sql += " WHERE asset_id = %s"
                params.append(asset_id)
                
                cur.execute(sql, params)
                return True
        except Exception as e:
            logger.error("update_asset_status failed: %s", e)
            return False

    def update_engine_hours(self, asset_id: str, hours: float) -> bool:
        """Update engine hours and check maintenance due."""
        try:
            asset = self.get_asset(asset_id)
            if not asset:
                return False
            
            utilization = asset.get('utilization', {})
            utilization['total_engine_hours'] = hours
            utilization['last_operation_date'] = str(date.today())
            
            # Check if maintenance is due
            service_interval = float(asset.get('service_interval_hours', 250))
            hours_at_last_service = float(utilization.get('engine_hours_at_last_service', 0))
            hours_since_service = hours - hours_at_last_service
            utilization['hours_since_last_service'] = hours_since_service
            
            # Block for maintenance if approaching service interval
            maintenance_blocked = hours_since_service >= (service_interval - 50)
            
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_assets 
                    SET utilization = %s,
                        maintenance_blocked = %s,
                        maintenance_block_reason = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = %s
                """, (
                    json.dumps(utilization),
                    maintenance_blocked,
                    'Service due soon' if maintenance_blocked else '',
                    asset_id
                ))
                return True
        except Exception as e:
            logger.error("update_engine_hours failed: %s", e)
            return False

    def delete_asset(self, asset_id: str) -> bool:
        """Soft delete an asset (set is_active = False)."""
        try:
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_assets 
                    SET is_active = FALSE, status = 'decommissioned',
                        updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = %s
                """, (asset_id,))
                return True
        except Exception as e:
            logger.error("delete_asset failed: %s", e)
            return False

    # ══════════════════════════════════════════════════════════════════════════
    # SITES - Project Sites, Yards, Workshops
    # ══════════════════════════════════════════════════════════════════════════

    def create_site(self, data: dict) -> Optional[str]:
        """Create a new site/yard."""
        site_id = data.get('site_id') or str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO machinery_sites (
                        site_id, company_id, name, site_type, address, city,
                        region, country, latitude, longitude, geofence_radius_m,
                        project_id, project_name, site_manager_id, site_manager_name,
                        contact_phone
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING site_id
                """, (
                    site_id, cid,
                    data.get('name', ''),
                    data.get('site_type', 'project'),
                    data.get('address', ''),
                    data.get('city', ''),
                    data.get('region', ''),
                    data.get('country', ''),
                    float(data.get('latitude', 0)),
                    float(data.get('longitude', 0)),
                    float(data.get('geofence_radius_m', 500)),
                    data.get('project_id', ''),
                    data.get('project_name', ''),
                    data.get('site_manager_id', ''),
                    data.get('site_manager_name', ''),
                    data.get('contact_phone', ''),
                ))
                row = cur.fetchone()
                return row['site_id'] if row else site_id
        except Exception as e:
            logger.error("create_site failed: %s", e)
            return None

    def get_site(self, site_id: str) -> Optional[dict]:
        """Get a site by ID."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM machinery_sites WHERE site_id = %s", (site_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_site failed: %s", e)
            return None

    def get_sites(self, company_id: str = None, site_type: str = None,
                  is_active: bool = True) -> List[dict]:
        """Get all sites."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_sites WHERE company_id = %s"
                params = [cid]
                if site_type:
                    sql += " AND site_type = %s"
                    params.append(site_type)
                if is_active is not None:
                    sql += " AND is_active = %s"
                    params.append(is_active)
                sql += " ORDER BY name ASC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_sites failed: %s", e)
            return []

    def get_assets_at_site(self, site_id: str, company_id: str = None) -> List[dict]:
        """Get all assets currently at a specific site."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT * FROM machinery_assets 
                    WHERE company_id = %s AND current_site_id = %s AND is_active = TRUE
                    ORDER BY internal_code
                """, (cid, site_id))
                return [self._row_to_asset_dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_assets_at_site failed: %s", e)
            return []

    # ══════════════════════════════════════════════════════════════════════════
    # TRANSFER ORDERS
    # ══════════════════════════════════════════════════════════════════════════

    def create_transfer(self, data: dict) -> Optional[str]:
        """Create a site-to-site transfer request."""
        transfer_id = str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO machinery_transfers (
                        transfer_id, company_id, asset_id, asset_name, asset_internal_code,
                        from_site_id, from_site_name, to_site_id, to_site_name,
                        status, requested_date, requested_by_id, requested_by_name,
                        transport_method, estimated_duration_hours, reason, priority, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING transfer_id
                """, (
                    transfer_id, cid,
                    data.get('asset_id', ''),
                    data.get('asset_name', ''),
                    data.get('asset_internal_code', ''),
                    data.get('from_site_id', ''),
                    data.get('from_site_name', ''),
                    data.get('to_site_id', ''),
                    data.get('to_site_name', ''),
                    'requested',
                    date.today(),
                    data.get('requested_by_id', ''),
                    data.get('requested_by_name', ''),
                    data.get('transport_method', ''),
                    float(data.get('estimated_duration_hours', 0)),
                    data.get('reason', ''),
                    data.get('priority', 'normal'),
                    data.get('notes', ''),
                ))
                row = cur.fetchone()
                return row['transfer_id'] if row else transfer_id
        except Exception as e:
            logger.error("create_transfer failed: %s", e)
            return None

    def get_transfer(self, transfer_id: str) -> Optional[dict]:
        """Get a transfer order by ID."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM machinery_transfers WHERE transfer_id = %s", (transfer_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_transfer failed: %s", e)
            return None

    def get_transfers(self, company_id: str = None, status: str = None,
                      asset_id: str = None) -> List[dict]:
        """Get all transfer orders."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_transfers WHERE company_id = %s"
                params = [cid]
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                if asset_id:
                    sql += " AND asset_id = %s"
                    params.append(asset_id)
                sql += " ORDER BY created_at DESC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_transfers failed: %s", e)
            return []

    def approve_transfer(self, transfer_id: str, approver_id: str, 
                        approver_name: str) -> bool:
        """Approve a transfer request."""
        try:
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_transfers 
                    SET status = 'approved', approved_date = %s,
                        approved_by_id = %s, approved_by_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE transfer_id = %s AND status = 'requested'
                """, (date.today(), approver_id, approver_name, transfer_id))
                return True
        except Exception as e:
            logger.error("approve_transfer failed: %s", e)
            return False

    def start_transfer(self, transfer_id: str, driver_id: str = '',
                      driver_name: str = '', vehicle_id: str = '',
                      vehicle_name: str = '') -> bool:
        """Mark transfer as in-transit and update asset status."""
        try:
            transfer = self.get_transfer(transfer_id)
            if not transfer or transfer.get('status') != 'approved':
                return False
            
            with get_cursor() as cur:
                # Update transfer
                cur.execute("""
                    UPDATE machinery_transfers 
                    SET status = 'in_transit', departure_date = %s,
                        driver_id = %s, driver_name = %s,
                        transport_vehicle_id = %s, transport_vehicle_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE transfer_id = %s
                """, (date.today(), driver_id, driver_name, vehicle_id, vehicle_name, transfer_id))
                
                # Update asset status
                cur.execute("""
                    UPDATE machinery_assets 
                    SET status = 'in_transit', updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = %s
                """, (transfer['asset_id'],))
                
                return True
        except Exception as e:
            logger.error("start_transfer failed: %s", e)
            return False

    def complete_transfer(self, transfer_id: str) -> bool:
        """Complete a transfer and update asset location."""
        try:
            transfer = self.get_transfer(transfer_id)
            if not transfer or transfer.get('status') != 'in_transit':
                return False
            
            with get_cursor() as cur:
                # Update transfer
                cur.execute("""
                    UPDATE machinery_transfers 
                    SET status = 'completed', arrival_date = %s, completed_date = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE transfer_id = %s
                """, (date.today(), date.today(), transfer_id))
                
                # Update asset location
                cur.execute("""
                    UPDATE machinery_assets 
                    SET status = 'available',
                        current_site_id = %s, current_site_name = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = %s
                """, (transfer['to_site_id'], transfer['to_site_name'], transfer['asset_id']))
                
                return True
        except Exception as e:
            logger.error("complete_transfer failed: %s", e)
            return False

    def reject_transfer(self, transfer_id: str, reason: str,
                       rejector_id: str = '', rejector_name: str = '') -> bool:
        """Reject a transfer request."""
        try:
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_transfers 
                    SET status = 'rejected', rejection_reason = %s,
                        approved_by_id = %s, approved_by_name = %s,
                        approved_date = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE transfer_id = %s AND status = 'requested'
                """, (reason, rejector_id, rejector_name, date.today(), transfer_id))
                return True
        except Exception as e:
            logger.error("reject_transfer failed: %s", e)
            return False

    # ══════════════════════════════════════════════════════════════════════════
    # MAINTENANCE WORK ORDERS
    # ══════════════════════════════════════════════════════════════════════════

    def create_maintenance(self, data: dict) -> Optional[str]:
        """Create a maintenance work order."""
        work_order_id = str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        
        # Generate work order number
        wo_number = f"WO-{datetime.now().strftime('%Y%m%d')}-{work_order_id[:6].upper()}"
        
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO machinery_maintenance (
                        work_order_id, work_order_number, company_id, asset_id,
                        asset_name, asset_internal_code, maintenance_type, status,
                        priority, scheduled_date, due_date, title, description,
                        assigned_technician_id, assigned_technician_name,
                        workshop_id, workshop_name, engine_hours_at_service,
                        requires_approval, created_by
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING work_order_id
                """, (
                    work_order_id, wo_number, cid,
                    data.get('asset_id', ''),
                    data.get('asset_name', ''),
                    data.get('asset_internal_code', ''),
                    data.get('maintenance_type', 'preventive'),
                    data.get('status', 'scheduled'),
                    data.get('priority', 'normal'),
                    data.get('scheduled_date'),
                    data.get('due_date'),
                    data.get('title', ''),
                    data.get('description', ''),
                    data.get('assigned_technician_id', ''),
                    data.get('assigned_technician_name', ''),
                    data.get('workshop_id', ''),
                    data.get('workshop_name', ''),
                    float(data.get('engine_hours_at_service', 0)),
                    data.get('requires_approval', False),
                    data.get('created_by', ''),
                ))
                
                # Block asset from reservations if maintenance is scheduled
                if data.get('asset_id'):
                    cur.execute("""
                        UPDATE machinery_assets 
                        SET maintenance_blocked = TRUE,
                            maintenance_block_reason = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE asset_id = %s
                    """, (f"Maintenance scheduled: {wo_number}", data['asset_id']))
                
                row = cur.fetchone()
                return row['work_order_id'] if row else work_order_id
        except Exception as e:
            logger.error("create_maintenance failed: %s", e)
            return None

    def get_maintenance(self, work_order_id: str) -> Optional[dict]:
        """Get a maintenance work order by ID."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM machinery_maintenance WHERE work_order_id = %s", (work_order_id,))
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    d['parts_used'] = d.get('parts_used') or []
                    d['attachments'] = d.get('attachments') or []
                    return d
                return None
        except Exception as e:
            logger.error("get_maintenance failed: %s", e)
            return None

    def get_maintenance_orders(self, company_id: str = None, status: str = None,
                               asset_id: str = None, priority: str = None) -> List[dict]:
        """Get all maintenance work orders."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_maintenance WHERE company_id = %s"
                params = [cid]
                if status:
                    sql += " AND status = %s"
                    params.append(status)
                if asset_id:
                    sql += " AND asset_id = %s"
                    params.append(asset_id)
                if priority:
                    sql += " AND priority = %s"
                    params.append(priority)
                sql += " ORDER BY scheduled_date ASC, priority DESC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_maintenance_orders failed: %s", e)
            return []

    def get_upcoming_maintenance(self, company_id: str = None, 
                                  days_ahead: int = 7) -> List[dict]:
        """Get maintenance due within N days."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT * FROM machinery_maintenance 
                    WHERE company_id = %s 
                      AND status IN ('scheduled', 'overdue')
                      AND (scheduled_date <= CURRENT_DATE + INTERVAL '%s days'
                           OR due_date <= CURRENT_DATE + INTERVAL '%s days')
                    ORDER BY scheduled_date ASC
                """, (cid, days_ahead, days_ahead))
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_upcoming_maintenance failed: %s", e)
            return []

    def start_maintenance(self, work_order_id: str) -> bool:
        """Mark maintenance as started."""
        try:
            wo = self.get_maintenance(work_order_id)
            if not wo:
                return False
            
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_maintenance 
                    SET status = 'in_progress', started_at = CURRENT_TIMESTAMP,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE work_order_id = %s
                """, (work_order_id,))
                
                # Update asset status
                cur.execute("""
                    UPDATE machinery_assets 
                    SET status = 'down_maintenance', updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = %s
                """, (wo['asset_id'],))
                
                return True
        except Exception as e:
            logger.error("start_maintenance failed: %s", e)
            return False

    def complete_maintenance(self, work_order_id: str, data: dict) -> bool:
        """Complete a maintenance work order."""
        try:
            wo = self.get_maintenance(work_order_id)
            if not wo:
                return False
            
            parts_cost = Decimal(str(data.get('parts_cost', 0)))
            labor_cost = Decimal(str(data.get('labor_cost', 0)))
            external_cost = Decimal(str(data.get('external_service_cost', 0)))
            total_cost = parts_cost + labor_cost + external_cost
            
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_maintenance 
                    SET status = 'completed', completed_at = CURRENT_TIMESTAMP,
                        work_performed = %s, findings = %s, recommendations = %s,
                        parts_used = %s, labor_hours = %s,
                        labor_cost = %s, parts_cost = %s, 
                        external_service_cost = %s, total_cost = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE work_order_id = %s
                """, (
                    data.get('work_performed', ''),
                    data.get('findings', ''),
                    data.get('recommendations', ''),
                    json.dumps(data.get('parts_used', [])),
                    float(data.get('labor_hours', 0)),
                    float(labor_cost),
                    float(parts_cost),
                    float(external_cost),
                    float(total_cost),
                    work_order_id,
                ))
                
                # Update asset - clear maintenance block and update service records
                asset = self.get_asset(wo['asset_id'])
                if asset:
                    utilization = asset.get('utilization', {})
                    engine_hours = float(data.get('engine_hours_at_service', 0))
                    if engine_hours > 0:
                        utilization['engine_hours_at_last_service'] = engine_hours
                        utilization['hours_since_last_service'] = 0
                    
                    service_interval = float(asset.get('service_interval_hours', 250))
                    next_due = engine_hours + service_interval
                    
                    cur.execute("""
                        UPDATE machinery_assets 
                        SET status = 'available',
                            maintenance_blocked = FALSE,
                            maintenance_block_reason = '',
                            last_service_date = %s,
                            next_service_due_hours = %s,
                            utilization = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE asset_id = %s
                    """, (date.today(), next_due, json.dumps(utilization), wo['asset_id']))
                    
                    # Update financial - add maintenance cost
                    financial = asset.get('financial', {})
                    financial['total_maintenance_cost'] = float(financial.get('total_maintenance_cost', 0)) + float(total_cost)
                    cur.execute("""
                        UPDATE machinery_assets SET financial = %s WHERE asset_id = %s
                    """, (json.dumps(financial), wo['asset_id']))
                
                return True
        except Exception as e:
            logger.error("complete_maintenance failed: %s", e)
            return False

    # ══════════════════════════════════════════════════════════════════════════
    # OPERATOR SHIFT LOGS
    # ══════════════════════════════════════════════════════════════════════════

    def create_shift_log(self, data: dict) -> Optional[str]:
        """Create an operator shift log."""
        log_id = str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO machinery_shift_logs (
                        log_id, company_id, asset_id, asset_name,
                        operator_id, operator_name, shift_date, shift_start,
                        engine_hours_start, fuel_start_liters,
                        site_id, site_name, project_id, project_name,
                        pre_shift_inspection_done, pre_shift_issues
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING log_id
                """, (
                    log_id, cid,
                    data.get('asset_id', ''),
                    data.get('asset_name', ''),
                    data.get('operator_id', ''),
                    data.get('operator_name', ''),
                    data.get('shift_date', date.today()),
                    data.get('shift_start', datetime.now()),
                    float(data.get('engine_hours_start', 0)),
                    float(data.get('fuel_start_liters', 0)),
                    data.get('site_id', ''),
                    data.get('site_name', ''),
                    data.get('project_id', ''),
                    data.get('project_name', ''),
                    data.get('pre_shift_inspection_done', False),
                    data.get('pre_shift_issues', ''),
                ))
                
                # Update asset status and operator
                if data.get('asset_id'):
                    cur.execute("""
                        UPDATE machinery_assets 
                        SET status = 'in_use',
                            primary_operator_id = %s,
                            primary_operator_name = %s,
                            updated_at = CURRENT_TIMESTAMP
                        WHERE asset_id = %s
                    """, (data.get('operator_id', ''), data.get('operator_name', ''), data['asset_id']))
                
                row = cur.fetchone()
                return row['log_id'] if row else log_id
        except Exception as e:
            logger.error("create_shift_log failed: %s", e)
            return None

    def end_shift_log(self, log_id: str, data: dict) -> bool:
        """End an operator shift and record hours worked."""
        try:
            log = self.get_shift_log(log_id)
            if not log:
                return False
            
            engine_start = float(log.get('engine_hours_start', 0))
            engine_end = float(data.get('engine_hours_end', engine_start))
            engine_worked = engine_end - engine_start
            
            fuel_start = float(log.get('fuel_start_liters', 0))
            fuel_end = float(data.get('fuel_end_liters', fuel_start))
            fuel_consumed = fuel_start - fuel_end + float(data.get('fuel_added_liters', 0))
            
            shift_start = log.get('shift_start')
            shift_end = data.get('shift_end', datetime.now())
            if isinstance(shift_start, str):
                shift_start = datetime.fromisoformat(shift_start)
            if isinstance(shift_end, str):
                shift_end = datetime.fromisoformat(shift_end)
            shift_duration = (shift_end - shift_start).total_seconds() / 3600 if shift_start else 0
            
            with get_cursor() as cur:
                cur.execute("""
                    UPDATE machinery_shift_logs 
                    SET shift_end = %s, shift_duration_hours = %s,
                        engine_hours_end = %s, engine_hours_worked = %s,
                        idle_hours = %s, fuel_end_liters = %s,
                        fuel_consumed_liters = %s, fuel_added_liters = %s,
                        post_shift_inspection_done = %s, post_shift_issues = %s,
                        work_description = %s, tasks_completed = %s,
                        incidents_reported = %s, incident_description = %s
                    WHERE log_id = %s
                """, (
                    shift_end, shift_duration, engine_end, engine_worked,
                    float(data.get('idle_hours', 0)), fuel_end,
                    fuel_consumed, float(data.get('fuel_added_liters', 0)),
                    data.get('post_shift_inspection_done', False),
                    data.get('post_shift_issues', ''),
                    data.get('work_description', ''),
                    json.dumps(data.get('tasks_completed', [])),
                    data.get('incidents_reported', False),
                    data.get('incident_description', ''),
                    log_id,
                ))
                
                # Update asset engine hours
                if log.get('asset_id'):
                    self.update_engine_hours(log['asset_id'], engine_end)
                
                return True
        except Exception as e:
            logger.error("end_shift_log failed: %s", e)
            return False

    def get_shift_log(self, log_id: str) -> Optional[dict]:
        """Get a shift log by ID."""
        try:
            with get_cursor() as cur:
                cur.execute("SELECT * FROM machinery_shift_logs WHERE log_id = %s", (log_id,))
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    d['tasks_completed'] = d.get('tasks_completed') or []
                    return d
                return None
        except Exception as e:
            logger.error("get_shift_log failed: %s", e)
            return None

    def get_shift_logs(self, company_id: str = None, asset_id: str = None,
                       operator_id: str = None, from_date: date = None,
                       to_date: date = None) -> List[dict]:
        """Get shift logs with filters."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_shift_logs WHERE company_id = %s"
                params = [cid]
                if asset_id:
                    sql += " AND asset_id = %s"
                    params.append(asset_id)
                if operator_id:
                    sql += " AND operator_id = %s"
                    params.append(operator_id)
                if from_date:
                    sql += " AND shift_date >= %s"
                    params.append(from_date)
                if to_date:
                    sql += " AND shift_date <= %s"
                    params.append(to_date)
                sql += " ORDER BY shift_date DESC, shift_start DESC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_shift_logs failed: %s", e)
            return []

    def get_active_shift(self, asset_id: str) -> Optional[dict]:
        """Get currently active shift for an asset."""
        try:
            with get_cursor() as cur:
                cur.execute("""
                    SELECT * FROM machinery_shift_logs 
                    WHERE asset_id = %s AND shift_end IS NULL
                    ORDER BY shift_start DESC LIMIT 1
                """, (asset_id,))
                row = cur.fetchone()
                return dict(row) if row else None
        except Exception as e:
            logger.error("get_active_shift failed: %s", e)
            return None

    # ══════════════════════════════════════════════════════════════════════════
    # FUEL LOGS
    # ══════════════════════════════════════════════════════════════════════════

    def create_fuel_log(self, data: dict) -> Optional[str]:
        """Create a fuel dispensing record."""
        fuel_log_id = str(uuid.uuid4())
        cid = data.get('company_id', 'default')
        
        quantity = float(data.get('quantity_liters', 0))
        unit_price = Decimal(str(data.get('unit_price', 0)))
        total_cost = Decimal(str(quantity)) * unit_price
        
        try:
            with get_cursor() as cur:
                cur.execute("""
                    INSERT INTO machinery_fuel_logs (
                        fuel_log_id, company_id, asset_id, asset_name,
                        fuel_type, quantity_liters, unit_price, total_cost,
                        odometer_reading, engine_hours, site_id, site_name,
                        fuel_station, receipt_number, fueled_by_id, fueled_by_name, notes
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING fuel_log_id
                """, (
                    fuel_log_id, cid,
                    data.get('asset_id', ''),
                    data.get('asset_name', ''),
                    data.get('fuel_type', 'diesel'),
                    quantity,
                    float(unit_price),
                    float(total_cost),
                    float(data.get('odometer_reading', 0)),
                    float(data.get('engine_hours', 0)),
                    data.get('site_id', ''),
                    data.get('site_name', ''),
                    data.get('fuel_station', ''),
                    data.get('receipt_number', ''),
                    data.get('fueled_by_id', ''),
                    data.get('fueled_by_name', ''),
                    data.get('notes', ''),
                ))
                
                # Update asset fuel cost
                if data.get('asset_id'):
                    asset = self.get_asset(data['asset_id'])
                    if asset:
                        financial = asset.get('financial', {})
                        financial['total_fuel_cost'] = float(financial.get('total_fuel_cost', 0)) + float(total_cost)
                        cur.execute("""
                            UPDATE machinery_assets SET financial = %s WHERE asset_id = %s
                        """, (json.dumps(financial), data['asset_id']))
                
                row = cur.fetchone()
                return row['fuel_log_id'] if row else fuel_log_id
        except Exception as e:
            logger.error("create_fuel_log failed: %s", e)
            return None

    def get_fuel_logs(self, company_id: str = None, asset_id: str = None,
                      from_date: date = None, to_date: date = None) -> List[dict]:
        """Get fuel logs with filters."""
        cid = company_id or 'default'
        try:
            with get_cursor() as cur:
                sql = "SELECT * FROM machinery_fuel_logs WHERE company_id = %s"
                params = [cid]
                if asset_id:
                    sql += " AND asset_id = %s"
                    params.append(asset_id)
                if from_date:
                    sql += " AND fueled_at >= %s"
                    params.append(from_date)
                if to_date:
                    sql += " AND fueled_at <= %s"
                    params.append(to_date)
                sql += " ORDER BY fueled_at DESC"
                cur.execute(sql, params)
                return [dict(row) for row in cur.fetchall()]
        except Exception as e:
            logger.error("get_fuel_logs failed: %s", e)
            return []

    # ══════════════════════════════════════════════════════════════════════════
    # HR / LMS INTEGRATION - Certification Checks
    # ══════════════════════════════════════════════════════════════════════════

    def check_operator_certification(self, operator_id: str, asset_id: str,
                                     company_id: str = None) -> dict:
        """
        Check if an operator has valid certifications to operate an asset.
        Integrates with HR module for licenses and LMS for training.
        """
        cid = company_id or 'default'
        result = {
            'is_certified': True,
            'missing_licenses': [],
            'missing_training': [],
            'expired_licenses': [],
            'warnings': [],
        }
        
        try:
            # Get asset requirements
            asset = self.get_asset(asset_id, cid)
            if not asset:
                result['is_certified'] = False
                result['warnings'].append('Asset not found')
                return result
            
            required_licenses = asset.get('required_licenses', [])
            required_training = asset.get('required_training_courses', [])
            
            # Check HR module for operator licenses
            try:
                from employee_store import get_employee_store
                emp_store = get_employee_store()
                employee = emp_store.get_employee(operator_id)
                
                if not employee:
                    result['is_certified'] = False
                    result['warnings'].append('Operator not found in HR system')
                    return result
                
                # Check for medical fitness
                medical_expiry = employee.get('medical_expiry_date')
                if medical_expiry:
                    if isinstance(medical_expiry, str):
                        medical_expiry = date.fromisoformat(medical_expiry)
                    if medical_expiry < date.today():
                        result['is_certified'] = False
                        result['expired_licenses'].append('Medical Fit-to-Work Certificate')
                
                # Check operator licenses (would typically be in employee record)
                employee_licenses = employee.get('licenses', [])
                for req_license in required_licenses:
                    found = False
                    for emp_license in employee_licenses:
                        if emp_license.get('name', '').lower() == req_license.lower():
                            # Check expiry
                            expiry = emp_license.get('expiry_date')
                            if expiry:
                                if isinstance(expiry, str):
                                    expiry = date.fromisoformat(expiry)
                                if expiry < date.today():
                                    result['expired_licenses'].append(req_license)
                                else:
                                    found = True
                            else:
                                found = True
                            break
                    if not found and req_license not in result['expired_licenses']:
                        result['missing_licenses'].append(req_license)
                        
            except ImportError:
                result['warnings'].append('HR module not available - license check skipped')
            except Exception as hr_err:
                result['warnings'].append(f'HR check failed: {str(hr_err)}')
            
            # Check LMS for required training courses
            try:
                from lms_data_store import lms_store
                
                for course_name in required_training:
                    # Check if operator completed the course
                    enrollments = lms_store.get_user_enrollments(operator_id, cid)
                    completed = False
                    for enrollment in enrollments:
                        if enrollment.get('course_title', '').lower() == course_name.lower():
                            if enrollment.get('status') == 'completed':
                                # Check certificate validity
                                certs = lms_store.get_user_certificates(operator_id, cid)
                                for cert in certs:
                                    if cert.get('course_title', '').lower() == course_name.lower():
                                        if cert.get('status') == 'valid':
                                            expiry = cert.get('expiry_date')
                                            if expiry and isinstance(expiry, str):
                                                expiry = date.fromisoformat(expiry)
                                            if not expiry or expiry >= date.today():
                                                completed = True
                                                break
                                if completed:
                                    break
                            break
                    
                    if not completed:
                        result['missing_training'].append(course_name)
                        
            except ImportError:
                result['warnings'].append('LMS module not available - training check skipped')
            except Exception as lms_err:
                result['warnings'].append(f'LMS check failed: {str(lms_err)}')
            
            # Determine final certification status
            if result['missing_licenses'] or result['expired_licenses'] or result['missing_training']:
                result['is_certified'] = False
            
            return result
            
        except Exception as e:
            logger.error("check_operator_certification failed: %s", e)
            result['is_certified'] = False
            result['warnings'].append(f'Certification check error: {str(e)}')
            return result

    def assign_operator(self, asset_id: str, operator_id: str, operator_name: str,
                       is_backup: bool = False, company_id: str = None,
                       force: bool = False) -> dict:
        """
        Assign an operator to an asset with certification verification.
        Returns success status and any warnings/blocks.
        """
        cid = company_id or 'default'
        result = {
            'success': False,
            'message': '',
            'certification_status': None,
        }
        
        # Check certifications unless forced
        if not force:
            cert_check = self.check_operator_certification(operator_id, asset_id, cid)
            result['certification_status'] = cert_check
            
            if not cert_check['is_certified']:
                issues = []
                if cert_check['missing_licenses']:
                    issues.append(f"Missing licenses: {', '.join(cert_check['missing_licenses'])}")
                if cert_check['expired_licenses']:
                    issues.append(f"Expired licenses: {', '.join(cert_check['expired_licenses'])}")
                if cert_check['missing_training']:
                    issues.append(f"Missing training: {', '.join(cert_check['missing_training'])}")
                
                result['message'] = "Cannot assign operator - " + "; ".join(issues)
                return result
        
        # Update asset with operator assignment
        try:
            field_id = 'backup_operator_id' if is_backup else 'primary_operator_id'
            field_name = 'backup_operator_name' if is_backup else 'primary_operator_name'
            
            with get_cursor() as cur:
                cur.execute(f"""
                    UPDATE machinery_assets 
                    SET {field_id} = %s, {field_name} = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE asset_id = %s
                """, (operator_id, operator_name, asset_id))
                
                result['success'] = True
                result['message'] = f"Operator assigned successfully"
                if result['certification_status'] and result['certification_status'].get('warnings'):
                    result['message'] += f" (Warnings: {', '.join(result['certification_status']['warnings'])})"
                
                return result
        except Exception as e:
            logger.error("assign_operator failed: %s", e)
            result['message'] = f"Database error: {str(e)}"
            return result

    # ══════════════════════════════════════════════════════════════════════════
    # JOB COSTING - Project Billing
    # ══════════════════════════════════════════════════════════════════════════

    def calculate_project_equipment_cost(self, project_id: str, 
                                          from_date: date = None,
                                          to_date: date = None,
                                          company_id: str = None) -> dict:
        """
        Calculate total equipment cost for a project based on logged hours
        and internal rental rates.
        """
        cid = company_id or 'default'
        result = {
            'project_id': project_id,
            'from_date': str(from_date) if from_date else None,
            'to_date': str(to_date) if to_date else None,
            'assets': [],
            'total_hours': 0,
            'total_equipment_cost': 0,
            'total_fuel_cost': 0,
            'grand_total': 0,
        }
        
        try:
            with get_cursor() as cur:
                # Get shift logs for the project
                sql = """
                    SELECT sl.asset_id, sl.asset_name, 
                           SUM(sl.engine_hours_worked) as total_hours,
                           SUM(sl.fuel_consumed_liters) as fuel_consumed
                    FROM machinery_shift_logs sl
                    WHERE sl.company_id = %s AND sl.project_id = %s
                """
                params = [cid, project_id]
                
                if from_date:
                    sql += " AND sl.shift_date >= %s"
                    params.append(from_date)
                if to_date:
                    sql += " AND sl.shift_date <= %s"
                    params.append(to_date)
                
                sql += " GROUP BY sl.asset_id, sl.asset_name"
                cur.execute(sql, params)
                
                for row in cur.fetchall():
                    asset = self.get_asset(row['asset_id'])
                    hours = float(row['total_hours'] or 0)
                    
                    # Get internal rental rate
                    rate = 0
                    if asset and asset.get('financial'):
                        rate = float(asset['financial'].get('internal_rental_rate_per_hour', 0))
                    
                    equipment_cost = hours * rate
                    
                    asset_entry = {
                        'asset_id': row['asset_id'],
                        'asset_name': row['asset_name'],
                        'hours_logged': hours,
                        'hourly_rate': rate,
                        'equipment_cost': equipment_cost,
                        'fuel_consumed_liters': float(row['fuel_consumed'] or 0),
                    }
                    result['assets'].append(asset_entry)
                    result['total_hours'] += hours
                    result['total_equipment_cost'] += equipment_cost
                
                # Get fuel costs
                cur.execute("""
                    SELECT COALESCE(SUM(fl.total_cost), 0) as fuel_total
                    FROM machinery_fuel_logs fl
                    JOIN machinery_shift_logs sl ON fl.asset_id = sl.asset_id 
                         AND fl.fueled_at::date = sl.shift_date
                    WHERE sl.company_id = %s AND sl.project_id = %s
                """, (cid, project_id))
                fuel_row = cur.fetchone()
                result['total_fuel_cost'] = float(fuel_row['fuel_total'] or 0) if fuel_row else 0
                
                result['grand_total'] = result['total_equipment_cost'] + result['total_fuel_cost']
                
                return result
        except Exception as e:
            logger.error("calculate_project_equipment_cost failed: %s", e)
            return result

    # ══════════════════════════════════════════════════════════════════════════
    # DASHBOARD & ANALYTICS
    # ══════════════════════════════════════════════════════════════════════════

    def get_dashboard_stats(self, company_id: str = None) -> dict:
        """Get machinery dashboard statistics."""
        cid = company_id or 'default'
        stats = {
            'total_assets': 0,
            'available': 0,
            'in_use': 0,
            'down': 0,
            'in_transit': 0,
            'pending_transfers': 0,
            'pending_maintenance': 0,
            'overdue_maintenance': 0,
            'underutilized': 0,
            'by_category': {},
            'by_status': {},
        }
        
        try:
            with get_cursor() as cur:
                # Asset counts by status
                cur.execute("""
                    SELECT status, COUNT(*) as cnt
                    FROM machinery_assets 
                    WHERE company_id = %s AND is_active = TRUE
                    GROUP BY status
                """, (cid,))
                
                for row in cur.fetchall():
                    status = row['status']
                    count = row['cnt']
                    stats['by_status'][status] = count
                    stats['total_assets'] += count
                    
                    if status == 'available':
                        stats['available'] = count
                    elif status == 'in_use':
                        stats['in_use'] = count
                    elif status in ('down_broken', 'down_maintenance'):
                        stats['down'] += count
                    elif status == 'in_transit':
                        stats['in_transit'] = count
                
                # Asset counts by category
                cur.execute("""
                    SELECT category, COUNT(*) as cnt
                    FROM machinery_assets 
                    WHERE company_id = %s AND is_active = TRUE
                    GROUP BY category
                """, (cid,))
                for row in cur.fetchall():
                    stats['by_category'][row['category']] = row['cnt']
                
                # Pending transfers
                cur.execute("""
                    SELECT COUNT(*) as cnt FROM machinery_transfers 
                    WHERE company_id = %s AND status = 'requested'
                """, (cid,))
                row = cur.fetchone()
                stats['pending_transfers'] = row['cnt'] or 0
                
                # Pending/overdue maintenance
                cur.execute("""
                    SELECT status, COUNT(*) as cnt FROM machinery_maintenance 
                    WHERE company_id = %s AND status IN ('scheduled', 'overdue')
                    GROUP BY status
                """, (cid,))
                for row in cur.fetchall():
                    if row['status'] == 'scheduled':
                        stats['pending_maintenance'] = row['cnt']
                    elif row['status'] == 'overdue':
                        stats['overdue_maintenance'] = row['cnt']
                
                # Underutilized assets
                cur.execute("""
                    SELECT COUNT(*) as cnt FROM machinery_assets 
                    WHERE company_id = %s AND status = 'in_use' AND is_active = TRUE
                      AND (utilization->>'is_underutilized')::boolean = TRUE
                """, (cid,))
                row = cur.fetchone()
                stats['underutilized'] = row['cnt'] or 0
                
                return stats
        except Exception as e:
            logger.error("get_dashboard_stats failed: %s", e)
            return stats


# Singleton instance
machinery_store = MachineryDataStore()
