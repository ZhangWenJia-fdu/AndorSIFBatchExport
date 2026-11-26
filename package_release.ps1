# Package AndorSIFBatchExport v1.1.0
param([string]$Python = ".\.venv\Scripts\python.exe")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$PythonPath = if ([System.IO.Path]::IsPathRooted($Python)) { (Resolve-Path -LiteralPath $Python).Path } else { (Resolve-Path (Join-Path $ProjectRoot $Python)).Path }
& $PythonPath (Join-Path $ProjectRoot "scripts/package_release.py")
if ($LASTEXITCODE -ne 0) { throw "Packaging failed." }

