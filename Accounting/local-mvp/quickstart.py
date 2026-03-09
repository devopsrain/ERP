#!/usr/bin/env python3
"""
Quick Start Guide for Ethiopian Business Management System
Run this script to get started with the system in minutes
"""

import subprocess
import sys
import os
from pathlib import Path

def print_header(title):
    """Print a formatted header"""
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}\n")

def print_step(step_num, title, description):
    """Print a formatted step"""
    print(f"🔹 Step {step_num}: {title}")
    print(f"   {description}\n")

def check_python_version():
    """Check if Python version is adequate"""
    version = sys.version_info
    if version.major < 3 or (version.major == 3 and version.minor < 8):
        print("❌ Python 3.8 or higher is required")
        print(f"   Current version: {version.major}.{version.minor}.{version.micro}")
        return False
    else:
        print(f"✅ Python version: {version.major}.{version.minor}.{version.micro}")
        return True

def install_requirements():
    """Install required packages"""
    try:
        print("📦 Installing requirements...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"], 
                      check=True, capture_output=True)
        print("✅ Requirements installed successfully")
        return True
    except subprocess.CalledProcessError as e:
        print(f"❌ Failed to install requirements: {e}")
        print(f"   Try manually: pip install -r requirements.txt")
        return False

def initialize_system():
    """Initialize the system with sample data"""
    try:
        print("🚀 Initializing system...")
        result = subprocess.run([sys.executable, "run.py", "--initialize"], 
                               capture_output=True, text=True)
        if result.returncode == 0:
            print("✅ System initialized with sample data")
            return True
        else:
            print(f"❌ Initialization failed: {result.stderr}")
            return False
    except Exception as e:
        print(f"❌ Initialization error: {e}")
        return False

def run_demo():
    """Run the system demonstration"""
    try:
        print("🎯 Running system demonstration...")
        subprocess.run([sys.executable, "run.py", "--demo"])
        return True
    except Exception as e:
        print(f"❌ Demo error: {e}")
        return False

def main():
    """Main setup and demo script"""
    
    print_header("🇪🇹 Ethiopian Business Management System - Quick Start")
    
    print("This script will set up and demonstrate the complete system:")
    print("  • Parquet-based high-performance storage")
    print("  • Ethiopian VAT compliance (15% standard, 0% zero-rated, 2% withholding)")
    print("  • Progressive income tax and pension calculations")
    print("  • Double-entry accounting with trial balance")
    print("  • Web interface and CLI access")
    print("  • Complete data export capabilities")
    
    print_step(1, "Check Python Version", "Verifying Python 3.8+ is installed")
    if not check_python_version():
        return 1
    
    print_step(2, "Install Dependencies", "Installing required Python packages")
    if not install_requirements():
        print("   You can try installing manually with:")
        print("   pip install pandas pyarrow flask click matplotlib")
        return 1
    
    print_step(3, "Initialize System", "Creating sample data and accounts")
    if not initialize_system():
        return 1
    
    print_step(4, "System Demonstration", "Running comprehensive feature demo")
    if not run_demo():
        return 1
    
    print_header("🎉 Setup Complete! Your System is Ready")
    
    print("🌟 Available Interfaces:")
    print("   • Web Interface:     python run.py")
    print("   • CLI Interface:     python run.py --cli")
    print("   • System Info:       python run.py --info")
    print("   • Full Demo:         python run.py --demo")
    
    print("\n📁 Data Location:")
    print("   • Parquet Files:     ./data/")
    print("   • Exports:           ./exports/")
    print("   • Reports:           ./reports/")
    print("   • Backups:           ./backups/")
    
    print("\n💼 Sample Business Data Created:")
    print("   • 25+ Chart of Accounts (Ethiopian standards)")
    print("   • 3 Sample Employees with realistic salaries")
    print("   • VAT Income/Expense records")
    print("   • Journal entries showing basic transactions")
    print("   • Payroll records with Ethiopian tax calculations")
    
    print("\n🚀 Next Steps:")
    print("   1. Start web interface: python run.py")
    print("   2. Open browser to: http://localhost:5000")
    print("   3. Explore the dashboard and all modules")
    print("   4. Try the CLI: python run.py --cli")
    print("   5. Check exports folder for sample reports")
    
    print("\n📚 Key Features Demonstrated:")
    print("   ✓ Double-entry accounting (automatic balance validation)")
    print("   ✓ Ethiopian VAT Portal (income, expenses, capital transactions)")
    print("   ✓ Payroll system (progressive tax, pension contributions)")
    print("   ✓ Trial balance and income statement generation")
    print("   ✓ High-performance Parquet storage")
    print("   ✓ Multi-format data export (Excel, CSV, JSON)")
    print("   ✓ Comprehensive audit trail")
    
    print(f"\n{'='*60}")
    print("Ethiopian Business Management System is now ready for use! 🇪🇹")
    print(f"{'='*60}")
    
    return 0

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n🛑 Setup interrupted by user. Run again to complete setup.")
        sys.exit(1)
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)