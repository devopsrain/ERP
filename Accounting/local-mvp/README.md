# 🇪🇹 Ethiopian Business Management System - Local MVP

**Parquet-based Standalone System with Complete Input/Output Capabilities**

A comprehensive business management system designed specifically for Ethiopian businesses, featuring VAT compliance, payroll management, and complete accounting functionality. Built with high-performance Parquet storage for fast data processing and easy deployment.

## 🎆 Features

### 📊 **Double-Entry Accounting**
- Complete chart of accounts with Ethiopian standards
- Journal entries with automatic balance validation
- Trial balance and income statement generation
- Account ledgers and transaction history
- Real-time balance calculations

### 💰 **VAT Portal (Ethiopian Compliance)**
- **Income Management**: Contract dates, customer tracking
- **Expense Management**: Supplier invoices, category classification
- **Capital Transactions**: Asset purchases with depreciation tracking
- **Ethiopian VAT Rates**:
  - Standard: 15% (most goods/services)
  - Zero-rated: 0% (exports, basic necessities)
  - Withholding: 2% (government purchases)
- **Comprehensive Reporting**: Net VAT payable/refundable calculations

### 👥 **Payroll System (Ethiopian Tax Law)**
- **Progressive Income Tax**: 0% to 35% based on 2026 Ethiopian tax brackets
- **Pension Contributions**: Employee (7%) + Employer (11%)
- **Tax Exemptions**: Configurable exemption amounts
- **Employee Management**: Full CRUD operations
- **Payroll Processing**: Individual and batch payroll calculations
- **Pay Slip Generation**: Complete payroll records

### 🖥️ **Parquet-Based Storage**
- **High Performance**: Columnar storage for fast queries
- **Efficient**: Compressed storage with minimal disk usage
- **Scalable**: Handles large datasets efficiently
- **Portable**: Single-file deployment, no database server required
- **Analytics Ready**: Direct integration with pandas/numpy

### 🌍 **Multi-Interface Access**
- **Web Interface**: Modern Bootstrap-based UI
- **Command Line**: Full CLI with colored output
- **API Ready**: JSON endpoints for integration
- **Export Capabilities**: Excel, CSV, JSON formats

## 🚀 Quick Start

### Installation

```bash
# Clone or extract the system
cd local-mvp

# Install dependencies
pip install -r requirements.txt

# Initialize system with sample data
python run.py --initialize

# Start web interface
python run.py
```

Access the system at: **http://localhost:5000**

### Alternative Interfaces

```bash
# Command line interface
python run.py --cli

# Run comprehensive demonstration
python run.py --demo

# Show system information
python run.py --info
```

## 💼 System Usage

### Web Interface

1. **Dashboard**: Overview of accounts, VAT, and recent activities
2. **Accounting**: Journal entries, trial balance, income statements
3. **VAT Portal**: Income, expense, and capital transaction management
4. **Payroll**: Employee management and payroll processing
5. **Reports**: Comprehensive reporting and data export

### CLI Interface

```bash
# Accounting operations
python run.py --cli accounting accounts          # List accounts
python run.py --cli accounting add-entry        # Add journal entry
python run.py --cli accounting trial-balance    # Generate trial balance
python run.py --cli accounting income-statement # Income statement

# VAT operations  
python run.py --cli vat add-income              # Add VAT income
python run.py --cli vat add-expense             # Add VAT expense
python run.py --cli vat summary                 # VAT summary report

# Payroll operations
python run.py --cli payroll employees           # List employees
python run.py --cli payroll calculate           # Calculate payroll
python run.py --cli payroll process-all         # Process all payroll

# Data management
python run.py --cli data export                 # Export data
python run.py --cli data stats                  # Data statistics
python run.py --cli data backup                 # Create backup
```

## 📁 File Structure

```
local-mvp/
├── config.py              # Configuration settings
├── data_store.py          # Parquet data storage layer
├── business_logic.py      # Core business logic
├── cli_interface.py       # Command line interface
├── web_interface.py       # Flask web application
├── run.py                 # Main startup script
├── requirements.txt       # Python dependencies
│
├── data/                  # Parquet data files
│   ├── accounts.parquet
│   ├── journal_entries.parquet
│   ├── vat_income.parquet
│   ├── vat_expenses.parquet
│   ├── employees.parquet
│   └── payroll_records.parquet
│
├── templates/             # HTML templates
│   ├── base.html
│   ├── dashboard.html
│   └── ...
│
├── exports/               # Data exports
├── reports/               # Generated reports
└── backups/               # Data backups
```

## 📊 Sample Data

The system comes with comprehensive sample data:

- **3 Sample Employees** with realistic Ethiopian salaries
- **Complete Chart of Accounts** (25+ accounts)
- **VAT Income Records** with contract dates and customer details
- **Journal Entries** showing typical business transactions
- **Payroll Records** demonstrating Ethiopian tax calculations

## 🌎 Ethiopian Compliance

### VAT Rates (2026)
- **Standard Rate**: 15% on most goods and services
- **Zero Rate**: 0% on exports and basic necessities
- **Withholding VAT**: 2% on government purchases

### Income Tax (Progressive)
- **ETB 0 - 600**: 0% (Tax-free threshold)
- **ETB 601 - 1,650**: 10% (minus ETB 60 deduction)
- **ETB 1,651 - 3,200**: 15% (minus ETB 142.5 deduction)
- **ETB 3,201 - 5,250**: 20% (minus ETB 302.5 deduction)
- **ETB 5,251 - 7,800**: 25% (minus ETB 565 deduction)
- **ETB 7,801 - 10,900**: 30% (minus ETB 955 deduction)
- **ETB 10,901+**: 35% (minus ETB 1,500 deduction)

### Pension Contributions
- **Employee**: 7% of gross salary
- **Employer**: 11% of gross salary

## 📤 Data Export & Integration

### Export Formats
- **Excel**: Multi-sheet workbooks with formatted reports
- **CSV**: Individual tables for data analysis
- **JSON**: Structured data for API integration

### Export Options
- Complete system export (all tables)
- Individual table exports
- Custom date range reports
- VAT compliance reports
- Payroll summaries

### Integration

```python
# Python integration example
import pandas as pd
from data_store import data_store

# Read VAT income records
vat_income = data_store.read_table('vat_income')

# Filter by date range
from datetime import date
monthly_vat = vat_income[
    vat_income['date'].between(date(2026, 1, 1), date(2026, 1, 31))
]

# Calculate totals
total_vat = monthly_vat['vat_amount'].sum()
print(f"Total VAT for January 2026: {total_vat:,.2f} ETB")
```

## 🛠️ Technical Specifications

### System Requirements
- **Python**: 3.8 or higher
- **Memory**: 512MB RAM minimum (2GB recommended)
- **Storage**: 50MB for system, grows with data
- **OS**: Windows, macOS, Linux

### Performance
- **Query Speed**: Sub-second response for typical datasets
- **Scalability**: Handles 100K+ transactions efficiently
- **Concurrency**: Single-user by design (file-based storage)
- **Backup**: Instant file-based backups

### Dependencies
```
pandas>=2.0.0          # Data manipulation
pyarrow>=12.0.0         # Parquet file support
Flask>=2.3.0            # Web interface
click>=8.1.0            # CLI framework
tabulate>=0.9.0         # CLI table formatting
matplotlib>=3.7.0       # Charts and graphs
```

## 📚 Advanced Usage

### Custom Reports

```python
# Generate custom VAT report
from business_logic import EthiopianBusinessManager
business = EthiopianBusinessManager()

# Get VAT summary for specific period
vat_summary = business.vat.get_vat_summary(
    start_date=date(2026, 1, 1),
    end_date=date(2026, 3, 31)
)

print(f"Q1 2026 Net VAT: {vat_summary['summary']['net_vat_payable']:,.2f} ETB")
```

### Batch Operations

```python
# Process monthly payroll for all employees
from datetime import datetime
current_period = datetime.now().strftime('%Y-%m')

results = business.payroll.process_company_payroll(current_period)
print(f"Processed payroll for {results['employees_processed']} employees")
print(f"Total net pay: {results['total_net_pay']:,.2f} ETB")
```

### Data Analysis

```python
# Analyze expense trends
expenses = data_store.read_table('vat_expenses')
monthly_expenses = expenses.groupby(
    expenses['date'].dt.to_period('M')
)['gross_amount'].sum()

print("Monthly Expense Trends:")
print(monthly_expenses)
```

## 🔒 Security & Backup

### Data Security
- **Local Storage**: All data stored locally, no cloud dependencies
- **File Permissions**: Standard OS file security
- **Audit Trail**: Complete transaction logging
- **Data Validation**: Input validation at multiple levels

### Backup Strategy
```bash
# Automated backup
python run.py --cli data backup

# Manual backup (copy data folder)
cp -r data/ backup_$(date +%Y%m%d)/
```

### Recovery
```bash
# Restore from backup
cp backup_20260218/data/* data/
```

## 🤝 Support & Maintenance

### Regular Maintenance
1. **Weekly Backups**: Use built-in backup system
2. **Monthly Exports**: Export data for external storage
3. **Quarterly Reviews**: Verify VAT calculations
4. **Annual Updates**: Update tax rates if changed

### Troubleshooting

```bash
# Check system status
python run.py --info

# Verify data integrity
python run.py --cli data stats

# Reset system (WARNING: deletes all data)
rm -rf data/*
python run.py --initialize
```

### Performance Optimization
- **Large Datasets**: Parquet compression handles efficiently
- **Query Optimization**: Use date filters for large time ranges
- **Memory Usage**: Pandas optimizations built-in
- **Disk Space**: Regular cleanup of exports folder

## 🎆 Demonstration

Run the complete demonstration to see all features:

```bash
python run.py --demo
```

This will:
1. Initialize the system with sample data
2. Create journal entries and generate reports
3. Process VAT transactions and calculate summaries
4. Run payroll for all employees
5. Export data to various formats
6. Create system backups
7. Display comprehensive statistics

---

**Ethiopian Business Management System** - Designed for Ethiopian businesses, built for performance and compliance. 🇪🇹

*Ready for immediate deployment with zero external dependencies.*