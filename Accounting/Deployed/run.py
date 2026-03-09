#!/usr/bin/env python3
"""
Main launcher for the Basic Accounting Software

This script provides options to run either the CLI or web interface.
"""
import sys
import os
from pathlib import Path

def display_menu():
    """Display the main launcher menu"""
    print("\n" + "="*60)
    print("           BASIC ACCOUNTING SOFTWARE")
    print("="*60)
    print("Choose your interface:")
    print("1. Command Line Interface (CLI)")
    print("2. Web Interface (Browser)")
    print("3. Exit")
    print("="*60)

def run_cli():
    """Run the command line interface"""
    print("\nStarting Command Line Interface...")
    try:
        from cli.main import AccountingCLI
        cli = AccountingCLI()
        cli.run()
    except ImportError as e:
        print(f"Error importing CLI: {e}")
        print("Make sure all dependencies are installed.")
    except KeyboardInterrupt:
        print("\nGoodbye!")

def run_web():
    """Run the web interface"""
    print("\nStarting Web Interface...")
    print("The application will open in your browser at: http://localhost:5000")
    print("Press Ctrl+C to stop the server.")
    
    try:
        from web.app import app
        app.run(debug=False, host='localhost', port=5000)
    except ImportError as e:
        print(f"Error importing web app: {e}")
        print("Make sure Flask is installed: pip install Flask")
    except KeyboardInterrupt:
        print("\nServer stopped. Goodbye!")

def check_dependencies():
    """Check if required dependencies are available"""
    try:
        import flask
        return True
    except ImportError:
        print("\nMissing dependencies detected!")
        print("Please install required packages:")
        print("\n  pip install -r requirements.txt")
        print("\nOr install Flask manually:")
        print("  pip install Flask")
        return False

def main():
    """Main launcher function"""
    print("Welcome to Basic Accounting Software!")
    print("This software implements double-entry bookkeeping for small businesses.")
    
    # Check if we're in the right directory
    if not Path("models").exists() or not Path("cli").exists():
        print("\nError: Please run this script from the accounting_software directory.")
        print("Current directory:", os.getcwd())
        return
    
    while True:
        try:
            display_menu()
            choice = input("\nEnter your choice (1-3): ").strip()
            
            if choice == '1':
                run_cli()
            elif choice == '2':
                if check_dependencies():
                    run_web()
            elif choice == '3':
                print("Thank you for using Basic Accounting Software!")
                break
            else:
                print("Invalid choice! Please select 1, 2, or 3.")
                
        except KeyboardInterrupt:
            print("\n\nGoodbye!")
            break
        except Exception as e:
            print(f"\nAn error occurred: {e}")
            input("Press Enter to continue...")

if __name__ == "__main__":
    main()