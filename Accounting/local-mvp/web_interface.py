"""
Flask web interface for Ethiopian Business Management System
Provides web-based access to all system features
"""

from flask import Flask, render_template, request, redirect, url_for, flash, jsonify, send_file
from flask_wtf import FlaskForm
from wtforms import StringField, FloatField, DateField, SelectField, TextAreaField, IntegerField
from wtforms.validators import DataRequired, NumberRange
from datetime import datetime, date
import pandas as pd
import io
import base64
import matplotlib.pyplot as plt
import seaborn as sns
from pathlib import Path

from business_logic import EthiopianBusinessManager
from data_store import data_store
from config import Config, VAT_RATES, EXPORTS_DIR

# Initialize Flask app
app = Flask(__name__)
app.config.from_object(Config)

# Initialize business manager
business_manager = EthiopianBusinessManager()

# WTForms for data input
class JournalEntryForm(FlaskForm):
    date = DateField('Transaction Date', validators=[DataRequired()], default=date.today)
    description = TextAreaField('Description', validators=[DataRequired()])
    account_code = StringField('Account Code', validators=[DataRequired()])
    debit = FloatField('Debit Amount', validators=[NumberRange(min=0)], default=0.0)
    credit = FloatField('Credit Amount', validators=[NumberRange(min=0)], default=0.0)

class VATIncomeForm(FlaskForm):
    date = DateField('Transaction Date', validators=[DataRequired()], default=date.today)
    contract_date = DateField('Contract Date', validators=[DataRequired()], default=date.today)
    description = TextAreaField('Description', validators=[DataRequired()])
    customer_name = StringField('Customer Name', validators=[DataRequired()])
    invoice_number = StringField('Invoice Number', validators=[DataRequired()])
    gross_amount = FloatField('Gross Amount (ETB)', validators=[DataRequired(), NumberRange(min=0)])
    vat_rate = SelectField('VAT Rate', choices=[
        ('standard', 'Standard (15%)'),
        ('zero_rated', 'Zero-rated (0%)'),
        ('withholding', 'Withholding (2%)')
    ], default='standard')

class VATExpenseForm(FlaskForm):
    date = DateField('Transaction Date', validators=[DataRequired()], default=date.today)
    description = TextAreaField('Description', validators=[DataRequired()])
    supplier_name = StringField('Supplier Name', validators=[DataRequired()])
    invoice_number = StringField('Invoice Number', validators=[DataRequired()])
    gross_amount = FloatField('Gross Amount (ETB)', validators=[DataRequired(), NumberRange(min=0)])
    expense_category = StringField('Expense Category', validators=[DataRequired()])
    vat_rate = SelectField('VAT Rate', choices=[
        ('standard', 'Standard (15%)'),
        ('zero_rated', 'Zero-rated (0%)'),
        ('withholding', 'Withholding (2%)')
    ], default='standard')

class EmployeeForm(FlaskForm):
    employee_number = StringField('Employee Number', validators=[DataRequired()])
    first_name = StringField('First Name', validators=[DataRequired()])
    last_name = StringField('Last Name', validators=[DataRequired()])
    position = StringField('Position', validators=[DataRequired()])
    department = StringField('Department', validators=[DataRequired()])
    hire_date = DateField('Hire Date', validators=[DataRequired()])
    basic_salary = FloatField('Basic Salary (ETB)', validators=[DataRequired(), NumberRange(min=0)])
    allowances = FloatField('Allowances (ETB)', validators=[NumberRange(min=0)], default=0.0)
    tax_exemption = FloatField('Tax Exemption (ETB)', validators=[NumberRange(min=0)], default=600.0)

@app.route('/')
def dashboard():
    """Main dashboard with system overview"""
    try:
        # Get summary statistics
        stats = data_store.get_table_stats()
        
        # Get recent activities
        recent_journal = data_store.read_table('journal_entries').tail(5)
        recent_vat = data_store.read_table('vat_income').tail(3)
        
        # Get VAT summary
        vat_summary = business_manager.vat.get_vat_summary()
        
        # Get account balances
        trial_balance = business_manager.accounting.generate_trial_balance()
        
        return render_template('dashboard.html',
                             stats=stats,
                             recent_journal=recent_journal,
                             recent_vat=recent_vat,
                             vat_summary=vat_summary,
                             trial_balance=trial_balance)
    except Exception as e:
        flash(f"Error loading dashboard: {e}", 'error')
        return render_template('dashboard.html', stats={}, recent_journal=pd.DataFrame(), 
                             recent_vat=pd.DataFrame(), vat_summary={}, trial_balance=pd.DataFrame())

@app.route('/initialize')
def initialize_system():
    """Initialize system with sample data"""
    try:
        results = business_manager.initialize_system()
        flash(f"System initialized successfully! Created {results['accounts']} accounts, "
              f"{results['sample_data']['employees']} employees, "
              f"{results['sample_data']['vat_records']} VAT records.", 'success')
    except Exception as e:
        flash(f"Initialization failed: {e}", 'error')
    
    return redirect(url_for('dashboard'))

@app.route('/accounting')
def accounting_home():
    """Accounting module home page"""
    try:
        accounts = data_store.query('accounts', company_id='default_company', is_active=True)
        recent_entries = data_store.read_table('journal_entries').tail(10)
        trial_balance = business_manager.accounting.generate_trial_balance()
        
        return render_template('accounting.html',
                             accounts=accounts,
                             recent_entries=recent_entries,
                             trial_balance=trial_balance)
    except Exception as e:
        flash(f"Error loading accounting data: {e}", 'error')
        return render_template('accounting.html', accounts=pd.DataFrame(), 
                             recent_entries=pd.DataFrame(), trial_balance=pd.DataFrame())

@app.route('/accounting/add-entry', methods=['GET', 'POST'])
def add_journal_entry():
    """Add journal entry form"""
    form = JournalEntryForm()
    accounts = data_store.query('accounts', company_id='default_company', is_active=True)
    
    if form.validate_on_submit():
        try:
            entries = []
            
            # Get all entry lines from form data
            for key, value in request.form.items():
                if key.startswith('entries-') and key.endswith('-account_code') and value:
                    index = key.split('-')[1]
                    account_code = value
                    debit = float(request.form.get(f'entries-{index}-debit', 0) or 0)
                    credit = float(request.form.get(f'entries-{index}-credit', 0) or 0)
                    
                    if debit > 0 or credit > 0:
                        entries.append({
                            'account_code': account_code,
                            'debit': debit,
                            'credit': credit
                        })
            
            if not entries:
                flash('Please add at least one entry line', 'error')
                return render_template('add_journal_entry.html', form=form, accounts=accounts)
            
            # Validate balance
            total_debits = sum(entry['debit'] for entry in entries)
            total_credits = sum(entry['credit'] for entry in entries)
            
            if abs(total_debits - total_credits) > 0.01:
                flash(f'Entry does not balance! Debits: {total_debits:,.2f}, Credits: {total_credits:,.2f}', 'error')
                return render_template('add_journal_entry.html', form=form, accounts=accounts)
            
            entry_id = business_manager.accounting.create_journal_entry(
                form.date.data, form.description.data, entries
            )
            
            flash(f'Journal entry created successfully! ID: {entry_id}', 'success')
            return redirect(url_for('accounting_home'))
            
        except Exception as e:
            flash(f'Failed to create journal entry: {e}', 'error')
    
    return render_template('add_journal_entry.html', form=form, accounts=accounts)

@app.route('/accounting/trial-balance')
def trial_balance():
    """Display trial balance report"""
    try:
        tb = business_manager.accounting.generate_trial_balance()
        return render_template('trial_balance.html', trial_balance=tb)
    except Exception as e:
        flash(f"Error generating trial balance: {e}", 'error')
        return render_template('trial_balance.html', trial_balance=pd.DataFrame())

@app.route('/accounting/income-statement')
def income_statement():
    """Display income statement"""
    try:
        income_stmt = business_manager.accounting.generate_income_statement()
        return render_template('income_statement.html', income_statement=income_stmt)
    except Exception as e:
        flash(f"Error generating income statement: {e}", 'error')
        return render_template('income_statement.html', income_statement={})

@app.route('/vat')
def vat_home():
    """VAT module home page"""
    try:
        income_records = data_store.query('vat_income', company_id='default_company')
        expense_records = data_store.query('vat_expenses', company_id='default_company')
        vat_summary = business_manager.vat.get_vat_summary()
        
        return render_template('vat_portal.html',
                             income_records=income_records,
                             expense_records=expense_records,
                             vat_summary=vat_summary)
    except Exception as e:
        flash(f"Error loading VAT data: {e}", 'error')
        return render_template('vat_portal.html', income_records=pd.DataFrame(),
                             expense_records=pd.DataFrame(), vat_summary={})

@app.route('/vat/add-income', methods=['GET', 'POST'])
def add_vat_income():
    """Add VAT income record"""
    form = VATIncomeForm()
    
    if request.method == 'POST':
        if form.validate_on_submit():
            try:
                rate = VAT_RATES[form.vat_rate.data]
                record_id = business_manager.vat.add_income_record(
                    form.date.data,
                    form.contract_date.data,
                    form.description.data,
                    form.customer_name.data,
                    form.invoice_number.data,
                    form.gross_amount.data,
                    rate
                )
                
                vat_amount = form.gross_amount.data * rate
                flash(f'VAT income record created! ID: {record_id}, VAT: {vat_amount:,.2f} ETB', 'success')
                return redirect(url_for('vat_home'))
                
            except Exception as e:
                flash(f'Failed to add VAT income: {str(e)}', 'error')
        else:
            # Form validation failed, show errors
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f'{field}: {error}', 'error')
    
    return render_template('add_vat_income.html', form=form)

@app.route('/vat/add-expense', methods=['GET', 'POST'])
def add_vat_expense():
    """Add VAT expense record"""
    form = VATExpenseForm()
    
    if request.method == 'POST':
        if form.validate_on_submit():
            try:
                rate = VAT_RATES[form.vat_rate.data]
                record_id = business_manager.vat.add_expense_record(
                    form.date.data,
                    form.description.data,
                    form.supplier_name.data,
                    form.invoice_number.data,
                    form.gross_amount.data,
                    rate,
                    form.expense_category.data
                )
                
                vat_amount = form.gross_amount.data * rate
                flash(f'VAT expense record created! ID: {record_id}, VAT: {vat_amount:,.2f} ETB', 'success')
                return redirect(url_for('vat_home'))
                
            except Exception as e:
                flash(f'Failed to add VAT expense: {str(e)}', 'error')
        else:
            # Form validation failed, show errors
            for field, errors in form.errors.items():
                for error in errors:
                    flash(f'{field}: {error}', 'error')
            
        except Exception as e:
            flash(f'Failed to add VAT expense: {e}', 'error')
    
    return render_template('add_vat_expense.html', form=form)

@app.route('/payroll')
def payroll_home():
    """Payroll module home page"""
    try:
        employees = data_store.query('employees', company_id='default_company', is_active=True)
        recent_payroll = data_store.read_table('payroll_records').tail(10)
        
        return render_template('payroll.html',
                             employees=employees,
                             recent_payroll=recent_payroll)
    except Exception as e:
        flash(f"Error loading payroll data: {e}", 'error')
        return render_template('payroll.html', employees=pd.DataFrame(), recent_payroll=pd.DataFrame())

@app.route('/payroll/add-employee', methods=['GET', 'POST'])
def add_employee():
    """Add new employee"""
    form = EmployeeForm()
    
    if form.validate_on_submit():
        try:
            employee_data = {
                'employee_number': form.employee_number.data,
                'first_name': form.first_name.data,
                'last_name': form.last_name.data,
                'position': form.position.data,
                'department': form.department.data,
                'hire_date': form.hire_date.data,
                'basic_salary': form.basic_salary.data,
                'allowances': form.allowances.data,
                'tax_exemption': form.tax_exemption.data,
                'is_active': True,
                'company_id': 'default_company'
            }
            
            employee_id = data_store.insert_record('employees', employee_data)
            flash(f'Employee added successfully! ID: {employee_id}', 'success')
            return redirect(url_for('payroll_home'))
            
        except Exception as e:
            flash(f'Failed to add employee: {e}', 'error')
    
    return render_template('add_employee.html', form=form)

@app.route('/payroll/calculate/<employee_id>')
def calculate_payroll(employee_id):
    """Calculate payroll for specific employee"""
    try:
        current_period = datetime.now().strftime('%Y-%m')
        payroll = business_manager.payroll.calculate_employee_payroll(employee_id, current_period)
        employee = data_store.get_record('employees', employee_id)
        
        return render_template('payroll_result.html', payroll=payroll, employee=employee)
    except Exception as e:
        flash(f"Error calculating payroll: {e}", 'error')
        return redirect(url_for('payroll_home'))

@app.route('/reports')
def reports_home():
    """Reports and analytics home page"""
    try:
        # Generate various reports
        trial_balance = business_manager.accounting.generate_trial_balance()
        income_statement = business_manager.accounting.generate_income_statement()
        vat_summary = business_manager.vat.get_vat_summary()
        
        # Create visualizations
        charts = generate_charts()
        
        return render_template('reports.html',
                             trial_balance=trial_balance,
                             income_statement=income_statement,
                             vat_summary=vat_summary,
                             charts=charts)
    except Exception as e:
        flash(f"Error generating reports: {e}", 'error')
        return render_template('reports.html', trial_balance=pd.DataFrame(),
                             income_statement={}, vat_summary={}, charts={})

@app.route('/export/<table_name>')
def export_data(table_name):
    """Export data to Excel"""
    try:
        if table_name == 'all':
            # Export all data to single Excel file
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"complete_export_{timestamp}.xlsx"
            filepath = EXPORTS_DIR / filename
            
            with pd.ExcelWriter(filepath) as writer:
                for table in data_store.schemas.keys():
                    df = data_store.read_table(table)
                    if not df.empty:
                        df.to_excel(writer, sheet_name=table, index=False)
            
            return send_file(filepath, as_attachment=True, download_name=filename)
        
        else:
            # Export specific table
            df = data_store.read_table(table_name)
            if df.empty:
                flash(f"No data found for {table_name}", 'warning')
                return redirect(url_for('reports_home'))
            
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            filename = f"{table_name}_{timestamp}.xlsx"
            filepath = EXPORTS_DIR / filename
            
            df.to_excel(filepath, index=False)
            return send_file(filepath, as_attachment=True, download_name=filename)
            
    except Exception as e:
        flash(f"Export failed: {e}", 'error')
        return redirect(url_for('reports_home'))

def generate_charts():
    """Generate charts for dashboard and reports"""
    charts = {}
    
    try:
        # VAT Analysis Chart
        vat_income = data_store.read_table('vat_income')
        vat_expenses = data_store.read_table('vat_expenses')
        
        if not vat_income.empty or not vat_expenses.empty:
            plt.figure(figsize=(10, 6))
            
            income_total = vat_income['vat_amount'].sum() if not vat_income.empty else 0
            expense_total = vat_expenses['vat_amount'].sum() if not vat_expenses.empty else 0
            
            categories = ['Output VAT', 'Input VAT']
            values = [income_total, expense_total]
            colors = ['#28a745', '#dc3545']
            
            plt.bar(categories, values, color=colors)
            plt.title('VAT Analysis')
            plt.ylabel('Amount (ETB)')
            
            # Convert plot to base64 string
            img = io.BytesIO()
            plt.savefig(img, format='png', bbox_inches='tight')
            img.seek(0)
            charts['vat_chart'] = base64.b64encode(img.getvalue()).decode()
            plt.close()
        
        # Account Balance Chart
        trial_balance = business_manager.accounting.generate_trial_balance()
        
        if not trial_balance.empty:
            plt.figure(figsize=(12, 6))
            
            # Group by account type
            account_types = trial_balance.groupby('Account Type').agg({
                'Debit': 'sum',
                'Credit': 'sum'
            })
            
            account_types.plot(kind='bar', ax=plt.gca())
            plt.title('Account Balances by Type')
            plt.ylabel('Amount (ETB)')
            plt.xticks(rotation=45)
            
            img = io.BytesIO()
            plt.savefig(img, format='png', bbox_inches='tight')
            img.seek(0)
            charts['balance_chart'] = base64.b64encode(img.getvalue()).decode()
            plt.close()
        
    except Exception as e:
        print(f"Error generating charts: {e}")
    
    return charts

@app.route('/api/stats')
def api_stats():
    """API endpoint for system statistics"""
    try:
        stats = data_store.get_table_stats()
        return jsonify(stats)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/vat-summary')
def api_vat_summary():
    """API endpoint for VAT summary"""
    try:
        summary = business_manager.vat.get_vat_summary()
        return jsonify(summary)
    except Exception as e:
        return jsonify({'error': str(e)}), 500

if __name__ == '__main__':
    # Create templates directory if it doesn't exist
    templates_dir = Path(__file__).parent / 'templates'
    templates_dir.mkdir(exist_ok=True)
    
    app.run(debug=True, host='0.0.0.0', port=5000)