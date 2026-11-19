$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Installing TrendTide 60 in $root" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11+ and add python to PATH first.' }
python -m venv .venv
$python = Join-Path $root '.venv\Scripts\python.exe'
& $python -m pip install --upgrade pip
& $python -m pip install -e . -r requirements.txt
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Path 'media\authorized','media\transcripts','output\trends','output\shorts','output\manifests','.secrets' -Force | Out-Null
if (-not (Test-Path 'media\authorized\assets.csv')) { Copy-Item 'examples\assets_template.csv' 'media\authorized\assets.csv' }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
 Write-Warning 'FFmpeg missing. Install with winget install Gyan.FFmpeg then reopen PowerShell.'
}
Write-Host "Done. Edit $root\.env and the authorized asset ledger before running." -ForegroundColor Green
Write-Host '.\.venv\Scripts\python.exe -m viral_yt.cli discover --top 10'
