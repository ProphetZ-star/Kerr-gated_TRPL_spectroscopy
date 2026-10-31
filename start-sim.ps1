param([ValidateRange(1024,65535)][int]$Port=8765,[switch]$NoBrowser,[switch]$Stop)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
. (Join-Path $PSScriptRoot 'bootstrap.ps1')
$projectPython=Initialize-KerrEnvironment $PSScriptRoot @('fastapi','uvicorn','scipy','h5py','websockets') 'requirements.txt'
$browserScript=Join-Path $PSScriptRoot 'browser.py'
$options=@('--port',[string]$Port)
if($NoBrowser){$options+='--no-browser'}
if($Stop){$options+='--stop'}
Write-Host "Kerr Studio local service: http://127.0.0.1:$Port"
& $projectPython $browserScript @options
if($LASTEXITCODE -ne 0){throw "Kerr Studio exited with code $LASTEXITCODE"}
