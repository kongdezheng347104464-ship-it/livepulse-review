<#
.SYNOPSIS
  Deterministic backend restart — runtime gatekeeper.

.DESCRIPTION
  This script is the ONLY supported way to restart the WorkBuddy
  backend during development. It solves the class of problem that
  caused the 2026-05-27 composite_score incident, where multiple
  uvicorn workers (some via system Python, some via venv, several
  holding stale bytecode) were simultaneously listening on port 8004
  and serving requests from inconsistent code versions.

  Guarantees on successful exit:
    1. No other python.exe matching `uvicorn.*app\.main` is running
       (other Python workflows like Jupyter / IDE LSP are NOT killed).
    2. All __pycache__/*.pyc files under backend/app are removed,
       eliminating stale bytecode from previous runs.
    3. The new backend is started using `.venv\Scripts\python.exe`
       explicitly — never `python`, `py`, or `python3` from PATH.
    4. By default, --reload is NOT used (reload causes multi-worker
       process trees that can drift). Pass -DevReload to opt in.
    5. The /api/v1/_debug/runtime endpoint is polled until it
       returns a boot_id different from the one we captured before
       the restart. Until that happens, the script does not return.
       This proves the new code is actually serving traffic.

  If the new boot fingerprint does not appear within $TimeoutSec
  seconds, the script exits non-zero and prints diagnostics.

.PARAMETER DevReload
  Enable uvicorn --reload mode. Sets WB_RELOAD_MODE=1 so the
  fingerprint endpoint surfaces the fact that this worker is in
  non-deterministic reload mode. Use only when actively iterating on
  API/frontend code that doesn't touch pipeline state.

.PARAMETER TimeoutSec
  Max seconds to wait for the new worker to come up and report a new
  boot_id. Default 30. Increase if you're loading large embedding
  models at startup.

.EXAMPLE
  .\scripts\runtime\restart_backend.ps1
  .\scripts\runtime\restart_backend.ps1 -DevReload
  .\scripts\runtime\restart_backend.ps1 -TimeoutSec 60
#>
[CmdletBinding()]
param(
    [switch]$DevReload,
    [int]$TimeoutSec = 30
)

$ErrorActionPreference = "Stop"

# Pin paths to project root regardless of where the script is invoked from.
# This script must work from any cwd — operators don't always run it from
# the project root.
$ProjectRoot = Split-Path -Parent (Split-Path -Parent $PSScriptRoot)
$BackendDir = Join-Path $ProjectRoot "backend"
$VenvPython = Join-Path $ProjectRoot ".venv\Scripts\python.exe"
$EnvFile = Join-Path $BackendDir ".env"
$StderrLog = Join-Path $ProjectRoot "backend.err.log"
$StdoutLog = Join-Path $ProjectRoot "backend.log"

if (-not (Test-Path $VenvPython)) {
    Write-Host "❌ venv python not found at $VenvPython" -ForegroundColor Red
    Write-Host "   Create the venv first: python -m venv .venv && .venv\Scripts\pip install -r backend/requirements.txt"
    exit 1
}

# -------------------------------------------------------------------------
# Step 0: Capture the current boot_id (if backend is running) so we can
# tell when the NEW one appears. If backend is down, this is just null
# and any boot_id from the new process counts as "started".
# -------------------------------------------------------------------------
$ApiKey = $null
if (Test-Path $EnvFile) {
    $line = Get-Content $EnvFile | Select-String "^API_KEY="
    if ($line) { $ApiKey = ($line.ToString().Split("=", 2)[1]).Trim() }
}
if (-not $ApiKey) {
    Write-Host "❌ API_KEY not found in $EnvFile" -ForegroundColor Red
    exit 1
}
$Headers = @{ "X-API-Key" = $ApiKey }

$OldBootId = $null
try {
    $r = Invoke-RestMethod -Uri "http://localhost:8004/api/v1/_debug/runtime" -Headers $Headers -TimeoutSec 3
    $OldBootId = $r.data.boot_id
    Write-Host "ℹ️  Current backend boot_id=$OldBootId (will wait for change)" -ForegroundColor Gray
} catch {
    Write-Host "ℹ️  No backend currently responding — fresh start" -ForegroundColor Gray
}

# -------------------------------------------------------------------------
# Step 1: Precise kill — only python.exe running our uvicorn entrypoint.
# DO NOT use Stop-Process -Name python -Force: that would kill the user's
# Jupyter notebook, IDE Python LSP, and any other unrelated Python work.
# -------------------------------------------------------------------------
Write-Host ""
Write-Host "▶ Stopping previous uvicorn workers..." -ForegroundColor Cyan
$victims = Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
    Where-Object { $_.CommandLine -and $_.CommandLine -match 'uvicorn.*app\.main' }

if ($victims) {
    foreach ($v in $victims) {
        $isVenv = $v.CommandLine -like "*\.venv\*"
        $marker = if ($isVenv) { "venv" } else { "system" }
        Write-Host "   kill PID=$($v.ProcessId) ($marker python) cmd=$($v.CommandLine.Substring(0, [Math]::Min($v.CommandLine.Length, 80)))..."
        Stop-Process -Id $v.ProcessId -Force -ErrorAction SilentlyContinue
    }
    # Give the OS a moment to release the port and tear down child workers.
    Start-Sleep -Seconds 2
} else {
    Write-Host "   (no matching python.exe processes found)"
}

# -------------------------------------------------------------------------
# Step 2: Clear .pyc caches under backend/app.
# Stale .pyc files can shadow source changes on rare occasions (file system
# mtime jitter, esp. on Windows). Removing them costs ~50ms and eliminates
# this class of debugging confusion entirely.
# -------------------------------------------------------------------------
Write-Host ""
Write-Host "▶ Clearing .pyc caches..." -ForegroundColor Cyan
$pycFiles = Get-ChildItem -Path (Join-Path $BackendDir "app") -Filter "*.pyc" -Recurse -Force -ErrorAction SilentlyContinue
if ($pycFiles) {
    $pycFiles | Remove-Item -Force -ErrorAction SilentlyContinue
    Write-Host "   removed $($pycFiles.Count) .pyc files"
} else {
    Write-Host "   (none found)"
}

# -------------------------------------------------------------------------
# Step 3: Launch new backend with venv python explicitly.
# Reload mode is opt-in via -DevReload. Setting WB_RELOAD_MODE=1 lets the
# fingerprint endpoint surface the fact that this worker is in non-
# deterministic mode, so anyone debugging knows what they're looking at.
# -------------------------------------------------------------------------
Write-Host ""
$modeStr = if ($DevReload) { "DEV-RELOAD (non-deterministic)" } else { "DETERMINISTIC (no reload)" }
Write-Host "▶ Starting backend in $modeStr mode..." -ForegroundColor Cyan
Write-Host "   python = $VenvPython"

# Clear logs so the boot fingerprint is the first thing we see when grepping.
Remove-Item $StderrLog -ErrorAction SilentlyContinue
Remove-Item $StdoutLog -ErrorAction SilentlyContinue

$uvicornArgs = @("-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8004")
if ($DevReload) { $uvicornArgs += "--reload" }

# Set WB_RELOAD_MODE BEFORE spawning so the child process inherits it.
# This is what the fingerprint module reads to surface reload_mode.
if ($DevReload) { $env:WB_RELOAD_MODE = "1" } else { $env:WB_RELOAD_MODE = "0" }

$proc = Start-Process -FilePath $VenvPython `
    -ArgumentList $uvicornArgs `
    -WorkingDirectory $BackendDir `
    -WindowStyle Hidden `
    -RedirectStandardOutput $StdoutLog `
    -RedirectStandardError $StderrLog `
    -PassThru

Write-Host "   launched PID=$($proc.Id)"

# -------------------------------------------------------------------------
# Step 4: Poll /runtime until boot_id changes (or appears, on cold start).
# Until this succeeds, the script has NOT confirmed the new code is
# actually serving traffic. "uvicorn says ready" is not the same thing as
# "the right code is running" — today's incident proved that.
# -------------------------------------------------------------------------
Write-Host ""
Write-Host "▶ Polling /api/v1/_debug/runtime until new boot_id appears (timeout ${TimeoutSec}s)..." -ForegroundColor Cyan

$startPoll = Get-Date
$newFingerprint = $null
$attempts = 0
while (((Get-Date) - $startPoll).TotalSeconds -lt $TimeoutSec) {
    $attempts++
    Start-Sleep -Milliseconds 800
    try {
        $r = Invoke-RestMethod -Uri "http://localhost:8004/api/v1/_debug/runtime" -Headers $Headers -TimeoutSec 3 -ErrorAction Stop
        $candidate = $r.data
        if ($candidate.boot_id -ne $OldBootId) {
            $newFingerprint = $candidate
            break
        }
        # Same boot_id — old worker still serving. Keep polling.
    } catch {
        # Backend not yet accepting requests — expected during startup,
        # especially with embedding model loading. Keep polling.
    }
}

if (-not $newFingerprint) {
    Write-Host ""
    Write-Host "❌ Timed out waiting for new boot_id after $attempts attempts" -ForegroundColor Red
    Write-Host ""
    Write-Host "=== backend.err.log tail ===" -ForegroundColor Yellow
    Get-Content $StderrLog -Tail 20 -ErrorAction SilentlyContinue
    Write-Host ""
    Write-Host "Process $($proc.Id) is_alive=$((Get-Process -Id $proc.Id -ErrorAction SilentlyContinue) -ne $null)"
    exit 2
}

# -------------------------------------------------------------------------
# Step 5: Report the new fingerprint. This is the proof — operators can
# compare these fields against what they expect, and the same fields
# appear in pipeline.log keyed by `[boot-fingerprint]`.
# -------------------------------------------------------------------------
$elapsed = [Math]::Round(((Get-Date) - $startPoll).TotalSeconds, 1)
Write-Host ""
Write-Host "✅ New worker confirmed serving traffic ($elapsed sec, $attempts polls)" -ForegroundColor Green
Write-Host ""
Write-Host "   boot_id       : $($newFingerprint.boot_id)"
Write-Host "   pid           : $($newFingerprint.pid)"
Write-Host "   python        : $($newFingerprint.python_executable)"
Write-Host "   is_venv       : $($newFingerprint.is_venv)"
Write-Host "   python_version: $($newFingerprint.python_version)"
Write-Host "   reload_mode   : $($newFingerprint.reload_mode)"
Write-Host "   pipeline_hash : $($newFingerprint.pipeline_hash)"
Write-Host "   git_sha       : $($newFingerprint.git_sha)"
Write-Host "   started_at    : $($newFingerprint.started_at)"

# Hard sanity check: this script's job is to put the deterministic
# runtime in place. If the new worker is somehow NOT venv, the gatekeeper
# failed and we should exit non-zero so CI / wrappers can react.
if (-not $newFingerprint.is_venv) {
    Write-Host ""
    Write-Host "❌ New worker is NOT running from venv — runtime identity lock FAILED" -ForegroundColor Red
    exit 3
}

exit 0
