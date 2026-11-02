param([switch]$SetupOnly)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
$appPython=Initialize-KerrEnvironment $PSScriptRoot @('webview','clr','clr_loader','bottle','proxy_tools','fastapi','uvicorn','numpy','scipy','h5py','pydantic','websockets','serial','pipython','usb') 'requirements.txt'
if(-not $SetupOnly){
    $windowPython=Join-Path (Split-Path $appPython -Parent) 'pythonw.exe'
    if(-not(Test-Path -LiteralPath $windowPython)){$windowPython=$appPython}
    $desktopScript=Join-Path $PSScriptRoot 'desktop.py'
    Write-KerrSetupStatus 4 'Opening the TRPL desktop window...' 'launching'
    Start-Process -FilePath $windowPython -ArgumentList ('"{0}"' -f $desktopScript) -WorkingDirectory $PSScriptRoot -WindowStyle Hidden
}
