# Registers a daily Windows Scheduled Task for the SAFE cycle: discover -> briefs -> render approved -> manifest.
# Add -Upload to also upload approved renders as PRIVATE (never public). PC must be on at the scheduled time.
param([string]$At = '9:17AM', [switch]$Upload)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root '.venv\Scripts\trendtide.exe'
if (-not (Test-Path $exe)) { throw 'Run scripts\install_windows.ps1 first.' }
$args = 'run' + ($(if ($Upload) { ' --execute' } else { '' }))
$action = New-ScheduledTaskAction -Execute $exe -Argument $args -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -Daily -At $At
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun -ExecutionTimeLimit (New-TimeSpan -Hours 2)
Register-ScheduledTask -TaskName 'TrendTide-Daily' -Action $action -Trigger $trigger -Settings $settings -Description 'TrendTide daily cycle' -Force | Out-Null
Write-Host "Scheduled 'trendtide $args' daily at $At (StartWhenAvailable catches up if the PC was asleep)."
