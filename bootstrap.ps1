$ErrorActionPreference='Stop'
function Get-KerrPython {
    $candidates=@()
    if(Get-Command py -ErrorAction SilentlyContinue){$candidates+=,@('py','-3.12');$candidates+=,@('py','-3')}
    foreach($name in @('python','python3')){if(Get-Command $name -ErrorAction SilentlyContinue){$candidates+=,@($name)}}
    foreach($candidate in $candidates){
        $program=$candidate[0];$options=@($candidate | Select-Object -Skip 1)
        try {
            $found=& $program @options -c 'import sys,struct,json; print(json.dumps(sys.executable) if sys.version_info >= (3,12) and struct.calcsize(chr(80))==8 else str())' 2>$null
            $path=ConvertFrom-Json -InputObject ([string](@($found)[-1]))
            if($LASTEXITCODE -eq 0 -and $path -and (Test-Path -LiteralPath $path)){return $path}
        } catch {}
    }
    throw 'Install a 64-bit Python 3.12 or newer and make py or python available on PATH.'
}
function Initialize-KerrEnvironment([string]$Root,[string[]]$Modules,[string]$Requirements) {
    $projectPython=Join-Path $Root '.venv\Scripts\python.exe'
    if(-not(Test-Path -LiteralPath $projectPython)){
        $candidate=Get-KerrPython
        & $candidate -m venv (Join-Path $Root '.venv') | Out-Host
        if($LASTEXITCODE -ne 0){throw 'Could not create the Python virtual environment.'}
    }
    & $projectPython -c 'import sys,struct; raise SystemExit(0 if sys.version_info >= (3,12) and struct.calcsize(chr(80))==8 else 2)'
    if($LASTEXITCODE -ne 0){throw 'The existing .venv needs 64-bit Python 3.12+. Recreate this project environment with a supported interpreter.'}
    $names=$Modules -join ','
    & $projectPython -c 'import importlib.util,sys; raise SystemExit(3 if any(importlib.util.find_spec(n) is None for n in sys.argv[1].split(chr(44))) else 0)' $names
    if($LASTEXITCODE -eq 3){
        & $projectPython -m pip install -r (Join-Path $Root $Requirements) | Out-Host
        if($LASTEXITCODE -ne 0){throw 'Dependency installation failed.'}
    } elseif($LASTEXITCODE -ne 0){throw 'Could not inspect the Python environment.'}
    return $projectPython
}
