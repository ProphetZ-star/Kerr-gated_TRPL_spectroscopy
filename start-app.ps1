param([switch]$SetupOnly)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
$appPython=Initialize-KerrEnvironment $PSScriptRoot @('webview','fastapi','uvicorn','scipy','h5py','websockets') 'requirements-desktop.txt'
if(-not $SetupOnly){
    $windowPython=Join-Path $PSScriptRoot '.venv\Scripts\pythonw.exe'
    if(-not(Test-Path -LiteralPath $windowPython)){$windowPython=$appPython}
    $desktopScript=Join-Path $PSScriptRoot 'desktop.py'
    Write-Host 'Launching Kerr Studio...'
    Start-Process -FilePath $windowPython -ArgumentList ('"{0}"' -f $desktopScript) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
