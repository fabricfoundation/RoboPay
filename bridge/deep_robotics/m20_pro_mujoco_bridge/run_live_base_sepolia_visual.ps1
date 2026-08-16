[CmdletBinding()]
param(
    [ValidateRange(0, 20)][int]$HoldSeconds = 5,
    [switch]$DryRun,
    [switch]$NoOpenBaseScan,
    [switch]$NoAutoLayout,
    [switch]$PauseAfter,
    [Parameter(Mandatory = $false)][string]$TunnelBin = $env:TUNNEL_BIN
)

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$commitSha = (& git -C $repoRoot rev-parse HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $commitSha -notmatch '^[0-9a-f]{40}$') {
    throw 'Unable to resolve the exact Git commit for the visual evidence run.'
}

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
    throw 'Missing PRIVATE_KEY or BASE_SEPOLIA_PRIVATE_KEY. The launcher never stores or prints it.'
}
if (-not $DryRun -and $privateKey -notmatch '^(0x)?[0-9a-fA-F]{64}$') {
    throw 'The Base Sepolia private key is invalid: expected exactly 32 bytes.'
}
if ([string]::IsNullOrWhiteSpace($payee)) {
    throw 'Missing ROBO_PAYEE_ADDRESS or ROBOT_PAYEE_ADDRESS.'
}
if ([string]::IsNullOrWhiteSpace($TunnelBin)) {
    $TunnelBin = Join-Path $repoRoot 'bin\tunnel'
}
if (-not (Test-Path -LiteralPath $TunnelBin)) {
    throw "Tunnel binary not found: '$TunnelBin'. Run make build first."
}

$staleZenohListener = Get-NetTCPConnection -LocalPort 7447 -State Listen -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($staleZenohListener) {
    $owner = Get-Process -Id $staleZenohListener.OwningProcess -ErrorAction SilentlyContinue
    $ownerDescription = if ($owner) { "$($owner.ProcessName) PID $($owner.Id)" } else { "PID $($staleZenohListener.OwningProcess)" }
    throw "Zenoh port 7447 is already occupied by $ownerDescription. Stop the stale router before recording."
}

$python = if ($env:M20_PYTHON_EXE) {
    $env:M20_PYTHON_EXE
} else {
    Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
}
if (-not (Test-Path -LiteralPath $python)) {
    throw "Python executable not found: $python"
}
if (-not [string]::IsNullOrWhiteSpace($privateKey)) {
    $env:PRIVATE_KEY = $privateKey
}
$env:ROBO_PAYEE_ADDRESS = $payee
$env:TUNNEL_BIN = (Resolve-Path -LiteralPath $TunnelBin).Path
$env:PYTHONPATH = $PSScriptRoot
$env:M20_MUJOCO_VIEWER_HOLD_SECONDS = [string]$HoldSeconds
$env:ROBO_PAY_COMMIT_SHA = $commitSha

$layoutJob = $null
if (-not $NoAutoLayout) {
    Add-Type -AssemblyName System.Windows.Forms
    Add-Type -TypeDefinition @"
using System;
using System.Runtime.InteropServices;
public static class RoboPayM20WindowLayout {
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
        [void][RoboPayM20WindowLayout]::MoveWindow(
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
public static class RoboPayM20ViewerLayout {
    [DllImport("user32.dll", SetLastError = true)]
    public static extern bool MoveWindow(IntPtr hWnd, int x, int y, int width, int height, bool repaint);
}
"@
        $deadline = [DateTime]::UtcNow.AddMinutes(4)
        while ([DateTime]::UtcNow -lt $deadline) {
            $viewers = Get-Process -ErrorAction SilentlyContinue | Where-Object {
                $_.MainWindowHandle -ne [IntPtr]::Zero -and $_.MainWindowTitle -like 'MuJoCo*'
            }
            foreach ($viewer in $viewers) {
                [void][RoboPayM20ViewerLayout]::MoveWindow(
                    $viewer.MainWindowHandle, $viewerLeft, $viewerTop,
                    $viewerWidth, $viewerHeight, $true
                )
            }
            Start-Sleep -Milliseconds 250
        }
    }
}

Write-Host 'OBS sequence: bridge ready -> discovery -> unpaid 402 -> first paid 202 -> M20 MuJoCo motion -> settlement -> BaseScan'
Write-Host "Evidence commit: $commitSha"
Write-Host "The final goal pose is held for $HoldSeconds seconds before the correlated result is published."
Write-Host 'Automatic layout: readable terminal on the left; complete MuJoCo course on the right.'
Write-Host 'Secrets are loaded from the current process and will not be printed or written.'
Write-Host ''
Read-Host 'Start OBS, then press Enter to begin the current-head recording'

$arguments = @(
    (Join-Path $PSScriptRoot 'test_base_sepolia_tunnel_e2e.py'),
    '--visual',
    '--wsl-tunnel',
    '--local-zenoh-router'
)
if (-not $NoOpenBaseScan) { $arguments += '--open-basescan' }
if ($DryRun) { $arguments += '--dry-run' }

& $python @arguments
$exitCode = $LASTEXITCODE
if ($layoutJob -ne $null) {
    Stop-Job -Job $layoutJob -ErrorAction SilentlyContinue
    Remove-Job -Job $layoutJob -Force -ErrorAction SilentlyContinue
}
Remove-Item Env:PRIVATE_KEY -ErrorAction SilentlyContinue
Remove-Item Env:BASE_SEPOLIA_PRIVATE_KEY -ErrorAction SilentlyContinue
Remove-Item Env:ROBO_PAY_COMMIT_SHA -ErrorAction SilentlyContinue
if ($PauseAfter) {
    [void](Read-Host 'Recording complete. Press Enter to close this window')
}
exit $exitCode
