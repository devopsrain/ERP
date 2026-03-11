#!/bin/bash
set -ex  # Exit on error, print commands for debugging in cloud-init-output.log
exec > >(tee /var/log/user_data.log) 2>&1
echo "=== user_data.sh starting at $(date) ==="

# Update system
apt-get update -y
apt-get upgrade -y

# Install required packages
apt-get install -y python3 python3-pip python3-venv nginx git postgresql-client supervisor

# Create application user
useradd -m -s /bin/bash businessapp
usermod -aG sudo businessapp

# Create application directory
mkdir -p /opt/ethiopian-business
chown businessapp:businessapp /opt/ethiopian-business

# Clone application
cd /opt/ethiopian-business
echo "Cloning repository..."
for i in 1 2 3; do
    git clone https://github.com/devopsrain/ERP.git . && break
    echo "Git clone attempt $i failed, retrying in 10s..."
    sleep 10
done
if [ ! -f requirements.txt ]; then
    echo "FATAL: Git clone failed — requirements.txt not found"
    exit 1
fi
chown -R businessapp:businessapp /opt/ethiopian-business

# Create Python virtual environment
sudo -u businessapp python3 -m venv venv
sudo -u businessapp /opt/ethiopian-business/venv/bin/pip install --upgrade pip

# Install Python dependencies
sudo -u businessapp /opt/ethiopian-business/venv/bin/pip install -r requirements.txt
sudo -u businessapp /opt/ethiopian-business/venv/bin/pip install "uvicorn[standard]" psycopg2-binary python-dotenv

# Create environment configuration
# ── All app env vars go here — run_production.py loads this via python-dotenv ──
cat > /opt/ethiopian-business/.env << EOF
FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL=postgresql://${db_username}:${db_password}@${db_host}:5432/${db_name}
# Redis: set to your ElastiCache endpoint if provisioned, otherwise in-memory fallback is used
REDIS_URL=redis://localhost:6379/0
# Set to true in production (HTTPS enforced by ALB)
SESSION_COOKIE_SECURE=true
# Optional CloudFront CDN prefix, e.g. https://d1234abcd.cloudfront.net
STATIC_CDN_URL=
DEFAULT_ADMIN_PASSWORD=Admin2026!Secure
DEFAULT_HR_PASSWORD=HR2026!Secure
DEFAULT_ACCOUNTANT_PASSWORD=Acc2026!Secure
DEFAULT_EMPLOYEE_PASSWORD=Emp2026!Secure
DEFAULT_DATA_ENTRY_PASSWORD=Data2026!Secure
AWS_DEFAULT_REGION=af-south-1
EOF

chown businessapp:businessapp /opt/ethiopian-business/.env
chmod 600 /opt/ethiopian-business/.env

# Initialise PostgreSQL schema (idempotent - safe to re-run)
sudo -u businessapp bash -c '
  source /opt/ethiopian-business/.env 2>/dev/null || true
  export DATABASE_URL
  /opt/ethiopian-business/venv/bin/python3 -c "
import os, psycopg2
url = os.environ.get(\"DATABASE_URL\", \"\")
if url:
    conn = psycopg2.connect(url)
    with open(\"/opt/ethiopian-business/aws-deployment/init_db.sql\") as f:
        conn.cursor().execute(f.read())
    conn.commit()
    conn.close()
    print(\"DB schema initialised\")
else:
    print(\"DATABASE_URL not set - skipping schema init\")
" || echo "DB schema init failed (non-fatal)"
'

# Production configuration loaded via .env and run_production.py

# Create application startup script
cat > /opt/ethiopian-business/run_production.py << 'PYEOF'
#!/usr/bin/env python3
"""Production entry point — start with: uvicorn run_production:app"""
import os
import sys

# Load .env BEFORE importing the FastAPI app so all env vars are set
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path, override=True)

# Put project root and web/ on sys.path so bare imports inside web/ resolve
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'web'))

# Change working directory to web/ so relative data-store paths resolve correctly
os.chdir(os.path.join(project_root, 'web'))

# Import the FastAPI application object
from app import app  # noqa: E402  (web/app.py)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level='info')
PYEOF

chown businessapp:businessapp /opt/ethiopian-business/run_production.py
chmod +x /opt/ethiopian-business/run_production.py

# Configure Supervisor for process management (env vars loaded by run_production.py via dotenv)
# First create log files owned by businessapp so uvicorn can write to them
touch /var/log/ethiopian-business.log
touch /var/log/ethiopian-business-error.log
touch /var/log/ethiopian-business-access.log
chown businessapp:businessapp /var/log/ethiopian-business.log
chown businessapp:businessapp /var/log/ethiopian-business-error.log
chown businessapp:businessapp /var/log/ethiopian-business-access.log

cat > /etc/supervisor/conf.d/ethiopian-business.conf << 'EOF'
[program:ethiopian-business]
command=/opt/ethiopian-business/venv/bin/uvicorn run_production:app --host 127.0.0.1 --port 5000 --workers 3 --log-level info --access-log
directory=/opt/ethiopian-business
user=businessapp
autostart=true
autorestart=true
startsecs=10
startretries=5
stopwaitsecs=30
; stdout gets the structured JSON app logs (INFO+)
redirect_stderr=false
stdout_logfile=/var/log/ethiopian-business.log
stdout_logfile_maxbytes=50MB
stdout_logfile_backups=10
; stderr gets uvicorn startup errors, tracebacks, and unhandled exceptions
stderr_logfile=/var/log/ethiopian-business-error.log
stderr_logfile_maxbytes=20MB
stderr_logfile_backups=5
environment=PATH="/opt/ethiopian-business/venv/bin"
EOF

# Also create an env file supervisor can source
# production.env = identical copy of .env
# Used by: systemd EnvironmentFile (fallback) — supervisor uses run_production.py dotenv
# Keeping them in sync avoids divergence if the app is started by either init system.
cp /opt/ethiopian-business/.env /opt/ethiopian-business/production.env
chown businessapp:businessapp /opt/ethiopian-business/production.env
chmod 600 /opt/ethiopian-business/production.env

# Nginx structured log format — includes upstream response time and request-id for correlation
# Must be in http{} context so we drop it into conf.d before the server block.
cat > /etc/nginx/conf.d/app_log_format.conf << 'EOF'
log_format app '$remote_addr - $remote_user [$time_local] "$request" '
               '$status $body_bytes_sent '
               'rt=$request_time uct="$upstream_connect_time" urt="$upstream_response_time" '
               'rid="$http_x_request_id" ua="$http_user_agent"';
EOF

# Configure Nginx
cat > /etc/nginx/sites-available/ethiopian-business << 'EOF'
server {
    listen 80;
    server_name _;

    # Write warn+ to error log (collected by CloudWatch)
    error_log /var/log/nginx/error.log warn;
    # Use structured 'app' format that includes upstream timing and request-id
    access_log /var/log/nginx/access.log app;

    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_connect_timeout 60s;
        proxy_send_timeout 60s;
        proxy_read_timeout 60s;
    }

    location /static {
        alias /opt/ethiopian-business/web/static;
        expires 1y;
        add_header Cache-Control "public, immutable";
    }

    # AWS ALB health check target — always returns 200 (even when DB is down)
    # Do NOT change this to /api/v1/health — that returns 503 when DB is unreachable
    # which would cause the ALB to mark the instance unhealthy and terminate it.
    location /health {
        proxy_pass http://127.0.0.1:5000/health;
        access_log off;
    }

    # Detailed health check for ops monitoring (can return 503 if DB is down)
    location /api/v1/health {
        proxy_pass http://127.0.0.1:5000/api/v1/health;
        access_log off;
    }
}
EOF

# Enable Nginx site
ln -sf /etc/nginx/sites-available/ethiopian-business /etc/nginx/sites-enabled/
rm -f /etc/nginx/sites-enabled/default

# Test Nginx configuration
nginx -t

# Wait for database to be available (optional — app uses parquet locally)
echo "Checking database connectivity..."
timeout 60 bash -c 'until pg_isready -h ${db_host} -p 5432 -U ${db_username} 2>/dev/null; do echo "Waiting for database..."; sleep 5; done' || echo "Database not reachable — app will use local parquet storage."

# Initialize application data directories
cd /opt/ethiopian-business
# Create ALL data directories the app needs (data stores use web/data/ and sub-paths)
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/platform
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/auth
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/bids
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/bids/documents
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/backups
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/siem
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/data/versions
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/exports
sudo -u businessapp mkdir -p /opt/ethiopian-business/data
# Also create a data/ dir at the project root (some stores use relative paths)
sudo -u businessapp mkdir -p /opt/ethiopian-business/web/sample_files
# Ensure businessapp owns everything
chown -R businessapp:businessapp /opt/ethiopian-business/web/data
chown -R businessapp:businessapp /opt/ethiopian-business/data
echo "Application data directories created."

# Create log rotation for application logs
cat > /etc/logrotate.d/ethiopian-business << 'EOF'
/var/log/ethiopian-business.log
/var/log/ethiopian-business-error.log {
    daily
    rotate 14
    compress
    delaycompress
    missingok
    notifempty
    sharedscripts
    create 644 businessapp businessapp
    postrotate
        supervisorctl restart ethiopian-business >/dev/null 2>&1 || true
    endscript
}
EOF

# Set up automated backups
cat > /opt/ethiopian-business/backup.sh << 'EOF'
#!/bin/bash
# Load database credentials from .env
source /opt/ethiopian-business/.env

BACKUP_DIR="/opt/backups"
DATE=$(date +%Y%m%d_%H%M%S)
DB_BACKUP_FILE="ethiopian_business_$DATE.sql"

mkdir -p $BACKUP_DIR

# Extract DB connection details from DATABASE_URL
# Format: postgresql://user:password@host:port/dbname
DB_USER=$(echo $DATABASE_URL | sed -n 's|.*://\([^:]*\):.*|\1|p')
DB_PASS=$(echo $DATABASE_URL | sed -n 's|.*://[^:]*:\([^@]*\)@.*|\1|p')
DB_HOST=$(echo $DATABASE_URL | sed -n 's|.*@\([^:]*\):.*|\1|p')
DB_NAME=$(echo $DATABASE_URL | sed -n 's|.*/\([^?]*\).*|\1|p')

# Database backup
PGPASSWORD=$DB_PASS pg_dump -h $DB_HOST -U $DB_USER -d $DB_NAME > $BACKUP_DIR/$DB_BACKUP_FILE

# Compress backup
gzip $BACKUP_DIR/$DB_BACKUP_FILE

# Keep only last 7 days of backups
find $BACKUP_DIR -name "*.gz" -mtime +7 -delete

echo "Backup completed: $BACKUP_DIR/$DB_BACKUP_FILE.gz"
EOF

chmod +x /opt/ethiopian-business/backup.sh
chown businessapp:businessapp /opt/ethiopian-business/backup.sh

# Add daily backup to cron (preserve any existing crontab entries)
{ crontab -u businessapp -l 2>/dev/null; echo "0 2 * * * /opt/ethiopian-business/backup.sh >> /var/log/eb-backup.log 2>&1"; } | sort -u | crontab -u businessapp -

# Health watchdog: auto-restart app if /health fails 2 consecutive times (every 5 min)
cat > /opt/ethiopian-business/health_watchdog.sh << 'WATCHDOG'
#!/bin/bash
APP_URL="http://127.0.0.1:5000"
FAIL_FILE="/tmp/.eb_health_failures"
ERR_LOG="/var/log/ethiopian-business-error.log"
STATUS=$(curl -sf -o /dev/null -w "%{http_code}" "$APP_URL/health" 2>/dev/null || echo "000")
if [ "$STATUS" = "200" ]; then
    rm -f "$FAIL_FILE"
else
    COUNT=$(cat "$FAIL_FILE" 2>/dev/null || echo 0)
    COUNT=$((COUNT + 1))
    echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) HEALTH_FAIL status=$STATUS count=$COUNT" >> "$ERR_LOG"
    echo $COUNT > "$FAIL_FILE"
    if [ "$COUNT" -ge 2 ]; then
        echo "$(date -u +%Y-%m-%dT%H:%M:%SZ) WATCHDOG_RESTART after $COUNT failures" >> "$ERR_LOG"
        supervisorctl restart ethiopian-business >> "$ERR_LOG" 2>&1
        rm -f "$FAIL_FILE"
    fi
fi
WATCHDOG
chmod +x /opt/ethiopian-business/health_watchdog.sh
chown businessapp:businessapp /opt/ethiopian-business/health_watchdog.sh
# Run watchdog as root every 5 minutes (needs supervisorctl access)
{ crontab -l 2>/dev/null; echo "*/5 * * * * /opt/ethiopian-business/health_watchdog.sh"; } | sort -u | crontab -

# Server-side diagnostic script (run over SSH for rapid incident diagnosis)
cat > /opt/ethiopian-business/diagnose.sh << 'DIAG'
#!/bin/bash
# Ethiopian Business System - Server-Side Diagnostic
# Usage: sudo bash /opt/ethiopian-business/diagnose.sh
TS=$(date '+%Y-%m-%d %H:%M:%S UTC')
APP="http://127.0.0.1:5000"
echo "========================================================"
echo "  Ethiopian Business System -- Diagnostics @ $TS"
echo "========================================================"

echo ""
echo "-- 1. PROCESS STATUS -----------------------------------"
supervisorctl status 2>/dev/null || echo "supervisor not running"
echo ""
echo "uvicorn processes:"
pgrep -a -f uvicorn 2>/dev/null || echo "  none found"

echo ""
echo "-- 2. PORT BINDINGS ------------------------------------"
ss -tlnp 2>/dev/null | grep -E ':5000|:80 ' || netstat -tlnp 2>/dev/null | grep -E ':5000|:80 '

echo ""
echo "-- 3. HEALTH CHECKS ------------------------------------"
echo -n "  /health (ALB):         "
curl -sf -o /dev/null -w "HTTP %{http_code}  %{time_total}s\n" "$APP/health" 2>/dev/null || echo "REFUSED"
echo -n "  /api/v1/health (full): "
curl -sf -w "HTTP %{http_code}\n" "$APP/api/v1/health" 2>/dev/null | head -1 || echo "REFUSED"

echo ""
echo "-- 4. LAST 40 APP LOG LINES ----------------------------"
tail -40 /var/log/ethiopian-business.log 2>/dev/null | grep -E 'ERROR|WARNING|CRITICAL|unhandled|startup' | tail -20 || tail -20 /var/log/ethiopian-business.log 2>/dev/null

echo ""
echo "-- 5. LAST 20 ERROR LOG LINES --------------------------"
tail -20 /var/log/ethiopian-business-error.log 2>/dev/null || echo "  not found"

echo ""
echo "-- 6. NGINX ERROR LOG (last 15) ------------------------"
tail -15 /var/log/nginx/error.log 2>/dev/null || echo "  not found"

echo ""
echo "-- 7. DISK / MEMORY ------------------------------------"
df -h / /opt 2>/dev/null
echo ""
free -h 2>/dev/null

echo ""
echo "-- 8. ENV VAR CHECK (values hidden) --------------------"
for v in FLASK_SECRET_KEY DATABASE_URL REDIS_URL SESSION_COOKIE_SECURE LOG_LEVEL; do
    val=$(grep -m1 "^${v}=" /opt/ethiopian-business/.env 2>/dev/null | cut -d= -f2-)
    [ -n "$val" ] && echo "  $v: SET" || echo "  $v: NOT SET"
done

echo ""
echo "-- 9. DATABASE CHECK -----------------------------------"
source /opt/ethiopian-business/.env 2>/dev/null
if [ -n "$DATABASE_URL" ]; then
    /opt/ethiopian-business/venv/bin/python3 -c "
import os, time
try:
    import psycopg2
    t=time.time()
    c=psycopg2.connect(os.environ['DATABASE_URL'])
    c.cursor().execute('SELECT version()')
    r=c.fetchone(); c.close()
    print('  CONNECTED in %.3fs' % (time.time()-t))
except Exception as e:
    print('  ERROR: %s' % e)
" 2>&1
else
    echo "  DATABASE_URL not set"
fi

echo ""
echo "-- 10. FAIL2BAN ----------------------------------------"
fail2ban-client status 2>/dev/null | head -8 || echo "  not running"

echo ""
echo "========================================================"
echo "  Done. For full logs: tail -100 /var/log/ethiopian-business.log"
echo "========================================================"
DIAG
chmod +x /opt/ethiopian-business/diagnose.sh
chown businessapp:businessapp /opt/ethiopian-business/diagnose.sh
echo "Server-side diagnose.sh created at /opt/ethiopian-business/diagnose.sh"

# Create systemd service for additional reliability
cat > /etc/systemd/system/ethiopian-business.service << 'EOF'
[Unit]
Description=Ethiopian Business Management System
After=network.target

[Service]
Type=simple
User=businessapp
Group=businessapp
WorkingDirectory=/opt/ethiopian-business
EnvironmentFile=/opt/ethiopian-business/production.env
Environment="PATH=/opt/ethiopian-business/venv/bin:/usr/bin:/bin"
ExecStart=/opt/ethiopian-business/venv/bin/uvicorn run_production:app --host 127.0.0.1 --port 5000 --workers 3 --log-level info
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

# Start and enable services (use supervisor only — NOT systemd for the app, to avoid port conflicts)
systemctl daemon-reload
systemctl enable nginx
systemctl enable supervisor
# Do NOT enable ethiopian-business.service — supervisor manages uvicorn

# Start services
systemctl start supervisor
systemctl restart nginx

# Update supervisor and start application
supervisorctl reread
supervisorctl update
supervisorctl start ethiopian-business

# Install and configure fail2ban for security
apt-get install -y fail2ban

cat > /etc/fail2ban/jail.local << 'EOF'
[DEFAULT]
bantime = 3600
findtime = 600
maxretry = 5

[sshd]
enabled = true
port = ssh
logpath = /var/log/auth.log

[nginx-http-auth]
enabled = true
filter = nginx-http-auth
logpath = /var/log/nginx/error.log
maxretry = 3
EOF

systemctl enable fail2ban
systemctl start fail2ban

# Set up CloudWatch monitoring (IAM role now configured) — non-fatal if it fails
set +e
curl -sS https://s3.amazonaws.com/amazoncloudwatch-agent/ubuntu/amd64/latest/amazon-cloudwatch-agent.deb -O
dpkg -i amazon-cloudwatch-agent.deb

# Create CloudWatch config directory if it doesn't exist
mkdir -p /opt/aws/amazon-cloudwatch-agent/etc

# Basic CloudWatch configuration
cat > /opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json << 'EOF'
{
    "metrics": {
        "namespace": "Ethiopian-Business-MVP",
        "metrics_collected": {
            "cpu": {
                "measurement": ["cpu_usage_idle", "cpu_usage_iowait", "cpu_usage_user", "cpu_usage_system"],
                "metrics_collection_interval": 60
            },
            "disk": {
                "measurement": ["used_percent"],
                "metrics_collection_interval": 60,
                "resources": ["*"]
            },
            "mem": {
                "measurement": ["mem_used_percent"],
                "metrics_collection_interval": 60
            }
        }
    },
    "logs": {
        "logs_collected": {
            "files": {
                "collect_list": [
                    {
                        "file_path": "/var/log/ethiopian-business.log",
                        "log_group_name": "ethiopian-business-logs",
                        "log_stream_name": "{instance_id}",
                        "timestamp_format": "%Y-%m-%d %H:%M:%S"
                    },
                    {
                        "file_path": "/var/log/ethiopian-business-error.log",
                        "log_group_name": "ethiopian-business-error-logs",
                        "log_stream_name": "{instance_id}",
                        "timestamp_format": "%Y-%m-%d %H:%M:%S"
                    },
                    {
                        "file_path": "/var/log/nginx/access.log",
                        "log_group_name": "nginx-access-logs",
                        "log_stream_name": "{instance_id}"
                    },
                    {
                        "file_path": "/var/log/nginx/error.log",
                        "log_group_name": "nginx-error-logs",
                        "log_stream_name": "{instance_id}"
                    },
                    {
                        "file_path": "/var/log/supervisor/supervisord.log",
                        "log_group_name": "supervisor-logs",
                        "log_stream_name": "{instance_id}"
                    },
                    {
                        "file_path": "/var/log/auth.log",
                        "log_group_name": "auth-logs",
                        "log_stream_name": "{instance_id}"
                    }
                ]
            }
        }
    }
}
EOF

# Start CloudWatch agent
/opt/aws/amazon-cloudwatch-agent/bin/amazon-cloudwatch-agent-ctl \
    -a fetch-config \
    -m ec2 \
    -s \
    -c file:/opt/aws/amazon-cloudwatch-agent/etc/amazon-cloudwatch-agent.json || echo "CloudWatch agent start failed (non-fatal)"
set -e

# Display completion message
echo "==================================="
echo "Ethiopian Business Management System"
echo "Deployment completed successfully!"
echo "==================================="
echo "Database Host: ${db_host}"
echo "Database Name: ${db_name}"
echo "Application Status: Check with 'supervisorctl status'"
echo "Logs: /var/log/ethiopian-business.log"
echo "==================================="
echo "=== user_data.sh completed at $(date) ==="
touch /opt/ethiopian-business/.deploy_complete