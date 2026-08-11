[CmdletBinding()]
param(
  [switch]$DryRun,
  [switch]$OpenBaseScan,
  [Parameter(Mandatory = $false)][string]$TunnelBin = $env:TUNNEL_BIN
)

$ErrorActionPreference = 'Stop'
$profileRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$payee = if (-not [string]::IsNullOrWhiteSpace($env:ROBO_PAYEE_ADDRESS)) {
  $env:ROBO_PAYEE_ADDRESS
} else {
  $env:ROBOT_PAYEE_ADDRESS
}

foreach ($name in 'ROBOT_PAYEE_ADDRESS', 'TUNNEL_BIN') {
  if ($name -eq 'TUNNEL_BIN' -and -not [string]::IsNullOrWhiteSpace($TunnelBin)) { continue }
  if ($name -eq 'ROBOT_PAYEE_ADDRESS' -and -not [string]::IsNullOrWhiteSpace($payee)) { continue }
  if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
    throw "Missing $name in the current process environment. Do not put credentials in this file."
  }
}
if (-not $DryRun -and [string]::IsNullOrWhiteSpace($env:BASE_SEPOLIA_PRIVATE_KEY)) {
  throw 'Missing BASE_SEPOLIA_PRIVATE_KEY in the current process environment. It is never stored by this launcher.'
}
if (-not (Test-Path -LiteralPath $TunnelBin)) {
  throw "Tunnel binary was not found: '$TunnelBin'. Supply -TunnelBin or TUNNEL_BIN from a hardened catalog-aware Tunnel build."
}

$env:TUNNEL_BIN = (Resolve-Path -LiteralPath $TunnelBin).Path
$env:ROBOT_PAYEE_ADDRESS = $payee
$env:ROBO_PAYEE_ADDRESS = $payee
$env:PYTHONPATH = Join-Path $profileRoot 'bridge'
py -3 (Join-Path $profileRoot 'bridge\download_vendor_assets.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$arguments = @((Join-Path $profileRoot 'bridge\run_live_base_sepolia_e2e.py'), '--visual')
if ($DryRun) { $arguments += '--dry-run' }
if ($OpenBaseScan) { $arguments += '--open-basescan' }
py -3 @arguments
exit $LASTEXITCODE
