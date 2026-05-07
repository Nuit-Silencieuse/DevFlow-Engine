param(
    [switch]$UseRealLlm,
    [int]$BackendPort = 8080,
    [int]$FrontendPort = 5173
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RepoRoot ".devflow-test-env"

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Convert-WindowsPathToWslPath {
    param([string]$Path)
    $fullPath = (Resolve-Path $Path).Path
    $drive = $fullPath.Substring(0, 1).ToLowerInvariant()
    $rest = $fullPath.Substring(2).Replace("\", "/")
    return "/mnt/$drive$rest"
}

$DockerCompose = Convert-WindowsPathToWslPath (Join-Path $RepoRoot "docker\docker-compose.yml")

function Wait-Port {
    param([int]$Port, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        if (Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue) {
            return $true
        }
        Start-Sleep -Seconds 2
    }
    return $false
}

function Wait-HttpOk {
    param([string]$Url, [int]$Seconds)
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-WebRequest -Uri $Url -Method Get -UseBasicParsing -TimeoutSec 3 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Stop-PortIfOwned {
    param([int]$Port)
    Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

function Save-Pid {
    param([string]$Name, [int]$ProcessId)
    Set-Content -Path (Join-Path $StateDir "$Name.pid") -Value $ProcessId -Encoding ASCII
}

Write-Host "Starting WSL Docker infrastructure..."
wsl -e docker compose -f $DockerCompose up -d postgres temporal temporal-ui

$backendDir = Join-Path $RepoRoot "control-plane\devflow-engine"
$executionDir = Join-Path $RepoRoot "execution-plane"
$frontendDir = Join-Path $RepoRoot "sandbox\frontend"

Stop-PortIfOwned $BackendPort
Stop-PortIfOwned $FrontendPort

Write-Host "Starting control plane with Java Workflow Worker on DEVFLOW_TASK_QUEUE..."
$backend = Start-Process `
    -FilePath "mvn.cmd" `
    -ArgumentList @("-Dmaven.repo.local=C:\Users\12252\.m2\repository", "spring-boot:run") `
    -WorkingDirectory $backendDir `
    -RedirectStandardOutput (Join-Path $StateDir "control-plane.out.log") `
    -RedirectStandardError (Join-Path $StateDir "control-plane.err.log") `
    -PassThru `
    -WindowStyle Hidden
Save-Pid "control-plane" $backend.Id

if (-not (Wait-Port $BackendPort 90)) {
    throw "Control plane did not listen on port $BackendPort. See .devflow-test-env/control-plane.*.log"
}

Write-Host "Starting execution-plane Python Activity Worker..."
$workerArgs = @("-TemporalTarget", "localhost:7233")
if ($UseRealLlm) {
    $workerArgs += "-UseRealLlm"
}
& (Join-Path $PSScriptRoot "start-execution-worker.ps1") @workerArgs

Write-Host "Starting frontend console..."
$frontend = Start-Process `
    -FilePath "npm.cmd" `
    -ArgumentList @("run", "dev", "--", "--port", "$FrontendPort", "--strictPort") `
    -WorkingDirectory $frontendDir `
    -RedirectStandardOutput (Join-Path $StateDir "frontend.out.log") `
    -RedirectStandardError (Join-Path $StateDir "frontend.err.log") `
    -PassThru `
    -WindowStyle Hidden
Save-Pid "frontend" $frontend.Id

if (-not (Wait-HttpOk "http://127.0.0.1:$FrontendPort" 45)) {
    throw "Frontend did not become ready. See .devflow-test-env/frontend.*.log"
}

Write-Host "Test environment is running."
Write-Host "Temporal UI: http://127.0.0.1:8234"
Write-Host "Control plane: http://127.0.0.1:$BackendPort"
Write-Host "Frontend console: http://127.0.0.1:$FrontendPort"
Write-Host "Logs: $StateDir"
if (-not $UseRealLlm) {
    Write-Host "LLM mode: fake provider. Use -UseRealLlm to inherit execution-plane/.env.local and real provider settings."
}
