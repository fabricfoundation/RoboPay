[CmdletBinding()]
param(
    [ValidateRange(0, 20)][int]$FinalHoldSeconds = 2,
    [ValidateRange(0, 10)][int]$TargetHoldSeconds = 1,
    [ValidateRange(0, 20)][int]$ViewerStartSeconds = 4,
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
    throw "Tunnel binary not found: '$TunnelBin'. Build it once before starting the recording."
}

$staleZenohListener = Get-NetTCPConnection -LocalPort 7447 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($staleZenohListener) {
    $owner = Get-Process -Id $staleZenohListener.OwningProcess -ErrorAction SilentlyContinue
    $ownerDescription = if ($owner) { "$($owner.ProcessName) PID $($owner.Id)" } else { "PID $($staleZenohListener.OwningProcess)" }
    throw "Zenoh port 7447 is already occupied by $ownerDescription. Stop the stale router before recording."
}

$python = if (-not [string]::IsNullOrWhiteSpace($env:UNITREE_G1_PYTHON_EXE)) {
    $env:UNITREE_G1_PYTHON_EXE
} else {
    (Get-Command python -ErrorAction Stop).Source
}
$env:PRIVATE_KEY = $privateKey
$env:ROBO_PAYEE_ADDRESS = $payee
$env:TUNNEL_BIN = (Resolve-Path -LiteralPath $TunnelBin).Path
$env:PYTHONPATH = $PSScriptRoot
$env:UNITREE_G1_MUJOCO_VIEWER_HOLD_SECONDS = [string]$FinalHoldSeconds
$env:UNITREE_G1_TARGET_HOLD_SECONDS = [string]$TargetHoldSeconds
$env:UNITREE_G1_VIEWER_START_HOLD_SECONDS = [string]$ViewerStartSeconds
$env:UNITREE_G1_TUNNEL_BACKEND = 'wsl'
$env:ROBO_PAY_COMMIT_SHA = $commitSha

$layoutJob = $null
if (-not $NoAutoLayout) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RoboPayG1WindowLayout {
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
        [void][RoboPayG1WindowLayout]::MoveWindow(
            $terminalHandle,
            $workArea.Left,
            $workArea.Top,
            $terminalWidth,
            $workArea.Height,
            $true
        )
    }
    $layoutJob = Start-Job -ArgumentList @(
        $viewerLeft,
        $workArea.Top,
        $viewerWidth,
        $workArea.Height
    ) -ScriptBlock {
        param($viewerLeft, $viewerTop, $viewerWidth, $viewerHeight)
        Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RoboPayG1ViewerLayout {
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
                [void][RoboPayG1ViewerLayout]::MoveWindow(
                    $viewer.MainWindowHandle,
                    $viewerLeft,
                    $viewerTop,
                    $viewerWidth,
                    $viewerHeight,
                    $true
                )
            }
            Start-Sleep -Milliseconds 250
        }
    }
}

Write-Host 'OBS sequence: bridge ready -> discovery -> unpaid 402 -> first paid 202 -> left -> center -> right -> correlated result -> settlement -> BaseScan'
Write-Host "Evidence commit: $commitSha"
Write-Host "Each target pose is held for $TargetHoldSeconds seconds; the final pose is held for $FinalHoldSeconds additional seconds."
Write-Host "The viewer holds its neutral start for $ViewerStartSeconds seconds."
Write-Host 'Automatic layout: readable terminal on the left; complete MuJoCo viewer on the right.'
Write-Host 'Secrets are loaded from the current process and will not be printed or written.'
Write-Host ''
Read-Host 'Start OBS, then press Enter to begin the current-head recording'
for ($remaining = $PreflightSeconds; $remaining -gt 0; $remaining--) {
    Write-Host "Starting in $remaining..."
    Start-Sleep -Seconds 1
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
if ($layoutJob -ne $null) {
    Stop-Job -Job $layoutJob -ErrorAction SilentlyContinue
    Remove-Job -Job $layoutJob -Force -ErrorAction SilentlyContinue
}
Remove-Item Env:PRIVATE_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BASE_SEPOLIA_PRIVATE_KEY -ErrorAction SilentlyContinue
Remove-Item Env:UNITREE_G1_TUNNEL_BACKEND -ErrorAction SilentlyContinue
Remove-Item Env:ROBO_PAY_COMMIT_SHA -ErrorAction SilentlyContinue
$privateKey = $null
if ($PauseAfter) {
    [void](Read-Host 'Recording complete. Press Enter to close this window')
}
exit $exitCode
