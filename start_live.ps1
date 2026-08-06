# AgentPad live mode: daemon (hidden) + always-on-top simulator.
# Use install_autostart.ps1 to run this at every Windows login.
# Closing the simulator window stops both.

$ErrorActionPreference = "Stop"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

$logDir = Join-Path $dir "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

$daemon = Start-Process python -ArgumentList "status_daemon.py" -WorkingDirectory $dir -WindowStyle Hidden -PassThru -RedirectStandardOutput (Join-Path $logDir "daemon.log") -RedirectStandardError (Join-Path $logDir "daemon.err.log")

Start-Sleep -Milliseconds 600

Write-Host "AgentPad live: daemon started (logs in logs\). Closing the simulator window stops it." -ForegroundColor Cyan

try {
    python simulator.py
} finally {
    Stop-Process -Id $daemon.Id -Force -ErrorAction SilentlyContinue
    Write-Host "AgentPad stopped." -ForegroundColor Green
}
