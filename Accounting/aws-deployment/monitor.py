#!/usr/bin/env python3
"""
AWS Monitoring Script — Ethiopian Business Management System

Checks application health, sends custom metrics to CloudWatch, and can run
continuously as a background watchdog.

Usage:
    python monitor.py                # Single run
    python monitor.py --watch        # Continuous (5-min intervals)
    python monitor.py --watch --interval 60
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime

import boto3
import psutil
import requests
from dotenv import load_dotenv

load_dotenv()


class EthiopianBusinessMonitor:
    def __init__(self, app_url: str | None = None):
        self.app_url    = (app_url or os.getenv("APP_URL", "http://localhost:5000")).rstrip("/")
        self.aws_region = os.getenv("AWS_DEFAULT_REGION", "af-south-1")
        try:
            self.cloudwatch = boto3.client("cloudwatch", region_name=self.aws_region)
        except Exception as e:
            print(f"  [warn] CloudWatch client init failed: {e}")
            self.cloudwatch = None

    # ── Application health ─────────────────────────────────────────────

    def check_health(self) -> dict:
        """Hit /health (ALB target — always 200) for liveness."""
        try:
            r = requests.get(f"{self.app_url}/health", timeout=10)
            return {
                "status":        "healthy" if r.status_code == 200 else "unhealthy",
                "http_code":     r.status_code,
                "response_time": r.elapsed.total_seconds(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def check_detailed_health(self) -> dict:
        """Hit /api/v1/health for DB + cache readiness (may return 503 if DB down)."""
        try:
            r = requests.get(f"{self.app_url}/api/v1/health", timeout=10)
            data = r.json()
            return {
                "overall":       data.get("status", "unknown"),
                "http_code":     r.status_code,
                "checks":        data.get("checks", {}),
                "response_time": r.elapsed.total_seconds(),
            }
        except Exception as e:
            return {"overall": "error", "error": str(e)}

    # ── System metrics ─────────────────────────────────────────────────

    def get_system_metrics(self) -> dict:
        """Return CPU, memory, disk, and load average."""
        try:
            cpu   = psutil.cpu_percent(interval=1)
            mem   = psutil.virtual_memory()
            disk  = psutil.disk_usage("/")
            load  = os.getloadavg() if hasattr(os, "getloadavg") else (0, 0, 0)
            return {
                "cpu_percent":      cpu,
                "mem_percent":      mem.percent,
                "mem_available_gb": round(mem.available / (1024 ** 3), 2),
                "disk_percent":     disk.percent,
                "disk_free_gb":     round(disk.free   / (1024 ** 3), 2),
                "load_1m":          load[0],
                "load_5m":          load[1],
            }
        except Exception as e:
            return {"error": str(e)}

    # ── Database ───────────────────────────────────────────────────────

    def check_database(self) -> dict:
        """Direct psycopg2 connection check (bypasses app layer)."""
        db_url = os.getenv("DATABASE_URL")
        if not db_url:
            return {"status": "skipped", "reason": "DATABASE_URL not set"}
        try:
            import psycopg2
            t = time.time()
            conn = psycopg2.connect(db_url)
            conn.cursor().execute("SELECT 1")
            conn.close()
            return {"status": "connected", "connect_time": round(time.time() - t, 3)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── CloudWatch ─────────────────────────────────────────────────────

    def send_cloudwatch_metrics(self, health: dict, system: dict, db: dict) -> dict:
        if not self.cloudwatch:
            return {"status": "skipped", "reason": "CloudWatch unavailable"}
        try:
            ts = datetime.utcnow()
            metrics = [
                {
                    "MetricName": "AppHealth",
                    "Value":      1 if health.get("status") == "healthy" else 0,
                    "Unit":       "None",
                    "Timestamp":  ts,
                },
                {
                    "MetricName": "AppResponseTime",
                    "Value":      health.get("response_time", 0),
                    "Unit":       "Seconds",
                    "Timestamp":  ts,
                },
                {
                    "MetricName": "DBConnected",
                    "Value":      1 if db.get("status") == "connected" else 0,
                    "Unit":       "None",
                    "Timestamp":  ts,
                },
            ]
            if "cpu_percent" in system:
                metrics += [
                    {
                        "MetricName": "CPUUsage",
                        "Value":      system["cpu_percent"],
                        "Unit":       "Percent",
                        "Timestamp":  ts,
                    },
                    {
                        "MetricName": "MemUsage",
                        "Value":      system["mem_percent"],
                        "Unit":       "Percent",
                        "Timestamp":  ts,
                    },
                    {
                        "MetricName": "DiskUsage",
                        "Value":      system["disk_percent"],
                        "Unit":       "Percent",
                        "Timestamp":  ts,
                    },
                ]
            # CloudWatch PutMetricData accepts max 20 metrics per call
            for i in range(0, len(metrics), 20):
                self.cloudwatch.put_metric_data(
                    Namespace="Ethiopian-Business-MVP",
                    MetricData=metrics[i : i + 20],
                )
            return {"status": "ok", "metrics_sent": len(metrics)}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ── Combined run ──────────────────────────────────────────────────

    def run(self, verbose: bool = True) -> dict:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        if verbose:
            print(f"\n{'='*60}")
            print(f"  Ethiopian Business System — Monitor  {ts}")
            print(f"{'='*60}")

        health   = self.check_health()
        detailed = self.check_detailed_health()
        db       = self.check_database()
        system   = self.get_system_metrics()
        cw       = self.send_cloudwatch_metrics(health, system, db)

        if verbose:
            alive = health.get("status", "?").upper()
            rt    = health.get("response_time")
            rt_s  = f"  ({rt:.3f}s)" if rt else ""
            print(f"  /health:          {alive}{rt_s}")

            d_overall = detailed.get("overall", "?").upper()
            d_checks  = json.dumps(detailed.get("checks", {}))
            print(f"  /api/v1/health:   {d_overall}  {d_checks}")

            db_s = db.get("status", "?").upper()
            ct   = db.get("connect_time")
            ct_s = f"  ({ct:.3f}s)" if ct else ""
            db_e = f"  {db.get('error','')}" if db.get("error") else ""
            print(f"  Database:         {db_s}{ct_s}{db_e}")

            if "cpu_percent" in system:
                print(
                    f"  CPU: {system['cpu_percent']:.1f}%  "
                    f"Mem: {system['mem_percent']:.1f}%  "
                    f"Disk: {system['disk_percent']:.1f}%  "
                    f"Load: {system['load_1m']:.2f}"
                )

            cw_s = cw.get("status", "?").upper()
            n    = cw.get("metrics_sent", 0)
            print(f"  CloudWatch:       {cw_s}  ({n} metrics)")

            healthy = (
                health.get("status") == "healthy"
                and db.get("status") in ("connected", "skipped")
            )
            print(f"\n  Overall: {'OK' if healthy else 'DEGRADED'}")
            print(f"{'='*60}")

        return {
            "ts":       ts,
            "health":   health,
            "detailed": detailed,
            "database": db,
            "system":   system,
            "cw":       cw,
        }

    def watch(self, interval: int = 300):
        print(f"Continuous monitoring — interval {interval}s.  Ctrl+C to stop.\n")
        while True:
            try:
                result = self.run()
                # Exit non-zero if app is down so a wrapper script can alert
            except KeyboardInterrupt:
                print("\nMonitoring stopped.")
                sys.exit(0)
            except Exception as e:
                print(f"[error] monitor loop: {e}")
            time.sleep(interval)


def main():
    parser = argparse.ArgumentParser(description="Ethiopian Business System Monitor")
    parser.add_argument("--url",      default=None,  help="App base URL (default: APP_URL env or localhost:5000)")
    parser.add_argument("--watch",    action="store_true", help="Run continuously")
    parser.add_argument("--interval", type=int, default=300, help="Watch interval in seconds (default 300)")
    parser.add_argument("--json",     action="store_true", help="Output as JSON (single run)")
    args = parser.parse_args()

    mon = EthiopianBusinessMonitor(app_url=args.url)
    if args.watch:
        mon.watch(interval=args.interval)
    else:
        result = mon.run(verbose=not args.json)
        if args.json:
            print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
