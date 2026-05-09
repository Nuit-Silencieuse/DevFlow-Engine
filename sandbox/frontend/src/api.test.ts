import assert from "node:assert/strict";
import { PipelineApiClient, PipelineApiError } from "./api";

async function testCreatePipelineUsesControlPlaneContract(): Promise<void> {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const client = new PipelineApiClient("http://control-plane/api/v1", async (input, init) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({ pipelineId: "p-001", status: "RUNNING" }), { status: 201 });
  });

  const response = await client.createPipeline({
    name: "实现审批台",
    requirement: "展示阶段产物并提交审批",
    stages: ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN"],
  });

  assert.deepEqual(response, { pipelineId: "p-001", status: "RUNNING" });
  assert.equal(calls[0].input, "http://control-plane/api/v1/pipelines");
  assert.equal(calls[0].init?.method, "POST");
  assert.equal((calls[0].init?.headers as Record<string, string>)["content-type"], "application/json; charset=utf-8");
  assert.match(String(calls[0].init?.body), /SYSTEM_DESIGN/);
}

async function testCreatePipelineSupportsAdvancedRepositoryOptions(): Promise<void> {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const client = new PipelineApiClient("/api/v1", async (input, init) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({ pipelineId: "p-advanced", status: "RUNNING" }), { status: 201 });
  });

  await client.createPipeline({
    name: "健康检查",
    requirement: "分析健康检查需求",
    stages: ["REQUIREMENT_ANALYSIS"],
    repository: {
      rootPath: "D:/ZPY/Agent学习/DevFlow-Engine",
      targetFiles: ["src/temporal_worker.py"],
      maxRounds: 3,
      maxFiles: 8,
      maxBytes: 60000,
      maxSearchResults: 20,
      privacyMode: "strict",
    },
  });

  assert.deepEqual(JSON.parse(String(calls[0].init?.body)).repository, {
    rootPath: "D:/ZPY/Agent学习/DevFlow-Engine",
    targetFiles: ["src/temporal_worker.py"],
    maxRounds: 3,
    maxFiles: 8,
    maxBytes: 60000,
    maxSearchResults: 20,
    privacyMode: "strict",
  });
}

async function testSubmitCheckpointEncodesStageAndBody(): Promise<void> {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const client = new PipelineApiClient("/api/v1", async (input, init) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({ status: "RUNNING", message: "ok", stageName: "SYSTEM_DESIGN", stageOutput: {} }));
  });

  await client.submitCheckpointDecision("id/with/slash", "SYSTEM_DESIGN", "REJECT", "补充数据库结构");

  assert.equal(calls[0].input, "/api/v1/pipelines/id%2Fwith%2Fslash/checkpoints/SYSTEM_DESIGN");
  assert.deepEqual(JSON.parse(String(calls[0].init?.body)), {
    decision: "REJECT",
    feedback: "补充数据库结构",
  });
}

async function testApiErrorKeepsBackendMessage(): Promise<void> {
  const client = new PipelineApiClient("/api/v1", async () =>
    new Response(JSON.stringify({ code: "INVALID_REQUEST", message: "Pipeline name and requirement are required." }), {
      status: 400,
    }),
  );

  await assert.rejects(
    () => client.getPipeline("missing"),
    (error) =>
      error instanceof PipelineApiError &&
      error.status === 400 &&
      error.message === "Pipeline name and requirement are required.",
  );
}

async function testGetPipelineParsesCodeContextAndExplorationTrace(): Promise<void> {
  const client = new PipelineApiClient("/api/v1", async () =>
    new Response(
      JSON.stringify({
        pipelineId: "p-ctx",
        status: "SUSPENDED",
        currentStage: "REQUIREMENT_ANALYSIS",
        repository: null,
        stages: [
          {
            name: "REQUIREMENT_ANALYSIS",
            status: "COMPLETED",
            requiresHumanApproval: false,
            output: {
              codeContext: {
                status: "COMPLETE",
                inspectedFiles: ["src/health_service.py"],
                evidence: [{ filePath: "src/health_service.py" }],
              },
              explorationTrace: [{ actionType: "PLAN", reason: "plan exploration" }],
            },
          },
        ],
      }),
      { status: 200 },
    ),
  );

  const response = await client.getPipeline("p-ctx");
  const stage = response.stages[0];

  assert.ok(stage);
  assert.equal(stage.output.codeContext?.status, "COMPLETE");
  assert.equal(stage.output.explorationTrace?.[0]?.actionType, "PLAN");
}

async function testSummaryAndArtifactUseSplitEndpoints(): Promise<void> {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const client = new PipelineApiClient("/api/v1", async (input, init) => {
    calls.push({ input, init });
    if (String(input).endsWith("/summary")) {
      return new Response(JSON.stringify({
        pipelineId: "p-split",
        status: "RUNNING",
        currentStage: "CODE_GENERATION",
        repository: null,
        stages: [
          {
            name: "CODE_GENERATION",
            status: "COMPLETED",
            requiresHumanApproval: true,
            outputAvailable: true,
            artifactRevision: "rev-1",
          },
        ],
      }));
    }
    return new Response(JSON.stringify({
      pipelineId: "p-split",
      stageName: "CODE_GENERATION",
      status: "COMPLETED",
      requiresHumanApproval: true,
      artifactRevision: "rev-1",
      output: { diff_patch: "diff --git a/a b/a" },
    }));
  });

  const summary = await client.getPipelineSummary("p-split");
  const artifact = await client.getStageArtifact("p-split", "CODE_GENERATION");

  assert.equal(calls[0].input, "/api/v1/pipelines/p-split/summary");
  assert.equal(calls[1].input, "/api/v1/pipelines/p-split/stages/CODE_GENERATION/artifact");
  assert.equal(summary.stages[0]?.artifactRevision, "rev-1");
  assert.equal(artifact.output.diff_patch, "diff --git a/a b/a");
}

async function testUpdateLlmConfigFileUsesConfigEndpoint(): Promise<void> {
  const calls: Array<{ input: string; init?: RequestInit }> = [];
  const client = new PipelineApiClient("/api/v1", async (input, init) => {
    calls.push({ input, init });
    return new Response(JSON.stringify({
      path: "execution-plane/config/llm.local.json",
      config: { defaultModel: "glm-5.1" },
    }));
  });

  const response = await client.updateLlmConfigFile({
    defaultConfig: {
      provider: "openai_compatible",
      baseUrl: "https://dashscope.aliyuncs.com/compatible-mode/v1",
      model: "glm-5.1",
      timeoutSeconds: 240,
      maxTokens: 1800,
      temperature: 0,
    },
  });

  assert.equal(calls[0].input, "/api/v1/llm/config-file");
  assert.equal(calls[0].init?.method, "PATCH");
  assert.equal(JSON.parse(String(calls[0].init?.body)).defaultConfig.model, "glm-5.1");
  assert.equal(response.config.defaultModel, "glm-5.1");
}

async function main(): Promise<void> {
  await testCreatePipelineUsesControlPlaneContract();
  await testCreatePipelineSupportsAdvancedRepositoryOptions();
  await testSubmitCheckpointEncodesStageAndBody();
  await testApiErrorKeepsBackendMessage();
  await testGetPipelineParsesCodeContextAndExplorationTrace();
  await testSummaryAndArtifactUseSplitEndpoints();
  await testUpdateLlmConfigFileUsesConfigEndpoint();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
