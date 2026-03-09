#!/usr/bin/env python3
"""
AWS Monitoring Script for Ethiopian Business Management System
Monitors application health, performance, and AWS resource utilization
"""

import os
import sys
import time
import json
import requests
import psutil
import boto3
from datetime import datetime, timedelta
from dotenv import load_dotenv

load_dotenv()

class EthiopianBusinessMonitor:
    def __init__(self):
        self.app_url = os.getenv('APP_URL', 'http://localhost:5000')
        self.aws_region = os.getenv('AWS_DEFAULT_REGION', 'af-south-1')
        self.cloudwatch = boto3.client('cloudwatch', region_name=self.aws_region)
        
    def check_application_health(self):
        """Check if the Ethiopian Business Management System is responding"""
        try:
            response = requests.get(f"{self.app_url}/health", timeout=10)
            if response.status_code == 200:
                data = response.json()
                return {
                    'status': 'healthy',
                    'response_time': response.elapsed.total_seconds(),
                    'service': data.get('service', 'Unknown')
                }
            else:
                return {
                    'status': 'unhealthy',
                    'response_code': response.status_code,
                    'response_time': response.elapsed.total_seconds()
                }
        except Exception as e:
            return {
                'status': 'error',
                'error': str(e)
            }
    
    def check_vat_portal(self):
        """Check VAT portal functionality"""
        try:
            response = requests.get(f"{self.app_url}/vat/dashboard", timeout=10)
            return {
                'vat_portal': 'accessible' if response.status_code == 200 else 'error',
                'response_code': response.status_code,
                'response_time': response.elapsed.total_seconds()
            }
        except Exception as e:
            return {
                'vat_portal': 'error',
                'error': str(e)
            }
    
    def check_payroll_system(self):
        """Check payroll system functionality"""
        try:
            response = requests.get(f"{self.app_url}/payroll/dashboard", timeout=10)
            return {
                'payroll_system': 'accessible' if response.status_code == 200 else 'error',
                'response_code': response.status_code,
                'response_time': response.elapsed.total_seconds()
            }
        except Exception as e:
            return {
                'payroll_system': 'error',
                'error': str(e)
            }
    
    def get_system_metrics(self):
        """Get system performance metrics"""
        try:
            cpu_percent = psutil.cpu_percent(interval=1)
            memory = psutil.virtual_memory()
            disk = psutil.disk_usage('/')
            
            return {
                'cpu_usage': cpu_percent,
                'memory_usage': memory.percent,
                'memory_available': memory.available / (1024**3),  # GB
                'disk_usage': disk.percent,
                'disk_free': disk.free / (1024**3),  # GB
                'load_average': os.getloadavg() if hasattr(os, 'getloadavg') else [0, 0, 0]
            }
        except Exception as e:
            return {
                'error': f"Failed to get system metrics: {str(e)}"
            }
    
    def check_database_connectivity(self):
        """Check database connection"""
        try:
            import psycopg2
            database_url = os.getenv('DATABASE_URL')
            if not database_url:
                return {'database': 'error', 'message': 'DATABASE_URL not configured'}
            
            start_time = time.time()
            conn = psycopg2.connect(database_url)
            cursor = conn.cursor()
            cursor.execute('SELECT 1;')
            cursor.fetchone()
            conn.close()
            
            connection_time = time.time() - start_time
            
            return {
                'database': 'connected',
                'connection_time': connection_time
            }
        except Exception as e:
            return {
                'database': 'error',
                'error': str(e)
            }
    
    def send_cloudwatch_metrics(self, metrics):
        """Send custom metrics to CloudWatch"""
        try:
            # Send application health metric
            health_value = 1 if metrics.get('health', {}).get('status') == 'healthy' else 0
            
            self.cloudwatch.put_metric_data(
                Namespace='Ethiopian-Business-MVP',
                MetricData=[
                    {
                        'MetricName': 'ApplicationHealth',
                        'Value': health_value,
                        'Unit': 'None',
                        'Timestamp': datetime.utcnow()
                    },
                    {
                        'MetricName': 'ResponseTime',
                        'Value': metrics.get('health', {}).get('response_time', 0),
                        'Unit': 'Seconds',
                        'Timestamp': datetime.utcnow()
                    }
                ]
            )
            
            # Send system metrics if available
            system_metrics = metrics.get('system', {})
            if 'cpu_usage' in system_metrics:
                self.cloudwatch.put_metric_data(
                    Namespace='Ethiopian-Business-MVP',
                    MetricData=[
                        {
                            'MetricName': 'CPUUsage',
                            'Value': system_metrics['cpu_usage'],
                            'Unit': 'Percent',
                            'Timestamp': datetime.utcnow()
                        },
                        {
                            'MetricName': 'MemoryUsage',
                            'Value': system_metrics['memory_usage'],
                            'Unit': 'Percent',
                            'Timestamp': datetime.utcnow()
                        }
                    ]
                )
            
            return {'cloudwatch': 'success'}
        except Exception as e:
            return {'cloudwatch': 'error', 'error': str(e)}
    
    def run_comprehensive_check(self):
        """Run all monitoring checks"""
        timestamp = datetime.now().isoformat()
        
        print(f"\n🔍 Ethiopian Business Management System - Health Check")
        print(f"📅 Timestamp: {timestamp}")
        print("=" * 60)
        
        # Application health
        health = self.check_application_health()
        print(f"🏥 Application Health: {health['status'].upper()}")
        if 'response_time' in health:
            print(f"⏱️  Response Time: {health['response_time']:.2f}s")
        
        # VAT Portal
        vat_check = self.check_vat_portal()
        print(f"💰 VAT Portal: {vat_check.get('vat_portal', 'unknown').upper()}")
        
        # Payroll System
        payroll_check = self.check_payroll_system()
        print(f"👥 Payroll System: {payroll_check.get('payroll_system', 'unknown').upper()}")
        
        # Database
        db_check = self.check_database_connectivity()
        print(f"🗄️  Database: {db_check.get('database', 'unknown').upper()}")
        if 'connection_time' in db_check:
            print(f"📊 DB Connection Time: {db_check['connection_time']:.3f}s")
        
        # System metrics
        system_metrics = self.get_system_metrics()
        if 'error' not in system_metrics:
            print(f"🖥️  CPU Usage: {system_metrics['cpu_usage']:.1f}%")
            print(f"🧠 Memory Usage: {system_metrics['memory_usage']:.1f}%")
            print(f"💾 Disk Usage: {system_metrics['disk_usage']:.1f}%")
            print(f"📈 Load Average: {system_metrics['load_average'][0]:.2f}")
        
        # Compile all metrics
        all_metrics = {
            'timestamp': timestamp,
            'health': health,
            'vat_portal': vat_check,
            'payroll': payroll_check,
            'database': db_check,
            'system': system_metrics
        }
        
        # Send to CloudWatch
        cloudwatch_result = self.send_cloudwatch_metrics(all_metrics)
        print(f"☁️  CloudWatch: {cloudwatch_result.get('cloudwatch', 'unknown').upper()}")
        
        # Overall status
        overall_healthy = (
            health.get('status') == 'healthy' and
            vat_check.get('vat_portal') == 'accessible' and
            payroll_check.get('payroll_system') == 'accessible' and
            db_check.get('database') == 'connected'
        )
        
        status_emoji = "✅" if overall_healthy else "❌"
        print(f"\n{status_emoji} Overall Status: {'HEALTHY' if overall_healthy else 'UNHEALTHY'}")
        print("=" * 60)
        
        return all_metrics
    
    def run_monitoring_loop(self, interval=300):
        """Run continuous monitoring with specified interval (default 5 minutes)"""
        print(f"🔄 Starting continuous monitoring (interval: {interval}s)")
        print("Press Ctrl+C to stop\n")
        
        try:
            while True:
                self.run_comprehensive_check()
                print(f"\n😴 Sleeping for {interval} seconds...\n")
                time.sleep(interval)
        except KeyboardInterrupt:
            print("\n🛑 Monitoring stopped by user")
        except Exception as e:
            print(f"\n❌ Monitoring error: {str(e)}")

def main():
    """Main function"""
    monitor = EthiopianBusinessMonitor()
    
    if len(sys.argv) > 1:
        if sys.argv[1] == 'loop':
            interval = int(sys.argv[2]) if len(sys.argv) > 2 else 300
            monitor.run_monitoring_loop(interval)
        elif sys.argv[1] == 'json':
            # Output as JSON for automation
            metrics = monitor.run_comprehensive_check()
            print(json.dumps(metrics, indent=2))
        else:
            print("Usage: python monitor.py [loop [interval]|json]")
    else:
        # Single check
        monitor.run_comprehensive_check()

if __name__ == '__main__':
    main()