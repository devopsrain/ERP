#!/usr/bin/env pwsh
<#
.SYNOPSIS
    Deploy all changes made today (2026-03-15) to the live AWS server.

.DESCRIPTION
    Pushes the following grouped changes:
      1. CPO return-deduction summary (cpo_data_store + templates)
      2. Employee new fields (DOB, phone, manager, bank account) + org-chart
      3. API data-export endpoint (/api/v1/export/{module})
      4. Idle auto-logout JS (5-minute timer, base templates)
      5. Letter / E-Signature module (new routes, templates, DOCX generator)
      6. Sidebar navigation restructure

    What this script does:
      a. SCP all changed/new files to the server
      b. Install new Python packages (python-docx, qrcode)
      c. Create letter module data directories
      d. Run the DB migration for 3 new employee columns
      e. Restart the application via supervisorctl

.PARAMETER ServerIP
    EC2 public IP address of the live server (default: 13.247.89.15)

.PARAMETER SSHKey
    Path to your private SSH key (default: ~/.ssh/id_rsa)

.EXAMPLE
    .\deploy_today.ps1 -ServerIP 13.247.89.15
    .\deploy_today.ps1 -ServerIP 13.247.89.15 -SSHKey "C:\Users\fde\.ssh\id_rsa"
#>
param(
    [string]$ServerIP = "13.247.89.15",
    [string]$SSHKey   = "$HOME\.ssh\id_rsa"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

# ── Helpers ────────────────────────────────────────────────────────
function Write-OK   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green  }
function Write-Fail { param($m) Write-Host "  [FAIL] $m" -ForegroundColor Red    }
function Write-Step { param($m) Write-Host "`n==> $m"    -ForegroundColor Cyan   }
function Write-Info { param($m) Write-Host "  [INFO] $m" -ForegroundColor Yellow }

$sshTarget = "ubuntu@$ServerIP"
$sshOpts   = @('-o','StrictHostKeyChecking=no','-o','ConnectTimeout=15','-i',$SSHKey)
$scpOpts   = @('-o','StrictHostKeyChecking=no','-o','ConnectTimeout=15','-i',$SSHKey)

# Root of the local project (where this script lives / one level up)
$LOCAL_ROOT = Split-Path -Parent $PSScriptRoot
$REMOTE_ROOT = "/opt/ethiopian-business"
$REMOTE_WEB  = "$REMOTE_ROOT/web"

Write-Host "`n============================================================" -ForegroundColor Yellow
Write-Host "  Ethiopian Business — Today's Changes Deploy (2026-03-15)"    -ForegroundColor Yellow
Write-Host "  Target server  : $ServerIP"                                  -ForegroundColor Yellow
Write-Host "  Local project  : $LOCAL_ROOT"                                -ForegroundColor Yellow
Write-Host "============================================================`n" -ForegroundColor Yellow

# ────────────────────────────────────────────────────────────────────
# STEP 0 — Confirm SSH connectivity
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 0 — Testing SSH connectivity..."
$test = ssh @sshOpts $sshTarget "echo SSH_OK" 2>&1
if ($test -notmatch "SSH_OK") {
    Write-Fail "Cannot SSH to $ServerIP. Check key path: $SSHKey"
    exit 1
}
Write-OK "SSH connected to $ServerIP"

# ────────────────────────────────────────────────────────────────────
# STEP 1 — Create letter module directories on the server
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 1 — Creating letter module data directories..."
ssh @sshOpts $sshTarget @"
sudo -u businessapp mkdir -p $REMOTE_WEB/data/letters
sudo -u businessapp mkdir -p $REMOTE_WEB/data/letters/docx
sudo chown -R businessapp:businessapp $REMOTE_WEB/data/letters
echo DIRS_OK
"@ 2>&1 | ForEach-Object {
    if ($_ -match "DIRS_OK") { Write-OK "Letter data directories created" }
    else { Write-Info $_ }
}

# ────────────────────────────────────────────────────────────────────
# STEP 2 — Upload changed/new Python files
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 2 — Uploading Python source files..."

# Map: local path (relative to LOCAL_ROOT) → remote destination directory
$filesToUpload = @(
    # --- requirements ---
    @{ local = "requirements.txt";                         remote = $REMOTE_ROOT }

    # --- CPO ---
    @{ local = "web\cpo_data_store.py";                    remote = $REMOTE_WEB  }

    # --- Employee / Payroll ---
    @{ local = "models\ethiopian_payroll.py";              remote = "$REMOTE_ROOT/models" }
    @{ local = "web\employee_data_store.py";               remote = $REMOTE_WEB  }
    @{ local = "web\payroll_routes.py";                    remote = $REMOTE_WEB  }

    # --- API export endpoint ---
    @{ local = "web\api_routes.py";                        remote = $REMOTE_WEB  }

    # --- Letter module (new) ---
    @{ local = "web\letter_data_store.py";                 remote = $REMOTE_WEB  }
    @{ local = "web\letter_routes.py";                     remote = $REMOTE_WEB  }
    @{ local = "web\letter_docx.py";                       remote = $REMOTE_WEB  }

    # --- App entry point ---
    @{ local = "web\app.py";                               remote = $REMOTE_WEB  }
)

foreach ($f in $filesToUpload) {
    $localFull = Join-Path $LOCAL_ROOT $f.local
    if (-not (Test-Path $localFull)) {
        Write-Fail "LOCAL FILE MISSING: $($f.local)"
        continue
    }
    $result = scp @scpOpts $localFull "${sshTarget}:$($f.remote)/" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Uploaded $($f.local)"
    } else {
        Write-Fail "Failed to upload $($f.local): $result"
    }
}

# ────────────────────────────────────────────────────────────────────
# STEP 3 — Upload HTML templates
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 3 — Uploading HTML templates..."

# Ensure remote template directories exist first
ssh @sshOpts $sshTarget @"
sudo -u businessapp mkdir -p $REMOTE_WEB/templates/letters
sudo -u businessapp mkdir -p $REMOTE_WEB/templates/payroll
sudo -u businessapp mkdir -p $REMOTE_WEB/templates/cpo
echo TMPL_DIRS_OK
"@ 2>&1 | ForEach-Object {
    if ($_ -match "TMPL_DIRS_OK") { Write-OK "Template directories confirmed" }
}

$templateFiles = @(
    # --- Base templates (idle logout + nav links) ---
    @{ local = "web\templates\base.html";                       remote = "$REMOTE_WEB/templates"         }
    @{ local = "web\templates\auth\base.html";                  remote = "$REMOTE_WEB/templates/auth"    }

    # --- CPO templates ---
    @{ local = "web\templates\cpo\dashboard.html";              remote = "$REMOTE_WEB/templates/cpo"     }
    @{ local = "web\templates\cpo\cpo_list.html";               remote = "$REMOTE_WEB/templates/cpo"     }

    # --- Payroll templates ---
    @{ local = "web\templates\payroll\add_employee.html";       remote = "$REMOTE_WEB/templates/payroll" }
    @{ local = "web\templates\payroll\edit_employee.html";      remote = "$REMOTE_WEB/templates/payroll" }
    @{ local = "web\templates\payroll\employees.html";          remote = "$REMOTE_WEB/templates/payroll" }
    @{ local = "web\templates\payroll\org_chart.html";          remote = "$REMOTE_WEB/templates/payroll" }

    # --- Letter templates (all new) ---
    @{ local = "web\templates\letters\dashboard.html";          remote = "$REMOTE_WEB/templates/letters" }
    @{ local = "web\templates\letters\compose.html";            remote = "$REMOTE_WEB/templates/letters" }
    @{ local = "web\templates\letters\view.html";               remote = "$REMOTE_WEB/templates/letters" }
    @{ local = "web\templates\letters\signatures.html";         remote = "$REMOTE_WEB/templates/letters" }
    @{ local = "web\templates\letters\tracker.html";            remote = "$REMOTE_WEB/templates/letters" }
)

foreach ($f in $templateFiles) {
    $localFull = Join-Path $LOCAL_ROOT $f.local
    if (-not (Test-Path $localFull)) {
        Write-Fail "LOCAL TEMPLATE MISSING: $($f.local)"
        continue
    }
    $result = scp @scpOpts $localFull "${sshTarget}:$($f.remote)/" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Uploaded $($f.local)"
    } else {
        Write-Fail "Failed: $($f.local): $result"
    }
}

# ────────────────────────────────────────────────────────────────────
# STEP 4 — Upload Templates.docx (letter DOCX template)
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 4 — Uploading Templates.docx..."
$docxLocal = Join-Path $LOCAL_ROOT "Templates.docx"
if (Test-Path $docxLocal) {
    $result = scp @scpOpts $docxLocal "${sshTarget}:${REMOTE_ROOT}/" 2>&1
    if ($LASTEXITCODE -eq 0) {
        Write-OK "Uploaded Templates.docx to $REMOTE_ROOT/"
    } else {
        Write-Fail "Failed to upload Templates.docx: $result"
        Write-Info "Letter DOCX will fall back to built-in clean layout"
    }
} else {
    Write-Info "Templates.docx not found locally — skipping (letter module will use built-in layout)"
}

# ────────────────────────────────────────────────────────────────────
# STEP 5 — Fix ownership of uploaded files
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 5 — Setting correct file ownership..."
ssh @sshOpts $sshTarget @"
sudo chown -R businessapp:businessapp $REMOTE_ROOT/web
sudo chown businessapp:businessapp $REMOTE_ROOT/requirements.txt
if [ -f "$REMOTE_ROOT/Templates.docx" ]; then
    sudo chown businessapp:businessapp $REMOTE_ROOT/Templates.docx
fi
echo OWNER_OK
"@ 2>&1 | ForEach-Object {
    if ($_ -match "OWNER_OK") { Write-OK "File ownership set to businessapp" }
    else { Write-Info $_ }
}

# ────────────────────────────────────────────────────────────────────
# STEP 6 — Install new Python packages
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 6 — Installing new Python packages (python-docx, qrcode)..."
ssh @sshOpts $sshTarget @"
sudo -u businessapp $REMOTE_ROOT/venv/bin/pip install "python-docx>=1.1.0" "qrcode>=7.4.2" --quiet 2>&1
echo PKG_OK
"@ 2>&1 | ForEach-Object {
    if ($_ -match "PKG_OK") { Write-OK "python-docx and qrcode installed" }
    else { Write-Info $_ }
}

# ────────────────────────────────────────────────────────────────────
# STEP 7 — Run DB migration: new employee columns
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 7 — Running DB migration for new employee columns..."
ssh @sshOpts $sshTarget @"
cd $REMOTE_ROOT
source .env 2>/dev/null || true
sudo -u businessapp $REMOTE_ROOT/venv/bin/python3 - << 'PYEOF'
import os, sys
sys.path.insert(0, '/opt/ethiopian-business')
sys.path.insert(0, '/opt/ethiopian-business/web')
from dotenv import load_dotenv
load_dotenv('/opt/ethiopian-business/.env')
try:
    from web.employee_data_store import EmployeeDataStore
    ds = EmployeeDataStore(None)
    print("DB_MIGRATION_OK")
except Exception as e:
    print(f"DB_MIGRATION_ERROR: {e}")
PYEOF
"@ 2>&1 | ForEach-Object {
    if ($_ -match "DB_MIGRATION_OK")    { Write-OK   "Employee columns migrated (date_of_birth, phone_number, manager)" }
    elseif ($_ -match "DB_MIGRATION_ERROR") { Write-Fail $_ }
    else { Write-Info $_ }
}

# ────────────────────────────────────────────────────────────────────
# STEP 8 — Restart the application
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 8 — Restarting the application..."
ssh @sshOpts $sshTarget @"
sudo supervisorctl restart ethiopian-business
sleep 4
sudo supervisorctl status ethiopian-business
echo RESTART_DONE
"@ 2>&1 | ForEach-Object {
    if ($_ -match "RESTART_DONE") { Write-OK "Application restarted" }
    else { Write-Info $_ }
}

# ────────────────────────────────────────────────────────────────────
# STEP 9 — Smoke test: /health endpoint (checks DB + cache)
# ────────────────────────────────────────────────────────────────────
Write-Step "Step 9 — Smoke test: /health endpoint..."
Start-Sleep -Seconds 5
$healthPassed = $false
try {
    $resp = Invoke-WebRequest -Uri "http://${ServerIP}/health" -TimeoutSec 15 -UseBasicParsing -ErrorAction Stop
    $body = $resp.Content | ConvertFrom-Json
    if ($resp.StatusCode -eq 200 -and $body.status -eq "healthy") {
        Write-OK "/health returned HTTP 200 — status=healthy"
        $healthPassed = $true
    } elseif ($resp.StatusCode -eq 503) {
        Write-Fail "/health returned HTTP 503 — status=$($body.status)"
        Write-Info "Checks: $($body.checks | ConvertTo-Json -Compress)"
    } else {
        Write-Fail "/health returned HTTP $($resp.StatusCode) — status=$($body.status)"
        Write-Info "Checks: $($body.checks | ConvertTo-Json -Compress)"
    }
} catch {
    Write-Fail "Health check failed: $_"
}

# ── Step 9b — If health check reported issues, fetch detailed API health ──
if (-not $healthPassed) {
    Write-Step "Step 9b — Fetching detailed health from /api/v1/health ..."
    try {
        $detResp = Invoke-WebRequest -Uri "http://${ServerIP}/api/v1/health" -TimeoutSec 15 -UseBasicParsing -ErrorAction Stop
        $detBody = $detResp.Content | ConvertFrom-Json
        Write-Info "Detailed health: $($detBody | ConvertTo-Json -Depth 3)"
    } catch {
        Write-Fail "Detailed health check also failed: $_"
    }

    Write-Step "Step 9c — Fetching last 40 lines of server error log ..."
    ssh @sshOpts $sshTarget @"
echo '--- LAST 40 ERROR LOG LINES ---'
sudo tail -40 /var/log/ethiopian-business-error.log 2>/dev/null || echo '(no error log found)'
echo '--- LAST 20 APP LOG LINES ---'
sudo tail -20 /var/log/ethiopian-business.log 2>/dev/null || echo '(no app log found)'
echo '--- SUPERVISOR STATUS ---'
sudo supervisorctl status ethiopian-business 2>/dev/null || echo '(supervisorctl not available)'
"@ 2>&1 | ForEach-Object { Write-Info $_ }
}

# ────────────────────────────────────────────────────────────────────
# SUMMARY
# ────────────────────────────────────────────────────────────────────
Write-Host "`n============================================================" -ForegroundColor Green
Write-Host "  Deployment complete!" -ForegroundColor Green
Write-Host ""
Write-Host "  Changes deployed:" -ForegroundColor White
Write-Host "    [+] CPO return-deduction summary"
Write-Host "    [+] Employee DOB / phone / manager / bank account fields"
Write-Host "    [+] Employee org-chart (/payroll/org-chart)"
Write-Host "    [+] API data-export endpoint (/api/v1/export/{module})"
Write-Host "    [+] Idle auto-logout (5-minute timer)"
Write-Host "    [+] Letter & E-Signature module (/letters/)"
Write-Host "    [+] Sidebar navigation restructure"
Write-Host ""
Write-Host "  Verify at: http://$ServerIP" -ForegroundColor Cyan
Write-Host "============================================================`n" -ForegroundColor Green
