param(
    [switch]$SetupOnly,
    [switch]$Probe,
    [switch]$NoLaunch
)
$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
Set-Location -LiteralPath $root
. (Join-Path $root 'bootstrap.ps1')

try {
    $requirements = Join-Path $root 'requirements.txt'
    $marker = Join-Path $root '.venv/.trpl-requirements.sha256'
    if ($Probe) {
        $python = Join-Path $root '.venv/Scripts/python.exe'
        if (-not (Test-Path -LiteralPath $python)) { throw 'The local environment is not installed. Run setup first.' }
        $probeScript = Join-Path $root 'runtime_probe.py'  # not $probe: PowerShell names are case-insensitive and would collide with -Probe
        & $python -u $probeScript --probe --requirements $requirements --marker $marker
        exit $LASTEXITCODE
    }
    $appPython = Initialize-KerrEnvironment -Root $root -Requirements 'requirements.txt'
    if ($SetupOnly -or $NoLaunch) { exit 0 }
    $windowPython = Join-Path $root '.venv/Scripts/pythonw.exe'
    if (-not (Test-Path -LiteralPath $windowPython)) { throw 'pythonw.exe is missing from the local environment.' }
    $desktop = Join-Path $root 'desktop.py'
    if (-not (Test-Path -LiteralPath $desktop)) { throw 'desktop.py is missing.' }
    Write-KerrSetupStatus 4 'Opening the native Qt application' 'launching'
    $start = [Diagnostics.ProcessStartInfo]::new()
    $start.FileName = $windowPython
    $start.Arguments = ConvertTo-KerrProcessArgument $desktop
    $start.WorkingDirectory = $root
    $start.UseShellExecute = $false
    $start.CreateNoWindow = $true
    [Diagnostics.Process]::Start($start) | Out-Null
    exit 0
} catch {
    Write-Host ('TRPL startup failed: ' + $_.Exception.Message) -ForegroundColor Red
    if ($script:KerrSetupLog) {
        $step = if ($script:KerrSetupStep) { [int]$script:KerrSetupStep } else { 1 }
        try { Write-KerrSetupStatus $step $_.Exception.Message 'error' } catch { }
    }
    exit 1
}
