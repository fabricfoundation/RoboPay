[CmdletBinding()]
param(
    [ValidateRange(0, 20)][int]$FinalHoldSeconds = 3,
    [ValidateRange(0, 10)][int]$TargetHoldSeconds = 2,
    [ValidateRange(0, 20)][int]$ViewerStartSeconds = 8,
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
$commitSha = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'Unable to resolve the exact Git commit for the visual evidence run.'
}
if ([string]::IsNullOrWhiteSpace($TunnelBin)) {
    $TunnelBin = Join-Path $repoRoot 'bin/tunnel'
}
if (-not (Test-Path -LiteralPath $TunnelBin)) {
    throw "Tunnel binary not found: '$TunnelBin'."
}

$python = if (-not [string]::IsNullOrWhiteSpace($env:BOOSTER_K1_PYTHON_EXE)) {
    $env:BOOSTER_K1_PYTHON_EXE
} else {
    (Get-Command python -ErrorAction Stop).Source
}
if (-not [string]::IsNullOrWhiteSpace($privateKey)) {
    $env:PRIVATE_KEY = $privateKey
}
$env:ROBO_PAYEE_ADDRESS = $payee
$env:TUNNEL_BIN = (Resolve-Path -LiteralPath $TunnelBin).Path
$env:PYTHONPATH = $PSScriptRoot
$env:BOOSTER_K1_MUJOCO_VIEWER_HOLD_SECONDS = [string]$FinalHoldSeconds
$env:BOOSTER_K1_TARGET_HOLD_SECONDS = [string]$TargetHoldSeconds
$env:BOOSTER_K1_VIEWER_START_HOLD_SECONDS = [string]$ViewerStartSeconds
$env:ROBO_PAY_COMMIT_SHA = $commitSha

Write-Host 'Arrange this terminal beside the MuJoCo viewer, then keep both visible for the complete run.'
Write-Host 'OBS sequence: bridge ready -> discovery -> unpaid 402 -> first paid 202 -> left -> center -> right -> correlated result -> settlement -> BaseScan'
Write-Host "Evidence commit: $commitSha"
Write-Host "Each target pose is held for $TargetHoldSeconds seconds; the final pose is held for $FinalHoldSeconds additional seconds."
Write-Host "The viewer holds its neutral start for $ViewerStartSeconds seconds so it can be positioned without losing the left target."
Write-Host 'Secrets are loaded from the current process and will not be printed or written.'

if (-not $DryRun) {
    [void](Read-Host 'Start OBS, keep this terminal visible, then press Enter to begin')
}

$arguments = @(
    (Join-Path $PSScriptRoot 'test_base_sepolia_tunnel_e2e.py'),
    '--visual',
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
Remove-Item Env:ROBO_PAY_COMMIT_SHA -ErrorAction SilentlyContinue
if ($PauseAfter) {
    [void](Read-Host 'Recording complete. Press Enter to close this window')
}
exit $exitCode
