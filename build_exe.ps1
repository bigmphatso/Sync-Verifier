$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $root

$exe = Join-Path $root "dist\SyncVerifier.exe"
$workPath = Join-Path ([System.IO.Path]::GetTempPath()) "SyncVerifier-pyinstaller-build"

if (Test-Path $exe) {
    Remove-Item -LiteralPath $exe -Force
}

py -m PyInstaller --clean --noconfirm --workpath $workPath --distpath (Join-Path $root "dist") SyncVerifier.spec
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE."
}

if (Test-Path $exe) {
    Write-Host "Built $exe"
} else {
    throw "Build finished, but dist\SyncVerifier.exe was not found."
}
