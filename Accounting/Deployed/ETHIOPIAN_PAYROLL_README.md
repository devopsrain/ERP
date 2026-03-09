# 🇪🇹 Ethiopian Payroll Integration - README

## Overview

This accounting software now includes a comprehensive **Ethiopian Payroll System** that handles salary calculations, tax withholding, and pension contributions according to Ethiopian labor law and tax regulations.

## 🚀 Features

### Core Payroll Functionality
- ✅ **Progressive Income Tax Calculation** (Ethiopian tax brackets)
- ✅ **Pension Contributions** (7% employee + 11% employer)
- ✅ **Multiple Employee Categories** (Regular, Contract, Casual, Executive)
- ✅ **Allowances Management** (Taxable and Non-taxable)
- ✅ **Automatic Deductions** (Union dues, loans, insurance)
- ✅ **Pay Slip Generation** in Ethiopian format
- ✅ **Complete Accounting Integration** with journal entries
- ✅ **Payroll Reports** and summaries

### Web Interface Features
- 📊 **Payroll Dashboard** with key metrics
- 👥 **Employee Management** (Add, Edit, View)
- 💰 **Payroll Processing** with batch calculations
- 🧮 **Tax Calculator** tool
- 📋 **Individual Pay Slips**
- 📈 **Payroll Reports**

## 📊 Ethiopian Tax Information (2026)

### Income Tax Brackets
| Income Range (ETB) | Tax Rate | Description |
|================---|----------|-------------|
| 0 - 600           | 0%       | Tax-free threshold |
| 601 - 1,650       | 10%      | First tax bracket |
| 1,651 - 3,200     | 15%      | Second tax bracket |
| 3,201 - 5,250     | 20%      | Third tax bracket |
| 5,251 - 7,800     | 25%      | Fourth tax bracket |
| 7,801 - 10,900    | 30%      | Fifth tax bracket |
| Above 10,900      | 35%      | Highest tax bracket |

### Pension Contributions
- **Employee Contribution:** 7% of basic salary
- **Employer Contribution:** 11% of basic salary
- **Total Pension Fund:** 18% of basic salary

## 🔧 Installation and Setup

### Prerequisites
- Python 3.7+
- Flask web framework
- All existing accounting software dependencies

### Files Added/Modified

#### New Ethiopian Payroll Files:
```
models/
├── ethiopian_payroll.py          # Core payroll calculations
core/
├── ethiopian_payroll_integration.py  # Accounting integration
web/
├── payroll_routes.py             # Flask routes for payroll
├── templates/payroll/            # Payroll web templates
    ├── dashboard.html
    ├── employees.html
    ├── calculate.html
    └── ...
ethiopian_payroll_demo.py         # Standalone payroll demo
```

#### Modified Files:
```
demo.py                           # Added payroll demonstration
web/app.py                       # Integrated payroll routes
web/templates/base.html          # Added payroll navigation
web/templates/dashboard.html     # Added payroll dashboard link
```

## 🚀 Quick Start

### 1. Run the Comprehensive Demo
```bash
python ethiopian_payroll_demo.py
```
This demonstrates:
- Tax calculations for various salary levels
- Multiple employee payroll processing
- Journal entry generation
- Pay slip creation
- Payroll reports

### 2. Web Interface
```bash
python web\app.py
```
Then navigate to: http://localhost:5000

### 3. Integrated Demo
```bash
python demo.py
```
Shows payroll integration within the main accounting system.

## 📖 Usage Guide

### Adding Employees
1. Navigate to **Ethiopian Payroll > Manage Employees**
2. Click **Add New Employee**
3. Fill in employee details:
   - Employee ID (unique)
   - Name and position
   - Basic salary (ETB per month)
   - Department and category
   - Tax and pension numbers

### Processing Monthly Payroll
1. Go to **Ethiopian Payroll > Calculate Payroll**
2. Select pay period dates
3. Review employees to be processed
4. Click **Process Payroll**
5. System automatically:
   - Calculates taxes and deductions
   - Generates journal entries
   - Updates accounting balances
   - Creates payroll reports

### Generating Pay Slips
1. From **Manage Employees**, click the pay slip icon
2. System generates detailed pay slip showing:
   - Earnings breakdown
   - Tax calculations
   - Pension contributions
   - Net salary
   - Employer costs

### Using the Tax Calculator
1. Navigate to **Ethiopian Payroll > Tax Calculator**
2. Enter salary amount
3. Get instant tax calculation with:
   - Tax amount by bracket
   - Effective tax rate
   - Net income after tax

## 🏦 Accounting Integration

### Journal Entries Created
The payroll system automatically creates these journal entries:

#### 1. Salary Expense Recognition
```
Dr. Basic Salary Expense         XX,XXX.XX
Dr. Allowances Expense           X,XXX.XX
Dr. Employer Pension Expense     X,XXX.XX
    Cr. Salaries Payable                     XX,XXX.XX
    Cr. Income Tax Withheld Payable          X,XXX.XX
    Cr. Employee Pension Payable            X,XXX.XX
    Cr. Employer Pension Payable            X,XXX.XX
```

#### 2. Salary Payment to Employees
```
Dr. Salaries Payable             XX,XXX.XX
    Cr. Cash                                 XX,XXX.XX
```

#### 3. Tax Remittance to Government
```
Dr. Income Tax Withheld Payable  X,XXX.XX
    Cr. Cash                                 X,XXX.XX
```

#### 4. Pension Fund Remittance
```
Dr. Employee Pension Payable     X,XXX.XX
Dr. Employer Pension Payable     X,XXX.XX
    Cr. Cash                                 XX,XXX.XX
```

### Chart of Accounts Extensions
New accounts automatically created:
- **6001:** Basic Salary Expense
- **6002:** Allowances Expense
- **6003:** Employer Pension Expense
- **2200:** Salaries Payable
- **2210:** Income Tax Withheld Payable
- **2220:** Employee Pension Payable
- **2230:** Employer Pension Payable

## 📊 Example Calculations

### Sample Employee: 15,000 ETB Basic Salary
```
Basic Salary:           15,000.00 ETB
Housing Allowance:       3,000.00 ETB (taxable)
Transport Allowance:       600.00 ETB (non-taxable)
--------------------------------------------
Gross Pay:             18,600.00 ETB
Taxable Income:        18,000.00 ETB

Employee Pension (7%):  1,050.00 ETB
Taxable after Pension: 16,950.00 ETB

Income Tax Calculation:
- First 600 @ 0%:          0.00 ETB
- Next 1,050 @ 10%:      105.00 ETB
- Next 1,550 @ 15%:      232.50 ETB
- Next 2,050 @ 20%:      410.00 ETB
- Next 2,550 @ 25%:      637.50 ETB
- Next 3,100 @ 30%:      930.00 ETB
- Remaining 6,050 @ 35%: 2,117.50 ETB
Total Income Tax:       4,432.50 ETB

Net Salary:            13,117.50 ETB
Employer Pension (11%): 1,650.00 ETB
Total Employer Cost:   20,250.00 ETB
```

## 🔧 Customization

### Adding New Allowance Types
Edit `models/ethiopian_payroll.py`:
```python
class AllowanceType(Enum):
    # Add new allowance types here
    EDUCATION = "Education Allowance"
    MEDICAL = "Medical Allowance"
```

### Modifying Tax Brackets
Update `EthiopianPayrollCalculator.INCOME_TAX_BRACKETS` in `models/ethiopian_payroll.py`

### Adding Custom Deductions
Use the `add_deduction()` method in payroll processing

## 🐛 Troubleshooting

### Common Issues

#### 1. "Employee ID already exists"
- Each employee must have a unique ID
- Use format like "EMP001", "EMP002", etc.

#### 2. Negative cash balance after payroll
- Ensure sufficient cash balance before processing payroll
- Add cash receipts or owner investment entries

#### 3. Payroll routes not found
- Ensure `payroll_routes.py` is in the web directory
- Check import statements in `web/app.py`

#### 4. Tax calculation errors
- Verify salary amounts are positive
- Check that pension contributions don't exceed salary

### Debug Mode
Run with debug information:
```python
# In demo or integration code
try:
    result = payroll_integration.process_monthly_payroll(employees, start, end)
    print(f"Debug: {result}")
except Exception as e:
    import traceback
    traceback.print_exc()
```

## 📚 API Reference

### Key Classes

#### `Employee`
Represents an employee with salary and personal information.

#### `EthiopianPayrollCalculator`
Core calculation engine for taxes and pension contributions.

#### `PayrollItem`
Individual payroll calculation for one employee for one period.

#### `EthiopianPayrollIntegration`
Integrates payroll with the accounting system.

### Key Methods

#### `calculate_payroll_item(payroll_item)`
Calculates complete payroll for one employee.

#### `process_monthly_payroll(employees, start_date, end_date)`
Processes payroll for multiple employees and creates journal entries.

#### `calculate_income_tax(taxable_income)`
Calculates Ethiopian progressive income tax.

#### `generate_pay_slip(payroll_item)`
Generates detailed pay slip information.

## 🤝 Contributing

### Areas for Enhancement
1. **Database Integration** - Replace in-memory storage
2. **Employee History** - Track salary changes over time
3. **Advanced Reporting** - Tax authority reports, annual summaries
4. **Multiple Currencies** - Support for USD alongside ETB
5. **Mobile Interface** - Responsive design improvements
6. **Backup/Restore** - Employee data backup functionality

### Development Guidelines
1. Follow existing code structure and naming conventions
2. Add unit tests for new functionality
3. Update documentation for any API changes
4. Test with various salary ranges and edge cases

## 📋 Compliance Notes

This payroll system is designed based on Ethiopian tax law as of 2026. For production use:

1. **Verify Current Tax Rates** - Tax brackets may change annually
2. **Legal Compliance** - Consult with Ethiopian tax authorities
3. **Audit Trail** - All transactions are logged in the accounting system
4. **Data Security** - Implement appropriate security measures for employee data
5. **Backup Strategy** - Regular backups of payroll and employee data

## 📞 Support

For questions about the Ethiopian payroll integration:

1. Review this documentation
2. Run the demo files to understand functionality
3. Check the source code comments for detailed implementation notes
4. Test with sample data before processing real payroll

---

**🎉 Congratulations!** You now have a fully integrated Ethiopian payroll system as part of your accounting software. The system handles complex tax calculations, pension contributions, and seamlessly integrates with your double-entry bookkeeping system.