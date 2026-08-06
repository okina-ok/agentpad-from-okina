# Remove the AgentPad autostart entry installed by install_autostart.ps1.

$ErrorActionPreference = "Stop"
$startup = [Environment]::GetFolderPath("Startup")
$shortcutPath = Join-Path $startup "AgentPad.lnk"

if (Test-Path -LiteralPath $shortcutPath) {
    Remove-Item -LiteralPath $shortcutPath
    Write-Host "Autostart removed: $shortcutPath" -ForegroundColor Green
} else {
    Write-Host "No AgentPad autostart entry found." -ForegroundColor Yellow
}
