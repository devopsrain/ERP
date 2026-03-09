param(
    [Parameter(Mandatory=$true)]
    [string]$ServerIP,
    [string]$SSHKey = "$HOME\.ssh\id_rsa",
    [string]$LogFile = "$PWD\diagnostic_log.txt"
)

function Write-Log { param($m) Add-Content -Path $LogFile -Value "$((Get-Date).ToString('s')) - $m"; Write-Host $m }
function Write-Step { param($m) Write-Host "`n==> $m" -ForegroundColor Cyan; Write-Log "STEP: $m" }

# Clear previous log
if (Test-Path $LogFile) { Remove-Item $LogFile }

Write-Step "Starting diagnostic for $ServerIP"

# Step 1: Ping the server
Write-Step "Testing network connectivity (ping)..."
try {
    if (Test-Connection -ComputerName $ServerIP -Count 3 -Quiet) {
        Write-Log "Ping successful"
    } else {
        Write-Log "Ping failed — network unreachable"
    }
} catch {
    Write-Log "Ping failed: $_"
}

# Step 2: Test SSH port 22
Write-Step "Testing SSH port 22"
try {
    $tcp = Test-NetConnection -ComputerName $ServerIP -Port 22
    if ($tcp.TcpTestSucceeded) {
        Write-Log "Port 22 is open"
    } else {
        Write-Log "Port 22 is closed or blocked"
    }
} catch {
    Write-Log "Port 22 test failed: $_"
}

# Step 3: Attempt SSH connection
Write-Step "Attempting SSH connection"
$sshTarget = "ubuntu@$ServerIP"
$sshOpts = @('-o','StrictHostKeyChecking=no','-o','ConnectTimeout=5','-i',$SSHKey)
try {
    $output = & ssh @sshOpts $sshTarget "echo CONNECTED" 2>&1
    Write-Log "SSH Output: $output"
    if ($output -match "CONNECTED") {
        Write-Log "SSH connection successful"
    } else {
        Write-Log "SSH connection failed"
    }
} catch {
    Write-Log "SSH attempt failed: $_"
}

# Step 4: If SSH works, check services
Write-Step "Checking services (only if SSH succeeded)"
try {
    $checkSSH = & ssh @sshOpts $sshTarget "echo CONNECTED" 2>&1
    if ($checkSSH -match "CONNECTED") {
        Write-Step "Querying supervisor status"
        $supervisor = & ssh @sshOpts $sshTarget "sudo supervisorctl status" 2>&1
        Write-Log "Supervisor Status:`n$supervisor"

        Write-Step "Checking nginx status"
        $nginx = & ssh @sshOpts $sshTarget "sudo systemctl status nginx" 2>&1
        Write-Log "Nginx Status:`n$nginx"

        Write-Step "Checking health endpoint"
        $health = & ssh @sshOpts $sshTarget "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/health" 2>&1
        Write-Log "Health Endpoint HTTP code: $health"
    } else {
        Write-Log "Skipping service checks — SSH not available"
    }
} catch {
    Write-Log "Service check failed: $_"
}

Write-Step "Diagnostic complete. See log at $LogFile"