param(
    [switch]$StopDocker
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RepoRoot ".devflow-test-env"

function Convert-WindowsPathToWslPath {
    param([string]$Path)
    $fullPath = (Resolve-Path $Path).Path
    $drive = $fullPath.Substring(0, 1).ToLowerInvariant()
    $rest = $fullPath.Substring(2).Replace("\", "/")
    return "/mnt/$drive$rest"
}

$DockerCompose = Convert-WindowsPathToWslPath (Join-Path $RepoRoot "docker\docker-compose.yml")

if (Test-Path $StateDir) {
    Get-ChildItem -Path $StateDir -Filter "*.pid" -File | ForEach-Object {
        $processId = [int](Get-Content -Path $_.FullName -Raw)
        Write-Host "Stopping process $processId from $($_.Name)..."
        Stop-Process -Id $processId -Force -ErrorAction SilentlyContinue
        Remove-Item -LiteralPath $_.FullName -Force -ErrorAction SilentlyContinue
    }
}

& (Join-Path $PSScriptRoot "stop-execution-worker.ps1") -ForceAllProjectPython

foreach ($port in @(8080, 5173)) {
    Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "Stopping process $($_.OwningProcess) on port $port..."
        Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue
    }
}

if ($StopDocker) {
    Write-Host "Stopping WSL Docker infrastructure..."
    wsl -e docker compose -f $DockerCompose down
} else {
    Write-Host "Docker infrastructure kept running. Pass -StopDocker to stop Postgres/Temporal as well."
}
