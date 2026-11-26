param(
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = if ([System.IO.Path]::IsPathRooted($Python)) { (Resolve-Path -LiteralPath $Python).Path } else { (Resolve-Path (Join-Path $ProjectRoot $Python)).Path }
$SpecPath = Join-Path $ProjectRoot "AndorSIFBatchExport.spec"
$DistPath = Join-Path $ProjectRoot "build/v1.1.0\dist"
$WorkPath = Join-Path $ProjectRoot "build/v1.1.0\pyinstaller"

$Architecture = & $PythonPath -c "import struct; print(struct.calcsize('P') * 8)"
if ($LASTEXITCODE -ne 0 -or $Architecture.Trim() -ne "64") {
    throw "A working 64-bit Python interpreter is required."
}
$PyInstallerVersion = & $PythonPath -m PyInstaller --version
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller is not installed in the selected Python environment."
}
Write-Host "Building Andor SIF Batch Export 1.1.0 with Python $(& $PythonPath --version) and PyInstaller $PyInstallerVersion"
& $PythonPath -m PyInstaller --noconfirm --clean --distpath $DistPath --workpath $WorkPath $SpecPath
if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller build failed with exit code $LASTEXITCODE."
}
Write-Host "Build output: $DistPath\AndorSIFBatchExport"
