[CmdletBinding()]
param(
    [ValidateRange(0, 20)][int]$HoldSeconds = 5,
    [ValidateRange(0, 20)][int]$StartHoldSeconds = 3,
    [ValidateRange(0, 30)][int]$PreflightSeconds = 0,
    [switch]$DryRun,
    [switch]$NoOpenBaseScan,
    [switch]$NoAutoLayout,
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
    throw 'Missing PRIVATE_KEY or BASE_SEPOLIA_PRIVATE_KEY. The runner never stores or prints it.'
}
if (-not $DryRun -and $privateKey -notmatch '^(0x)?[0-9a-fA-F]{64}$') {
    throw 'Invalid Base Sepolia private key: expected 64 hex characters, optionally prefixed with 0x.'
}
if ([string]::IsNullOrWhiteSpace($payee)) {
    throw 'Missing ROBO_PAYEE_ADDRESS or ROBOT_PAYEE_ADDRESS.'
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
    throw "Tunnel binary not found: '$TunnelBin'. Build it in Ubuntu-22.04 with make build."
}

& wsl.exe -d Ubuntu-22.04 -- true
if ($LASTEXITCODE -ne 0) {
    throw 'Ubuntu-22.04 is unavailable in WSL; the Windows visual runner requires that distro.'
}
$staleZenohListener = Get-NetTCPConnection -LocalPort 7447 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($staleZenohListener) {
    $owner = Get-Process -Id $staleZenohListener.OwningProcess -ErrorAction SilentlyContinue
    $description = if ($owner) { "$($owner.ProcessName) PID $($owner.Id)" } else { "PID $($staleZenohListener.OwningProcess)" }
    throw "Zenoh port 7447 is already occupied by $description. Stop it before recording."
}

$python = (Get-Command python -ErrorAction Stop).Source
$env:PRIVATE_KEY = $privateKey
$env:ROBO_PAYEE_ADDRESS = $payee
$env:TUNNEL_BIN = (Resolve-Path -LiteralPath $TunnelBin).Path
$env:PYTHONPATH = $PSScriptRoot
$env:ATLAS_MUJOCO_VIEWER_HOLD_SECONDS = [string]$HoldSeconds
$env:ATLAS_MUJOCO_VIEWER_START_HOLD_SECONDS = [string]$StartHoldSeconds
$env:ATLAS_MUJOCO_VIEWER_TURN_HOLD_SECONDS = '0.45'
$env:ROBO_PAY_COMMIT_SHA = $commitSha

$layoutJob = $null
if (-not $NoAutoLayout) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AtlasEvidenceLayout {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int width, int height, bool repaint);
}
"@
    $workArea = [System.Windows.Forms.Screen]::PrimaryScreen.WorkingArea
    $terminalWidth = [int]($workArea.Width * 0.45)
    $viewerWidth = $workArea.Width - $terminalWidth
    $viewerLeft = $workArea.Left + $terminalWidth
    $terminalHandle = (Get-Process -Id $PID).MainWindowHandle
    if ($terminalHandle -ne [IntPtr]::Zero) {
        [void][AtlasEvidenceLayout]::MoveWindow(
            $terminalHandle, $workArea.Left, $workArea.Top,
            $terminalWidth, $workArea.Height, $true
        )
    }
    $layoutJob = Start-Job -ArgumentList @(
        $viewerLeft, $workArea.Top, $viewerWidth, $workArea.Height
    ) -ScriptBlock {
        param($viewerLeft, $viewerTop, $viewerWidth, $viewerHeight)
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class AtlasViewerLayout {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int width, int height, bool repaint);
}
"@
        $deadline = [DateTime]::UtcNow.AddMinutes(3)
        while ([DateTime]::UtcNow -lt $deadline) {
            $viewers = Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle -like 'MuJoCo*'
            }
            foreach ($viewer in $viewers) {
                [void][AtlasViewerLayout]::MoveWindow(
                    $viewer.MainWindowHandle, $viewerLeft, $viewerTop,
                    $viewerWidth, $viewerHeight, $true
                )
            }
            Start-Sleep -Milliseconds 250
        }
    }
}

Write-Host 'OBS sequence: bridge ready -> discovery -> unpaid 402 -> first paid 202 -> Atlas DRC wave -> correlated result -> settlement -> BaseScan'
Write-Host "Evidence commit: $commitSha"
Write-Host "Neutral pose hold: $StartHoldSeconds seconds; each measured turning point: 0.45 seconds; final pose: $HoldSeconds seconds."
Write-Host 'Automatic layout: terminal on the left; complete MuJoCo Atlas on the right.'
Write-Host 'The model is Atlas DRC/v4 hydraulic legacy, not the current electric Atlas.'
Write-Host 'Secrets are loaded from this process and will not be printed or written.'
Write-Host ''
Read-Host 'Start OBS, keep both windows visible, then press Enter to begin the current-head recording'
for ($remaining = $PreflightSeconds; $remaining -gt 0; $remaining--) {
    Write-Host "Starting in $remaining..."
    Start-Sleep -Seconds 1
}

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

try {
    & $python @arguments
    $exitCode = $LASTEXITCODE
} finally {
    if ($layoutJob -ne $null) {
        Stop-Job -Job $layoutJob -ErrorAction SilentlyContinue
        Remove-Job -Job $layoutJob -Force -ErrorAction SilentlyContinue
    }
    Remove-Item Env:PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:BASE_SEPOLIA_PRIVATE_KEY -ErrorAction SilentlyContinue
    Remove-Item Env:ROBO_PAY_COMMIT_SHA -ErrorAction SilentlyContinue
}
if ($PauseAfter) {
    [void](Read-Host 'Recording complete. Press Enter to close this window')
}
exit $exitCode
