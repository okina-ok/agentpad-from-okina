# Install AgentPad autostart (per-user, no admin needed).
# Creates a Startup shortcut that launches start_live.ps1 at every login.
# Disable anytime with uninstall_autostart.ps1.

$ErrorActionPreference = "Stop"
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "AgentPad.lnk"

$ws = New-Object -ComObject WScript.Shell
$sc = $ws.CreateShortcut($shortcutPath)
$sc.TargetPath = "powershell.exe"
$sc.Arguments = "-NoProfile -ExecutionPolicy Bypass -File `"$scriptDir\start_live.ps1`""
$sc.WorkingDirectory = $scriptDir
$sc.WindowStyle = 7
$sc.Description = "AgentPad status daemon + simulator"
$sc.Save()

Write-Host "Autostart installed: $shortcutPath" -ForegroundColor Green
Write-Host "AgentPad will start at next login. Disable anytime with uninstall_autostart.ps1."
