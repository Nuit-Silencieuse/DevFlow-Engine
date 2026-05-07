param(
    [switch]$ForceAllProjectPython,
    [switch]$Quiet
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RepoRoot ".devflow-test-env"
$PidFile = Join-Path $StateDir "execution-worker.pid"
$ExecutionDir = Join-Path $RepoRoot "execution-plane"
$ProjectPythonPaths = @(
    (Join-Path $ExecutionDir "venv\Scripts\python.exe"),
    (Join-Path $ExecutionDir "venv\python.exe")
) | ForEach-Object {
    if (Test-Path $_) {
        (Resolve-Path $_).Path
    }
}

function Write-Info {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host $Message
    }
}

function Stop-ProcessIfRunning {
    param(
        [int]$ProcessId,
        [string]$Reason
    )
    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        Write-Info "Process $ProcessId is not running ($Reason)."
        return
    }
    Write-Info "Stopping execution worker process $ProcessId ($Reason)..."
    Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue
}

if (Test-Path $PidFile) {
    $rawPid = (Get-Content -Path $PidFile -Raw).Trim()
    if ($rawPid) {
        Stop-ProcessIfRunning -ProcessId ([int]$rawPid) -Reason "pid file"
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

if ($ForceAllProjectPython -and $ProjectPythonPaths.Count -gt 0) {
    Get-Process -Name python -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.Path -and ($ProjectPythonPaths -contains $_.Path)) {
            Stop-ProcessIfRunning -ProcessId $_.Id -Reason "project execution-plane python"
        }
    }
}

Write-Info "Execution worker cleanup completed."
