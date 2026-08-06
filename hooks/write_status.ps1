param(
    [string]$State = "idle",
    [int]$Agent = 1,
    [string]$Summary = ""
)

$dir = Join-Path $env:USERPROFILE ".agent-status"
New-Item -ItemType Directory -Force -Path $dir | Out-Null

$payload = @{
    state   = $State
    ts      = [int][DateTimeOffset]::UtcNow.ToUnixTimeSeconds()
    agent   = "agent-$Agent"
    summary = $Summary
} | ConvertTo-Json

# 用无 BOM 的 UTF-8 写入，避免 Windows PowerShell 5.1 Set-Content 自带的 BOM 问题
$target = Join-Path $dir "agent-$Agent.json"
[System.IO.File]::WriteAllText($target, $payload, (New-Object System.Text.UTF8Encoding $false))
Write-Output "[hook] agent-$Agent -> $State"
