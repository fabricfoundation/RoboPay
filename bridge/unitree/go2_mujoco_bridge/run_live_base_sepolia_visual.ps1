[CmdletBinding()]
param(
    [ValidateRange(0, 20)][int]$HoldSeconds = 5,
    [switch]$DryRun,
    [switch]$NoOpenBaseScan,
    [switch]$PauseAfter,
    [Parameter(Mandatory = $false)][string]$TunnelBin = $env:TUNNEL_BIN
)

$ErrorActionPreference = 'Stop'

$privateKey = if (-not [string]::IsNullOrWhiteSpace($env:PRIVATE_KEY)) {
    $env:PRIVATE_KEY
} else {
    $env:BASE_SEPOLIA_PRIVATE_KEY
}
$payee = if (-not [string]::IsNullOrWhiteSpace($env:ROBO_PAYEE_ADDRESS)) {
    $env:ROBO_PAYEE_ADDRESS
} else {
    $env:ROBOT_PAYEE_ADDRESS
}
if (-not $DryRun -and [string]::IsNullOrWhiteSpace($privateKey)) {
    throw 'Missing PRIVATE_KEY or BASE_SEPOLIA_PRIVATE_KEY in the current process environment. The launcher never stores or prints it.'
}
if (-not $DryRun -and $privateKey -notmatch '^(0x)?[0-9a-fA-F]{64}$') {
    throw 'The Base Sepolia private key is invalid: expected exactly 32 bytes (64 hex characters, optionally prefixed with 0x).'
}
if ([string]::IsNullOrWhiteSpace($payee)) {
    throw 'Missing ROBO_PAYEE_ADDRESS or ROBOT_PAYEE_ADDRESS in the current process environment.'
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
if ([string]::IsNullOrWhiteSpace($TunnelBin)) {
    $TunnelBin = Join-Path $repoRoot 'bin/tunnel'
}
if (-not (Test-Path -LiteralPath $TunnelBin)) {
    throw "Tunnel binary not found: '$TunnelBin'. Run 'wsl.exe -d Ubuntu-22.04 -- make build' from $repoRoot first."
}

$python = if (-not [string]::IsNullOrWhiteSpace($env:GO2_PYTHON_EXE)) {
    $env:GO2_PYTHON_EXE
} else {
    (Get-Command python -ErrorAction Stop).Source
}
if (-not [string]::IsNullOrWhiteSpace($privateKey)) {
    $env:PRIVATE_KEY = $privateKey
}
$env:ROBO_PAYEE_ADDRESS = $payee
$env:TUNNEL_BIN = (Resolve-Path -LiteralPath $TunnelBin).Path
$env:PYTHONPATH = $PSScriptRoot
$env:GO2_MUJOCO_VIEWER_HOLD_SECONDS = [string]$HoldSeconds

Write-Host 'OBS sequence: bridge ready -> discovery -> unpaid 402 -> first paid 202 -> Go2 MuJoCo motion -> settlement -> BaseScan'
Write-Host "The terminal goal pose is held for $HoldSeconds seconds before the correlated result is published."
Write-Host 'Secrets are loaded from the current process and will not be printed or written.'

$arguments = @(
    (Join-Path $PSScriptRoot 'test_base_sepolia_tunnel_e2e.py'),
    '--visual',
    '--wsl-tunnel',
    '--local-zenoh-router'
)
if (-not $NoOpenBaseScan) {
    $arguments += '--open-basescan'
}
if ($DryRun) {
    $arguments += '--dry-run'
}

& $python @arguments
$exitCode = $LASTEXITCODE
Remove-Item Env:PRIVATE_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BASE_SEPOLIA_PRIVATE_KEY -ErrorAction SilentlyContinue
if ($PauseAfter) {
    [void](Read-Host 'Recording complete. Press Enter to close this window')
}
exit $exitCode
