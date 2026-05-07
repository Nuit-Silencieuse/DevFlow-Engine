param(
    [switch]$UseRealLlm,
    [string]$TemporalTarget = "localhost:7233",
    [switch]$KeepExisting
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RepoRoot ".devflow-test-env"
$ExecutionDir = Join-Path $RepoRoot "execution-plane"
$PidFile = Join-Path $StateDir "execution-worker.pid"

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

if (-not $KeepExisting) {
    & (Join-Path $PSScriptRoot "stop-execution-worker.ps1") -ForceAllProjectPython -Quiet
}

$pythonCandidates = @(
    (Join-Path $ExecutionDir "venv\Scripts\python.exe"),
    (Join-Path $ExecutionDir "venv\python.exe"),
    "python"
)
$python = $pythonCandidates | Where-Object { $_ -eq "python" -or (Test-Path $_) } | Select-Object -First 1
if (-not $python) {
    throw "Python executable was not found. Expected execution-plane venv or python on PATH."
}

$oldTemporalTarget = $env:TEMPORAL_TARGET
$oldProvider = $env:DEVFLOW_LLM_PROVIDER
$oldTrace = $env:DEVFLOW_LLM_TRACE

$env:TEMPORAL_TARGET = $TemporalTarget
if (-not $UseRealLlm) {
    # 默认测试部署使用 fake provider，便于只验证 Temporal Worker 与阶段契约。
    # 真实模型联调时传入 -UseRealLlm，让 worker 读取 execution-plane/.env.local。
    $env:DEVFLOW_LLM_PROVIDER = "fake"
    $env:DEVFLOW_LLM_TRACE = "1"
}

try {
    $worker = Start-Process `
        -FilePath $python `
        -ArgumentList @("-m", "src.workers.worker") `
        -WorkingDirectory $ExecutionDir `
        -RedirectStandardOutput (Join-Path $StateDir "execution-worker.out.log") `
        -RedirectStandardError (Join-Path $StateDir "execution-worker.err.log") `
        -PassThru `
        -WindowStyle Hidden
    Set-Content -Path $PidFile -Value $worker.Id -Encoding ASCII
    Write-Host "Execution worker started. PID: $($worker.Id)"
    Write-Host "Task queue: DEVFLOW_TASK_QUEUE"
    Write-Host "Temporal target: $TemporalTarget"
    Write-Host "Logs: $StateDir"
    if (-not $UseRealLlm) {
        Write-Host "LLM mode: fake provider. Use -UseRealLlm for real model settings."
    }
} finally {
    $env:TEMPORAL_TARGET = $oldTemporalTarget
    $env:DEVFLOW_LLM_PROVIDER = $oldProvider
    $env:DEVFLOW_LLM_TRACE = $oldTrace
}
