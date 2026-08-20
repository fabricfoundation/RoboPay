[CmdletBinding()]
param()

$profileRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$env:PYTHONPATH = Join-Path $profileRoot 'bridge'
py -3 (Join-Path $profileRoot 'bridge\download_vendor_assets.py')
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
py -3 -m pytest -q (Join-Path $profileRoot 'tests')
