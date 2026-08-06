[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

foreach ($name in 'PRIVATE_KEY', 'ROBO_PAYEE_ADDRESS') {
    if ([string]::IsNullOrWhiteSpace([Environment]::GetEnvironmentVariable($name))) {
        throw "Missing $name in the current process environment. Do not put secrets in this file."
    }
}

$repoRoot = (Resolve-Path (Join-Path $PSScriptRoot '../../..')).Path
$env:PYTHONPATH = (Join-Path $repoRoot 'bridge/deep_robotics/m20_pro_mujoco_bridge')
$python = if ($env:M20_PYTHON_EXE) {
    $env:M20_PYTHON_EXE
} else {
    Join-Path $env:LOCALAPPDATA 'Programs/Python/Python312/python.exe'
}
if (-not (Test-Path $python)) {
    throw "Python executable not found: $python"
}
& $python bridge/deep_robotics/m20_pro_mujoco_bridge/test_base_sepolia_tunnel_e2e.py --visual --wsl-tunnel --open-basescan
exit $LASTEXITCODE
