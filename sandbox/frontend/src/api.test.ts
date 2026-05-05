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
  assert.equal((calls[0].init?.headers as Record<string, string>)["content-type"], "application/json");
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
      rootPath: "D:/projects/demo-app",
      targetFiles: ["src/temporal_worker.py"],
      maxRounds: 3,
      maxFiles: 8,
      maxBytes: 60000,
      maxSearchResults: 20,
      privacyMode: "strict",
    },
  });

  assert.deepEqual(JSON.parse(String(calls[0].init?.body)).repository, {
    rootPath: "D:/projects/demo-app",
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

async function main(): Promise<void> {
  await testCreatePipelineUsesControlPlaneContract();
  await testCreatePipelineSupportsAdvancedRepositoryOptions();
  await testSubmitCheckpointEncodesStageAndBody();
  await testApiErrorKeepsBackendMessage();
}

main().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
