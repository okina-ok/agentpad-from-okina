# AgentPad 一键启动：守护看门狗（未运行才启动）+ 模拟器 + 绑定面板
$ErrorActionPreference = "SilentlyContinue"
$dir = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $dir

$running = Get-CimInstance Win32_Process -Filter "Name='python.exe'" |
    Where-Object { $_.CommandLine -like '*status_daemon.py*' -or $_.CommandLine -like '*guard_daemon.py*' }
if (-not $running) {
    Start-Process python -ArgumentList "guard_daemon.py" -WorkingDirectory $dir -WindowStyle Hidden
}
Start-Process pythonw -ArgumentList "simulator.py" -WorkingDirectory $dir
Start-Process pythonw -ArgumentList "bind_panel.py" -WorkingDirectory $dir
