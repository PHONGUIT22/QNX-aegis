# ==============================================================================
# QNX-Aegis: Target Cross-Compilation Script (PowerShell)
# Cross-compiles native QNX C source code for QNX Neutrino RTOS 8.0 (x86_64).
# ==============================================================================

[CmdletBinding()]
param (
    [string]$TargetArch = "gcc_ntox86_64",
    [switch]$Clean
)

$ErrorActionPreference = "Stop"

$RootDir = Resolve-Path "$PSScriptRoot\.."
$TargetDir = Join-Path $RootDir "qnx-target"
$BinDir = Join-Path $TargetDir "bin"

Write-Host "`n========================================================" -ForegroundColor Cyan
Write-Host "  QNX-Aegis: Building QNX 8.0 Target Binaries ($TargetArch) " -ForegroundColor Cyan
Write-Host "========================================================`n" -ForegroundColor Cyan

# Source QNX environment if qcc is not already in PATH
if (-not (Get-Command "qcc" -ErrorAction SilentlyContinue)) {
    $setupScript = Join-Path $PSScriptRoot "setup-env.ps1"
    if (Test-Path $setupScript) {
        . $setupScript
    }
}

if (-not (Get-Command "qcc" -ErrorAction SilentlyContinue)) {
    Write-Host "[ERROR] 'qcc' cross-compiler could not be found. Please check QNX_SDP_PATH." -ForegroundColor Red
    exit 1
}

if ($Clean) {
    Write-Host "[*] Cleaning build artifacts..." -ForegroundColor Yellow
    if (Test-Path $BinDir) { Remove-Item -Recurse -Force $BinDir }
    Get-ChildItem -Path $TargetDir -Filter "*.o" -Recurse | Remove-Item -Force
}

if (-not (Test-Path $BinDir)) {
    New-Item -ItemType Directory -Path $BinDir -Force | Out-Null
}

$cflags = "-V$TargetArch", "-O2", "-Wall", "-Wextra", "-D_QNX_SOURCE"

# 1. Build Inspector Daemon
Write-Host "[1/5] Compiling /dev/qnx_inspector Resource Manager..." -ForegroundColor White
$daemonSrc = @(
    (Join-Path $TargetDir "inspector-daemon\main.c"),
    (Join-Path $TargetDir "inspector-daemon\ipc_analyzer.c"),
    (Join-Path $TargetDir "inspector-daemon\slog2_reader.c")
)
$daemonOut = Join-Path $BinDir "qnx_inspector"
$incDir = Join-Path $TargetDir "inspector-daemon"

$daemonCmd = @("qcc") + $cflags + @("-I$incDir", "-lslog2", "-o", $daemonOut) + $daemonSrc
Write-Host "     > $($daemonCmd -join ' ')" -ForegroundColor DarkGray
& qcc @($cflags + @("-I$incDir", "-lslog2", "-o", $daemonOut) + $daemonSrc)
if ($LASTEXITCODE -eq 0 -and (Test-Path $daemonOut)) {
    Write-Host "     [OK] Built: $daemonOut" -ForegroundColor Green
} else {
    Write-Host "     [FAIL] Failed to build $daemonOut" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 2. Build Deadlock Demo
Write-Host "[2/5] Compiling QNX IPC Deadlock Demo..." -ForegroundColor White
$deadlockSrc = Join-Path $TargetDir "demos\ipc_deadlock_demo.c"
$deadlockOut = Join-Path $BinDir "ipc_deadlock_demo"
& qcc @($cflags + @("-o", $deadlockOut, $deadlockSrc))
if ($LASTEXITCODE -eq 0 -and (Test-Path $deadlockOut)) {
    Write-Host "     [OK] Built: $deadlockOut" -ForegroundColor Green
} else {
    Write-Host "     [FAIL] Failed to build $deadlockOut" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 3. Build Healthy IPC Demo
Write-Host "[3/5] Compiling QNX Healthy IPC Demo..." -ForegroundColor White
$healthySrc = Join-Path $TargetDir "demos\ipc_healthy_demo.c"
$healthyOut = Join-Path $BinDir "ipc_healthy_demo"
& qcc @($cflags + @("-o", $healthyOut, $healthySrc))
if ($LASTEXITCODE -eq 0 -and (Test-Path $healthyOut)) {
    Write-Host "     [OK] Built: $healthyOut" -ForegroundColor Green
} else {
    Write-Host "     [FAIL] Failed to build $healthyOut" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 4. Build Slog2 Crash Demo
Write-Host "[4/5] Compiling Slog2 Logging & Crash Demo..." -ForegroundColor White
$slogSrc = Join-Path $TargetDir "demos\slog2_crash_demo.c"
$slogOut = Join-Path $BinDir "slog2_crash_demo"
& qcc @($cflags + @("-lslog2", "-o", $slogOut, $slogSrc))
if ($LASTEXITCODE -eq 0 -and (Test-Path $slogOut)) {
    Write-Host "     [OK] Built: $slogOut" -ForegroundColor Green
} else {
    Write-Host "     [FAIL] Failed to build $slogOut" -ForegroundColor Red
    exit $LASTEXITCODE
}

# 5. Build IPC Hardware Benchmark Demo
Write-Host "[5/5] Compiling QNX IPC ClockCycles Benchmark Demo..." -ForegroundColor White
$benchSrc = Join-Path $TargetDir "demos\ipc_benchmark_demo.c"
$benchOut = Join-Path $BinDir "ipc_benchmark_demo"
& qcc @($cflags + @("-lslog2", "-o", $benchOut, $benchSrc))
if ($LASTEXITCODE -eq 0 -and (Test-Path $benchOut)) {
    Write-Host "     [OK] Built: $benchOut" -ForegroundColor Green
} else {
    Write-Host "     [FAIL] Failed to build $benchOut" -ForegroundColor Red
    exit $LASTEXITCODE
}

Write-Host "`n========================================================" -ForegroundColor Green
Write-Host "  All Target Binaries Successfully Cross-Compiled!     " -ForegroundColor Green
Write-Host "  Location: $BinDir" -ForegroundColor Green
Write-Host "========================================================`n" -ForegroundColor Green
