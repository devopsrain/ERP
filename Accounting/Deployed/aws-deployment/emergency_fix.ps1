#!/usr/bin/env pwsh
param(
    [Parameter(Mandatory=$true)]
    [string]$ServerIP,
    [string]$SSHKey = "$HOME\.ssh\id_rsa"
)

function Write-OK   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Fail { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red }
function Write-Warn { param($m) Write-Host "  [WARN] $m" -ForegroundColor Yellow }
function Write-Step { param($m) Write-Host "`n=> $m" -ForegroundColor Cyan }

$sshTarget = "ubuntu@$ServerIP"
$sshOpts   = @('-o','StrictHostKeyChecking=no','-o','ConnectTimeout=15','-i',$SSHKey)
$APP       = "/opt/ethiopian-business"

Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  Ethiopian Business - Emergency Fix" -ForegroundColor Yellow
Write-Host "  Target: $ServerIP" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow

# Step 1: SSH test
Write-Step "Testing SSH connectivity..."
$test = (ssh @sshOpts $sshTarget "echo OK" 2>&1)
if ($test -notmatch "OK") {
    Write-Fail "Cannot SSH to $ServerIP. Check key path: $SSHKey"
    exit 1
}
Write-OK "SSH connected"

# Step 2: Check app code
Write-Step "Checking application code..."
$check = (ssh @sshOpts $sshTarget "test -f $APP/requirements.txt && echo REQ_EXISTS || echo REQ_MISSING" 2>&1)
if ($check -match "REQ_MISSING") {
    Write-Warn "requirements.txt not found - re-running git clone..."
    $cloneScript = 'cd /opt/ethiopian-business && for i in 1 2 3; do sudo -u businessapp git clone https://github.com/devopsrain/ERP.git . && break; echo "Retry $i"; sleep 10; done && sudo chown -R businessapp:businessapp /opt/ethiopian-business && test -f /opt/ethiopian-business/requirements.txt && echo CLONE_OK || echo CLONE_FAIL'
    $result = ssh @sshOpts $sshTarget $cloneScript 2>&1
    if ($result -match "CLONE_OK") { Write-OK "Repository cloned" }
    else { Write-Fail "Clone failed - check GitHub URL"; exit 1 }
} else {
    Write-OK "Application code present"
}

# Step 3: Install packages
Write-Step "Installing packages..."
$pip1 = ssh @sshOpts $sshTarget "test -f $APP/venv/bin/python || python3 -m venv $APP/venv && echo VENV_OK" 2>&1
if ($pip1 -match "VENV_OK") { Write-OK "Venv ready" }

$pip2 = ssh @sshOpts $sshTarget "$APP/venv/bin/pip install --upgrade pip -q && $APP/venv/bin/pip install -r $APP/requirements.txt -q && echo PIP_REQ_OK" 2>&1
if ($pip2 -match "PIP_REQ_OK") { Write-OK "requirements.txt installed" }

$pip3 = ssh @sshOpts $sshTarget "$APP/venv/bin/pip install 'uvicorn[standard]>=0.29.0' 'psycopg2-binary>=2.9.0' -q && echo PIP_EXTRAS_OK" 2>&1
if ($pip3 -match "PIP_EXTRAS_OK") { Write-OK "uvicorn + psycopg2-binary installed" }

ssh @sshOpts $sshTarget "sudo chown -R businessapp:businessapp $APP/venv" 2>&1 | Out-Null

# Step 4: Write run_production.py
Write-Step "Writing run_production.py..."
$pyContent = @'
#!/usr/bin/env python3
import os, sys
from dotenv import load_dotenv
env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env')
load_dotenv(env_path, override=True)
project_root = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, project_root)
sys.path.insert(0, os.path.join(project_root, 'web'))
os.chdir(os.path.join(project_root, 'web'))
from app import app
if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host='0.0.0.0', port=5000, log_level='info')
'@
$pyContent | ssh @sshOpts $sshTarget "sudo tee $APP/run_production.py > /dev/null && sudo chown businessapp:businessapp $APP/run_production.py && echo RUN_PROD_OK" 2>&1 | ForEach-Object {
    if ($_ -match "RUN_PROD_OK") { Write-OK "run_production.py written" }
}

# Step 5: Write supervisor config
Write-Step "Writing supervisor config..."
$supContent = @'
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
'@
$supContent | ssh @sshOpts $sshTarget "sudo tee /etc/supervisor/conf.d/ethiopian-business.conf > /dev/null && echo CONF_OK" 2>&1 | ForEach-Object {
    if ($_ -match "CONF_OK") { Write-OK "Supervisor config written" }
}

# Step 6: Dirs and logs
Write-Step "Creating directories and log files..."
$d1 = ssh @sshOpts $sshTarget "sudo -u businessapp mkdir -p $APP/web/data/platform $APP/web/data/auth $APP/web/data/bids/documents $APP/web/data/backups $APP/web/exports $APP/data && echo DIRS_OK" 2>&1
if ($d1 -match "DIRS_OK") { Write-OK "Directories ready" }

$d2 = ssh @sshOpts $sshTarget "sudo touch /var/log/ethiopian-business.log /var/log/ethiopian-business-error.log && sudo chown businessapp:businessapp /var/log/ethiopian-business.log /var/log/ethiopian-business-error.log && sudo chown -R businessapp:businessapp $APP && echo LOGS_OK" 2>&1
if ($d2 -match "LOGS_OK") { Write-OK "Log files ready" }

# Step 7: Nginx config
Write-Step "Configuring nginx..."
$ngxContent = @'
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
'@
$ngxContent | ssh @sshOpts $sshTarget "sudo tee /etc/nginx/sites-available/ethiopian-business > /dev/null && sudo ln -sf /etc/nginx/sites-available/ethiopian-business /etc/nginx/sites-enabled/ && sudo rm -f /etc/nginx/sites-enabled/default && sudo nginx -t && echo NGINX_OK" 2>&1 | ForEach-Object {
    if ($_ -match "NGINX_OK")      { Write-OK "Nginx configured" }
    if ($_ -match "emerg|error")   { Write-Fail "Nginx error: $_" }
}

# Step 8: Start everything
Write-Step "Starting application..."
$s1 = ssh @sshOpts $sshTarget "sudo fuser -k 5000/tcp 2>/dev/null; sleep 1 && sudo supervisorctl reread && sudo supervisorctl update && sudo supervisorctl restart ethiopian-business && sudo systemctl restart nginx && echo START_OK" 2>&1
if ($s1 -match "START_OK") { Write-OK "Services restarted" }

Start-Sleep -Seconds 8

$status = ssh @sshOpts $sshTarget "sudo supervisorctl status ethiopian-business" 2>&1
if ($status -match "RUNNING") { Write-OK "Supervisor: $status" }
else { Write-Fail "Supervisor: $status" }

$health = ssh @sshOpts $sshTarget "curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:5000/health" 2>&1
if ($health -match "200") { Write-OK "Local health check: HTTP 200" }
else { Write-Fail "Local health check: HTTP $health" }

# Step 9: Show logs
Write-Step "Last 30 lines of app log..."
$log = ssh @sshOpts $sshTarget "sudo tail -30 /var/log/ethiopian-business.log 2>&1"
Write-Host $log -ForegroundColor DarkGray

# Step 10: External check
Write-Step "External health check..."
Start-Sleep -Seconds 3
try {
    $resp = Invoke-WebRequest -UseBasicParsing -Uri "http://$ServerIP/health" -TimeoutSec 12 -ErrorAction Stop
    Write-OK "External HTTP $($resp.StatusCode) - deployment successful!"
    Write-Host "  App is live at: http://$ServerIP/" -ForegroundColor Green
} catch {
    $code = $_.Exception.Response.StatusCode.value__
    Write-Fail "External health check: HTTP $code"
    Write-Host "  Debug: ssh -i $SSHKey ubuntu@$ServerIP" -ForegroundColor DarkGray
}

Write-Host "========================================" -ForegroundColor Yellow
Write-Host "  Emergency fix complete!" -ForegroundColor Yellow
Write-Host "========================================" -ForegroundColor Yellow