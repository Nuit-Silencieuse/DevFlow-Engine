param(
    [string]$PipelineName = "",
    [string]$Requirement = "",
    [string]$Model = "glm-5.1",
    [string]$BaseUrl = "https://dashscope.aliyuncs.com/compatible-mode/v1",
    [string]$Provider = "openai_compatible",
    [int]$DefaultTimeoutSeconds = 240,
    [int]$DefaultMaxTokens = 2200,
    [int]$CodeTimeoutSeconds = 360,
    [int]$CodeMaxTokens = 3500,
    [int]$BackendPort = 8080,
    [string]$TemporalTarget = "127.0.0.1:7233",
    [int]$PollSeconds = 10,
    [int]$MaxMinutes = 14,
    [switch]$SkipStartServices
)

$ErrorActionPreference = "Stop"
$RepoRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$StateDir = Join-Path $RepoRoot ".devflow-test-env"
$BackendDir = Join-Path $RepoRoot "control-plane\devflow-engine"
$DefaultPipelineNameBase64 = "RGV2RmxvdyDmj5Lku7blvIDlj5E="
$DefaultRequirementBase64 = "5a6M5oiQIERldkZsb3ctRW5naW5lIOmYtuautSA0IOeahCBUMDMwIOS7u+WKoeOAguivt+WcqOagueebruW9leeahGRlbW/mlofku7blpLnkuIvnlJ/miJDkuIDkuKrnroDljZXnmoTmtYvor5XnvZHpobXlkozmj5Lku7bku6PnoIHvvIzkuI3pnIDopoHlvojlpI3mnYLvvIzlrozmiJDmoYbmnrbljbPlj6/jgII="

if ([string]::IsNullOrWhiteSpace($PipelineName)) {
    $PipelineName = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($DefaultPipelineNameBase64))
}
if ([string]::IsNullOrWhiteSpace($Requirement)) {
    $Requirement = [System.Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($DefaultRequirementBase64))
}

New-Item -ItemType Directory -Force -Path $StateDir | Out-Null

function Wait-HttpOk {
    param(
        [string]$Url,
        [int]$Seconds
    )
    $deadline = (Get-Date).AddSeconds($Seconds)
    while ((Get-Date) -lt $deadline) {
        try {
            Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 5 | Out-Null
            return $true
        } catch {
            Start-Sleep -Seconds 2
        }
    }
    return $false
}

function Start-ControlPlaneIfNeeded {
    $healthUrl = "http://127.0.0.1:$BackendPort/api/v1/llm/config-file"
    if (Wait-HttpOk -Url $healthUrl -Seconds 3) {
        Write-Host "Control plane is already available on port $BackendPort."
        return
    }

    Write-Host "Starting control plane on port $BackendPort..."
    $backend = Start-Process `
        -FilePath "mvn.cmd" `
        -ArgumentList @("-Dmaven.repo.local=C:\Users\12252\.m2\repository", "spring-boot:run") `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput (Join-Path $StateDir "pipeline-test-control-plane.out.log") `
        -RedirectStandardError (Join-Path $StateDir "pipeline-test-control-plane.err.log") `
        -PassThru `
        -WindowStyle Hidden

    Set-Content -Path (Join-Path $StateDir "pipeline-test-control-plane.pid") -Value $backend.Id -Encoding ASCII

    if (-not (Wait-HttpOk -Url $healthUrl -Seconds 90)) {
        throw "Control plane did not become ready. See .devflow-test-env/pipeline-test-control-plane.*.log"
    }
}

function Start-ExecutionWorker {
    Write-Host "Starting execution-plane worker against Temporal target $TemporalTarget..."
    & (Join-Path $PSScriptRoot "start-execution-worker.ps1") -UseRealLlm -TemporalTarget $TemporalTarget
    Start-Sleep -Seconds 5
}

function Save-LlmConfigFile {
    $config = @{
        defaultConfig = @{
            provider = $Provider
            baseUrl = $BaseUrl
            model = $Model
            timeoutSeconds = $DefaultTimeoutSeconds
            maxTokens = $DefaultMaxTokens
            temperature = 0
        }
        stageOverrides = @{
            REQUIREMENT_ANALYSIS = @{
                provider = $Provider
                baseUrl = $BaseUrl
                model = $Model
                timeoutSeconds = $DefaultTimeoutSeconds
                maxTokens = 2200
                temperature = 0
            }
            SYSTEM_DESIGN = @{
                provider = $Provider
                baseUrl = $BaseUrl
                model = $Model
                timeoutSeconds = 360
                maxTokens = 3000
                temperature = 0
            }
            CODE_GENERATION = @{
                provider = $Provider
                baseUrl = $BaseUrl
                model = $Model
                timeoutSeconds = $CodeTimeoutSeconds
                maxTokens = $CodeMaxTokens
                temperature = 0
            }
            TEST_GENERATION = @{
                provider = $Provider
                baseUrl = $BaseUrl
                model = $Model
                timeoutSeconds = $DefaultTimeoutSeconds
                maxTokens = 3000
                temperature = 0
            }
            CODE_REVIEW = @{
                provider = $Provider
                baseUrl = $BaseUrl
                model = $Model
                timeoutSeconds = $DefaultTimeoutSeconds
                maxTokens = 2200
                temperature = 0
            }
        }
    }

    $json = $config | ConvertTo-Json -Depth 20
    $response = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$BackendPort/api/v1/llm/config-file" `
        -Method Patch `
        -ContentType "application/json; charset=utf-8" `
        -Body $json

    Write-Host "LLM config saved: $($response.path)"
    return $config
}

function New-DefaultPipeline {
    param([hashtable]$LlmConfig)

    $request = @{
        name = $PipelineName
        requirement = $Requirement
        stages = @(
            "REQUIREMENT_ANALYSIS",
            "SYSTEM_DESIGN",
            "CODE_GENERATION",
            "TEST_GENERATION",
            "APPLY_AND_RUN_TESTS",
            "CODE_REVIEW"
        )
        repository = @{
            rootPath = $RepoRoot
            includePaths = @()
            excludePaths = @("node_modules", "target", "dist", ".git")
            targetFiles = @()
            maxRounds = $null
            maxFiles = 800
            maxBytes = 262144
            maxSearchResults = $null
            privacyMode = $null
        }
        llmConfig = $LlmConfig
    }

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $requestPath = Join-Path $StateDir "pipeline-test-request-$timestamp.json"
    $request | ConvertTo-Json -Depth 30 | Set-Content -Path $requestPath -Encoding UTF8

    $create = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$BackendPort/api/v1/pipelines" `
        -Method Post `
        -ContentType "application/json; charset=utf-8" `
        -Body ($request | ConvertTo-Json -Depth 30)

    Write-Host "Pipeline ID: $($create.pipelineId)"
    Write-Host "Workflow ID: $($create.workflowId)"
    Write-Host "Request file: $requestPath"
    return $create.pipelineId
}

function Wait-Pipeline {
    param([string]$PipelineId)

    $timestamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $pollPath = Join-Path $StateDir "pipeline-test-poll-$timestamp.jsonl"
    $finalPath = Join-Path $StateDir "pipeline-test-final-$timestamp.json"
    $traceErrorPath = Join-Path $StateDir "pipeline-test-trace-errors-$timestamp.jsonl"
    $tracePath = Join-Path $RepoRoot "execution-plane\logs\agent-trace-$PipelineId.jsonl"
    $deadline = (Get-Date).AddMinutes($MaxMinutes)
    $lastLine = $null
    $seenTraceErrors = @{}

    while ((Get-Date) -lt $deadline) {
        Start-Sleep -Seconds $PollSeconds
        $summary = Invoke-RestMethod `
            -Uri "http://127.0.0.1:$BackendPort/api/v1/pipelines/$PipelineId/summary" `
            -Method Get `
            -TimeoutSec 10

        $summary | ConvertTo-Json -Depth 20 -Compress | Add-Content -Path $pollPath -Encoding UTF8
        $stageText = ($summary.stages | ForEach-Object { "$($_.name):$($_.status)" }) -join ","
        $line = "status=$($summary.status) current=$($summary.currentStage) stages=$stageText"
        if ($line -ne $lastLine) {
            Write-Host $line
            $lastLine = $line
        }

        if (Test-Path $tracePath) {
            Get-Content -LiteralPath $tracePath -Tail 120 | ForEach-Object {
                $traceLine = $_
                if (
                    $traceLine -match '"type"\s*:\s*"activity\.stage\.error"' -or
                    $traceLine -match '"type"\s*:\s*"agent\.error"' -or
                    $traceLine -match '"type"\s*:\s*"agent\.llm\.error"' -or
                    $traceLine -match 'LlmJsonParseError|LlmTimeoutError|LlmAuthenticationError|AttributeError'
                ) {
                    $key = [Convert]::ToBase64String([System.Text.Encoding]::UTF8.GetBytes($traceLine))
                    if (-not $seenTraceErrors.ContainsKey($key)) {
                        $seenTraceErrors[$key] = $true
                        Add-Content -Path $traceErrorPath -Value $traceLine -Encoding UTF8
                        Write-Host "TRACE_ERROR: $traceLine" -ForegroundColor Red
                    }
                }
            }
        }

        if ($summary.status -in @("FAILED", "COMPLETED", "SUSPENDED")) {
            break
        }
    }

    $final = Invoke-RestMethod `
        -Uri "http://127.0.0.1:$BackendPort/api/v1/pipelines/$PipelineId" `
        -Method Get `
        -TimeoutSec 20
    $final | ConvertTo-Json -Depth 40 | Set-Content -Path $finalPath -Encoding UTF8

    Write-Host "Final status: $($final.status)"
    Write-Host "Final current stage: $($final.currentStage)"
    Write-Host "Poll log: $pollPath"
    Write-Host "Trace file: $tracePath"
    Write-Host "Trace errors: $traceErrorPath"
    Write-Host "Final JSON: $finalPath"
}

if (-not $SkipStartServices) {
    Start-ControlPlaneIfNeeded
    Start-ExecutionWorker
}

$llmConfig = Save-LlmConfigFile
$pipelineId = New-DefaultPipeline -LlmConfig $llmConfig
Wait-Pipeline -PipelineId $pipelineId
