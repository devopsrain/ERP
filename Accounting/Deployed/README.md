# Ethiopian Business Management System

A comprehensive business management platform designed specifically for Ethiopian businesses, integrating VAT accounting, payroll management, and multi-company operations.

## 🌟 Features Overview

### 📊 **VAT Portal**
- **Income Management**: Contract dates, client details, VAT calculations
- **Expense Tracking**: Categories, suppliers, tax-deductible status
- **Capital Transactions**: Investment tracking, ownership management
- **Financial Summaries**: Comprehensive P&L, cash flow, VAT reports
- **Ethiopian VAT Compliance**: Standard (15%), Zero-rated (0%), Exempt, Withholding (2%)

### 👥 **Payroll System**
- **Ethiopian Tax Calculations**: Progressive income tax (0% to 35%)
- **Pension Contributions**: Employee (7%) + Employer (11%)
- **Employee Categories**: Regular, Contract, Executive, Casual Worker
- **Payroll Processing**: Automated salary calculations and payslip generation
- **Employee Management**: Full CRUD operations with department tracking

### 🏢 **Multi-Company Support**
- **Role-Based Access**: Super Admin, Company Admin, HR Manager, Payroll Clerk, Employee
- **Company Isolation**: Complete data separation between companies
- **Subscription Plans**: Free, Basic, Professional, Enterprise tiers
- **User Management**: Comprehensive user roles and permissions

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Flask 2.0+
- Modern web browser

### Installation
```bash
# Clone the repository
git clone <repository-url>
cd Accounting

# Install dependencies
pip install -r requirements.txt

# Start the application
cd web
python app.py
```

### Access the System
- **Main Portal**: http://localhost:5000
- **VAT Dashboard**: http://localhost:5000/vat/dashboard
- **Payroll System**: http://localhost:5000/payroll
- **Employee Management**: http://localhost:5000/payroll/employees

## 📋 System Architecture

```
Ethiopian Business Management System
├── VAT Portal Module
│   ├── Income Management
│   ├── Expense Tracking
│   ├── Capital Transactions
│   └── Financial Reporting
├── Payroll System Module
│   ├── Employee Management
│   ├── Salary Calculations
│   ├── Tax Processing
│   └── Payslip Generation
└── Multi-Company Framework
    ├── User Authentication
    ├── Role Management
    ├── Company Isolation
    └── Subscription Management
```

## 🛠️ Technical Stack

- **Backend**: Python, Flask
- **Frontend**: Bootstrap 5, JavaScript, jQuery
- **Database**: In-memory (demo), PostgreSQL (production)
- **Authentication**: Session-based with role management
- **UI Framework**: Responsive Bootstrap design

## 🏗️ Project Structure

```
Accounting/
├── models/                     # Data models and business logic
│   ├── vat_portal.py          # VAT system models
│   ├── ethiopian_payroll.py   # Payroll calculations
│   ├── multi_company.py       # Multi-company framework
│   ├── account.py             # General ledger accounts
│   └── journal_entry.py       # Accounting entries
├── core/                       # Core business logic
│   ├── ledger.py              # General ledger operations
│   └── multi_company_payroll.py # Payroll integration
├── web/                        # Web interface
│   ├── app.py                 # Flask application
│   ├── vat_routes.py          # VAT portal routes
│   ├── payroll_routes.py      # Payroll system routes
│   ├── multicompany_routes.py # Multi-company routes
│   └── templates/             # HTML templates
│       ├── vat/               # VAT portal templates
│       ├── payroll/           # Payroll templates
│       └── multicompany/      # Multi-company templates
├── demo.py                     # Demo data generation
└── requirements.txt           # Python dependencies
```

## 📊 Key Modules Documentation

### VAT Portal Features
- ✅ **Income Recording**: Contract dates, descriptions, client management
- ✅ **Expense Management**: Categories, suppliers, VAT integration
- ✅ **Capital Tracking**: Investments, withdrawals, ownership changes
- ✅ **Financial Reports**: P&L statements, cash flow, VAT summaries
- ✅ **Ethiopian Compliance**: Local VAT rates and regulations

### Payroll System Features
- ✅ **Progressive Taxation**: Ethiopian income tax brackets (0-35%)
- ✅ **Pension Integration**: Automatic pension calculations
- ✅ **Employee Categories**: Different calculation rules per category
- ✅ **Payroll Processing**: Monthly salary runs with detailed payslips
- ✅ **Reporting**: Comprehensive payroll reports and summaries

### Multi-Company Framework
- ✅ **Data Isolation**: Complete separation between companies
- ✅ **Role Management**: Granular permission system
- ✅ **User Authentication**: Secure login and session management
- ✅ **Subscription Tiers**: Flexible pricing and feature access

## 🎯 Usage Examples

### Adding Income Transaction
1. Navigate to VAT Portal → Add Income
2. Enter contract date and description
3. Add client details and TIN number
4. Set gross amount and VAT type
5. System automatically calculates VAT and net amounts

### Processing Payroll
1. Access Payroll System → Calculate Payroll
2. Select month and employees
3. System calculates taxes, pensions, and net pay
4. Generate payslips and accounting entries
5. Export reports for tax authorities

### Managing Multiple Companies
1. Create company profiles with subscription plans
2. Assign users with appropriate roles
3. Switch between companies seamlessly
4. Maintain isolated data and operations

## 🔧 Configuration

### Environment Variables
```bash
FLASK_ENV=development  # or production
FLASK_DEBUG=True      # for development
SECRET_KEY=your-secret-key
DATABASE_URL=postgresql://... # for production
```

### Database Configuration
For production deployment, configure PostgreSQL:
```python
# In production, replace in-memory storage with:
DATABASE_CONFIG = {
    'host': 'localhost',
    'database': 'ethiopian_business',
    'user': 'business_user',
    'password': 'secure_password'
}
```

## 🚀 Quick Start

### 1. Installation

```bash
# Navigate to the accounting software directory
cd accounting_software

# Install dependencies (for web interface)
pip install -r requirements.txt
```

### 2. Run the Software

```bash
python run.py
```

Choose your interface:
- **Option 1**: Command Line Interface (no additional dependencies)
- **Option 2**: Web Interface (requires Flask)

### 🇪🇹 Ethiopian Payroll Demo

```bash
# Run comprehensive Ethiopian payroll demonstration
python ethiopian_payroll_demo.py
```

This demo showcases:
- Ethiopian tax calculations for various salary levels
- Pension contributions (7% + 11%)
- Complete accounting integration with journal entries
- Pay slip generation and payroll reports

### 3. Initial Setup

1. Create standard chart of accounts (recommended for first-time setup)
2. Start entering transactions
3. Generate reports

## 💻 Command Line Interface

The CLI provides a text-based menu system with the following options:

- **Setup**: Create standard chart of accounts
- **Accounts**: View and manage accounts
- **Journal Entries**: Create manual transactions
- **Quick Transactions**: Pre-configured transaction templates
- **Reports**: Trial balance, income statement, balance sheet
- **Account Ledgers**: Detailed transaction history
- **Data Export**: Export to JSON format

### CLI Quick Transactions

- **Cash Sale**: Records revenue and increases cash
- **Expense Payment**: Records expenses and decreases cash
- **Asset Purchase**: Records asset acquisition

## 🌐 Web Interface

The web interface provides a modern, user-friendly experience:

- **Dashboard**: Overview of accounts and balances
- **Chart of Accounts**: Visual account management
- **Journal Entries**: Form-based transaction entry
- **Reports**: Formatted financial statements
- **Quick Transactions**: Template-based entries

Access at: `http://localhost:5000` (when running web interface)

## 📊 Core Accounting Concepts

### Double-Entry Bookkeeping Rules

1. **Every transaction affects at least two accounts**
2. **Total debits must equal total credits**
3. **Account types and normal balances**:
   - Assets: Debit increases, Credit decreases
   - Liabilities: Credit increases, Debit decreases
   - Equity: Credit increases, Debit decreases
   - Revenue: Credit increases, Debit decreases
   - Expenses: Debit increases, Credit decreases

### Fundamental Equation

```
Assets = Liabilities + Equity
```

### Standard Chart of Accounts

| ID | Account Name | Type | Description |
|----|--------------|------|-------------|
| 1000 | Cash | Asset | Company cash on hand |
| 1100 | Accounts Receivable | Asset | Money owed by customers |
| 1200 | Inventory | Asset | Goods for sale |
| 1500 | Equipment | Asset | Business equipment |
| 2000 | Accounts Payable | Liability | Money owed to suppliers |
| 2100 | Accrued Expenses | Liability | Expenses incurred but not paid |
| 3000 | Owner's Equity | Equity | Owner's investment |
| 4000 | Sales Revenue | Revenue | Income from sales |
| 6000 | Salaries Expense | Expense | Employee wages |
| 6100 | Rent Expense | Expense | Office/facility rent |

## 📈 Reports Available

### 1. Trial Balance
- Lists all accounts with their balances
- Verifies that total debits = total credits
- Shows the fundamental accounting equation

### 2. Income Statement
- Revenue and expenses for a specific period
- Calculates net income/loss
- Can be generated for custom date ranges

### 3. Balance Sheet
- Financial position at a specific date
- Assets, liabilities, and equity
- Verifies the accounting equation

### 4. Account Ledger
- Detailed transaction history for any account
- Running balance calculations
- Filterable by date range

## 🔧 Advanced Usage

### Programmatic Usage

```python
from core.ledger import GeneralLedger
from models.journal_entry import JournalEntryBuilder
from models.account import Account, AccountType

# Create ledger
ledger = GeneralLedger()
ledger.create_standard_chart_of_accounts()

# Create a sale transaction
sale_entry = JournalEntryBuilder.cash_sale("1000", "4000", 100.0, "Product sale")
ledger.post_journal_entry(sale_entry)

# Generate reports
trial_balance = ledger.generate_trial_balance()
income_statement = ledger.generate_income_statement()
balance_sheet = ledger.generate_balance_sheet()
```

### Ethiopian Business Compliance

The system provides built-in support for Ethiopian business requirements:

```python
# Ethiopian VAT Rates
VAT_RATES = {
    'standard': 0.15,      # 15% standard VAT
    'zero_rated': 0.0,     # 0% for exports, basic necessities
    'exempt': None,        # VAT exempt items
    'withholding': 0.02    # 2% withholding VAT
}

# Ethiopian Progressive Tax Brackets
PROGRESSIVE_TAX = [
    {'min': 0, 'max': 600, 'rate': 0.0, 'deduction': 0},
    {'min': 601, 'max': 1650, 'rate': 0.10, 'deduction': 60},
    {'min': 1651, 'max': 3200, 'rate': 0.15, 'deduction': 142.5},
    {'min': 3201, 'max': 5250, 'rate': 0.20, 'deduction': 302.5},
    {'min': 5251, 'max': 7800, 'rate': 0.25, 'deduction': 565},
    {'min': 7801, 'max': 10900, 'rate': 0.30, 'deduction': 955},
    {'min': 10901, 'max': float('inf'), 'rate': 0.35, 'deduction': 1500}
]
```

## 🚀 Production Deployment

### Recommended Server Configuration

For in-house deployment, we recommend Dell PowerEdge R430 with:
- **CPU**: Intel Xeon E5-2620 v4 (8 cores)
- **RAM**: 32GB DDR4 ECC (minimum 16GB)
- **Storage**: 2x 1TB SSD RAID 1 (redundancy)
- **Network**: Dual Gigabit Ethernet
- **OS**: Ubuntu Server 20.04 LTS

### Installation Steps

1. **Prepare the Server**
```bash
sudo apt update && sudo apt upgrade -y
sudo apt install python3 python3-pip nginx postgresql postgresql-contrib
```

2. **Setup PostgreSQL Database**
```bash
sudo -u postgres psql
CREATE DATABASE ethiopian_business;
CREATE USER business_user WITH PASSWORD 'secure_password';
GRANT ALL PRIVILEGES ON DATABASE ethiopian_business TO business_user;
\q
```

3. **Deploy Application**
```bash
git clone <repository-url>
cd accounting_software
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt gunicorn psycopg2-binary
```

4. **Configure Nginx**
```nginx
server {
    listen 80;
    server_name your-domain.com;
    
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
    }
}
```

5. **Setup Systemd Service**
```ini
[Unit]
Description=Ethiopian Business Management System
After=network.target

[Service]
User=businessapp
Group=www-data
WorkingDirectory=/path/to/accounting_software
Environment="PATH=/path/to/accounting_software/venv/bin"
ExecStart=/path/to/accounting_software/venv/bin/gunicorn --workers 3 --bind 127.0.0.1:5000 run:app
Restart=always

[Install]
WantedBy=multi-user.target
```

### Security Considerations

- **SSL Certificate**: Use Let's Encrypt for HTTPS
- **Firewall**: Configure UFW to allow only necessary ports
- **Database**: Use strong passwords and limit connections
- **Backups**: Automated daily backups to external storage
- **Monitoring**: Setup system monitoring and alerting

## 📚 API Documentation

### VAT Portal API Endpoints

```bash
# Income Management
GET    /vat/income          # List all income records
POST   /vat/income          # Create new income record
PUT    /vat/income/<id>     # Update income record
DELETE /vat/income/<id>     # Delete income record

# Expense Management
GET    /vat/expenses        # List all expense records
POST   /vat/expenses        # Create new expense record
PUT    /vat/expenses/<id>   # Update expense record
DELETE /vat/expenses/<id>   # Delete expense record

# Capital Transactions
GET    /vat/capital         # List capital transactions
POST   /vat/capital         # Create capital transaction
PUT    /vat/capital/<id>    # Update capital transaction
DELETE /vat/capital/<id>    # Delete capital transaction

# Financial Reports
GET    /vat/financial_summary  # Comprehensive financial summary
GET    /vat/vat_report         # VAT compliance report
```

### Payroll API Endpoints

```bash
# Employee Management
GET    /payroll/employees       # List all employees
POST   /payroll/employees       # Add new employee
PUT    /payroll/employees/<id>  # Update employee
DELETE /payroll/employees/<id>  # Delete employee

# Payroll Processing
GET    /payroll/calculate/<employee_id>  # Calculate pay for employee
POST   /payroll/process                  # Process payroll for period
GET    /payroll/payslips/<period>        # Get payslips for period
```

## 🧪 Testing

### Running Tests

```bash
# Run all tests
python -m pytest

# Run with coverage
python -m pytest --cov=.

# Run specific test modules
python -m pytest tests/test_vat_portal.py
python -m pytest tests/test_ethiopian_payroll.py
```

### Test Coverage

The system includes comprehensive tests for:
- VAT calculations and compliance
- Ethiopian tax calculations
- Payroll processing
- Multi-company isolation
- Database operations
- API endpoints

## 📞 Support and Maintenance

### Regular Maintenance Tasks

1. **Daily Backups**: Automated PostgreSQL dumps
2. **System Updates**: Monthly security patches
3. **Performance Monitoring**: CPU, memory, disk usage
4. **Log Rotation**: Prevent log files from consuming disk space
5. **Database Optimization**: Regular VACUUM and REINDEX

### Troubleshooting

**Common Issues:**

1. **Database Connection Errors**
   - Check PostgreSQL service status
   - Verify connection credentials
   - Test network connectivity

2. **Performance Issues**
   - Monitor system resources
   - Check database query performance
   - Review application logs

3. **VAT Calculation Discrepancies**
   - Verify Ethiopian VAT rates are current
   - Check transaction categorization
   - Review period calculations

### Support Contacts

For technical support and system maintenance:
- **System Administrator**: admin@company.com
- **Business Analyst**: business@company.com
- **Emergency Contact**: +251-xxx-xxx-xxxx

## 📋 Compliance Checklist

### Ethiopian Business Compliance

- ✅ **VAT Registration**: System supports VAT-registered businesses
- ✅ **Tax Calculations**: Progressive income tax implementation
- ✅ **Pension Contributions**: Private (7%) + Government (11%)
- ✅ **Payroll Records**: Complete employee record keeping
- ✅ **Financial Reporting**: Standard Ethiopian business reports
- ✅ **Multi-Company**: Support for business groups
- ✅ **Audit Trail**: Complete transaction history
- ✅ **Data Security**: Encrypted sensitive information

### Regulatory Requirements

1. **Monthly VAT Returns**: Automated calculation and reporting
2. **Annual Tax Filing**: Comprehensive income and expense tracking
3. **Employee Records**: Full compliance with labor law requirements
4. **Financial Statements**: Standard format for regulatory submission

## 🎯 Roadmap

### Planned Features

**Phase 1** (Current):
- ✅ Core accounting system
- ✅ VAT portal with Ethiopian compliance
- ✅ Payroll system with tax calculations
- ✅ Multi-company support

**Phase 2** (Next Quarter):
- 🔄 **Mobile Application**: Android/iOS companion app
- 🔄 **Advanced Reporting**: Custom report builder
- 🔄 **API Integration**: Banks and financial institutions
- 🔄 **Document Management**: Invoice and receipt scanning

**Phase 3** (Future):
- 🔄 **AI Assistant**: Automated categorization and insights
- 🔄 **Blockchain Integration**: Immutable audit trails
- 🔄 **International Expansion**: Support for other African countries
- 🔄 **Advanced Analytics**: Business intelligence dashboard

---

## 💡 About

The **Ethiopian Business Management System** is designed specifically for Ethiopian businesses, incorporating local tax laws, VAT regulations, and payroll requirements. Built with modern web technologies and designed for scalability and compliance.

**Version**: 1.0.0
**Last Updated**: December 2024
**License**: Proprietary

For more information, contact: info@ethiopianbusiness.com
trial_balance = ledger.get_trial_balance()
income_statement = ledger.get_income_statement(start_date, end_date)
```

### Custom Account Creation

```python
# Create custom account
custom_account = Account("7000", "Marketing Expense", AccountType.EXPENSE)
ledger.add_account(custom_account)
```

### Manual Journal Entry

```python
from models.journal_entry import JournalEntry

# Create complex transaction
entry = JournalEntry(description="Equipment purchase with loan")
entry.add_line("1500", debit_amount=5000)   # Equipment (asset)
entry.add_line("1000", credit_amount=1000)  # Cash (asset decrease)
entry.add_line("2500", credit_amount=4000)  # Long-term debt (liability)

ledger.post_journal_entry(entry)
```

## 💾 Data Management

### Export Data

The system can export all data to JSON format:
- All accounts and their balances
- Complete transaction history
- All journal entries
- Preserves data structure for backup/analysis

### Data Persistence

- CLI: Data exists only during session (add database for persistence)
- Web: Data exists during server session
- Export: Use JSON export for data backup

## 🛠️ Technical Details

### Dependencies

- **Core System**: Pure Python (no external dependencies)
- **Web Interface**: Flask, Jinja2, Werkzeug
- **Frontend**: Bootstrap 5, Font Awesome icons

### Architecture

- **Models**: Account and JournalEntry classes with proper validation
- **Core**: GeneralLedger class managing all business logic
- **Interfaces**: Separate CLI and web interfaces using the same core
- **Separation**: Clear separation between data models and presentation

### Error Handling

- Journal entry validation (balanced entries required)
- Account existence verification
- Duplicate account ID prevention
- Graceful error recovery in both interfaces

## 📚 Example Transactions

### Recording a Sale

```
Description: Sale of products
Debit:  1000 Cash             $500.00
Credit: 4000 Sales Revenue    $500.00
```

### Recording an Expense

```
Description: Office rent payment
Debit:  6100 Rent Expense     $1,200.00
Credit: 1000 Cash             $1,200.00
```

### Asset Purchase

```
Description: Computer equipment
Debit:  1500 Equipment        $2,000.00
Credit: 1000 Cash             $2,000.00
```

## 🔍 Troubleshooting

### Common Issues

1. **"Account not found"**: Ensure accounts exist before creating transactions
2. **"Entry not balanced"**: Check that total debits equal total credits
3. **Flask import error**: Install Flask with `pip install Flask`
4. **Module import issues**: Run from the accounting_software directory

### Getting Help

- Check account names and IDs in the Chart of Accounts
- Use the trial balance to verify system balance
- Review journal entries for transaction details
- Export data to JSON for external analysis

## 📄 License

This software is provided as-is for educational and small business use. Feel free to modify and distribute according to your needs.

## 🤝 Contributing

This is a basic implementation. Potential enhancements:
- Database persistence (SQLite, PostgreSQL)
- Multi-company support
- Advanced reporting features
- Import/export formats (CSV, QBO)
- User authentication and permissions
- Audit trails and change logging
- Mobile-responsive design improvements

---

**Note**: This software implements basic accounting principles but should not replace professional accounting advice. Always consult with a qualified accountant for business financial decisions.