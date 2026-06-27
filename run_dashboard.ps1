# Launch the Options Wheel Dashboard.
#
#   .\run_dashboard.ps1            # bind to your Tailscale IP (phone-accessible, private)
#   .\run_dashboard.ps1 -Mode local   # bind to 127.0.0.1 only (this PC, or for `tailscale serve`)
#
# In tailnet mode the app listens ONLY on your Tailscale address, so it is
# reachable from your phone over the encrypted WireGuard tunnel and nowhere else.

param([ValidateSet("tailnet", "local")] [string]$Mode = "tailnet")

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$addr = "127.0.0.1"
if ($Mode -eq "tailnet") {
    $tsip = $null
    try { $tsip = (& tailscale ip -4 2>$null | Select-Object -First 1) } catch {}
    if (-not $tsip) {
        # Fallback: find an address in Tailscale's 100.64.0.0/10 range.
        $tsip = Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
            Where-Object { $_.IPAddress -like "100.*" } |
            Select-Object -First 1 -ExpandProperty IPAddress
    }
    if (-not $tsip) {
        throw "Tailscale IP not found. Start Tailscale and sign in, then retry (or use -Mode local)."
    }
    $addr = $tsip.Trim()
    Write-Host ""
    Write-Host "Dashboard binding to tailnet address: $addr" -ForegroundColor Green
    Write-Host "On your phone (with Tailscale connected), open:  http://${addr}:8501" -ForegroundColor Cyan
    Write-Host ""
}

python -m streamlit run app.py --server.headless true --server.address $addr --server.port 8501
