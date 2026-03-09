"""
Ethiopian Payroll Demo 

This demo shows how to use the Ethiopian payroll system integrated with the
accounting software. It creates sample employees, calculates payroll, and
generates the appropriate accounting entries.
"""

import sys
from datetime import datetime, date, timedelta
from pathlib import Path

# Add the current directory to path for imports
sys.path.append(str(Path(__file__).parent))

from models.ethiopian_payroll import (
    Employee, EmployeeCategory, EthiopianPayrollCalculator,
    PayrollItem, AllowanceType, DeductionType
)
from core.ethiopian_payroll_integration import EthiopianPayrollIntegration
from core.ledger import GeneralLedger
import json


def create_sample_employees() -> list[Employee]:
    """Create sample employees with different salary levels and categories"""
    
    employees = [
        Employee(
            employee_id="EMP001",
            name="Almaz Tesfaye",
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            basic_salary=12000,  # ETB per month
            hire_date=date(2023, 1, 15),
            department="Finance",
            position="Accountant",
            tin_number="TIN001234567",
            pension_number="PN001234567"
        ),
        Employee(
            employee_id="EMP002", 
            name="Bekele Mengistu",
            category=EmployeeCategory.EXECUTIVE,
            basic_salary=25000,  # ETB per month
            hire_date=date(2022, 3, 1),
            department="Management",
            position="General Manager",
            tin_number="TIN002345678", 
            pension_number="PN002345678"
        ),
        Employee(
            employee_id="EMP003",
            name="Chaltu Abera",
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            basic_salary=8500,   # ETB per month
            hire_date=date(2023, 6, 10),
            department="Operations",
            position="Operations Assistant",
            tin_number="TIN003456789",
            pension_number="PN003456789"
        ),
        Employee(
            employee_id="EMP004",
            name="Daniel Wolde",
            category=EmployeeCategory.CONTRACT_EMPLOYEE,
            basic_salary=15000,  # ETB per month
            hire_date=date(2024, 1, 1),
            department="IT",
            position="Software Developer",
            tin_number="TIN004567890",
            pension_number="PN004567890"
        ),
        Employee(
            employee_id="EMP005",
            name="Eyerusalem Haile",
            category=EmployeeCategory.REGULAR_EMPLOYEE,
            basic_salary=6500,   # ETB per month
            hire_date=date(2024, 2, 1),
            department="Sales",
            position="Sales Representative",
            tin_number="TIN005678901",
            pension_number="PN005678901"
        )
    ]
    
    return employees


def demonstrate_payroll_calculations():
    """Demonstrate basic payroll calculations"""
    
    print("="*70)
    print("         ETHIOPIAN PAYROLL SYSTEM - CALCULATIONS DEMO")
    print("="*70)
    
    calculator = EthiopianPayrollCalculator()
    
    # Create a sample payroll item
    employee = Employee(
        employee_id="DEMO001",
        name="Sample Employee",
        category=EmployeeCategory.REGULAR_EMPLOYEE,
        basic_salary=10000,
        hire_date=date(2023, 1, 1),
        department="Demo",
        position="Demo Position"
    )
    
    pay_period_start = date(2026, 2, 1)
    pay_period_end = date(2026, 2, 28)
    
    payroll_item = PayrollItem(
        employee=employee,
        pay_period_start=pay_period_start,
        pay_period_end=pay_period_end
    )
    
    # Add some allowances
    payroll_item.add_allowance(AllowanceType.TRANSPORT, 500, False, "Monthly transport")
    payroll_item.add_allowance(AllowanceType.HOUSING, 2000, True, "Housing allowance")
    payroll_item.add_allowance(AllowanceType.OVERTIME, 800, True, "Overtime hours")
    
    # Add custom deductions
    payroll_item.add_deduction(DeductionType.UNION_DUES, 50, "Monthly union dues")
    
    # Calculate payroll
    calculated_item = calculator.calculate_payroll_item(payroll_item)
    
    # Display calculation breakdown
    print(f"\n👤 EMPLOYEE: {employee.name} ({employee.employee_id})")
    print(f"   Basic Salary: {calculated_item.basic_salary:,.2f} ETB")
    
    print(f"\n💰 EARNINGS:")
    print(f"   Basic Salary:     {calculated_item.basic_salary:>12,.2f} ETB")
    print(f"   Allowances:       {calculated_item.total_allowances:>12,.2f} ETB")
    print(f"   - Taxable:        {calculated_item.taxable_allowances:>12,.2f} ETB")
    print(f"   - Non-taxable:    {calculated_item.non_taxable_allowances:>12,.2f} ETB")
    print(f"   {'Gross Pay:':<17} {calculated_item.basic_salary + calculated_item.total_allowances:>12,.2f} ETB")
    
    print(f"\n📉 DEDUCTIONS:")
    print(f"   Income Tax:       {calculated_item.income_tax:>12,.2f} ETB")
    print(f"   Employee Pension: {calculated_item.employee_pension:>12,.2f} ETB")
    other_deductions = calculated_item.total_deductions - calculated_item.income_tax - calculated_item.employee_pension
    if other_deductions > 0:
        print(f"   Other Deductions: {other_deductions:>12,.2f} ETB")
    print(f"   {'Total Deductions:':<17} {calculated_item.total_deductions:>12,.2f} ETB")
    
    print(f"\n📊 SUMMARY:")
    print(f"   Net Salary:       {calculated_item.net_salary:>12,.2f} ETB")
    print(f"   Employer Pension: {calculated_item.employer_pension:>12,.2f} ETB")
    print(f"   Total Cost:       {calculated_item.total_employer_cost:>12,.2f} ETB")
    
    # Show tax bracket calculation 
    taxable_income = calculated_item.gross_taxable_income - calculated_item.employee_pension
    print(f"\n🏛️ TAX CALCULATION:")
    print(f"   Gross Taxable Income:     {calculated_item.gross_taxable_income:,.2f} ETB")
    print(f"   Less: Employee Pension:   {calculated_item.employee_pension:,.2f} ETB")
    print(f"   Taxable After Pension:    {taxable_income:,.2f} ETB")
    print(f"   Income Tax:               {calculated_item.income_tax:,.2f} ETB")
    print(f"   Effective Tax Rate:       {(calculated_item.income_tax/taxable_income)*100 if taxable_income > 0 else 0:.2f}%")
    
    return calculated_item


def demonstrate_integrated_payroll():
    """Demonstrate payroll integration with accounting system"""
    
    print("\n" + "="*70)
    print("       ETHIOPIAN PAYROLL - ACCOUNTING INTEGRATION DEMO")
    print("="*70)
    
    # Create ledger and setup accounts
    ledger = GeneralLedger()
    ledger.company_name = "Ethiopian Demo Company PLC"
    
    # Create payroll integration
    payroll_integration = EthiopianPayrollIntegration(ledger)
    
    print(f"\n1. Setting up Chart of Accounts...")
    ledger.create_standard_chart_of_accounts()  # Create base accounts
    print(f"   ✓ Created {len(ledger.accounts)} accounts (including payroll accounts)")
    
    # Create sample employees
    employees = create_sample_employees()
    print(f"\n2. Created {len(employees)} sample employees:")
    for emp in employees:
        print(f"   • {emp.name} ({emp.employee_id}) - {emp.position} - {emp.basic_salary:,.0f} ETB/month")
    
    # Process monthly payroll
    print(f"\n3. Processing February 2026 Payroll...")
    pay_period_start = date(2026, 2, 1)
    pay_period_end = date(2026, 2, 28)
    payment_date = datetime(2026, 2, 28, 17, 0)  # Pay at end of month
    
    # Add some allowances for variety
    employees[0].basic_salary = 12000  # Ensure we have the salary set
    employees[1].basic_salary = 25000
    employees[2].basic_salary = 8500
    employees[3].basic_salary = 15000
    employees[4].basic_salary = 6500
    
    # Process payroll
    result = payroll_integration.process_monthly_payroll(
        employees, pay_period_start, pay_period_end, payment_date
    )
    
    payroll_summary = result['payroll_summary']
    journal_entries = result['journal_entries']
    
    print(f"   ✓ Processed payroll for {payroll_summary['total_employees']} employees")
    print(f"   ✓ Created {len(journal_entries)} journal entries")
    
    # Display payroll summary
    print(f"\n📊 PAYROLL SUMMARY:")
    totals = payroll_summary['totals']
    print(f"   Basic Salaries:     {totals['basic_salary']:>15,.2f} ETB")
    print(f"   Total Allowances:   {totals['allowances']:>15,.2f} ETB")
    print(f"   Gross Payroll:      {totals['gross_pay']:>15,.2f} ETB")
    print(f"   Total Deductions:   {totals['deductions']:>15,.2f} ETB")
    print(f"   Net Payroll:        {totals['net_pay']:>15,.2f} ETB")
    print(f"   Income Tax:         {totals['income_tax']:>15,.2f} ETB")
    print(f"   Employee Pension:   {totals['employee_pension']:>15,.2f} ETB")
    print(f"   Employer Pension:   {totals['employer_pension']:>15,.2f} ETB")
    print(f"   Total Employer Cost:{totals['total_employer_cost']:>15,.2f} ETB")
    
    # Show individual employee details
    print(f"\n📋 INDIVIDUAL PAYROLL DETAILS:")
    payroll_items = result['payroll_items']
    print(f"{'Employee':<20} {'Basic':<10} {'Gross':<10} {'Tax':<8} {'Pension':<8} {'Net':<10}")
    print("-" * 70)
    for item in payroll_items:
        print(f"{item.employee.name:<20} {item.basic_salary:>9,.0f} "
              f"{item.basic_salary + item.total_allowances:>9,.0f} "
              f"{item.income_tax:>7,.0f} {item.employee_pension:>7,.0f} "
              f"{item.net_salary:>9,.0f}")
    
    # Show journal entries created
    print(f"\n📝 JOURNAL ENTRIES CREATED:")
    for i, entry in enumerate(journal_entries, 1):
        print(f"   {i}. {entry.description}")
        print(f"      Entry ID: {entry.entry_id}")
        print(f"      Date: {entry.date}")
        print(f"      Lines: {len(entry.lines)}")
        
        # Show entry details
        for line in entry.lines:
            account = ledger.get_account(line.account_id)
            account_name = account.name if account else "Unknown Account"
            if line.debit_amount:
                print(f"        Dr. {line.account_id} - {account_name}: {line.debit_amount:,.2f} ETB")
            if line.credit_amount:
                print(f"        Cr. {line.account_id} - {account_name}: {line.credit_amount:,.2f} ETB")
        print()
    
    return ledger, payroll_integration, result


def demonstrate_payroll_reports(ledger, payroll_integration):
    """Demonstrate payroll reporting"""
    
    print("="*70)
    print("              PAYROLL REPORTS DEMO")
    print("="*70)
    
    # Generate trial balance to show payroll account balances
    print("\n📊 TRIAL BALANCE (Payroll Accounts Only):")
    print("-" * 60)
    trial_balance = ledger.get_trial_balance()
    
    payroll_account_prefixes = ['6000', '6001', '6002', '6003', '2200', '2210', '2220', '2230']
    
    for account_id, balance in trial_balance.items():
        if any(account_id.startswith(prefix) for prefix in payroll_account_prefixes):
            account = ledger.get_account(account_id)
            if account and balance != 0:
                balance_type = "DR" if balance >= 0 else "CR"
                print(f"{account_id:<6} {account.name:<30} {abs(balance):>12,.2f} {balance_type}")
    
    # Generate payroll report
    report_start = date(2026, 2, 1)
    report_end = date(2026, 2, 28)
    
    payroll_report = payroll_integration.get_payroll_reports(report_start, report_end)
    
    print(f"\n📈 PAYROLL EXPENSE REPORT:")
    print(f"   Period: {payroll_report['period']}")
    print("-" * 60)
    summary = payroll_report['summary']
    print(f"   Total Salary Expense:   {summary['total_salary_expense']:>15,.2f} ETB")
    print(f"   Total Tax Withheld:     {summary['total_tax_withheld']:>15,.2f} ETB")
    print(f"   Employee Pension:       {summary['total_employee_pension']:>15,.2f} ETB") 
    print(f"   Employer Pension:       {summary['total_employer_pension']:>15,.2f} ETB")
    print(f"   Total Payroll Cost:     {summary['total_payroll_cost']:>15,.2f} ETB")
    print(f"   Journal Entries:        {payroll_report['journal_entries_count']:>15} entries")


def demonstrate_pay_slip():
    """Demonstrate pay slip generation"""
    
    print("\n" + "="*70)
    print("                  PAY SLIP DEMO")
    print("="*70)
    
    calculator = EthiopianPayrollCalculator()
    
    # Create an employee with various allowances and deductions
    employee = Employee(
        employee_id="EMP999",
        name="Demo Employee",
        category=EmployeeCategory.REGULAR_EMPLOYEE,
        basic_salary=18000,
        hire_date=date(2023, 1, 1),
        department="Administration",
        position="Office Manager"
    )
    
    # Create payroll item
    payroll_item = PayrollItem(
        employee=employee,
        pay_period_start=date(2026, 2, 1),
        pay_period_end=date(2026, 2, 28)
    )
    
    # Add allowances
    payroll_item.add_allowance(AllowanceType.TRANSPORT, 600, False, "Transport allowance")
    payroll_item.add_allowance(AllowanceType.HOUSING, 3000, True, "Housing allowance")
    payroll_item.add_allowance(AllowanceType.MEAL, 800, True, "Meal allowance")
    payroll_item.add_allowance(AllowanceType.PHONE, 200, True, "Phone allowance")
    payroll_item.add_allowance(AllowanceType.OVERTIME, 1200, True, "Overtime payment")
    
    # Add deductions
    payroll_item.add_deduction(DeductionType.UNION_DUES, 75, "Union membership")
    payroll_item.add_deduction(DeductionType.LOAN_REPAYMENT, 500, "Personal loan")
    
    # Calculate
    calculated_item = calculator.calculate_payroll_item(payroll_item)
    
    # Generate pay slip
    pay_slip = calculator.generate_pay_slip(calculated_item)
    
    # Display pay slip
    print(f"\n" + "="*60)
    print(f"                    PAY SLIP")
    print(f"              Demo Company PLC")
    print(f"="*60)
    
    emp_info = pay_slip['employee_info']
    print(f"Employee ID:     {emp_info['employee_id']}")
    print(f"Employee Name:   {emp_info['name']}")
    print(f"Department:      {emp_info['department']}")
    print(f"Position:        {emp_info['position']}")
    print(f"Pay Period:      {emp_info['pay_period']}")
    print(f"="*60)
    
    earnings = pay_slip['earnings']
    print(f"EARNINGS:")
    print(f"  Basic Salary:                 {earnings['basic_salary']:>12,.2f} ETB")
    
    for allowance in earnings['allowances']:
        taxable_indicator = "" if allowance['taxable'] else " (Non-taxable)"
        print(f"  {allowance['type']:<25}{taxable_indicator}: {allowance['amount']:>12,.2f} ETB")
    
    print(f"  {'-'*40}")
    print(f"  Gross Pay:                    {earnings['gross_pay']:>12,.2f} ETB")
    
    deductions = pay_slip['deductions']
    print(f"\nDEDUCTIONS:")
    print(f"  Income Tax:                   {deductions['income_tax']:>12,.2f} ETB")
    print(f"  Employee Pension (7%):        {deductions['employee_pension']:>12,.2f} ETB")
    
    for deduction in deductions['other_deductions']:
        print(f"  {deduction['type']:<25}: {deduction['amount']:>12,.2f} ETB")
    
    print(f"  {'-'*40}")
    print(f"  Total Deductions:             {deductions['total_deductions']:>12,.2f} ETB")
    
    summary = pay_slip['summary']
    print(f"\nSUMMARY:")
    print(f"  Gross Taxable Income:         {summary['gross_taxable_income']:>12,.2f} ETB")
    print(f"  NET SALARY:                   {summary['net_salary']:>12,.2f} ETB")
    print(f"\nEMPLOYER COSTS:")
    print(f"  Employer Pension (11%):       {summary['employer_pension']:>12,.2f} ETB")
    print(f"  Total Employer Cost:          {summary['total_employer_cost']:>12,.2f} ETB")
    print(f"="*60)


def main():
    """Main demo function"""
    
    try:
        print("🇪🇹 ETHIOPIAN PAYROLL SYSTEM DEMONSTRATION")
        print("This demo shows Ethiopian salary and pension calculations")
        print("integrated with the accounting system.\n")
        
        # 1. Basic payroll calculations
        demonstrate_payroll_calculations()
        
        # 2. Integrated payroll with accounting  
        ledger, payroll_integration, payroll_result = demonstrate_integrated_payroll()
        
        # 3. Payroll reports
        demonstrate_payroll_reports(ledger, payroll_integration)
        
        # 4. Pay slip generation
        demonstrate_pay_slip()
        
        print(f"\n" + "="*70)
        print("🎉 ETHIOPIAN PAYROLL DEMO COMPLETED SUCCESSFULLY!")
        print("="*70)
        
        print(f"\n📚 FEATURES DEMONSTRATED:")
        print(f"   ✓ Ethiopian Tax Calculation (Progressive rates)")
        print(f"   ✓ Pension Contributions (7% employee, 11% employer)")
        print(f"   ✓ Allowances (Taxable and Non-taxable)")
        print(f"   ✓ Multiple Employee Categories")
        print(f"   ✓ Complete Accounting Integration") 
        print(f"   ✓ Journal Entry Generation")
        print(f"   ✓ Pay Slip Generation")
        print(f"   ✓ Payroll Reports")
        
        print(f"\n🔧 TAX BRACKETS USED (2026 Ethiopian Tax Law):")
        print(f"   • 0 - 600 ETB: 0%")
        print(f"   • 601 - 1,650 ETB: 10%")
        print(f"   • 1,651 - 3,200 ETB: 15%")
        print(f"   • 3,201 - 5,250 ETB: 20%")
        print(f"   • 5,251 - 7,800 ETB: 25%")
        print(f"   • 7,801 - 10,900 ETB: 30%")
        print(f"   • Above 10,900 ETB: 35%")
        
        print(f"\n🏛️ PENSION CONTRIBUTIONS:")
        print(f"   • Employee: 7% of basic salary")
        print(f"   • Employer: 11% of basic salary")
        print(f"   • Total: 18% of basic salary")
        
        print(f"\n📄 NEXT STEPS:")
        print(f"   • Integrate with main run.py application")
        print(f"   • Add web interface for payroll management")
        print(f"   • Add employee database management")
        print(f"   • Add payroll history tracking")
        print(f"   • Add tax authority reporting features")
        
    except Exception as e:
        print(f"\n❌ Error during payroll demo: {e}")
        import traceback
        traceback.print_exc()


if __name__ == "__main__":
    main()