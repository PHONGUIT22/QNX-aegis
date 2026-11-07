# ==============================================================================
# QNX-Aegis: Deploy to QEMU Target (PowerShell)
# Transfers cross-compiled binaries to the QNX 8.0 QEMU VM and prepares execution.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$HostIP = "127.0.0.1",
    [int]$Port = 2222,
    [string]$User = "qnxuser",
    [string]$Password = "qnxuser",
    [string]$RemoteDir = "/tmp/qnx-aegis",
    [switch]$StartDaemon
)

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path "$PSScriptRoot\.."
$BinDir = Join-Path $RootDir "qnx-target\bin"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  QNX-Aegis: Deploying Binaries to QNX 8.0 QEMU Target  " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# Load .env if present
$EnvFile = Join-Path $RootDir ".env"
if (Test-Path $EnvFile) {
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $name = $parts[0].Trim()
            $val = $parts[1].Trim()
            if ($name -eq "QEMU_HOST" -and $HostIP -eq "127.0.0.1") { $HostIP = $val }
            if ($name -eq "QEMU_SSH_PORT" -and $Port -eq 2222) { $Port = [int]$val }
            if ($name -eq "QEMU_SSH_USER" -and $User -eq "qnxuser") { $User = $val }
            if ($name -eq "QEMU_SSH_PASSWORD" -and $Password -eq "qnxuser") { $Password = $val }
            if ($name -eq "QEMU_DEPLOY_DIR" -and $RemoteDir -eq "/tmp/qnx-aegis") { $RemoteDir = $val }
        }
    }
}

if (-not (Test-Path $BinDir)) {
    Write-Host "[ERROR] Target binaries directory not found: $BinDir" -ForegroundColor Red
    Write-Host "        Run .\scripts\build-target.ps1 first." -ForegroundColor Yellow
    exit 1
}

$binaries = Get-ChildItem -Path $BinDir -File
if ($binaries.Count -eq 0) {
    Write-Host "[ERROR] No binaries found in $BinDir. Run .\scripts\build-target.ps1 first." -ForegroundColor Red
    exit 1
}

Write-Host "[*] Target Host: ${User}@${HostIP}:${Port}" -ForegroundColor Cyan
Write-Host "[*] Destination: ${RemoteDir}" -ForegroundColor Cyan
Write-Host "[*] Binaries to deploy ($($binaries.Count)):" -ForegroundColor Cyan
$binaries | ForEach-Object { Write-Host "    - $($_.Name) ($([math]::Round($_.Length / 1KB, 1)) KB)" -ForegroundColor DarkGray }

# Deploy using Python paramiko script for clean password handling across Windows hosts
$pythonExe = Join-Path $RootDir "mcp-server\venv\Scripts\python.exe"
if (-not (Test-Path $pythonExe)) {
    $pythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
    if ($pythonCmd) { $pythonExe = "python" }
}

if ($pythonExe -and ((Test-Path $pythonExe -ErrorAction SilentlyContinue) -or ($pythonExe -eq "python"))) {
    Write-Host "`n[*] Initiating SCP transfer via Python bridge ($pythonExe)..." -ForegroundColor White
    $deployPy = @"
import sys
import os
import paramiko
from scp import SCPClient

host = '$HostIP'
port = $Port
user = '$User'
password = '$Password'
remote_dir = '$RemoteDir'
bin_dir = r'$BinDir'

try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.connect(host, port=port, username=user, password=password, timeout=10)
    
    # Create remote directory and stop existing processes to release file locks
    stdin, stdout, stderr = ssh.exec_command(f'echo {password} | sudo -S slay -f qnx_inspector 2>/dev/null; slay -f qnx_inspector 2>/dev/null; mkdir -p {remote_dir}')
    stdout.channel.recv_exit_status()
    # Remove existing binaries so scp never hits 'Resource busy'
    ssh.exec_command(f'rm -f {remote_dir}/* 2>/dev/null')

    # SCP files
    with SCPClient(ssh.get_transport()) as scp:
        for fname in os.listdir(bin_dir):
            fpath = os.path.join(bin_dir, fname)
            if os.path.isfile(fpath):
                print(f' -> Uploading: {fname}')
                scp.put(fpath, remote_path=f'{remote_dir}/{fname}')

    # Make executable
    stdin, stdout, stderr = ssh.exec_command(f'chmod +x {remote_dir}/*')
    stdout.channel.recv_exit_status()
    print('[OK] Permissions updated (chmod +x)')

    if '$StartDaemon' == 'True':
        print('[*] Starting /dev/qnx_inspector daemon in background (sudo detached)...')
        ssh.exec_command('slay -f qnx_inspector 2>/dev/null')
        launch_cmd = f'sh -c "echo {password} | sudo -S nohup {remote_dir}/qnx_inspector >/dev/null 2>&1 </dev/null &"'
        ssh.exec_command(launch_cmd)
        print('[OK] Inspector daemon launched on QNX target.')

    ssh.close()
    print('[OK] Deployment finished successfully.')
except Exception as e:
    print(f'[FAIL] Deployment failed: {e}', file=sys.stderr)
    sys.exit(1)
"@
    
    $tempPy = [System.IO.Path]::GetTempFileName() + ".py"
    Set-Content -Path $tempPy -Value $deployPy
    try {
        & $pythonExe $tempPy
    } finally {
        if (Test-Path $tempPy) { Remove-Item -Force $tempPy }
    }
} else {
    Write-Host "[WARNING] Python not found. Falling back to OpenSSH scp command..." -ForegroundColor Yellow
    Write-Host "          Run: scp -P $Port $BinDir/* ${User}@${HostIP}:${RemoteDir}" -ForegroundColor DarkGray
}

Write-Host "`n[OK] Deploy script complete.`n" -ForegroundColor Green
