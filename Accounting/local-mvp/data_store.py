"""
Parquet-based data storage layer for Ethiopian Business Management System
Provides unified interface for all data operations using Parquet files
"""

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Any, Optional, Union
import uuid
import json
from config import PARQUET_FILES, DATA_DIR

class ParquetDataStore:
    """Unified Parquet-based data storage for all business entities"""
    
    def __init__(self):
        self.schemas = self._define_schemas()
        self._initialize_data_files()
    
    def _define_schemas(self) -> Dict[str, pa.Schema]:
        """Define PyArrow schemas for all data entities"""
        return {
            'accounts': pa.schema([
                ('id', pa.string()),
                ('code', pa.string()),
                ('name', pa.string()),
                ('type', pa.string()),
                ('description', pa.string()),
                ('normal_balance', pa.string()),
                ('company_id', pa.string()),
                ('is_active', pa.bool_()),
                ('created_date', pa.timestamp('ns')),
                ('updated_date', pa.timestamp('ns'))
            ]),
            
            'journal_entries': pa.schema([
                ('id', pa.string()),
                ('date', pa.date32()),
                ('description', pa.string()),
                ('reference', pa.string()),
                ('account_code', pa.string()),
                ('debit', pa.float64()),
                ('credit', pa.float64()),
                ('company_id', pa.string()),
                ('entry_type', pa.string()),
                ('created_date', pa.timestamp('ns')),
                ('created_by', pa.string())
            ]),
            
            'vat_income': pa.schema([
                ('id', pa.string()),
                ('date', pa.date32()),
                ('contract_date', pa.date32()),
                ('description', pa.string()),
                ('customer_name', pa.string()),
                ('invoice_number', pa.string()),
                ('gross_amount', pa.float64()),
                ('vat_rate', pa.float64()),
                ('vat_amount', pa.float64()),
                ('net_amount', pa.float64()),
                ('company_id', pa.string()),
                ('created_date', pa.timestamp('ns'))
            ]),
            
            'vat_expenses': pa.schema([
                ('id', pa.string()),
                ('date', pa.date32()),
                ('description', pa.string()),
                ('supplier_name', pa.string()),
                ('invoice_number', pa.string()),
                ('gross_amount', pa.float64()),
                ('vat_rate', pa.float64()),
                ('vat_amount', pa.float64()),
                ('net_amount', pa.float64()),
                ('expense_category', pa.string()),
                ('company_id', pa.string()),
                ('created_date', pa.timestamp('ns'))
            ]),
            
            'vat_capital': pa.schema([
                ('id', pa.string()),
                ('date', pa.date32()),
                ('description', pa.string()),
                ('asset_type', pa.string()),
                ('gross_amount', pa.float64()),
                ('vat_rate', pa.float64()),
                ('vat_amount', pa.float64()),
                ('net_amount', pa.float64()),
                ('depreciation_years', pa.int32()),
                ('company_id', pa.string()),
                ('created_date', pa.timestamp('ns'))
            ]),
            
            'employees': pa.schema([
                ('id', pa.string()),
                ('employee_number', pa.string()),
                ('first_name', pa.string()),
                ('last_name', pa.string()),
                ('position', pa.string()),
                ('department', pa.string()),
                ('hire_date', pa.date32()),
                ('basic_salary', pa.float64()),
                ('allowances', pa.float64()),
                ('tax_exemption', pa.float64()),
                ('is_active', pa.bool_()),
                ('company_id', pa.string()),
                ('created_date', pa.timestamp('ns'))
            ]),
            
            'payroll_records': pa.schema([
                ('id', pa.string()),
                ('employee_id', pa.string()),
                ('pay_period', pa.string()),
                ('basic_salary', pa.float64()),
                ('allowances', pa.float64()),
                ('gross_pay', pa.float64()),
                ('taxable_income', pa.float64()),
                ('income_tax', pa.float64()),
                ('employee_pension', pa.float64()),
                ('employer_pension', pa.float64()),
                ('net_pay', pa.float64()),
                ('company_id', pa.string()),
                ('created_date', pa.timestamp('ns'))
            ]),
            
            'companies': pa.schema([
                ('id', pa.string()),
                ('name', pa.string()),
                ('tin_number', pa.string()),
                ('vat_number', pa.string()),
                ('address', pa.string()),
                ('currency', pa.string()),
                ('fiscal_year_start', pa.string()),
                ('is_active', pa.bool_()),
                ('created_date', pa.timestamp('ns'))
            ]),
            
            'audit_log': pa.schema([
                ('id', pa.string()),
                ('timestamp', pa.timestamp('ns')),
                ('user', pa.string()),
                ('action', pa.string()),
                ('entity_type', pa.string()),
                ('entity_id', pa.string()),
                ('old_values', pa.string()),
                ('new_values', pa.string()),
                ('company_id', pa.string())
            ])
        }
    
    def _initialize_data_files(self):
        """Create empty Parquet files if they don't exist"""
        for table_name, file_path in PARQUET_FILES.items():
            if not file_path.exists():
                schema = self.schemas.get(table_name)
                if schema:
                    # Create empty DataFrame with columns matching schema
                    columns = [field.name for field in schema]
                    empty_df = pd.DataFrame(columns=columns)
                    
                    # Convert DataFrame types to match schema where possible
                    for field in schema:
                        if field.type == pa.date32():
                            empty_df[field.name] = pd.to_datetime(empty_df[field.name]).dt.date
                        elif field.type == pa.timestamp('ns'):
                            empty_df[field.name] = pd.to_datetime(empty_df[field.name])
                        elif field.type == pa.bool_():
                            empty_df[field.name] = empty_df[field.name].astype('bool')
                        elif field.type == pa.float64():
                            empty_df[field.name] = pd.to_numeric(empty_df[field.name], errors='coerce')
                        elif field.type == pa.int32():
                            empty_df[field.name] = pd.to_numeric(empty_df[field.name], errors='coerce', downcast='integer')
                    
                    # Create empty table with schema
                    empty_table = pa.Table.from_pandas(empty_df, schema=schema, preserve_index=False)
                    pq.write_table(empty_table, file_path)
                    print(f"Created {table_name}.parquet")
    
    def read_table(self, table_name: str, filters: List = None, columns: List[str] = None) -> pd.DataFrame:
        """Read data from Parquet table with optional filtering"""
        file_path = PARQUET_FILES.get(table_name)
        if not file_path or not file_path.exists():
            return pd.DataFrame()
        
        try:
            return pd.read_parquet(file_path, filters=filters, columns=columns)
        except Exception as e:
            print(f"Error reading {table_name}: {e}")
            return pd.DataFrame()
    
    def write_table(self, table_name: str, df: pd.DataFrame, append: bool = False):
        """Write DataFrame to Parquet table"""
        file_path = PARQUET_FILES.get(table_name)
        if not file_path:
            raise ValueError(f"Unknown table: {table_name}")
        
        try:
            if append and file_path.exists():
                # Read existing data and append
                existing_df = self.read_table(table_name)
                df = pd.concat([existing_df, df], ignore_index=True)
            
            # Ensure proper data types based on schema
            schema = self.schemas.get(table_name)
            if schema:
                table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
                pq.write_table(table, file_path)
            else:
                df.to_parquet(file_path, index=False)
            
        except Exception as e:
            print(f"Error writing to {table_name}: {e}")
            raise
    
    def insert_record(self, table_name: str, record: Dict[str, Any]) -> str:
        """Insert a single record into table"""
        # Generate ID if not provided
        if 'id' not in record:
            record['id'] = str(uuid.uuid4())
        
        # Add timestamps
        now = datetime.now()
        if 'created_date' not in record:
            record['created_date'] = now
        if 'updated_date' not in record and table_name in ['accounts']:
            record['updated_date'] = now
        
        # Convert to DataFrame and append
        df = pd.DataFrame([record])
        self.write_table(table_name, df, append=True)
        
        # Log the action
        self._log_action('INSERT', table_name, record['id'], {}, record)
        
        return record['id']
    
    def update_record(self, table_name: str, record_id: str, updates: Dict[str, Any]) -> bool:
        """Update an existing record"""
        df = self.read_table(table_name)
        if df.empty or record_id not in df['id'].values:
            return False
        
        # Get old values for audit log
        old_record = df[df['id'] == record_id].iloc[0].to_dict()
        
        # Update the record
        mask = df['id'] == record_id
        for field, value in updates.items():
            if field in df.columns:
                df.loc[mask, field] = value
        
        # Add updated timestamp if applicable
        if 'updated_date' in df.columns:
            df.loc[mask, 'updated_date'] = datetime.now()
        
        # Write back
        self.write_table(table_name, df)
        
        # Log the action
        self._log_action('UPDATE', table_name, record_id, old_record, updates)
        
        return True
    
    def delete_record(self, table_name: str, record_id: str) -> bool:
        """Delete a record (soft delete for audit trail)"""
        df = self.read_table(table_name)
        if df.empty or record_id not in df['id'].values:
            return False
        
        # Get record for audit log
        old_record = df[df['id'] == record_id].iloc[0].to_dict()
        
        # For accounts and employees, use soft delete
        if table_name in ['accounts', 'employees'] and 'is_active' in df.columns:
            return self.update_record(table_name, record_id, {'is_active': False})
        
        # Hard delete for other tables
        df = df[df['id'] != record_id]
        self.write_table(table_name, df)
        
        # Log the action
        self._log_action('DELETE', table_name, record_id, old_record, {})
        
        return True
    
    def query(self, table_name: str, **filters) -> pd.DataFrame:
        """Query table with filters"""
        df = self.read_table(table_name)
        
        for field, value in filters.items():
            if field in df.columns:
                if isinstance(value, list):
                    df = df[df[field].isin(value)]
                else:
                    df = df[df[field] == value]
        
        return df
    
    def get_record(self, table_name: str, record_id: str) -> Optional[Dict[str, Any]]:
        """Get a single record by ID"""
        df = self.query(table_name, id=record_id)
        if df.empty:
            return None
        return df.iloc[0].to_dict()
    
    def _log_action(self, action: str, entity_type: str, entity_id: str, 
                   old_values: Dict, new_values: Dict, user: str = 'system'):
        """Log actions for audit trail"""
        audit_record = {
            'id': str(uuid.uuid4()),
            'timestamp': datetime.now(),
            'user': user,
            'action': action,
            'entity_type': entity_type,
            'entity_id': entity_id,
            'old_values': json.dumps(old_values, default=str),
            'new_values': json.dumps(new_values, default=str),
            'company_id': new_values.get('company_id', old_values.get('company_id', 'default'))
        }
        
        try:
            df = pd.DataFrame([audit_record])
            self.write_table('audit_log', df, append=True)
        except Exception as e:
            print(f"Warning: Failed to log action: {e}")
    
    def backup_data(self, backup_dir: Path):
        """Create backup of all data files"""
        backup_dir.mkdir(exist_ok=True)
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        for table_name, file_path in PARQUET_FILES.items():
            if file_path.exists():
                backup_file = backup_dir / f"{table_name}_{timestamp}.parquet"
                import shutil
                shutil.copy2(file_path, backup_file)
        
        print(f"Backup completed to {backup_dir}")
    
    def get_table_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get statistics for all tables"""
        stats = {}
        
        for table_name in PARQUET_FILES.keys():
            df = self.read_table(table_name)
            file_path = PARQUET_FILES[table_name]
            
            stats[table_name] = {
                'rows': len(df),
                'columns': len(df.columns) if not df.empty else 0,
                'file_size_mb': file_path.stat().st_size / (1024 * 1024) if file_path.exists() else 0,
                'last_modified': datetime.fromtimestamp(file_path.stat().st_mtime) if file_path.exists() else None
            }
        
        return stats

# Global instance
data_store = ParquetDataStore()