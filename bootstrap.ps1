$ErrorActionPreference='Stop'
function Get-KerrPython([string]$Root) {
    $privatePython=if($Root){Join-Path $Root 'data\python-runtime-3.12\python.exe'}else{''}
    if($privatePython -and (Test-Path -LiteralPath $privatePython)){return $privatePython}
    $candidates=@()
    if(Get-Command py -ErrorAction SilentlyContinue){$candidates+=,@('py','-3.12');$candidates+=,@('py','-3')}
    foreach($name in @('python','python3')){if(Get-Command $name -ErrorAction SilentlyContinue){$candidates+=,@($name)}}
    foreach($candidate in $candidates){
        $program=$candidate[0];$options=@($candidate|Select-Object -Skip 1)
        try{
            $found=& $program @options -c 'import sys,struct,json; print(json.dumps(sys.executable) if sys.version_info >= (3,12) and struct.calcsize(chr(80))==8 else str())' 2>$null
            $path=ConvertFrom-Json -InputObject ([string](@($found)[-1]))
            if($LASTEXITCODE -eq 0 -and $path -and (Test-Path -LiteralPath $path)){return $path}
        }catch{}
    }
    if($Root -and (Get-Command py -ErrorAction SilentlyContinue)){
        Write-KerrSetupStatus 2 'Preparing a private Python 3.12 runtime; system Python stays unchanged'
        $manager=(Get-Command py).Source
        $code=Invoke-KerrInstaller $manager @('install',('--target='+(Split-Path $privatePython -Parent)),'--yes','3.12') 180 2
        if($code -eq 0 -and (Test-Path -LiteralPath $privatePython)){return $privatePython}
    }
    throw 'Install 64-bit Python 3.12, or install the Python install manager so TRPL can prepare its private runtime.'
}

function Write-KerrSetupStatus([int]$Step,[string]$Message,[string]$State='working',[Nullable[double]]$Percent=$null){
    $script:KerrSetupStep=$Step
    $elapsed=[int]([DateTime]::UtcNow-$script:KerrSetupStarted).TotalSeconds
    $status=[ordered]@{step=$Step;steps=4;message=$Message;state=$State;elapsed_seconds=$elapsed;updated_utc=[DateTime]::UtcNow.ToString('o')}
    if($null -ne $Percent){$status.download_percent=[double]$Percent}
    [IO.File]::WriteAllText($script:KerrSetupStatusPath,($status|ConvertTo-Json -Compress),[Text.UTF8Encoding]::new($false))
    [IO.File]::AppendAllText($script:KerrSetupLog,('[{0}] [{1}/4] {2}{3}' -f [DateTime]::Now.ToString('HH:mm:ss'),$Step,$Message,[Environment]::NewLine),[Text.UTF8Encoding]::new($false))
    Write-Host ('[{0}/4] {1}' -f $Step,$Message)
}

function ConvertTo-KerrProcessArgument([string]$Value){
    return '"'+[regex]::Replace($Value,'(\\*)"','$1$1\"')+[regex]::Match($Value,'\\*$').Value+'"'
}

function Invoke-KerrInstaller([string]$Python,[string[]]$Parameters,[int]$IdleTimeoutSeconds=180,[int]$Step=3){
    $start=[Diagnostics.ProcessStartInfo]::new();$start.FileName=$Python
    $start.Arguments=($Parameters|ForEach-Object {ConvertTo-KerrProcessArgument $_}) -join ' '
    $start.WorkingDirectory=$script:KerrSetupRoot;$start.UseShellExecute=$false;$start.CreateNoWindow=$true
    $start.RedirectStandardOutput=$true;$start.RedirectStandardError=$true
    $start.StandardOutputEncoding=[Text.UTF8Encoding]::new($false);$start.StandardErrorEncoding=[Text.UTF8Encoding]::new($false)
    $start.EnvironmentVariables['PYTHONUNBUFFERED']='1';$start.EnvironmentVariables['PYTHONIOENCODING']='utf-8'
    $start.EnvironmentVariables['PIP_NO_CACHE_DIR']='1'
    $temporary=Join-Path $script:KerrSetupRoot 'data\setup-temp';New-Item -ItemType Directory -Force -Path $temporary|Out-Null
    $start.EnvironmentVariables['TEMP']=$temporary;$start.EnvironmentVariables['TMP']=$temporary
    $process=[Diagnostics.Process]::new();$process.StartInfo=$start;$started=$false
    $lastLine=[DateTime]::UtcNow;$lastHeartbeat=$lastLine;$lastProgress=$lastLine
    $downloadName='';$progressBytes=0.0;$progressAt=$lastLine
    try{
        if(-not $process.Start()){throw 'Could not start the dependency installer.'};$started=$true
        $outputTask=$process.StandardOutput.ReadLineAsync();$errorTask=$process.StandardError.ReadLineAsync()
        $outputDone=$false;$errorDone=$false
        while(-not $process.HasExited -or -not $outputDone -or -not $errorDone){
            foreach($channel in @('output','error')){
                $done=if($channel -eq 'output'){$outputDone}else{$errorDone}
                $task=if($channel -eq 'output'){$outputTask}else{$errorTask}
                if(-not $done -and $task.IsCompleted){
                    $line=$task.Result
                    if($null -eq $line){if($channel -eq 'output'){$outputDone=$true}else{$errorDone=$true}}
                    else{
                        $lastLine=[DateTime]::UtcNow;$line=[regex]::Replace($line,'(https?://)[^/@\s]+@','$1***@')
                        if($line -match '^Progress\s+(\d+)\s+of\s+(\d+)'){
                            $received=[double]$Matches[1];$total=[double]$Matches[2]
                            if($received -lt $progressBytes){$progressBytes=0;$progressAt=$lastLine}
                            if(($lastLine-$lastProgress).TotalSeconds -ge 1 -or $received -eq $total){
                                $percentage=if($total -gt 0){[Math]::Min(100,[Math]::Round(100*$received/$total,1))}else{0}
                                $rate=($received-$progressBytes)/[Math]::Max(.001,($lastLine-$progressAt).TotalSeconds)/1MB
                                Write-KerrSetupStatus $Step ('{0} {1:N1}/{2:N1} MB ({3}%, {4:N1} MB/s)' -f $downloadName,($received/1MB),($total/1MB),$percentage,$rate) 'downloading' $percentage
                                $lastProgress=$lastLine;$progressBytes=$received;$progressAt=$lastLine
                            }
                        }else{
                            Write-Host $line
                            [IO.File]::AppendAllText($script:KerrSetupLog,$line+[Environment]::NewLine,[Text.UTF8Encoding]::new($false))
                            if($line -match '^\s*(Downloading|Using cached)\s+([^\s]+)'){$downloadName=$Matches[2];$progressBytes=0;$progressAt=$lastLine;Write-KerrSetupStatus $Step $downloadName 'downloading'}
                            elseif($line -match '^(Collecting|Installing collected packages|Building wheel|Successfully installed)'){Write-KerrSetupStatus $Step $line 'installing'}
                        }
                        if($channel -eq 'output'){$outputTask=$process.StandardOutput.ReadLineAsync()}else{$errorTask=$process.StandardError.ReadLineAsync()}
                    }
                }
            }
            $now=[DateTime]::UtcNow
            if(-not $process.HasExited -and ($now-$lastHeartbeat).TotalSeconds -ge 8){
                Write-KerrSetupStatus $Step ('Still working; last installer output {0}s ago. Log: data\setup-pip.log' -f [int]($now-$lastLine).TotalSeconds) 'waiting'
                $lastHeartbeat=$now
            }
            if(-not $process.HasExited -and ($now-$lastLine).TotalSeconds -gt $IdleTimeoutSeconds){throw ('Installer produced no output for {0}s. Check data\setup-pip.log and retry.' -f $IdleTimeoutSeconds)}
            Start-Sleep -Milliseconds 40
        }
        $process.WaitForExit();return $process.ExitCode
    }finally{
        if($started -and -not $process.HasExited){
            try{
                $stop=[Diagnostics.ProcessStartInfo]::new();$stop.FileName=Join-Path $env:SystemRoot 'System32\taskkill.exe';$stop.Arguments='/PID '+$process.Id+' /T /F'
                $stop.UseShellExecute=$false;$stop.CreateNoWindow=$true;$stop.RedirectStandardOutput=$true;$stop.RedirectStandardError=$true
                $stopProcess=[Diagnostics.Process]::Start($stop);$stopProcess.WaitForExit(5000)|Out-Null;$stopProcess.Dispose()
                if(-not $process.HasExited){$process.Kill()}
            }catch{Write-Host 'Could not stop the installer automatically. Close its window before retrying.' -ForegroundColor Yellow}
        }
        $process.Dispose()
    }
}

function Initialize-KerrEnvironment([string]$Root,[string[]]$Modules,[string]$Requirements){
    $mutexKey=[BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes([IO.Path]::GetFullPath($Root).ToLowerInvariant()))).Replace('-','')
    $mutex=[Threading.Mutex]::new($false,('Local\KerrSetup.'+$mutexKey));$acquired=$false
    try{
        try{$acquired=$mutex.WaitOne(0)}catch [Threading.AbandonedMutexException]{$acquired=$true}
        if(-not $acquired){throw 'TRPL dependency setup is already running. Wait for its progress window to finish.'}
        $script:KerrSetupRoot=$Root;$script:KerrSetupStarted=[DateTime]::UtcNow
        $folder=Join-Path $Root 'data';New-Item -ItemType Directory -Force -Path $folder|Out-Null
        $script:KerrSetupLog=Join-Path $folder 'setup.log';$script:KerrSetupStatusPath=Join-Path $folder 'setup-progress.json'
        Write-Host 'TRPL environment setup' -ForegroundColor Cyan
        Write-Host ('Environment: '+(Join-Path $Root '.venv'))
        Write-Host 'First setup needs internet. Normal launch opens TRPL after all four stages.'
        Write-KerrSetupStatus 1 'Checking Python and the local environment'
        $projectPython=Join-Path $Root '.venv\Scripts\python.exe'
        if(-not(Test-Path -LiteralPath $projectPython)){
            $candidate=Get-KerrPython $Root;Write-KerrSetupStatus 2 ('Creating the environment with '+$candidate)
            & $candidate -m venv (Join-Path $Root '.venv')|Out-Host
            if($LASTEXITCODE -ne 0){throw 'Could not create the Python virtual environment.'}
        }else{Write-KerrSetupStatus 2 'Using the existing local environment'}
        & $projectPython -c 'import sys,struct; print(str(sys.version).split()[0]); raise SystemExit(0 if sys.version_info >= (3,12) and struct.calcsize(chr(80))==8 else 2)'|Out-Host
        if($LASTEXITCODE -ne 0){throw 'The desktop environment needs 64-bit Python 3.12 or newer.'}
        $names=$Modules -join ','
        & $projectPython -c 'import importlib.util,sys; missing=[n for n in sys.argv[1].split(chr(44)) if importlib.util.find_spec(n) is None]; print(chr(44).join(missing)); raise SystemExit(3 if missing else 0)' $names|Out-Host
        if($LASTEXITCODE -eq 3){
            Write-KerrSetupStatus 3 'Resolving, downloading and installing dependencies'
            $parameters=@('-u','-m','pip','--no-cache-dir','--disable-pip-version-check','--no-input','--retries','2','--timeout','20','--log',(Join-Path $folder 'setup-pip.log'),'install','--progress-bar','raw','--prefer-binary','--only-binary','numpy,scipy,h5py,pydantic-core,cffi','-r',(Join-Path $Root $Requirements))
            $exitCode=Invoke-KerrInstaller $projectPython $parameters
            if($exitCode -ne 0){throw ('Dependency installation failed (exit '+$exitCode+'). See data\setup-pip.log.')}
        }elseif($LASTEXITCODE -ne 0){throw 'Could not inspect the Python environment.'}
        Write-KerrSetupStatus 4 'Verifying installed dependencies'
        if($Modules -contains 'clr'){
            $runtimeHelper=Join-Path $Root 'desktop_runtime.py'
            if(-not(Test-Path -LiteralPath $runtimeHelper) -or -not(Test-Path -LiteralPath (Join-Path $Root 'desktop-runtime.config'))){throw 'Incomplete desktop package: copy desktop_runtime.py and desktop-runtime.config, then retry.'}
            $verification=Invoke-KerrInstaller $projectPython @('-u',$runtimeHelper,'--verify',$names) 180 4
        }else{
            $verification=Invoke-KerrInstaller $projectPython @('-u','-c','import importlib,sys; [importlib.import_module(n) for n in sys.argv[1].split(chr(44))]',$names) 180 4
        }
        if($verification -ne 0){throw 'Dependency or desktop-component verification failed. The failed module and full error are in data\setup.log; retry after correcting that error.'}
        if($Modules -contains 'clr'){$projectPython=Join-Path $Root '.venv\DesktopRuntime\python.exe'}
        Write-KerrSetupStatus 4 'Dependencies ready' 'ready'
        return $projectPython
    }catch{
        if($acquired -and $script:KerrSetupLog){Write-KerrSetupStatus $script:KerrSetupStep $_.Exception.Message 'error'}
        throw
    }finally{if($acquired){$mutex.ReleaseMutex()};$mutex.Dispose()}
}
