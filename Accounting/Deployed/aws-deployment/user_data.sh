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
cat > /opt/ethiopian-business/.env << EOF
FLASK_SECRET_KEY=$(python3 -c "import secrets; print(secrets.token_hex(32))")
DATABASE_URL=postgresql://${db_username}:${db_password}@${db_host}:5432/${db_name}
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
    with open(\"/opt/ethiopian-business/Deployed/aws-deployment/init_db.sql\") as f:
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
redirect_stderr=true
stdout_logfile=/var/log/ethiopian-business.log
environment=PATH="/opt/ethiopian-business/venv/bin"
EOF

# Also create an env file supervisor can source
cat > /opt/ethiopian-business/production.env << 'ENVEOF'
FLASK_SECRET_KEY=PLACEHOLDER
DEFAULT_ADMIN_PASSWORD=Admin2026!Secure
DEFAULT_HR_PASSWORD=HR2026!Secure
DEFAULT_ACCOUNTANT_PASSWORD=Acc2026!Secure
DEFAULT_EMPLOYEE_PASSWORD=Emp2026!Secure
DEFAULT_DATA_ENTRY_PASSWORD=Data2026!Secure
ENVEOF
# Replace placeholder with actual generated key
GENERATED_KEY=$(grep FLASK_SECRET_KEY /opt/ethiopian-business/.env | cut -d= -f2)
sed -i "s/PLACEHOLDER/$GENERATED_KEY/" /opt/ethiopian-business/production.env
chown businessapp:businessapp /opt/ethiopian-business/production.env
chmod 600 /opt/ethiopian-business/production.env

# Configure Nginx
cat > /etc/nginx/sites-available/ethiopian-business << 'EOF'
server {
    listen 80;
    server_name _;

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

    location /health {
        proxy_pass http://127.0.0.1:5000/health;
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
/var/log/ethiopian-business.log {
    daily
    rotate 14
    compress
    missingok
    notifempty
    create 644 businessapp businessapp
    postrotate
        supervisorctl restart ethiopian-business
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

# Add daily backup to cron
echo "0 2 * * * /opt/ethiopian-business/backup.sh" | crontab -u businessapp -

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
                        "log_stream_name": "{instance_id}"
                    },
                    {
                        "file_path": "/var/log/nginx/access.log",
                        "log_group_name": "nginx-access-logs",
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