# Run once in PowerShell with adequate permissions to create a Windows scheduled task.
# This schedules only public metadata discovery, NOT rendering or publishing.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root '.venv\Scripts\python.exe'
if (-not (Test-Path $python)) { throw 'Run install_windows.ps1 first.' }
$action = New-ScheduledTaskAction -Execute $python -Argument '-m viral_yt.cli discover --top 10' -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At 8:00AM
Register-ScheduledTask -TaskName 'TrendTide60-Daily-Trend-Discovery' -Action $action -Trigger $trigger -Description 'Official YouTube API research; no video upload' -Force | Out-Null
Write-Host 'Scheduled daily at 8:00AM local. PC must be on; verify key and quota.'
