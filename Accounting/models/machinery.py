"""
Machinery & Equipment Management Module - Data Models

Enterprise-grade asset management for construction equipment with:
- Hierarchical asset classification
- Real-time status & location tracking
- HR/LMS integration for operator certification
- Maintenance scheduling & job costing
"""
from dataclasses import dataclass, field
from datetime import datetime, date, timedelta
from decimal import Decimal
from enum import Enum
from typing import Optional, List, Dict, Any
import uuid


# ══════════════════════════════════════════════════════════════════════════════
# ENUMERATIONS
# ══════════════════════════════════════════════════════════════════════════════

class AssetCategory(str, Enum):
    """Top-level asset categories"""
    EARTHMOVING = "earthmoving"
    LIFTING = "lifting"
    POWER_GENERATION = "power_generation"
    TRANSPORT = "transport"
    CONCRETE = "concrete"
    COMPACTION = "compaction"
    DRILLING = "drilling"
    MATERIAL_HANDLING = "material_handling"
    SURVEYING = "surveying"
    SAFETY_EQUIPMENT = "safety_equipment"
    OTHER = "other"


class AssetType(str, Enum):
    """Specific equipment types"""
    # Earthmoving
    EXCAVATOR = "excavator"
    BULLDOZER = "bulldozer"
    WHEEL_LOADER = "wheel_loader"
    BACKHOE = "backhoe"
    GRADER = "grader"
    SCRAPER = "scraper"
    # Lifting
    TOWER_CRANE = "tower_crane"
    MOBILE_CRANE = "mobile_crane"
    FORKLIFT = "forklift"
    TELEHANDLER = "telehandler"
    HOIST = "hoist"
    # Power Generation
    GENERATOR = "generator"
    COMPRESSOR = "compressor"
    WELDER = "welder"
    LIGHT_TOWER = "light_tower"
    # Transport
    DUMP_TRUCK = "dump_truck"
    WATER_TANKER = "water_tanker"
    FLATBED_TRUCK = "flatbed_truck"
    LOW_LOADER = "low_loader"
    PICKUP_TRUCK = "pickup_truck"
    # Concrete
    CONCRETE_MIXER = "concrete_mixer"
    CONCRETE_PUMP = "concrete_pump"
    BATCHING_PLANT = "batching_plant"
    # Compaction
    ROLLER = "roller"
    PLATE_COMPACTOR = "plate_compactor"
    # Drilling
    PILE_DRIVER = "pile_driver"
    DRILL_RIG = "drill_rig"
    # Other
    SCAFFOLD = "scaffold"
    PUMP = "pump"
    OTHER = "other"


class AssetStatus(str, Enum):
    """Real-time availability status"""
    AVAILABLE = "available"           # Ready at yard, can be deployed
    IN_USE = "in_use"                 # Currently operating on a project
    RESERVED = "reserved"             # Booked for upcoming project
    DOWN_BROKEN = "down_broken"       # Mechanical failure, needs repair
    DOWN_MAINTENANCE = "down_maintenance"  # Scheduled maintenance
    IN_TRANSIT = "in_transit"         # Being transported between sites
    DECOMMISSIONED = "decommissioned" # End of lifecycle
    PENDING_INSPECTION = "pending_inspection"


class OwnershipType(str, Enum):
    """Asset ownership/acquisition type"""
    OWNED = "owned"
    LEASED = "leased"
    RENTED = "rented"
    SUBCONTRACTOR = "subcontractor"


class MaintenanceType(str, Enum):
    """Types of maintenance activities"""
    PREVENTIVE = "preventive"         # Scheduled based on hours/time
    CORRECTIVE = "corrective"         # Breakdown repair
    PREDICTIVE = "predictive"         # Based on condition monitoring
    INSPECTION = "inspection"         # Safety/compliance inspection
    OVERHAUL = "overhaul"             # Major rebuild


class MaintenanceStatus(str, Enum):
    """Status of maintenance work orders"""
    SCHEDULED = "scheduled"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    OVERDUE = "overdue"


class TransferStatus(str, Enum):
    """Status of site-to-site transfers"""
    REQUESTED = "requested"
    APPROVED = "approved"
    REJECTED = "rejected"
    IN_TRANSIT = "in_transit"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class FuelType(str, Enum):
    """Fuel/power source types"""
    DIESEL = "diesel"
    PETROL = "petrol"
    ELECTRIC = "electric"
    HYBRID = "hybrid"
    LPG = "lpg"
    NONE = "none"  # For non-powered equipment


class DepreciationMethod(str, Enum):
    """Asset depreciation methods"""
    STRAIGHT_LINE = "straight_line"
    DOUBLE_DECLINING = "double_declining"
    SUM_OF_YEARS = "sum_of_years"
    UNITS_OF_PRODUCTION = "units_of_production"


# ══════════════════════════════════════════════════════════════════════════════
# CORE DATA MODELS
# ══════════════════════════════════════════════════════════════════════════════

@dataclass
class TechnicalSpec:
    """Technical specifications for an asset"""
    engine_model: str = ""
    engine_power_hp: float = 0.0
    engine_power_kw: float = 0.0
    fuel_tank_capacity_liters: float = 0.0
    fuel_consumption_per_hour: float = 0.0
    operating_weight_kg: float = 0.0
    max_load_capacity_kg: float = 0.0
    max_reach_meters: float = 0.0
    max_dig_depth_meters: float = 0.0
    bucket_capacity_m3: float = 0.0
    lifting_capacity_tons: float = 0.0
    boom_length_meters: float = 0.0
    width_mm: float = 0.0
    height_mm: float = 0.0
    length_mm: float = 0.0
    tire_size: str = ""
    hydraulic_flow_lpm: float = 0.0
    noise_level_db: float = 0.0
    compatible_attachments: List[str] = field(default_factory=list)
    custom_specs: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            'engine_model': self.engine_model,
            'engine_power_hp': self.engine_power_hp,
            'engine_power_kw': self.engine_power_kw,
            'fuel_tank_capacity_liters': self.fuel_tank_capacity_liters,
            'fuel_consumption_per_hour': self.fuel_consumption_per_hour,
            'operating_weight_kg': self.operating_weight_kg,
            'max_load_capacity_kg': self.max_load_capacity_kg,
            'max_reach_meters': self.max_reach_meters,
            'max_dig_depth_meters': self.max_dig_depth_meters,
            'bucket_capacity_m3': self.bucket_capacity_m3,
            'lifting_capacity_tons': self.lifting_capacity_tons,
            'boom_length_meters': self.boom_length_meters,
            'width_mm': self.width_mm,
            'height_mm': self.height_mm,
            'length_mm': self.length_mm,
            'tire_size': self.tire_size,
            'hydraulic_flow_lpm': self.hydraulic_flow_lpm,
            'noise_level_db': self.noise_level_db,
            'compatible_attachments': self.compatible_attachments,
            'custom_specs': self.custom_specs,
        }


@dataclass
class GPSLocation:
    """GPS coordinates and geofencing data"""
    latitude: float = 0.0
    longitude: float = 0.0
    altitude_m: float = 0.0
    accuracy_m: float = 0.0
    heading: float = 0.0
    speed_kmh: float = 0.0
    last_updated: datetime = field(default_factory=datetime.utcnow)
    geofence_site_id: str = ""
    geofence_radius_m: float = 500.0
    is_within_geofence: bool = True
    telematics_device_id: str = ""
    telematics_provider: str = ""  # CAT Product Link, John Deere JDLink, etc.

    def to_dict(self) -> dict:
        return {
            'latitude': self.latitude,
            'longitude': self.longitude,
            'altitude_m': self.altitude_m,
            'accuracy_m': self.accuracy_m,
            'heading': self.heading,
            'speed_kmh': self.speed_kmh,
            'last_updated': self.last_updated.isoformat() if self.last_updated else None,
            'geofence_site_id': self.geofence_site_id,
            'geofence_radius_m': self.geofence_radius_m,
            'is_within_geofence': self.is_within_geofence,
            'telematics_device_id': self.telematics_device_id,
            'telematics_provider': self.telematics_provider,
        }


@dataclass
class UtilizationMetrics:
    """Engine hours and utilization tracking"""
    total_engine_hours: float = 0.0
    engine_hours_at_last_service: float = 0.0
    hours_since_last_service: float = 0.0
    idle_hours_current_week: float = 0.0
    working_hours_current_week: float = 0.0
    utilization_rate_percent: float = 0.0
    days_idle: int = 0
    last_operation_date: Optional[date] = None
    average_daily_hours: float = 0.0
    fuel_consumed_liters: float = 0.0
    distance_traveled_km: float = 0.0
    # Thresholds
    underutilized_threshold_days: int = 3
    is_underutilized: bool = False

    def to_dict(self) -> dict:
        return {
            'total_engine_hours': self.total_engine_hours,
            'engine_hours_at_last_service': self.engine_hours_at_last_service,
            'hours_since_last_service': self.hours_since_last_service,
            'idle_hours_current_week': self.idle_hours_current_week,
            'working_hours_current_week': self.working_hours_current_week,
            'utilization_rate_percent': self.utilization_rate_percent,
            'days_idle': self.days_idle,
            'last_operation_date': str(self.last_operation_date) if self.last_operation_date else None,
            'average_daily_hours': self.average_daily_hours,
            'fuel_consumed_liters': self.fuel_consumed_liters,
            'distance_traveled_km': self.distance_traveled_km,
            'underutilized_threshold_days': self.underutilized_threshold_days,
            'is_underutilized': self.is_underutilized,
        }

    def check_underutilization(self):
        """Check if asset is underutilized"""
        if self.last_operation_date:
            self.days_idle = (date.today() - self.last_operation_date).days
            self.is_underutilized = self.days_idle >= self.underutilized_threshold_days


@dataclass
class FinancialInfo:
    """Financial and depreciation data for an asset"""
    purchase_price: Decimal = Decimal("0")
    purchase_date: Optional[date] = None
    purchase_invoice_number: str = ""
    vendor_name: str = ""
    salvage_value: Decimal = Decimal("0")
    useful_life_years: int = 10
    depreciation_method: DepreciationMethod = DepreciationMethod.STRAIGHT_LINE
    current_book_value: Decimal = Decimal("0")
    accumulated_depreciation: Decimal = Decimal("0")
    monthly_depreciation: Decimal = Decimal("0")
    internal_rental_rate_per_hour: Decimal = Decimal("0")
    external_rental_rate_per_hour: Decimal = Decimal("0")
    insurance_value: Decimal = Decimal("0")
    insurance_policy_number: str = ""
    insurance_expiry_date: Optional[date] = None
    warranty_expiry_date: Optional[date] = None
    total_maintenance_cost: Decimal = Decimal("0")
    total_fuel_cost: Decimal = Decimal("0")
    cost_center: str = ""
    gl_account: str = ""

    def to_dict(self) -> dict:
        return {
            'purchase_price': float(self.purchase_price),
            'purchase_date': str(self.purchase_date) if self.purchase_date else None,
            'purchase_invoice_number': self.purchase_invoice_number,
            'vendor_name': self.vendor_name,
            'salvage_value': float(self.salvage_value),
            'useful_life_years': self.useful_life_years,
            'depreciation_method': self.depreciation_method.value,
            'current_book_value': float(self.current_book_value),
            'accumulated_depreciation': float(self.accumulated_depreciation),
            'monthly_depreciation': float(self.monthly_depreciation),
            'internal_rental_rate_per_hour': float(self.internal_rental_rate_per_hour),
            'external_rental_rate_per_hour': float(self.external_rental_rate_per_hour),
            'insurance_value': float(self.insurance_value),
            'insurance_policy_number': self.insurance_policy_number,
            'insurance_expiry_date': str(self.insurance_expiry_date) if self.insurance_expiry_date else None,
            'warranty_expiry_date': str(self.warranty_expiry_date) if self.warranty_expiry_date else None,
            'total_maintenance_cost': float(self.total_maintenance_cost),
            'total_fuel_cost': float(self.total_fuel_cost),
            'cost_center': self.cost_center,
            'gl_account': self.gl_account,
        }

    def calculate_depreciation(self) -> Decimal:
        """Calculate current book value based on depreciation method"""
        if not self.purchase_date or not self.purchase_price:
            return Decimal("0")
        
        years_owned = (date.today() - self.purchase_date).days / 365.25
        depreciable_base = self.purchase_price - self.salvage_value
        
        if self.depreciation_method == DepreciationMethod.STRAIGHT_LINE:
            annual_depreciation = depreciable_base / self.useful_life_years
            self.accumulated_depreciation = min(
                annual_depreciation * Decimal(str(years_owned)),
                depreciable_base
            )
            self.monthly_depreciation = annual_depreciation / 12
            
        elif self.depreciation_method == DepreciationMethod.DOUBLE_DECLINING:
            rate = Decimal("2") / self.useful_life_years
            book_value = self.purchase_price
            for _ in range(int(years_owned)):
                if book_value > self.salvage_value:
                    depreciation = book_value * rate
                    book_value -= depreciation
            self.accumulated_depreciation = self.purchase_price - max(book_value, self.salvage_value)
        
        self.current_book_value = self.purchase_price - self.accumulated_depreciation
        return self.current_book_value


@dataclass
class Asset:
    """
    Main Asset/Machinery record - the "single source of truth" for each equipment.
    """
    # Core Identification
    asset_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    internal_code: str = ""  # e.g., "EXC-001"
    qr_code: str = ""
    barcode: str = ""
    serial_number: str = ""
    vin_chassis_number: str = ""
    
    # Classification
    name: str = ""
    description: str = ""
    category: AssetCategory = AssetCategory.OTHER
    asset_type: AssetType = AssetType.OTHER
    product_class: str = ""  # e.g., "20-Ton Excavator with GPS"
    manufacturer: str = ""
    model: str = ""
    year_manufactured: int = 0
    
    # Current Status
    status: AssetStatus = AssetStatus.AVAILABLE
    ownership_type: OwnershipType = OwnershipType.OWNED
    fuel_type: FuelType = FuelType.DIESEL
    
    # Location & Assignment
    current_site_id: str = ""
    current_site_name: str = ""
    current_project_id: str = ""
    current_project_name: str = ""
    home_yard_id: str = ""
    home_yard_name: str = ""
    
    # Operator Assignment
    primary_operator_id: str = ""
    primary_operator_name: str = ""
    backup_operator_id: str = ""
    backup_operator_name: str = ""
    
    # Nested Data Objects
    technical_specs: TechnicalSpec = field(default_factory=TechnicalSpec)
    gps_location: GPSLocation = field(default_factory=GPSLocation)
    utilization: UtilizationMetrics = field(default_factory=UtilizationMetrics)
    financial: FinancialInfo = field(default_factory=FinancialInfo)
    
    # Maintenance Scheduling
    service_interval_hours: float = 250.0
    next_service_due_hours: float = 250.0
    next_service_due_date: Optional[date] = None
    last_service_date: Optional[date] = None
    maintenance_blocked: bool = False
    maintenance_block_reason: str = ""
    
    # Documents
    documents: List[Dict[str, Any]] = field(default_factory=list)
    # [{'type': 'invoice', 'name': 'Purchase Invoice', 'url': '...', 'uploaded_at': ...}]
    
    # Images
    images: List[str] = field(default_factory=list)  # URLs to asset photos
    
    # Certifications Required
    required_licenses: List[str] = field(default_factory=list)
    required_training_courses: List[str] = field(default_factory=list)
    
    # Metadata
    tags: List[str] = field(default_factory=list)
    notes: str = ""
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)
    created_by: str = ""

    def to_dict(self) -> dict:
        return {
            'asset_id': self.asset_id,
            'company_id': self.company_id,
            'internal_code': self.internal_code,
            'qr_code': self.qr_code,
            'barcode': self.barcode,
            'serial_number': self.serial_number,
            'vin_chassis_number': self.vin_chassis_number,
            'name': self.name,
            'description': self.description,
            'category': self.category.value if isinstance(self.category, AssetCategory) else self.category,
            'asset_type': self.asset_type.value if isinstance(self.asset_type, AssetType) else self.asset_type,
            'product_class': self.product_class,
            'manufacturer': self.manufacturer,
            'model': self.model,
            'year_manufactured': self.year_manufactured,
            'status': self.status.value if isinstance(self.status, AssetStatus) else self.status,
            'ownership_type': self.ownership_type.value if isinstance(self.ownership_type, OwnershipType) else self.ownership_type,
            'fuel_type': self.fuel_type.value if isinstance(self.fuel_type, FuelType) else self.fuel_type,
            'current_site_id': self.current_site_id,
            'current_site_name': self.current_site_name,
            'current_project_id': self.current_project_id,
            'current_project_name': self.current_project_name,
            'home_yard_id': self.home_yard_id,
            'home_yard_name': self.home_yard_name,
            'primary_operator_id': self.primary_operator_id,
            'primary_operator_name': self.primary_operator_name,
            'backup_operator_id': self.backup_operator_id,
            'backup_operator_name': self.backup_operator_name,
            'technical_specs': self.technical_specs.to_dict() if self.technical_specs else {},
            'gps_location': self.gps_location.to_dict() if self.gps_location else {},
            'utilization': self.utilization.to_dict() if self.utilization else {},
            'financial': self.financial.to_dict() if self.financial else {},
            'service_interval_hours': self.service_interval_hours,
            'next_service_due_hours': self.next_service_due_hours,
            'next_service_due_date': str(self.next_service_due_date) if self.next_service_due_date else None,
            'last_service_date': str(self.last_service_date) if self.last_service_date else None,
            'maintenance_blocked': self.maintenance_blocked,
            'maintenance_block_reason': self.maintenance_block_reason,
            'documents': self.documents,
            'images': self.images,
            'required_licenses': self.required_licenses,
            'required_training_courses': self.required_training_courses,
            'tags': self.tags,
            'notes': self.notes,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
            'created_by': self.created_by,
        }


@dataclass
class Site:
    """Project site or yard location"""
    site_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    name: str = ""
    site_type: str = "project"  # project, yard, workshop
    address: str = ""
    city: str = ""
    region: str = ""
    country: str = ""
    latitude: float = 0.0
    longitude: float = 0.0
    geofence_radius_m: float = 500.0
    project_id: str = ""
    project_name: str = ""
    site_manager_id: str = ""
    site_manager_name: str = ""
    contact_phone: str = ""
    is_active: bool = True
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'site_id': self.site_id,
            'company_id': self.company_id,
            'name': self.name,
            'site_type': self.site_type,
            'address': self.address,
            'city': self.city,
            'region': self.region,
            'country': self.country,
            'latitude': self.latitude,
            'longitude': self.longitude,
            'geofence_radius_m': self.geofence_radius_m,
            'project_id': self.project_id,
            'project_name': self.project_name,
            'site_manager_id': self.site_manager_id,
            'site_manager_name': self.site_manager_name,
            'contact_phone': self.contact_phone,
            'is_active': self.is_active,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


@dataclass
class TransferOrder:
    """Site-to-site asset transfer request"""
    transfer_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    asset_id: str = ""
    asset_name: str = ""
    asset_internal_code: str = ""
    
    # Locations
    from_site_id: str = ""
    from_site_name: str = ""
    to_site_id: str = ""
    to_site_name: str = ""
    
    # Status & Dates
    status: TransferStatus = TransferStatus.REQUESTED
    requested_date: date = field(default_factory=date.today)
    requested_by_id: str = ""
    requested_by_name: str = ""
    approved_date: Optional[date] = None
    approved_by_id: str = ""
    approved_by_name: str = ""
    departure_date: Optional[date] = None
    arrival_date: Optional[date] = None
    completed_date: Optional[date] = None
    
    # Transport Details
    transport_method: str = ""  # low_loader, self_drive, towed
    transport_vehicle_id: str = ""
    transport_vehicle_name: str = ""
    driver_id: str = ""
    driver_name: str = ""
    estimated_duration_hours: float = 0.0
    actual_duration_hours: float = 0.0
    transport_cost: Decimal = Decimal("0")
    
    # Reason & Notes
    reason: str = ""
    priority: str = "normal"  # low, normal, high, urgent
    notes: str = ""
    rejection_reason: str = ""
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'transfer_id': self.transfer_id,
            'company_id': self.company_id,
            'asset_id': self.asset_id,
            'asset_name': self.asset_name,
            'asset_internal_code': self.asset_internal_code,
            'from_site_id': self.from_site_id,
            'from_site_name': self.from_site_name,
            'to_site_id': self.to_site_id,
            'to_site_name': self.to_site_name,
            'status': self.status.value if isinstance(self.status, TransferStatus) else self.status,
            'requested_date': str(self.requested_date) if self.requested_date else None,
            'requested_by_id': self.requested_by_id,
            'requested_by_name': self.requested_by_name,
            'approved_date': str(self.approved_date) if self.approved_date else None,
            'approved_by_id': self.approved_by_id,
            'approved_by_name': self.approved_by_name,
            'departure_date': str(self.departure_date) if self.departure_date else None,
            'arrival_date': str(self.arrival_date) if self.arrival_date else None,
            'completed_date': str(self.completed_date) if self.completed_date else None,
            'transport_method': self.transport_method,
            'transport_vehicle_id': self.transport_vehicle_id,
            'transport_vehicle_name': self.transport_vehicle_name,
            'driver_id': self.driver_id,
            'driver_name': self.driver_name,
            'estimated_duration_hours': self.estimated_duration_hours,
            'actual_duration_hours': self.actual_duration_hours,
            'transport_cost': float(self.transport_cost),
            'reason': self.reason,
            'priority': self.priority,
            'notes': self.notes,
            'rejection_reason': self.rejection_reason,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class MaintenanceWorkOrder:
    """Maintenance and service work order"""
    work_order_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    work_order_number: str = ""
    company_id: str = "default"
    asset_id: str = ""
    asset_name: str = ""
    asset_internal_code: str = ""
    
    # Type & Status
    maintenance_type: MaintenanceType = MaintenanceType.PREVENTIVE
    status: MaintenanceStatus = MaintenanceStatus.SCHEDULED
    priority: str = "normal"  # low, normal, high, critical
    
    # Scheduling
    scheduled_date: Optional[date] = None
    due_date: Optional[date] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    engine_hours_at_service: float = 0.0
    
    # Description & Work Done
    title: str = ""
    description: str = ""
    work_performed: str = ""
    findings: str = ""
    recommendations: str = ""
    
    # Assigned Resources
    assigned_technician_id: str = ""
    assigned_technician_name: str = ""
    workshop_id: str = ""
    workshop_name: str = ""
    
    # Parts & Costs
    parts_used: List[Dict[str, Any]] = field(default_factory=list)
    # [{'part_id': '...', 'name': 'Oil Filter', 'quantity': 1, 'unit_cost': 50}]
    labor_hours: float = 0.0
    labor_cost: Decimal = Decimal("0")
    parts_cost: Decimal = Decimal("0")
    external_service_cost: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    
    # Documents
    attachments: List[Dict[str, str]] = field(default_factory=list)
    
    # Approval
    requires_approval: bool = False
    approved_by_id: str = ""
    approved_by_name: str = ""
    approved_at: Optional[datetime] = None
    
    created_at: datetime = field(default_factory=datetime.utcnow)
    created_by: str = ""
    updated_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'work_order_id': self.work_order_id,
            'work_order_number': self.work_order_number,
            'company_id': self.company_id,
            'asset_id': self.asset_id,
            'asset_name': self.asset_name,
            'asset_internal_code': self.asset_internal_code,
            'maintenance_type': self.maintenance_type.value if isinstance(self.maintenance_type, MaintenanceType) else self.maintenance_type,
            'status': self.status.value if isinstance(self.status, MaintenanceStatus) else self.status,
            'priority': self.priority,
            'scheduled_date': str(self.scheduled_date) if self.scheduled_date else None,
            'due_date': str(self.due_date) if self.due_date else None,
            'started_at': self.started_at.isoformat() if self.started_at else None,
            'completed_at': self.completed_at.isoformat() if self.completed_at else None,
            'engine_hours_at_service': self.engine_hours_at_service,
            'title': self.title,
            'description': self.description,
            'work_performed': self.work_performed,
            'findings': self.findings,
            'recommendations': self.recommendations,
            'assigned_technician_id': self.assigned_technician_id,
            'assigned_technician_name': self.assigned_technician_name,
            'workshop_id': self.workshop_id,
            'workshop_name': self.workshop_name,
            'parts_used': self.parts_used,
            'labor_hours': self.labor_hours,
            'labor_cost': float(self.labor_cost),
            'parts_cost': float(self.parts_cost),
            'external_service_cost': float(self.external_service_cost),
            'total_cost': float(self.total_cost),
            'attachments': self.attachments,
            'requires_approval': self.requires_approval,
            'approved_by_id': self.approved_by_id,
            'approved_by_name': self.approved_by_name,
            'approved_at': self.approved_at.isoformat() if self.approved_at else None,
            'created_at': self.created_at.isoformat() if self.created_at else None,
            'created_by': self.created_by,
            'updated_at': self.updated_at.isoformat() if self.updated_at else None,
        }


@dataclass
class OperatorShiftLog:
    """Digital log for operator shift tracking"""
    log_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    asset_id: str = ""
    asset_name: str = ""
    operator_id: str = ""
    operator_name: str = ""
    
    # Shift Details
    shift_date: date = field(default_factory=date.today)
    shift_start: Optional[datetime] = None
    shift_end: Optional[datetime] = None
    shift_duration_hours: float = 0.0
    
    # Engine Hours
    engine_hours_start: float = 0.0
    engine_hours_end: float = 0.0
    engine_hours_worked: float = 0.0
    idle_hours: float = 0.0
    
    # Fuel
    fuel_start_liters: float = 0.0
    fuel_end_liters: float = 0.0
    fuel_consumed_liters: float = 0.0
    fuel_added_liters: float = 0.0
    
    # Location
    site_id: str = ""
    site_name: str = ""
    project_id: str = ""
    project_name: str = ""
    
    # Pre/Post Inspection
    pre_shift_inspection_done: bool = False
    pre_shift_issues: str = ""
    post_shift_inspection_done: bool = False
    post_shift_issues: str = ""
    
    # Task Description
    work_description: str = ""
    tasks_completed: List[str] = field(default_factory=list)
    
    # Incidents
    incidents_reported: bool = False
    incident_description: str = ""
    
    created_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            'log_id': self.log_id,
            'company_id': self.company_id,
            'asset_id': self.asset_id,
            'asset_name': self.asset_name,
            'operator_id': self.operator_id,
            'operator_name': self.operator_name,
            'shift_date': str(self.shift_date) if self.shift_date else None,
            'shift_start': self.shift_start.isoformat() if self.shift_start else None,
            'shift_end': self.shift_end.isoformat() if self.shift_end else None,
            'shift_duration_hours': self.shift_duration_hours,
            'engine_hours_start': self.engine_hours_start,
            'engine_hours_end': self.engine_hours_end,
            'engine_hours_worked': self.engine_hours_worked,
            'idle_hours': self.idle_hours,
            'fuel_start_liters': self.fuel_start_liters,
            'fuel_end_liters': self.fuel_end_liters,
            'fuel_consumed_liters': self.fuel_consumed_liters,
            'fuel_added_liters': self.fuel_added_liters,
            'site_id': self.site_id,
            'site_name': self.site_name,
            'project_id': self.project_id,
            'project_name': self.project_name,
            'pre_shift_inspection_done': self.pre_shift_inspection_done,
            'pre_shift_issues': self.pre_shift_issues,
            'post_shift_inspection_done': self.post_shift_inspection_done,
            'post_shift_issues': self.post_shift_issues,
            'work_description': self.work_description,
            'tasks_completed': self.tasks_completed,
            'incidents_reported': self.incidents_reported,
            'incident_description': self.incident_description,
            'created_at': self.created_at.isoformat() if self.created_at else None,
        }


@dataclass 
class FuelLog:
    """Fuel dispensing record"""
    fuel_log_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    company_id: str = "default"
    asset_id: str = ""
    asset_name: str = ""
    
    # Fuel Details
    fuel_type: FuelType = FuelType.DIESEL
    quantity_liters: float = 0.0
    unit_price: Decimal = Decimal("0")
    total_cost: Decimal = Decimal("0")
    odometer_reading: float = 0.0
    engine_hours: float = 0.0
    
    # Location & Source
    site_id: str = ""
    site_name: str = ""
    fuel_station: str = ""
    receipt_number: str = ""
    
    # Who & When
    fueled_by_id: str = ""
    fueled_by_name: str = ""
    fueled_at: datetime = field(default_factory=datetime.utcnow)
    
    notes: str = ""

    def to_dict(self) -> dict:
        return {
            'fuel_log_id': self.fuel_log_id,
            'company_id': self.company_id,
            'asset_id': self.asset_id,
            'asset_name': self.asset_name,
            'fuel_type': self.fuel_type.value if isinstance(self.fuel_type, FuelType) else self.fuel_type,
            'quantity_liters': self.quantity_liters,
            'unit_price': float(self.unit_price),
            'total_cost': float(self.total_cost),
            'odometer_reading': self.odometer_reading,
            'engine_hours': self.engine_hours,
            'site_id': self.site_id,
            'site_name': self.site_name,
            'fuel_station': self.fuel_station,
            'receipt_number': self.receipt_number,
            'fueled_by_id': self.fueled_by_id,
            'fueled_by_name': self.fueled_by_name,
            'fueled_at': self.fueled_at.isoformat() if self.fueled_at else None,
            'notes': self.notes,
        }


@dataclass
class OperatorCertification:
    """Required certifications/licenses for operating specific equipment"""
    certification_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    name: str = ""
    description: str = ""
    certification_type: str = ""  # license, training, medical
    issuing_authority: str = ""
    validity_period_months: int = 12
    required_for_categories: List[str] = field(default_factory=list)
    required_for_types: List[str] = field(default_factory=list)
    is_mandatory: bool = True
    lms_course_id: str = ""  # Link to LMS course for training certifications

    def to_dict(self) -> dict:
        return {
            'certification_id': self.certification_id,
            'name': self.name,
            'description': self.description,
            'certification_type': self.certification_type,
            'issuing_authority': self.issuing_authority,
            'validity_period_months': self.validity_period_months,
            'required_for_categories': self.required_for_categories,
            'required_for_types': self.required_for_types,
            'is_mandatory': self.is_mandatory,
            'lms_course_id': self.lms_course_id,
        }


# ══════════════════════════════════════════════════════════════════════════════
# DEFAULT CERTIFICATION REQUIREMENTS
# ══════════════════════════════════════════════════════════════════════════════

DEFAULT_CERTIFICATIONS = [
    {
        'name': 'Heavy Equipment Operator License',
        'certification_type': 'license',
        'issuing_authority': 'Ministry of Transport',
        'validity_period_months': 24,
        'required_for_categories': ['earthmoving', 'lifting'],
        'is_mandatory': True,
    },
    {
        'name': 'Crane Operator Certificate',
        'certification_type': 'license',
        'issuing_authority': 'Construction Safety Authority',
        'validity_period_months': 12,
        'required_for_types': ['tower_crane', 'mobile_crane'],
        'is_mandatory': True,
    },
    {
        'name': 'Forklift Operator License',
        'certification_type': 'license',
        'issuing_authority': 'Workplace Safety Board',
        'validity_period_months': 36,
        'required_for_types': ['forklift', 'telehandler'],
        'is_mandatory': True,
    },
    {
        'name': 'Medical Fit-to-Work Certificate',
        'certification_type': 'medical',
        'issuing_authority': 'Occupational Health Provider',
        'validity_period_months': 12,
        'required_for_categories': ['earthmoving', 'lifting', 'transport'],
        'is_mandatory': True,
    },
    {
        'name': 'Site-Specific Safety Induction',
        'certification_type': 'training',
        'issuing_authority': 'LMS',
        'validity_period_months': 12,
        'required_for_categories': ['earthmoving', 'lifting', 'transport', 'power_generation'],
        'is_mandatory': True,
    },
    {
        'name': 'Machine Operations Level 2',
        'certification_type': 'training',
        'issuing_authority': 'LMS',
        'validity_period_months': 24,
        'required_for_categories': ['earthmoving', 'lifting'],
        'is_mandatory': True,
    },
]


# ══════════════════════════════════════════════════════════════════════════════
# ASSET TYPE METADATA
# ══════════════════════════════════════════════════════════════════════════════

ASSET_TYPE_INFO = {
    AssetType.EXCAVATOR: {
        'display_name': 'Excavator',
        'category': AssetCategory.EARTHMOVING,
        'icon': 'bi-truck',
        'default_service_interval': 250,
        'typical_daily_hours': 8,
    },
    AssetType.BULLDOZER: {
        'display_name': 'Bulldozer',
        'category': AssetCategory.EARTHMOVING,
        'icon': 'bi-truck',
        'default_service_interval': 250,
        'typical_daily_hours': 8,
    },
    AssetType.WHEEL_LOADER: {
        'display_name': 'Wheel Loader',
        'category': AssetCategory.EARTHMOVING,
        'icon': 'bi-truck',
        'default_service_interval': 250,
        'typical_daily_hours': 8,
    },
    AssetType.TOWER_CRANE: {
        'display_name': 'Tower Crane',
        'category': AssetCategory.LIFTING,
        'icon': 'bi-building',
        'default_service_interval': 500,
        'typical_daily_hours': 10,
    },
    AssetType.MOBILE_CRANE: {
        'display_name': 'Mobile Crane',
        'category': AssetCategory.LIFTING,
        'icon': 'bi-truck',
        'default_service_interval': 250,
        'typical_daily_hours': 6,
    },
    AssetType.GENERATOR: {
        'display_name': 'Generator',
        'category': AssetCategory.POWER_GENERATION,
        'icon': 'bi-lightning-charge',
        'default_service_interval': 500,
        'typical_daily_hours': 12,
    },
    AssetType.DUMP_TRUCK: {
        'display_name': 'Dump Truck',
        'category': AssetCategory.TRANSPORT,
        'icon': 'bi-truck',
        'default_service_interval': 250,
        'typical_daily_hours': 8,
    },
    AssetType.CONCRETE_MIXER: {
        'display_name': 'Concrete Mixer',
        'category': AssetCategory.CONCRETE,
        'icon': 'bi-truck',
        'default_service_interval': 200,
        'typical_daily_hours': 6,
    },
}
