param([switch]$SetupOnly)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$appPython=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $appPython)) {
    $bundledPython=Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { & $bundledPython -m venv .venv }
    else { py -3.12 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python 3.12+ environment.' }
}
$check = @'
import importlib.util, sys
if sys.version_info < (3, 12):
    print('Python 3.12+ is required by the pinned SciPy version.', file=sys.stderr)
    raise SystemExit(2)
missing = [name for name in ('webview', 'fastapi', 'uvicorn', 'scipy', 'h5py', 'websockets') if importlib.util.find_spec(name) is None]
if missing:
    print('Missing desktop dependencies: ' + ', '.join(missing), file=sys.stderr)
    raise SystemExit(3)
'@
& $appPython -c $check
$checkExit=$LASTEXITCODE
if ($checkExit -eq 2) { throw 'Please recreate .venv using Python 3.12 or newer.' }
if ($checkExit -eq 3) {
    & $appPython -m pip install -r requirements-desktop.txt
    if ($LASTEXITCODE -ne 0) { throw 'Desktop dependency installation failed.' }
}
elseif ($checkExit -ne 0) { throw 'Could not verify the desktop environment.' }
if (-not $SetupOnly) {
    $windowPython=Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    $desktopScript=Join-Path $PSScriptRoot 'desktop.py'
    Start-Process -FilePath $windowPython -ArgumentList ('"{0}"' -f $desktopScript) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
