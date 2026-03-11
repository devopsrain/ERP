param(
    [Parameter(Mandatory=$true)]
    [string]$ServerIP,
    [string]$SSHKey = "$HOME\.ssh\id_rsa",
    [string]$LocalWebPath = (Resolve-Path "$PSScriptRoot\..\..\web"),
    [string]$RemotePath = "/opt/ethiopian-business/web"
)

function Write-OK   { param($m) Write-Host "  [OK]   $m" -ForegroundColor Green }
function Write-Step { param($m) Write-Host "`n=> $m" -ForegroundColor Cyan }

$sshTarget = "ubuntu@$ServerIP"
$sshOpts   = @('-o','StrictHostKeyChecking=no','-o','ConnectTimeout=10','-i',$SSHKey)

# Step 1: Create tar of web folder
Write-Step "Creating tarball..."
$tarFile = Join-Path $env:TEMP "web.tar.gz"
if (Test-Path $tarFile) { Remove-Item $tarFile }

tar -C $LocalWebPath -czf $tarFile .

# Step 2: Upload tarball
Write-Step "Uploading tarball..."
scp @sshOpts $tarFile "${sshTarget}:/tmp/web.tar.gz"

# Step 3: Extract tarball and fix ownership
Write-Step "Extracting on server..."
ssh @sshOpts $sshTarget @"
sudo mkdir -p $RemotePath
sudo tar -xzf /tmp/web.tar.gz -C $RemotePath
sudo chown -R businessapp:businessapp $RemotePath
echo DEPLOY_OK
"@ 2>&1 | ForEach-Object {
    if ($_ -match "DEPLOY_OK") { Write-OK "Web folder deployed" }
}

# Step 4: Restart app
Write-Step "Restarting supervisor + nginx..."
ssh @sshOpts $sshTarget @"
sudo supervisorctl restart ethiopian-business
sudo systemctl restart nginx
echo RESTART_DONE
"@ 2>&1 | ForEach-Object {
    if ($_ -match "RESTART_DONE") { Write-OK "App restarted" }
}

# Step 5: Post-deploy health check
Write-Step "Verifying deployment..."
Start-Sleep -Seconds 5
$health = (ssh @sshOpts $sshTarget "curl -s -o /dev/null -w '%{http_code}' --max-time 8 http://127.0.0.1:5000/health 2>&1").Trim()
if ($health -eq "200") {
    Write-OK "Health check passed - app is serving"
} else {
    Write-Host "  [WARN] Health check returned HTTP $health - check logs with diagnose.ps1" -ForegroundColor Yellow
}

Write-Host "`nDeployment complete!" -ForegroundColor Yellow