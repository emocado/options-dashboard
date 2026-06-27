# Run the local sync agent: pull from moomoo OpenD and push to the cloud (Turso).
# Intended for Windows Task Scheduler (e.g. at logon + every few hours), but also
# fine to run by hand. Turso creds are read from .streamlit/secrets.toml by the
# Python script. Output is appended to logs\sync_to_cloud.log.
#
#   .\tools\sync_to_cloud.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot   # project root (this script lives in tools\)
Set-Location $root

$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$log = Join-Path $logDir "sync_to_cloud.log"

python tools/sync_to_cloud.py *>> $log
Write-Host "Sync agent finished. Log: $log"
