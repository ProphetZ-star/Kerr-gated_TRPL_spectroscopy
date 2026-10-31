param([int]$Port = 8765, [switch]$NoBrowser)
$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
$projectPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $projectPython)) {
    $bundledPython = Join-Path $env:USERPROFILE '.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe'
    if (Test-Path -LiteralPath $bundledPython) { & $bundledPython -m venv .venv }
    else { py -3.12 -m venv .venv }
    if ($LASTEXITCODE -ne 0) { throw 'Could not create Python virtual environment.' }
}
$check = @'
import importlib.util, sys
if sys.version_info < (3, 12):
    print('Python 3.12+ is required by the pinned SciPy version.', file=sys.stderr)
    raise SystemExit(2)
missing = [name for name in ('fastapi', 'uvicorn', 'scipy', 'h5py', 'websockets') if importlib.util.find_spec(name) is None]
if missing:
    print('Missing dependencies: ' + ', '.join(missing), file=sys.stderr)
    raise SystemExit(3)
'@
& $projectPython -c $check
$checkExit = $LASTEXITCODE
if ($checkExit -eq 2) { throw 'Please recreate .venv using Python 3.12 or newer.' }
if ($checkExit -eq 3) {
    & $projectPython -m pip install -r requirements.txt
    if ($LASTEXITCODE -ne 0) { throw 'Dependency installation failed.' }
}
elseif ($checkExit -ne 0) { throw 'Could not verify the simulation environment.' }
Write-Host "Kerr Studio simulation: http://127.0.0.1:$Port"
$browserScript = Join-Path $PSScriptRoot 'browser.py'
if ($NoBrowser) { & $projectPython $browserScript --port $Port --no-browser }
else { & $projectPython $browserScript --port $Port }
