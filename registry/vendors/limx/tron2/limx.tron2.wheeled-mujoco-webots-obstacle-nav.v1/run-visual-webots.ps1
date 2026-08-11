[CmdletBinding()]
param(
  [string]$WebotsExe = "$env:LOCALAPPDATA\Programs\Webots\msys64\mingw64\bin\webots.exe"
)

$profileRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
if (-not (Test-Path -LiteralPath $WebotsExe)) {
  throw "Webots executable was not found at '$WebotsExe'. Pass -WebotsExe with the Webots R2025a executable path."
}
$env:WEBOTS_EXE = $WebotsExe
$env:PYTHONPATH = Join-Path $profileRoot 'bridge'
py -3 (Join-Path $profileRoot 'bridge\download_vendor_assets.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
py -3 (Join-Path $profileRoot 'bridge\run_webots_obstacle_course.py') --viewer
