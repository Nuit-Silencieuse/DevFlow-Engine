import assert from "node:assert/strict";
import {
  buildCreatePipelineRequest,
  formatJson,
  hasArtifact,
  parsePathList,
  selectReviewStage,
  stageLabel,
  statusTone,
} from "./viewModel";

function testBuildCreateRequestOmitsEmptyRepository(): void {
  const request = buildCreatePipelineRequest({
    name: "  新需求  ",
    requirement: "  请生成方案  ",
    stages: [],
    rootPath: "",
    includePaths: "",
    excludePaths: "",
    targetFiles: "",
    maxFiles: "",
    maxBytes: "",
  });

  assert.equal(request.name, "新需求");
  assert.equal(request.requirement, "请生成方案");
  assert.deepEqual(request.stages, ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"]);
  assert.equal("repository" in request, false);
}

function testBuildCreateRequestNormalizesRepositoryContext(): void {
  const request = buildCreatePipelineRequest({
    name: "上下文感知",
    requirement: "分析当前项目",
    stages: ["REQUIREMENT_ANALYSIS"],
    rootPath: " D:/repo ",
    includePaths: "src\nREADME.md",
    excludePaths: "node_modules, dist",
    targetFiles: "src/App.tsx",
    maxFiles: "50",
    maxBytes: "65536",
  });

  assert.deepEqual(request.repository, {
    rootPath: "D:/repo",
    includePaths: ["src", "README.md"],
    excludePaths: ["node_modules", "dist"],
    targetFiles: ["src/App.tsx"],
    maxFiles: 50,
    maxBytes: 65_536,
  });
}

function testSelectReviewStagePrefersCurrentStage(): void {
  const selected = selectReviewStage({
    pipelineId: "p1",
    status: "SUSPENDED",
    currentStage: "SYSTEM_DESIGN",
    repository: null,
    stages: [
      { name: "REQUIREMENT_ANALYSIS", status: "COMPLETED", requiresHumanApproval: false, output: {} },
      {
        name: "SYSTEM_DESIGN",
        status: "COMPLETED",
        requiresHumanApproval: true,
        output: { design_doc: { summary: "方案" } },
      },
    ],
  });

  assert.equal(selected?.name, "SYSTEM_DESIGN");
  assert.equal(hasArtifact(selected), true);
}

function testFormattingHelpers(): void {
  assert.deepEqual(parsePathList("src, README.md\npackage.json"), ["src", "README.md", "package.json"]);
  assert.equal(stageLabel("CODE_REVIEW"), "代码评审");
  assert.equal(statusTone("FAILED"), "danger");
  assert.match(formatJson({ a: 1 }), /"a": 1/);
}

function main(): void {
  testBuildCreateRequestOmitsEmptyRepository();
  testBuildCreateRequestNormalizesRepositoryContext();
  testSelectReviewStagePrefersCurrentStage();
  testFormattingHelpers();
}

main();
