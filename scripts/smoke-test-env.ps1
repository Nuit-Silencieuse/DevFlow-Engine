param(
    [int]$FrontendPort = 5173,
    [int]$TimeoutSeconds = 180
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$BaseUrl = "http://127.0.0.1:$FrontendPort/api/v1"

function New-PipelinePayload {
    [ordered]@{
        name = "DevFlow test environment smoke"
        requirement = "Use the current DevFlow-Engine repository as context and run one full test-environment deployment smoke test."
        stages = @("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION")
    } | ConvertTo-Json -Depth 8
}

Write-Host "Creating pipeline through frontend proxy..."
$created = Invoke-RestMethod -Method Post -Uri "$BaseUrl/pipelines" -ContentType "application/json" -Body (New-PipelinePayload) -TimeoutSec 30
Write-Host "Created pipeline $($created.pipelineId), status=$($created.status)"

$approved = $false
$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastStatus = $null

while ((Get-Date) -lt $deadline) {
    $lastStatus = Invoke-RestMethod -Method Get -Uri "$BaseUrl/pipelines/$($created.pipelineId)" -TimeoutSec 30
    Write-Host "Status=$($lastStatus.status), currentStage=$($lastStatus.currentStage)"

    if (-not $approved -and $lastStatus.status -eq "SUSPENDED" -and $lastStatus.currentStage -eq "SYSTEM_DESIGN") {
        Write-Host "Approving SYSTEM_DESIGN checkpoint..."
        $decisionBody = @{ decision = "APPROVE"; feedback = "Smoke test approval." } | ConvertTo-Json
        Invoke-RestMethod `
            -Method Post `
            -Uri "$BaseUrl/pipelines/$($created.pipelineId)/checkpoints/SYSTEM_DESIGN" `
            -ContentType "application/json" `
            -Body $decisionBody `
            -TimeoutSec 30 | Out-Null
        $approved = $true
    }

    if ($lastStatus.status -eq "COMPLETED") {
        Write-Host "Smoke test passed. Pipeline completed."
        $lastStatus | ConvertTo-Json -Depth 8
        exit 0
    }

    Start-Sleep -Seconds 4
}

Write-Error "Smoke test timed out. Last status: $($lastStatus | ConvertTo-Json -Depth 8)"
exit 1
