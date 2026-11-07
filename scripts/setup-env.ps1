# ==============================================================================
# QNX-Aegis: Environment Setup & Diagnostics (PowerShell)
# Loads QNX SDP 8.0 environment variables and validates toolchains.
# Automatically detects QNX 8.0 SDP installations across Windows drives.
# ==============================================================================

[CmdletBinding()]
param (
    [string]$EnvFile = "$PSScriptRoot\..\.env"
)

$ErrorActionPreference = "Continue"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  QNX-Aegis (QNX-MCP Studio) - Host Environment Setup   " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# 1. Load .env file if present
if (Test-Path $EnvFile) {
    Write-Host "[*] Reading configuration from: $EnvFile" -ForegroundColor DarkGray
    Get-Content $EnvFile | ForEach-Object {
        $line = $_.Trim()
        if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
            $parts = $line.Split("=", 2)
            $name = $parts[0].Trim()
            $val = $parts[1].Trim()
            if (-not [System.Environment]::GetEnvironmentVariable($name, "Process")) {
                [System.Environment]::SetEnvironmentVariable($name, $val, "Process")
            }
        }
    }
} else {
    Write-Host "[!] No .env file found. Using defaults and active environment." -ForegroundColor Yellow
}

# 2. Check or auto-detect QNX SDP 8.0 installation
$candidatePaths = @(
    $env:QNX_SDP_PATH,
    "$env:USERPROFILE\qnx800\qnxsdp-env.bat",
    "C:\qnx800\qnxsdp-env.bat",
    "D:\qnx800\qnxsdp-env.bat",
    "D:\QNX\qnx800\qnxsdp-env.bat",
    "C:\QNX\qnx800\qnxsdp-env.bat",
    "C:\QNX800\qnxsdp-env.bat",
    "D:\QNX800\qnxsdp-env.bat"
)

$qnxSdpPath = $null
foreach ($cp in $candidatePaths) {
    if ($cp -and (Test-Path $cp)) {
        $qnxSdpPath = $cp
        break
    }
}

$qccFound = $null -ne (Get-Command "qcc" -ErrorAction SilentlyContinue)

if (-not $qccFound -and $qnxSdpPath) {
    Write-Host "[*] Found QNX SDP 8.0 environment at: $qnxSdpPath" -ForegroundColor Yellow
    Write-Host "[*] Sourcing environment variables into current session..." -ForegroundColor DarkGray
    
    # Run batch file in cmd.exe and extract exported environment variables
    $cmdOutput = cmd.exe /c "call `"$qnxSdpPath`" >nul 2>&1 && set"
    foreach ($varLine in $cmdOutput) {
        if ($varLine -match "^([^=]+)=(.*)$") {
            $vName = $matches[1]
            $vVal = $matches[2]
            [System.Environment]::SetEnvironmentVariable($vName, $vVal, "Process")
        }
    }
    [System.Environment]::SetEnvironmentVariable("QNX_SDP_PATH", $qnxSdpPath, "Process")
    $qccFound = $null -ne (Get-Command "qcc" -ErrorAction SilentlyContinue)
}

# 3. Validate QCC
if ($qccFound) {
    $qccVersion = & qcc -V 2>&1 | Out-String
    Write-Host "[OK] QNX Cross-Compiler 'qcc' is available!" -ForegroundColor Green
    Write-Host "     QNX_HOST:   $env:QNX_HOST" -ForegroundColor DarkGray
    Write-Host "     QNX_TARGET: $env:QNX_TARGET" -ForegroundColor DarkGray
    Write-Host "     SDP Script: $qnxSdpPath" -ForegroundColor DarkGray
} else {
    Write-Host "[WARNING] 'qcc' compiler not found in current PATH." -ForegroundColor Red
    Write-Host "          Checked candidate paths: $($candidatePaths -join ', ')" -ForegroundColor DarkGray
    Write-Host "          Note: You can still run the MCP server and UI in diagnostic/simulation mode." -ForegroundColor DarkYellow
}

# 4. Check Python & Virtual Environment
$pythonCmd = Get-Command "python" -ErrorAction SilentlyContinue
if ($pythonCmd) {
    $pyVersion = & python --version 2>&1 | Out-String
    Write-Host "[OK] Python detected: $($pyVersion.Trim())" -ForegroundColor Green
} else {
    Write-Host "[ERROR] Python 3.10+ is required but was not found in PATH." -ForegroundColor Red
}

$venvPath = Join-Path $PSScriptRoot "..\mcp-server\venv"
if (Test-Path $venvPath) {
    Write-Host "[OK] Python virtual environment found at: $venvPath" -ForegroundColor Green
} else {
    Write-Host "[INFO] Python venv not found at $venvPath. Run: cd mcp-server; python -m venv venv" -ForegroundColor DarkGray
}

# 5. Check QEMU Target Configuration
$qemuHost = if ($env:QEMU_HOST) { $env:QEMU_HOST } else { "127.0.0.1" }
$qemuPort = if ($env:QEMU_SSH_PORT) { $env:QEMU_SSH_PORT } else { "2222" }
Write-Host "[*] QEMU Target SSH configured as: ${qemuHost}:${qemuPort}" -ForegroundColor Cyan

Write-Host "`n[OK] Setup verification complete.`n" -ForegroundColor Green
