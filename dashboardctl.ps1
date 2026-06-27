# Background controller for the Options Wheel Dashboard.
#
#   .\dashboardctl.ps1 start              # start hidden in the background (tailnet)
#   .\dashboardctl.ps1 start -Mode local  # start bound to localhost only
#   .\dashboardctl.ps1 status             # is it running?
#   .\dashboardctl.ps1 logs               # tail the log file
#   .\dashboardctl.ps1 stop               # stop it
#   .\dashboardctl.ps1 restart            # stop then start
#
# Runs detached: it keeps running after you close this terminal.

param(
    [Parameter(Position = 0)]
    [ValidateSet("start", "stop", "status", "logs", "restart")]
    [string]$Action = "status",
    [ValidateSet("tailnet", "local")]
    [string]$Mode = "tailnet"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$logDir = Join-Path $PSScriptRoot "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "dashboard.out.log"
$errLog = Join-Path $logDir "dashboard.err.log"

function Get-DashboardProc {
    Get-CimInstance Win32_Process -Filter "Name='python.exe'" -ErrorAction SilentlyContinue |
        Where-Object { $_.CommandLine -match 'streamlit run app\.py' }
}

function Start-Dashboard {
    if (Get-DashboardProc) { Write-Host "Already running." -ForegroundColor Yellow; return }
    $psArgs = "-NoProfile -ExecutionPolicy Bypass -File `"$PSScriptRoot\run_dashboard.ps1`" -Mode $Mode"
    Start-Process -FilePath "powershell.exe" -ArgumentList $psArgs `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog -RedirectStandardError $errLog | Out-Null
    Start-Sleep -Seconds 3
    $proc = Get-DashboardProc
    if ($proc) {
        Write-Host "Started in background (python PID $($proc.ProcessId -join ', '))." -ForegroundColor Green
        Write-Host "Logs: $outLog"
    } else {
        Write-Host "Did not start. Check logs:" -ForegroundColor Red
        if (Test-Path $errLog) { Get-Content $errLog -Tail 20 }
    }
}

function Stop-Dashboard {
    $procs = Get-DashboardProc
    if (-not $procs) { Write-Host "Not running." -ForegroundColor Yellow; return }
    $procs | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
    Write-Host "Stopped." -ForegroundColor Green
}

switch ($Action) {
    "start"   { Start-Dashboard }
    "stop"    { Stop-Dashboard }
    "restart" { Stop-Dashboard; Start-Sleep -Seconds 1; Start-Dashboard }
    "status"  {
        $procs = Get-DashboardProc
        if ($procs) { Write-Host "RUNNING (PID $($procs.ProcessId -join ', '))" -ForegroundColor Green }
        else { Write-Host "STOPPED" -ForegroundColor Yellow }
    }
    "logs"    {
        if (Test-Path $outLog) { Get-Content $outLog -Tail 40 } else { Write-Host "No logs yet." }
    }
}
