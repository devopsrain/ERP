"""
VAT Context Portal Models

This module handles VAT accounting, income tracking, expense management,
and financial summary reports for the Ethiopian accounting system.
"""

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Dict, List, Optional, Set
from enum import Enum
import uuid
from decimal import Decimal


class VATType(Enum):
    STANDARD = "Standard VAT (15%)"
    ZERO_RATED = "Zero Rated (0%)"
    EXEMPT = "Exempt"
    WITHHELD = "Withholding VAT"
    WITHHOLDING = "Withholding VAT (2%)"


class TransactionType(Enum):
    INCOME = "Income"
    EXPENSE = "Expense"
    CAPITAL = "Capital"


class IncomeCategory(Enum):
    SALES_REVENUE = "Sales Revenue"
    SERVICE_INCOME = "Service Income"
    RENTAL_INCOME = "Rental Income"
    INVESTMENT_INCOME = "Investment Income"
    OTHER_INCOME = "Other Income"


class ExpenseCategory(Enum):
    OPERATING_EXPENSES = "Operating Expenses"
    ADMINISTRATIVE = "Administrative Expenses"
    RAW_MATERIALS = "Raw Materials"
    UTILITIES = "Utilities"
    RENT = "Rent"
    SALARIES = "Salaries & Wages"
    MARKETING = "Marketing & Advertising"
    TRAVEL = "Travel Expenses"
    PROFESSIONAL_FEES = "Professional Fees"
    DEPRECIATION = "Depreciation"
    OTHER_EXPENSES = "Other Expenses"
    # Additional categories used by the expense form
    OFFICE_SUPPLIES = "Office Supplies"
    TRANSPORTATION = "Transportation"
    EQUIPMENT = "Equipment & Machinery"
    MATERIALS = "Materials"
    INSURANCE = "Insurance"
    TAXES_FEES = "Taxes & Government Fees"
    MAINTENANCE = "Maintenance & Repairs"
    OTHER = "Other"


@dataclass
class VATConfiguration:
    """VAT rate configuration"""
    vat_id: str
    vat_type: VATType
    rate: Decimal = field(default=Decimal('0.15'))
    description: str = ""
    is_active: bool = True
    
    def __post_init__(self):
        if not self.vat_id:
            self.vat_id = str(uuid.uuid4())


@dataclass
class IncomeRecord:
    """Income transaction record"""
    income_id: str
    company_id: str
    
    # Contract/Transaction Details
    contract_date: date
    description: str
    category: IncomeCategory

    # Financial Details
    gross_amount: Decimal
    vat_type: VATType
    vat_rate: Decimal
    vat_amount: Decimal = field(default=Decimal('0'))
    net_amount: Decimal = field(default=Decimal('0'))

    # Date the income was actually received — used for period filtering.
    # Defaults to contract_date for backwards compatibility.
    income_date: Optional[date] = None
    
    # Customer Information
    customer_name: str = ""
    customer_tin: str = ""
    invoice_number: str = ""

    # Tender linkage + payment mode ('advance' or 'total')
    tender_id: str = ""
    payment_mode: str = ""
    
    # System Fields
    created_date: datetime = field(default_factory=datetime.now)
    updated_date: datetime = field(default_factory=datetime.now)
    created_by: str = ""
    is_active: bool = True
    
    def __post_init__(self):
        if not self.income_id:
            self.income_id = str(uuid.uuid4())
        if self.income_date is None:
            self.income_date = self.contract_date

        # Calculate VAT and net amount
        if self.vat_type == VATType.STANDARD:
            self.vat_amount = self.gross_amount * self.vat_rate
        elif self.vat_type in [VATType.ZERO_RATED, VATType.EXEMPT]:
            self.vat_amount = Decimal('0')

        self.net_amount = self.gross_amount - self.vat_amount


@dataclass
class ExpenseRecord:
    """Expense transaction record"""
    expense_id: str
    company_id: str
    
    # Transaction Details
    expense_date: date
    description: str
    category: ExpenseCategory
    
    # Financial Details
    gross_amount: Decimal
    vat_type: VATType
    vat_rate: Decimal
    vat_amount: Decimal = field(default=Decimal('0'))
    net_amount: Decimal = field(default=Decimal('0'))
    
    # Supplier Information
    supplier_name: str = ""
    supplier_tin: str = ""
    receipt_number: str = ""

    # Tender linkage
    tender_id: str = ""
    
    # System Fields
    created_date: datetime = field(default_factory=datetime.now)
    updated_date: datetime = field(default_factory=datetime.now)
    created_by: str = ""
    is_active: bool = True
    
    def __post_init__(self):
        if not self.expense_id:
            self.expense_id = str(uuid.uuid4())

        # Calculate VAT and net amount
        if self.vat_type == VATType.STANDARD:
            self.vat_amount = self.gross_amount * self.vat_rate
        elif self.vat_type in [VATType.ZERO_RATED, VATType.EXEMPT]:
            self.vat_amount = Decimal('0')

        self.net_amount = self.gross_amount + self.vat_amount  # For expenses, we add VAT

    @property
    def total_amount(self) -> Decimal:
        """Alias used by templates: total paid = gross + VAT."""
        return self.net_amount


@dataclass
class CapitalRecord:
    """Capital transaction record"""
    capital_id: str = ""
    company_id: str = ""

    # Transaction Details
    transaction_date: date = field(default_factory=date.today)
    description: str = ""
    capital_type: str = ""  # Investment, Loan, Equipment, etc.
    transaction_type: str = "INJECTION"  # INJECTION | WITHDRAWAL

    # Financial Details
    amount: Decimal = field(default=Decimal('0'))

    # Additional Information
    source: str = ""  # Bank, Investor, etc.
    reference_number: str = ""
    
    # System Fields
    created_date: datetime = field(default_factory=datetime.now)
    updated_date: datetime = field(default_factory=datetime.now)
    created_by: str = ""
    is_active: bool = True
    
    def __post_init__(self):
        if not self.capital_id:
            self.capital_id = str(uuid.uuid4())


@dataclass
class FinancialSummary:
    """Financial summary report"""
    company_id: str
    period_start: date
    period_end: date
    
    # Income Summary
    total_income_gross: Decimal = field(default=Decimal('0'))
    total_income_vat: Decimal = field(default=Decimal('0'))
    total_income_net: Decimal = field(default=Decimal('0'))
    income_by_category: Dict[str, Decimal] = field(default_factory=dict)
    
    # Expense Summary
    total_expense_net: Decimal = field(default=Decimal('0'))
    total_expense_vat: Decimal = field(default=Decimal('0'))
    total_expense_gross: Decimal = field(default=Decimal('0'))
    expense_by_category: Dict[str, Decimal] = field(default_factory=dict)
    
    # Capital Summary
    total_capital: Decimal = field(default=Decimal('0'))
    capital_by_type: Dict[str, Decimal] = field(default_factory=dict)
    
    # VAT Summary
    vat_payable: Decimal = field(default=Decimal('0'))  # Output VAT - Input VAT
    output_vat: Decimal = field(default=Decimal('0'))   # VAT on sales
    input_vat: Decimal = field(default=Decimal('0'))    # VAT on purchases
    
    # Financial Position
    gross_profit: Decimal = field(default=Decimal('0'))
    net_profit: Decimal = field(default=Decimal('0'))
    total_assets: Decimal = field(default=Decimal('0'))
    
    # Generated timestamp
    generated_at: datetime = field(default_factory=datetime.now)


class VATContextManager:
    """Manages VAT context portal operations with parquet persistence"""
    
    def __init__(self):
        self.vat_configurations: Dict[str, VATConfiguration] = {}

        # In-memory fallback stores — always created so add_* methods never
        # hit AttributeError regardless of whether the DB store loads.
        self.income_records: Dict[str, List[IncomeRecord]] = {}
        self.expense_records: Dict[str, List[ExpenseRecord]] = {}
        self.capital_records: Dict[str, List[CapitalRecord]] = {}

        # Initialize the PostgreSQL-backed data store
        try:
            import sys
            import os
            sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'web'))
            from vat_data_store import VATDataStore
            self.data_store = VATDataStore()
        except ImportError:
            # Fallback to in-memory storage if the DB store is unavailable
            self.data_store = None

        # Initialize default VAT configurations
        self._initialize_default_vat_rates()
    
    def _initialize_default_vat_rates(self):
        """Initialize default Ethiopian VAT rates"""
        default_configs = [
            VATConfiguration(
                vat_id="std_vat_15",
                vat_type=VATType.STANDARD,
                rate=Decimal('0.15'),
                description="Standard VAT rate for most goods and services"
            ),
            VATConfiguration(
                vat_id="zero_rated",
                vat_type=VATType.ZERO_RATED,
                rate=Decimal('0.00'),
                description="Zero-rated exports and specified goods"
            ),
            VATConfiguration(
                vat_id="exempt_vat",
                vat_type=VATType.EXEMPT,
                rate=Decimal('0.00'),
                description="VAT-exempt transactions (financial services, etc.)"
            ),
            VATConfiguration(
                vat_id="withholding_vat",
                vat_type=VATType.WITHHELD,
                rate=Decimal('0.02'),
                description="Withholding VAT (2%) on certain transactions"
            )
        ]
        
        for config in default_configs:
            self.vat_configurations[config.vat_id] = config
    
    def add_income_record(self, company_id: str, income_data: dict) -> IncomeRecord:
        """Add new income record"""
        income_data['company_id'] = company_id
        income_data.setdefault('income_id', '')   # __post_init__ generates a uuid
        income_record = IncomeRecord(**income_data)

        if self.data_store:
            record_dict = {
                'income_id': income_record.income_id,
                'company_id': income_record.company_id,
                'contract_date': income_record.contract_date,
                'income_date': income_record.income_date,
                'description': income_record.description,
                'category': income_record.category.value,
                'gross_amount': float(income_record.gross_amount),
                'vat_type': income_record.vat_type.value,
                'vat_rate': float(income_record.vat_rate),
                'vat_amount': float(income_record.vat_amount),
                'net_amount': float(income_record.net_amount),
                'customer_name': income_record.customer_name,
                'customer_tin': income_record.customer_tin,
                'invoice_number': income_record.invoice_number,
                'tender_id': income_record.tender_id,
                'payment_mode': income_record.payment_mode,
                'created_date': income_record.created_date,
                'updated_date': income_record.updated_date,
                'created_by': income_record.created_by,
            }
            saved = self.data_store.add_record('vat_income', record_dict)
            if not saved:
                # Surfacing this matters: Excel imports counted records as
                # "imported" while the INSERT silently failed.
                raise RuntimeError(
                    "Income record could not be saved to the database — check server logs")
        else:
            # Fallback to in-memory storage
            if company_id not in self.income_records:
                self.income_records[company_id] = []
            self.income_records[company_id].append(income_record)

        return income_record
    
    def add_expense_record(self, company_id: str, expense_data: dict) -> ExpenseRecord:
        """Add new expense record with DB persistence"""
        expense_data['company_id'] = company_id
        expense_data.setdefault('expense_id', '')  # __post_init__ generates a uuid
        expense_record = ExpenseRecord(**expense_data)
        
        if self.data_store:
            # Use parquet storage
            record_dict = {
                'expense_id': expense_record.expense_id,
                'company_id': expense_record.company_id,
                'expense_date': expense_record.expense_date,
                'description': expense_record.description,
                'category': expense_record.category.value,
                'gross_amount': float(expense_record.gross_amount),
                'vat_type': expense_record.vat_type.value,
                'vat_rate': float(expense_record.vat_rate),
                'vat_amount': float(expense_record.vat_amount),
                'net_amount': float(expense_record.net_amount),
                'supplier_name': expense_record.supplier_name,
                'supplier_tin': expense_record.supplier_tin,
                'receipt_number': expense_record.receipt_number,
                'tender_id': expense_record.tender_id,
                'created_date': expense_record.created_date,
                'updated_date': expense_record.updated_date,
                'created_by': expense_record.created_by,
                'is_active': expense_record.is_active
            }
            saved = self.data_store.add_record('vat_expenses', record_dict)
            if not saved:
                raise RuntimeError(
                    "Expense record could not be saved to the database — check server logs")
        else:
            # Fallback to in-memory storage
            if company_id not in self.expense_records:
                self.expense_records[company_id] = []
            self.expense_records[company_id].append(expense_record)
        
        return expense_record
    
    def add_capital_record(self, company_id: str, capital_data: dict) -> CapitalRecord:
        """Add new capital record"""
        capital_data['company_id'] = company_id
        capital_record = CapitalRecord(**capital_data)

        if self.data_store:
            # NOTE: the vat_capital table uses investment_date / investor_name;
            # map from the dataclass's transaction_date / source.
            record_dict = {
                'capital_id': capital_record.capital_id,
                'company_id': capital_record.company_id,
                'investment_date': capital_record.transaction_date,
                'description': capital_record.description,
                'capital_type': capital_record.capital_type,
                'transaction_type': capital_record.transaction_type,
                'amount': float(capital_record.amount),
                'investor_name': capital_record.source,
                'created_date': capital_record.created_date,
                'updated_date': capital_record.updated_date,
                'created_by': capital_record.created_by,
            }
            self.data_store.add_record('vat_capital', record_dict)
        else:
            # Fallback to in-memory storage
            if company_id not in self.capital_records:
                self.capital_records[company_id] = []
            self.capital_records[company_id].append(capital_record)

        return capital_record
    
    def get_company_income_records(self, company_id: str, start_date: Optional[date] = None, 
                                 end_date: Optional[date] = None) -> List[IncomeRecord]:
        """Get income records for a company within date range"""
        if self.data_store:
            # Use parquet storage
            df = self.data_store.get_company_records('vat_income', company_id, start_date, end_date)
            
            if df.empty:
                return []
            
            # Convert DataFrame to IncomeRecord objects
            records = []
            for _, row in df.iterrows():
                try:
                    record = IncomeRecord(
                        income_id=row['income_id'],
                        company_id=row['company_id'],
                        contract_date=row['contract_date'] if hasattr(row['contract_date'], 'date') else row['contract_date'],
                        income_date=row.get('income_date') or row['contract_date'],
                        description=row['description'],
                        category=IncomeCategory(row['category']),
                        gross_amount=Decimal(str(row['gross_amount'])),
                        vat_type=VATType(row['vat_type']),
                        vat_rate=Decimal(str(row['vat_rate'])),
                        vat_amount=Decimal(str(row['vat_amount'])),
                        net_amount=Decimal(str(row['net_amount'])),
                        customer_name=row.get('customer_name', ''),
                        customer_tin=row.get('customer_tin', ''),
                        invoice_number=row.get('invoice_number', ''),
                        tender_id=row.get('tender_id', '') or '',
                        payment_mode=row.get('payment_mode', '') or '',
                        created_date=row['created_date'],
                        updated_date=row['updated_date'],
                        created_by=row.get('created_by', ''),
                        is_active=row.get('is_active', True)
                    )
                    records.append(record)
                except Exception as e:
                    print(f"Error converting row to IncomeRecord: {e}")
                    continue
            
            return records
        else:
            # Fallback to in-memory storage
            records = self.income_records.get(company_id, [])
            
            if start_date or end_date:
                filtered_records = []
                for record in records:
                    if start_date and record.contract_date < start_date:
                        continue
                    if end_date and record.contract_date > end_date:
                        continue
                    filtered_records.append(record)
                return filtered_records
            
            return records
    
    def get_company_expense_records(self, company_id: str, start_date: Optional[date] = None,
                                  end_date: Optional[date] = None) -> List[ExpenseRecord]:
        """Get expense records for a company within date range"""
        if self.data_store:
            # Use parquet storage
            df = self.data_store.get_company_records('vat_expenses', company_id, start_date, end_date)
            
            if df.empty:
                return []
            
            # Convert DataFrame to ExpenseRecord objects
            records = []
            for _, row in df.iterrows():
                try:
                    record = ExpenseRecord(
                        expense_id=row['expense_id'],
                        company_id=row['company_id'],
                        expense_date=row['expense_date'] if hasattr(row['expense_date'], 'date') else row['expense_date'],
                        description=row['description'],
                        category=ExpenseCategory(row['category']),
                        gross_amount=Decimal(str(row['gross_amount'])),
                        vat_type=VATType(row['vat_type']),
                        vat_rate=Decimal(str(row['vat_rate'])),
                        vat_amount=Decimal(str(row['vat_amount'])),
                        net_amount=Decimal(str(row['net_amount'])),
                        supplier_name=row.get('supplier_name', ''),
                        supplier_tin=row.get('supplier_tin', ''),
                        receipt_number=row.get('receipt_number', ''),
                        tender_id=row.get('tender_id', '') or '',
                        created_date=row['created_date'],
                        updated_date=row['updated_date'],
                        created_by=row.get('created_by', ''),
                        is_active=row.get('is_active', True)
                    )
                    records.append(record)
                except Exception as e:
                    print(f"Error converting row to ExpenseRecord: {e}")
                    continue
            
            return records
        else:
            # Fallback to in-memory storage
            records = self.expense_records.get(company_id, [])
            
            if start_date or end_date:
                filtered_records = []
                for record in records:
                    if start_date and record.expense_date < start_date:
                        continue
                    if end_date and record.expense_date > end_date:
                        continue
                    filtered_records.append(record)
                return filtered_records
            
            return records
    
    def get_company_capital_records(self, company_id: str, start_date: Optional[date] = None,
                                  end_date: Optional[date] = None) -> List[CapitalRecord]:
        """Get capital records for a company within date range"""
        if self.data_store:
            # Use parquet storage
            df = self.data_store.get_company_records('vat_capital', company_id, start_date, end_date)
            
            if df.empty:
                return []
            
            # Convert DataFrame to CapitalRecord objects
            records = []
            for _, row in df.iterrows():
                try:
                    record = CapitalRecord(
                        capital_id=row['capital_id'],
                        company_id=row['company_id'],
                        # DB column is investment_date; investor_name holds the source
                        transaction_date=row['investment_date'],
                        description=row['description'],
                        capital_type=row['capital_type'],
                        transaction_type=row.get('transaction_type') or 'INJECTION',
                        amount=Decimal(str(row['amount'])),
                        source=row.get('investor_name', ''),
                        created_date=row['created_date'],
                        updated_date=row['updated_date'],
                        created_by=row.get('created_by', ''),
                        is_active=row.get('is_active', True)
                    )
                    records.append(record)
                except Exception as e:
                    print(f"Error converting row to CapitalRecord: {e}")
                    continue
            
            return records
        else:
            # Fallback to in-memory storage
            records = self.capital_records.get(company_id, [])
            
            if start_date or end_date:
                filtered_records = []
                for record in records:
                    if start_date and record.transaction_date < start_date:
                        continue
                    if end_date and record.transaction_date > end_date:
                        continue
                    filtered_records.append(record)
                return filtered_records
            
            return records
    
    def generate_financial_summary(self, company_id: str, period_start: date, 
                                 period_end: date) -> FinancialSummary:
        """Generate comprehensive financial summary for a period"""
        
        # Get records for the period
        income_records = self.get_company_income_records(company_id, period_start, period_end)
        expense_records = self.get_company_expense_records(company_id, period_start, period_end)
        capital_records = self.get_company_capital_records(company_id, period_start, period_end)
        
        summary = FinancialSummary(
            company_id=company_id,
            period_start=period_start,
            period_end=period_end
        )
        
        # Calculate Income Summary
        for record in income_records:
            summary.total_income_gross += record.gross_amount
            summary.total_income_vat += record.vat_amount
            summary.total_income_net += record.net_amount
            summary.output_vat += record.vat_amount
            
            category = record.category.value
            summary.income_by_category[category] = summary.income_by_category.get(category, Decimal('0')) + record.gross_amount
        
        # Calculate Expense Summary
        for record in expense_records:
            summary.total_expense_net += record.gross_amount
            summary.total_expense_vat += record.vat_amount
            summary.total_expense_gross += record.net_amount
            summary.input_vat += record.vat_amount
            
            category = record.category.value
            summary.expense_by_category[category] = summary.expense_by_category.get(category, Decimal('0')) + record.net_amount
        
        # Calculate Capital Summary
        for record in capital_records:
            summary.total_capital += record.amount
            capital_type = record.capital_type
            summary.capital_by_type[capital_type] = summary.capital_by_type.get(capital_type, Decimal('0')) + record.amount
        
        # Calculate VAT Summary
        summary.vat_payable = summary.output_vat - summary.input_vat
        
        # Calculate Financial Position
        summary.gross_profit = summary.total_income_gross - summary.total_expense_net
        summary.net_profit = summary.total_income_net - summary.total_expense_gross
        summary.total_assets = summary.total_capital + summary.net_profit
        
        return summary
    
    def get_vat_configurations(self) -> Dict[str, VATConfiguration]:
        """Get all VAT configurations"""
        return self.vat_configurations
    
    def add_vat_configuration(self, vat_config: VATConfiguration) -> bool:
        """Add or update VAT configuration"""
        self.vat_configurations[vat_config.vat_id] = vat_config
        return True
    
    def get_company_statistics(self, company_id: str) -> Dict:
        """Get basic statistics for a company"""
        if self.data_store:
            # Use parquet storage
            stats = self.data_store.get_statistics(company_id)
            
            # Calculate totals for current year
            current_year = date.today().year
            year_start = date(current_year, 1, 1)
            year_end = date(current_year, 12, 31)
            
            summary = self.generate_financial_summary(company_id, year_start, year_end)
            
            return {
                'income_transactions': stats.get('vat_income_count', 0),
                'expense_transactions': stats.get('vat_expenses_count', 0),
                'capital_transactions': stats.get('vat_capital_count', 0),
                'total_income_ytd': float(summary.total_income_net),
                'total_expenses_ytd': float(summary.total_expense_gross),
                'net_profit_ytd': float(summary.net_profit),
                'vat_payable_ytd': float(summary.vat_payable),
            }
        else:
            # Fallback to in-memory storage
            income_count = len(self.income_records.get(company_id, []))
            expense_count = len(self.expense_records.get(company_id, []))
            capital_count = len(self.capital_records.get(company_id, []))
            
            # Calculate totals for current year
            current_year = date.today().year
            year_start = date(current_year, 1, 1)
            year_end = date(current_year, 12, 31)
            
            summary = self.generate_financial_summary(company_id, year_start, year_end)
            
            return {
                'income_transactions': income_count,
                'expense_transactions': expense_count,
                'capital_transactions': capital_count,
                'total_income_ytd': float(summary.total_income_net),
                'total_expenses_ytd': float(summary.total_expense_gross),
                'net_profit_ytd': float(summary.net_profit),
                'vat_payable_ytd': float(summary.vat_payable),
            }