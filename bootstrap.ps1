$ErrorActionPreference = 'Stop'

function Get-KerrFileSha256([string]$Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [IO.File]::ReadAllBytes($Path)
        return ([BitConverter]::ToString($sha.ComputeHash($bytes))).Replace('-', '').ToLowerInvariant()
    } finally {
        $sha.Dispose()
    }
}

function Get-KerrPython {
    $candidates = New-Object System.Collections.ArrayList
    if (Get-Command py -ErrorAction SilentlyContinue) {
        [void]$candidates.Add([object]@('py', '-3.12'))
        [void]$candidates.Add([object]@('py', '-3'))
    }
    foreach ($name in @('python', 'python3', 'python3.12')) {
        if (Get-Command $name -ErrorAction SilentlyContinue) {
            [void]$candidates.Add([object]@($name))
        }
    }
    $code = 'import sys,struct; print(sys.executable if sys.version_info >= (3,12) and struct.calcsize(chr(80)) == 8 else str())'
    foreach ($candidate in $candidates) {
        $program = [string]$candidate[0]
        $arguments = @($candidate | Select-Object -Skip 1) + @('-c', $code)
        try {
            $output = & $program @arguments 2>$null
            if ($LASTEXITCODE -eq 0) {
                $path = [string](@($output) | Select-Object -Last 1)
                if ($path -and (Test-Path -LiteralPath $path)) { return $path }
            }
        } catch { }
    }
    throw 'Python 3.12 64-bit was not found. Install it from python.org or with the Python install manager, then retry.'
}

function Write-KerrSetupStatus([int]$Step, [string]$Message, [string]$State = 'working', [Nullable[double]]$Percent = $null) {
    if (-not $script:KerrSetupStatusPath) { return }
    $script:KerrSetupStep = $Step
    $elapsed = 0
    if ($script:KerrSetupStarted) {
        $elapsed = [int]([DateTime]::UtcNow - $script:KerrSetupStarted).TotalSeconds
    }
    $status = [ordered]@{
        step = $Step
        steps = 4
        message = $Message
        state = $State
        elapsed_seconds = $elapsed
        updated_utc = [DateTime]::UtcNow.ToString('o')
    }
    if ($null -ne $Percent) { $status.download_percent = [double]$Percent }
    [IO.File]::WriteAllText($script:KerrSetupStatusPath, ($status | ConvertTo-Json -Compress), [Text.UTF8Encoding]::new($false))
    [IO.File]::AppendAllText($script:KerrSetupLog, ('[{0}] [{1}/4] {2}{3}' -f [DateTime]::Now.ToString('HH:mm:ss'), $Step, $Message, [Environment]::NewLine), [Text.UTF8Encoding]::new($false))
    if ($State -eq 'error') {
        Write-Host ('[{0}/4] {1}' -f $Step, $Message) -ForegroundColor Red
    } else {
        Write-Host ('[{0}/4] {1}' -f $Step, $Message)
    }
}

function ConvertTo-KerrProcessArgument([string]$Value) {
    if ($null -eq $Value) { return '' }
    return '"'+[regex]::Replace($Value,'(\\*)"','$1$1\"')+[regex]::Match($Value,'\\*$').Value+'"'
}

function Invoke-KerrInstaller([string]$Python, [string[]]$Parameters, [int]$IdleTimeoutSeconds = 300, [int]$Step = 3) {
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $Python
    $start.Arguments = ($Parameters | ForEach-Object { ConvertTo-KerrProcessArgument $_ }) -join ' '
    $start.WorkingDirectory = $script:KerrSetupRoot
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    $start.RedirectStandardOutput = $true
    $start.RedirectStandardError = $true
    $start.StandardOutputEncoding = [Text.UTF8Encoding]::new($false)
    $start.StandardErrorEncoding = [Text.UTF8Encoding]::new($false)
    $start.EnvironmentVariables['PYTHONUNBUFFERED'] = '1'
    $start.EnvironmentVariables['PYTHONIOENCODING'] = 'utf-8'
    $start.EnvironmentVariables['PIP_DISABLE_PIP_VERSION_CHECK'] = '1'
    $temporary = Join-Path $script:KerrSetupRoot 'data/setup-temp'
    New-Item -ItemType Directory -Force -Path $temporary | Out-Null
    $start.EnvironmentVariables['TEMP'] = $temporary
    $start.EnvironmentVariables['TMP'] = $temporary

    $process = [Diagnostics.Process]::new()
    $process.StartInfo = $start
    $started = $false
    $lastLine = [DateTime]::UtcNow
    $lastHeartbeat = $lastLine
    $lastProgress = $lastLine
    $downloadName = ''
    $progressBytes = 0.0
    $progressAt = $lastLine
    try {
        if (-not $process.Start()) { throw 'Could not start the dependency installer.' }
        $started = $true
        $outputTask = $process.StandardOutput.ReadLineAsync()
        $errorTask = $process.StandardError.ReadLineAsync()
        $outputDone = $false
        $errorDone = $false
        while (-not $process.HasExited -or -not $outputDone -or -not $errorDone) {
            foreach ($channel in @('output', 'error')) {
                $done = if ($channel -eq 'output') { $outputDone } else { $errorDone }
                $task = if ($channel -eq 'output') { $outputTask } else { $errorTask }
                if (-not $done -and $task.IsCompleted) {
                    $line = $task.Result
                    if ($null -eq $line) {
                        if ($channel -eq 'output') { $outputDone = $true } else { $errorDone = $true }
                    } else {
                        $lastLine = [DateTime]::UtcNow
                        $line = [regex]::Replace($line,'(https?://)[^/@\s]+@','$1***@')
                        if ($line.StartsWith('Progress ')) {
                            $parts = @($line -split ' +')
                            if ($parts.Length -ge 4 -and $parts[2] -eq 'of') {
                                $received = 0.0
                                $total = 0.0
                                if ([double]::TryParse($parts[1], [ref]$received) -and [double]::TryParse($parts[3], [ref]$total)) {
                                    if ($received -lt $progressBytes) { $progressBytes = 0; $progressAt = $lastLine }
                                    if (($lastLine - $lastProgress).TotalSeconds -ge 1 -or $received -eq $total) {
                                        $percentage = if ($total -gt 0) { [Math]::Min(100, [Math]::Round(100 * $received / $total, 1)) } else { 0 }
                                        $rate = ($received - $progressBytes) / [Math]::Max(.001, ($lastLine - $progressAt).TotalSeconds) / 1MB
                                        Write-KerrSetupStatus $Step ('{0} {1:N1}/{2:N1} MB ({3}%, {4:N1} MB/s)' -f $downloadName, ($received / 1MB), ($total / 1MB), $percentage, $rate) 'downloading' $percentage
                                        $lastProgress = $lastLine
                                        $progressBytes = $received
                                        $progressAt = $lastLine
                                    }
                                }
                            }
                        } elseif ($line.StartsWith('Downloading ')) {
                            $parts = @($line -split ' +')
                            if ($parts.Length -ge 2) { $downloadName = $parts[1] }
                            $progressBytes = 0
                            $progressAt = $lastLine
                            Write-KerrSetupStatus $Step $downloadName 'downloading'
                        } elseif ($line.StartsWith('Using cached ')) {
                            $parts = @($line -split ' +')
                            if ($parts.Length -ge 3) { $downloadName = $parts[2] }
                            $progressBytes = 0
                            $progressAt = $lastLine
                            Write-KerrSetupStatus $Step $downloadName 'downloading'
                        } else {
                            Write-Host $line
                            [IO.File]::AppendAllText($script:KerrSetupLog, $line + [Environment]::NewLine, [Text.UTF8Encoding]::new($false))
                            if ($line.StartsWith('Collecting ') -or $line.StartsWith('Installing collected packages') -or $line.StartsWith('Building wheel') -or $line.StartsWith('Successfully installed')) {
                                Write-KerrSetupStatus $Step $line 'installing'
                            }
                        }
                        if ($channel -eq 'output') {
                            $outputTask = $process.StandardOutput.ReadLineAsync()
                        } else {
                            $errorTask = $process.StandardError.ReadLineAsync()
                        }
                    }
                }
            }
            $now = [DateTime]::UtcNow
            if (-not $process.HasExited -and ($now - $lastHeartbeat).TotalSeconds -ge 8) {
                Write-KerrSetupStatus $Step ('Still working; last installer output {0}s ago. Log: data/setup-pip.log' -f [int]($now - $lastLine).TotalSeconds) 'waiting'
                $lastHeartbeat = $now
            }
            if (-not $process.HasExited -and ($now - $lastLine).TotalSeconds -gt $IdleTimeoutSeconds) {
                throw ('Installer produced no output for {0}s. Check data/setup-pip.log and retry.' -f $IdleTimeoutSeconds)
            }
            Start-Sleep -Milliseconds 40
        }
        $process.WaitForExit()
        return $process.ExitCode
    } finally {
        if ($started -and -not $process.HasExited) {
            try {
                $stop = [Diagnostics.ProcessStartInfo]::new()
                $stop.FileName = Join-Path $env:SystemRoot 'System32/taskkill.exe'
                $stop.Arguments = '/PID ' + $process.Id + ' /T /F'
                $stop.UseShellExecute = $false
                $stop.CreateNoWindow = $true
                $stop.RedirectStandardOutput = $true
                $stop.RedirectStandardError = $true
                $stopProcess = [Diagnostics.Process]::Start($stop)
                $stopProcess.WaitForExit(5000) | Out-Null
                $stopProcess.Dispose()
                if (-not $process.HasExited) { $process.Kill() }
            } catch {
                Write-Host 'Could not stop the installer automatically. Close its window before retrying.' -ForegroundColor Yellow
            }
        }
        $process.Dispose()
    }
}

function Initialize-KerrEnvironment([string]$Root, [string]$Requirements = 'requirements.txt') {
    if (-not $Root) { throw 'Project root is required.' }
    $rootFull = [IO.Path]::GetFullPath($Root)
    $mutexKey = [BitConverter]::ToString([Security.Cryptography.SHA256]::Create().ComputeHash([Text.Encoding]::UTF8.GetBytes($rootFull.ToLowerInvariant()))).Replace('-', '')
    $mutex = [Threading.Mutex]::new($false, ('KerrQtSetup.' + $mutexKey))
    $acquired = $false
    try {
        try { $acquired = $mutex.WaitOne(0) } catch [Threading.AbandonedMutexException] { $acquired = $true }
        if (-not $acquired) { throw 'TRPL dependency setup is already running. Wait for the other setup to finish.' }
        $script:KerrSetupRoot = $rootFull
        $script:KerrSetupStarted = [DateTime]::UtcNow
        $folder = Join-Path $rootFull 'data'
        New-Item -ItemType Directory -Force -Path $folder | Out-Null
        $script:KerrSetupLog = Join-Path $folder 'setup.log'
        $script:KerrSetupStatusPath = Join-Path $folder 'setup-progress.json'
        $requirementsPath = Join-Path $rootFull $Requirements
        $probePath = Join-Path $rootFull 'runtime_probe.py'
        if (-not (Test-Path -LiteralPath $requirementsPath)) { throw ('Missing requirements file: ' + $Requirements) }
        if (-not (Test-Path -LiteralPath $probePath)) { throw 'Missing runtime_probe.py.' }
        Write-Host 'TRPL Qt environment setup' -ForegroundColor Cyan
        Write-Host ('Environment: ' + (Join-Path $rootFull '.venv'))
        Write-Host 'First setup needs internet. Normal launches use pythonw.exe without a console.'
        Write-KerrSetupStatus 1 'Checking Python 3.12 64-bit and the local environment'
        $projectPython = Join-Path $rootFull '.venv/Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $projectPython)) {
            $candidate = Get-KerrPython
            Write-KerrSetupStatus 2 ('Creating the local environment with ' + $candidate)
            & $candidate -m venv (Join-Path $rootFull '.venv') | Out-Host
            if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $projectPython)) {
                throw 'Could not create the local Python virtual environment.'
            }
        } else {
            Write-KerrSetupStatus 2 'Using the existing local environment'
        }
        $versionCode = 'import sys,struct; print(sys.version.split()[0]); raise SystemExit(0 if sys.version_info >= (3,12) and struct.calcsize(chr(80)) == 8 else 2)'
        & $projectPython -c $versionCode | Out-Host
        if ($LASTEXITCODE -ne 0) { throw 'The local environment needs 64-bit Python 3.12 or newer. Remove .venv and retry.' }
        $requirementsHash = Get-KerrFileSha256 $requirementsPath
        $markerPath = Join-Path $rootFull '.venv/.trpl-requirements.sha256'
        $markerMatches = $false
        if (Test-Path -LiteralPath $markerPath) {
            $markerValue = ([IO.File]::ReadAllText($markerPath)).Trim()
            if ($markerValue -eq $requirementsHash) { $markerMatches = $true }
        }
        $repair = $false
        if ($markerMatches) {
            Write-KerrSetupStatus 2 'Requirements unchanged; verifying the local runtime' 'checking'
            $probeExit = Invoke-KerrInstaller -Python $projectPython -Parameters @('-u', $probePath, '--probe', '--quick', '--requirements', $requirementsPath, '--marker', $markerPath) -IdleTimeoutSeconds 120 -Step 2
            if ($probeExit -eq 0) {
                Write-KerrSetupStatus 4 'Dependencies ready' 'ready'
                return $projectPython
            }
            $repair = $true
            Write-KerrSetupStatus 3 'The local runtime needs repair; reinstalling pinned dependencies' 'repairing'
        } else {
            Write-KerrSetupStatus 3 'Requirements changed or environment not stamped; installing pinned dependencies' 'installing'
        }
        $pipLog = Join-Path $folder 'setup-pip.log'
        $pip = @('-u', '-m', 'pip', '--disable-pip-version-check', '--no-input', '--retries', '3', '--timeout', '30', '--log', $pipLog, 'install', '--progress-bar', 'raw', '--prefer-binary')
        if ($repair) { $pip += @('--no-cache-dir', '--force-reinstall') }
        $pip += @('-r', $requirementsPath)
        Write-KerrSetupStatus 3 'Resolving, downloading and installing pinned dependencies (attempt 1 of 2)' 'installing'
        $exitCode = Invoke-KerrInstaller -Python $projectPython -Parameters $pip -IdleTimeoutSeconds 300 -Step 3
        if ($exitCode -ne 0) {
            Write-KerrSetupStatus 3 'Installer failed; retrying with a clean cache and fresh downloads' 'installing'
            $retry = @('-u', '-m', 'pip', '--disable-pip-version-check', '--no-input', '--no-cache-dir', '--retries', '2', '--timeout', '30', '--log', $pipLog, 'install', '--progress-bar', 'raw', '--prefer-binary', '--force-reinstall', '-r', $requirementsPath)
            $exitCode = Invoke-KerrInstaller -Python $projectPython -Parameters $retry -IdleTimeoutSeconds 300 -Step 3
        }
        if ($exitCode -ne 0) { throw ('Dependency installation failed (exit ' + $exitCode + '). See data/setup-pip.log and data/setup.log.') }
        Write-KerrSetupStatus 4 'Verifying installed packages with a full import check' 'verifying'
        $verifyExit = Invoke-KerrInstaller -Python $projectPython -Parameters @('-u', $probePath, '--probe', '--full', '--requirements', $requirementsPath) -IdleTimeoutSeconds 240 -Step 4
        if ($verifyExit -ne 0) { throw 'Runtime verification failed. The failing import and full error are in data/setup.log; retry after correcting it.' }
        [IO.File]::WriteAllText($markerPath, $requirementsHash, [Text.UTF8Encoding]::new($false))
        Write-KerrSetupStatus 4 'Dependencies ready' 'ready'
        return $projectPython
    } catch {
        if ($acquired -and $script:KerrSetupLog) {
            $step = if ($script:KerrSetupStep) { [int]$script:KerrSetupStep } else { 1 }
            try { Write-KerrSetupStatus $step $_.Exception.Message 'error' } catch { }
        }
        throw
    } finally {
        if ($acquired) { $mutex.ReleaseMutex() }
        $mutex.Dispose()
    }
}
