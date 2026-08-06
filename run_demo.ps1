# AgentPad one-click demo (M1)
# Starts the daemon and demo writer in the background,
# then opens the simulator window in the foreground.
# Closing the simulator window (or pressing Ctrl+C) stops everything.
# Use -NoDemo to skip the fake demo writer and wait for a real agent
# (e.g. this Codex session) to write status files.

param(
    [switch]$NoDemo
)

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

$logDir = Join-Path $dir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$daemon = Start-Process python -ArgumentList "status_daemon.py" -WorkingDirectory $dir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "daemon.log") -RedirectStandardError (Join-Path $logDir "daemon.err.log")

$demo = $null
if (-not $NoDemo) {
    $demo = Start-Process python -ArgumentList "demo_write.py --loop" -WorkingDirectory $dir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "demo.log") -RedirectStandardError (Join-Path $logDir "demo.err.log")
}

Start-Sleep -Milliseconds 800

Write-Host "daemon started in background (logs in logs\)." -ForegroundColor Cyan
if ($NoDemo) {
    Write-Host "-NoDemo: fake writer skipped. Waiting for a real agent to write status files..." -ForegroundColor Yellow
} else {
    Write-Host "demo writer started too. Close the simulator window or press Ctrl+C to stop." -ForegroundColor Cyan
}

try {
    python simulator.py
} finally {
    Stop-Process -Id $daemon.Id -Force -ErrorAction SilentlyContinue
    if ($demo) { Stop-Process -Id $demo.Id -Force -ErrorAction SilentlyContinue }
    Write-Host "cleaned up. demo finished." -ForegroundColor Green
}
