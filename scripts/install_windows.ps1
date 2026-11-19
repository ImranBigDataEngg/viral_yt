# TrendTide installer (Windows PowerShell). Usage:
#   powershell -ExecutionPolicy Bypass -File .\scripts\install_windows.ps1 [-WithAI] [-WithVoice] [-WithWeb]
param([switch]$WithAI, [switch]$WithVoice, [switch]$WithWeb)
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
Write-Host "Installing TrendTide in $root" -ForegroundColor Cyan
if (-not (Get-Command python -ErrorAction SilentlyContinue)) { throw 'Install Python 3.11+ (winget install Python.Python.3.12) and reopen PowerShell.' }
if (-not (Get-Command ffmpeg -ErrorAction SilentlyContinue)) {
  Write-Warning 'FFmpeg missing -> installing with winget (Gyan.FFmpeg, includes libass/libflite)'
  winget install --id Gyan.FFmpeg -e --accept-source-agreements --accept-package-agreements
  Write-Warning 'Reopen PowerShell after FFmpeg install so PATH updates.'
}
python -m venv .venv
$py = Join-Path $root '.venv\Scripts\python.exe'
& $py -m pip install --upgrade pip
& $py -m pip install -e . -r requirements.txt
if ($WithAI) {
  & $py -m pip install -r requirements-optional.txt
  & (Join-Path $root '.venv\Scripts\trendtide.exe') doctor --fetch-models | Out-Null   # YuNet face model (230 KB)
}
if ($WithWeb) { & $py -m pip install -r requirements-web.txt }
if ($WithVoice) {
  New-Item -ItemType Directory -Force -Path 'models\piper' | Out-Null
  $base = 'https://huggingface.co/rhasspy/piper-voices/resolve/main/en/en_US/ryan/high'
  Invoke-WebRequest "$base/en_US-ryan-high.onnx" -OutFile 'models\piper\en_US-ryan-high.onnx'
  Invoke-WebRequest "$base/en_US-ryan-high.onnx.json" -OutFile 'models\piper\en_US-ryan-high.onnx.json'
}
if (-not (Test-Path .env)) { Copy-Item .env.example .env }
New-Item -ItemType Directory -Force -Path 'media\authorized\evidence','media\transcripts','media\voice','output','.secrets' | Out-Null
if (-not (Test-Path 'media\authorized\assets.csv')) { Copy-Item 'examples\assets_template.csv' 'media\authorized\assets.csv' }
& $py -m unittest discover -s tests
Write-Host "Done. Next: edit .env (YOUTUBE_API_KEY), then:  .\.venv\Scripts\trendtide.exe discover" -ForegroundColor Green
