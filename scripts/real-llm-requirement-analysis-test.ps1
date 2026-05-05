param(
    [int]$FrontendPort = 5173,
    [int]$TimeoutSeconds = 240,
    [string]$RepositoryRoot = "",
    [string]$OutputPath = ""
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
if (-not $RepositoryRoot) {
    $RepositoryRoot = $RepoRoot.Replace("\", "/")
}
if (-not $OutputPath) {
    $OutputPath = Join-Path $RepoRoot ".devflow-test-env\real-llm-requirement-analysis-result.json"
}

$BaseUrl = "http://127.0.0.1:$FrontendPort/api/v1"

function Invoke-JsonUtf8 {
    param(
        [Parameter(Mandatory=$true)][string]$Method,
        [Parameter(Mandatory=$true)][string]$Uri,
        [string]$JsonBody = ""
    )

    $headers = @{
        Accept = "application/json; charset=utf-8"
    }
    $arguments = @{
        Method = $Method
        Uri = $Uri
        Headers = $headers
        UseBasicParsing = $true
        TimeoutSec = 30
    }
    if ($JsonBody) {
        $arguments.ContentType = "application/json; charset=utf-8"
        $arguments.Body = [System.Text.Encoding]::UTF8.GetBytes($JsonBody)
    }

    $response = Invoke-WebRequest @arguments
    if ($response.RawContentStream) {
        $response.RawContentStream.Position = 0
        $reader = [System.IO.StreamReader]::new(
            $response.RawContentStream,
            [System.Text.Encoding]::UTF8,
            $true
        )
        $content = $reader.ReadToEnd()
    } else {
        $content = $response.Content
    }
    return $content | ConvertFrom-Json
}

function New-RequirementOnlyPayload {
    [ordered]@{
        name = "Real LLM requirement analysis test"
        requirement = "Use the current DevFlow-Engine repository as context. Analyze how the frontend pipeline console should display intermediate artifacts for the requirement analysis stage. Explain how a user creates a pipeline, how the control plane submits a Temporal workflow, how the execution-plane RequirementAgent performs progressive code exploration, and list acceptance criteria."
        stages = @("REQUIREMENT_ANALYSIS")
        repository = [ordered]@{
            rootPath = $RepositoryRoot
            targetFiles = @(
                "sandbox/frontend/src/main.ts",
                "sandbox/frontend/src/viewModel.ts",
                "execution-plane/src/agents/requirement_agent.py",
                "execution-plane/src/context/repository_context.py",
                "control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java"
            )
            excludePaths = @(
                "execution-plane/venv",
                "sandbox/frontend/node_modules",
                "sandbox/daemon/node_modules",
                "control-plane/devflow-engine/target",
                "execution-plane/logs"
            )
            maxFiles = 20
            maxBytes = 180000
        }
    } | ConvertTo-Json -Depth 10
}

Write-Host "Creating requirement-analysis-only pipeline through http://127.0.0.1:$FrontendPort ..."
$payload = New-RequirementOnlyPayload
$created = Invoke-JsonUtf8 -Method "Post" -Uri "$BaseUrl/pipelines" -JsonBody $payload

Write-Host "Created pipeline $($created.pipelineId), status=$($created.status)"

$deadline = (Get-Date).AddSeconds($TimeoutSeconds)
$lastStatus = $null
while ((Get-Date) -lt $deadline) {
    $lastStatus = Invoke-JsonUtf8 -Method "Get" -Uri "$BaseUrl/pipelines/$($created.pipelineId)"

    Write-Host "Status=$($lastStatus.status), currentStage=$($lastStatus.currentStage)"
    if ($lastStatus.status -in @("COMPLETED", "FAILED")) {
        break
    }
    Start-Sleep -Seconds 4
}

if (-not $lastStatus) {
    throw "No pipeline status was returned."
}

New-Item -ItemType Directory -Force -Path (Split-Path $OutputPath -Parent) | Out-Null
$lastStatus | ConvertTo-Json -Depth 20 | Set-Content -Path $OutputPath -Encoding UTF8
Write-Host "Result saved to $OutputPath"

if ($lastStatus.status -ne "COMPLETED") {
    throw "Pipeline did not complete. Last status: $($lastStatus.status)"
}

$requirementStage = $lastStatus.stages | Where-Object { $_.name -eq "REQUIREMENT_ANALYSIS" } | Select-Object -First 1
if (-not $requirementStage) {
    throw "REQUIREMENT_ANALYSIS stage was not returned."
}
if ($requirementStage.status -ne "COMPLETED") {
    throw "REQUIREMENT_ANALYSIS stage is not completed: $($requirementStage.status)"
}

$output = $requirementStage.output
if (-not $output) {
    throw "REQUIREMENT_ANALYSIS output is empty."
}
if (-not $output.structured_prd) {
    throw "structured_prd is missing."
}
if (-not $output.codeContext) {
    throw "codeContext is missing."
}
if (-not $output.explorationTrace) {
    throw "explorationTrace is missing."
}

Write-Host "Requirement analysis completed."
Write-Host "Inspected files: $($output.codeContext.inspectedFiles.Count)"
Write-Host "Evidence count: $($output.codeContext.evidence.Count)"
Write-Host "Trace steps: $($output.explorationTrace.Count)"
