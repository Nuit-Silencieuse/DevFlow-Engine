import assert from "node:assert/strict";
import {
  buildCreatePipelineRequest,
  buildCodeContextViewModel,
  buildStageArtifactViewModel,
  formatJson,
  hasArtifact,
  parseUnifiedDiffForDisplay,
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
  assert.deepEqual(request.stages, [
    "REQUIREMENT_ANALYSIS",
    "SYSTEM_DESIGN",
    "CODE_GENERATION",
    "TEST_GENERATION",
    "APPLY_AND_RUN_TESTS",
    "CODE_REVIEW",
  ]);
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
  assert.equal(stageLabel("APPLY_AND_RUN_TESTS"), "应用代码并运行测试");
  assert.match(formatJson({ a: 1 }), /"a": 1/);
}

function testBuildCodeContextViewModelFromStageOutput(): void {
  const view = buildCodeContextViewModel({
    codeContext: {
      status: "DEGRADED",
      inspectedFiles: ["src/health_service.py"],
      searchQueries: ["health", "worker"],
      evidence: [
        {
          filePath: "src/health_service.py",
          lineStart: 1,
          lineEnd: 40,
          excerpt: "health summary",
          relevanceReason: "matches health requirement",
          supports: ["health"],
        },
      ],
      skippedPaths: [{ path: "logs/runtime.log", reason: "EXCLUDED", detail: "default exclude" }],
      budgetUsage: { roundsUsed: 1, filesRead: 1, bytesRead: 200, searchesUsed: 2 },
      confidence: 0.42,
      openQuestions: ["需要确认 worker 状态来源"],
    },
    explorationTrace: [
      {
        stepIndex: 1,
        roundIndex: 1,
        actionType: "PLAN",
        reason: "plan exploration",
        resultSummary: "search health",
        selectedFiles: [],
      },
    ],
  });

  assert.equal(view?.status, "DEGRADED");
  assert.equal(view?.inspectedFiles[0], "src/health_service.py");
  assert.equal(view?.evidence[0].filePath, "src/health_service.py");
  assert.equal(view?.skippedPaths[0].reason, "EXCLUDED");
  assert.equal(view?.budgetUsage.filesRead, 1);
  assert.equal(view?.trace[0].actionType, "PLAN");
  assert.equal(view?.openQuestions[0], "需要确认 worker 状态来源");
}

function testBuildRequirementArtifactShowsOnlyStructuredPrd(): void {
  const view = buildStageArtifactViewModel({
    name: "REQUIREMENT_ANALYSIS",
    status: "COMPLETED",
    requiresHumanApproval: false,
    output: {
      structured_prd: {
        summary: "实现代码生成 Agent",
        problem_statement: "当前代码生成阶段仍是占位逻辑",
        user_stories: [{ id: "US1", description: "用户可以生成 diff" }],
        acceptance_criteria: [{ id: "AC1", description: "输出 diff_patch", verification: "检查阶段产物" }],
      },
      codeContext: {
        inspectedFiles: ["execution-plane/src/graph/flow.py"],
      },
    },
  });

  assert.equal(view?.sourceKey, "structured_prd");
  assert.equal(view?.summaryFields[0]?.value, "实现代码生成 Agent");
  assert.equal(view?.sections.some((section) => section.title === "用户故事"), true);
  assert.equal(JSON.stringify(view).includes("inspectedFiles"), false);
}

function testBuildDesignArtifactShowsOnlyDesignDoc(): void {
  const view = buildStageArtifactViewModel({
    name: "SYSTEM_DESIGN",
    status: "COMPLETED",
    requiresHumanApproval: true,
    output: {
      structured_prd: { summary: "需求不应在设计产物中展示" },
      design_doc: {
        summary: "新增 CoderAgent 子图",
        modules: [{ name: "CoderAgent", responsibility: "生成统一 diff" }],
        file_plan: [{ path: "execution-plane/src/agents/coder_agent.py", action: "create" }],
      },
    },
  });

  assert.equal(view?.sourceKey, "design_doc");
  assert.equal(view?.title, "方案设计文档");
  assert.equal(view?.sections.some((section) => section.title === "模块设计"), true);
  assert.equal(JSON.stringify(view).includes("需求不应在设计产物中展示"), false);
}

function testBuildCodeGenerationArtifactShowsDiffByFile(): void {
  const view = buildStageArtifactViewModel({
    name: "CODE_GENERATION",
    status: "COMPLETED",
    requiresHumanApproval: true,
    output: {
      design_doc: { summary: "设计文档不应作为代码生成核心产物" },
      diff_patch:
        "diff --git a/src/app.py b/src/app.py\n" +
        "--- a/src/app.py\n" +
        "+++ b/src/app.py\n" +
        "@@ -1,2 +1,2 @@\n" +
        "-old_value = 1\n" +
        "+new_value = 1\n",
      code_generation_report: {
        summary: "生成 app.py 修改",
      },
    },
  });

  assert.equal(view?.sourceKey, "diff_patch");
  assert.equal(view?.title, "代码 Diff");
  assert.equal(view?.summaryFields.some((field) => field.label === "修改文件数" && field.value === "1"), true);
  assert.equal(view?.sections.some((section) => section.title === "Diff 文件"), true);
  assert.equal(JSON.stringify(view).includes("src/app.py"), true);
  assert.equal(JSON.stringify(view).includes("设计文档不应作为代码生成核心产物"), false);
}

function testBuildCodeGenerationArtifactShowsLlmDiagnostics(): void {
  const view = buildStageArtifactViewModel({
    name: "CODE_GENERATION",
    status: "COMPLETED",
    requiresHumanApproval: true,
    output: {
      diff_patch: "",
      code_generation_report: {
        status: "BLOCKED",
        summary: "无法生成代码 diff",
        llm_diagnostics: {
          attempts: 2,
          raw_model_output: { diff_patch: "not a unified diff" },
          validation_report: { issues: ["diff_patch is not a unified diff"] },
        },
      },
    },
  });

  assert.equal(view?.sourceKey, "diff_patch");
  assert.equal(view?.sections.some((section) => section.title === "LLM 诊断"), true);
  assert.equal(JSON.stringify(view).includes("原始模型输出"), true);
  assert.equal(JSON.stringify(view).includes("not a unified diff"), true);
  assert.equal(JSON.stringify(view).includes("diff_patch is not a unified diff"), true);
}

function testParseUnifiedDiffForDisplayKeepsFullDiff(): void {
  const longBody = Array.from({ length: 30 }, (_, index) => `+line_${index + 1}`).join("\n");
  const files = parseUnifiedDiffForDisplay(
    "diff --git a/specs/001-devflow-engine/execution-plane-architecture.md b/specs/001-devflow-engine/execution-plane-architecture.md\n" +
      "--- a/specs/001-devflow-engine/execution-plane-architecture.md\n" +
      "+++ b/specs/001-devflow-engine/execution-plane-architecture.md\n" +
      "@@ -1,1 +1,30 @@\n" +
      "-old line\n" +
      `${longBody}\n`,
  );

  assert.equal(files.length, 1);
  assert.equal(files[0].header, "Edited specs\\001-devflow-engine\\execution-plane-architecture.md (+30 -1)");
  assert.equal(files[0].additions, 30);
  assert.equal(files[0].deletions, 1);
  assert.equal(files[0].lines.some((line) => line.text === "+line_30"), true);
  assert.equal(files[0].lines.find((line) => line.text === "+line_1")?.type, "add");
  assert.equal(files[0].lines.find((line) => line.text === "-old line")?.type, "delete");
}

function testBuildTestGenerationArtifactShowsCodeAndExecutionResults(): void {
  const view = buildStageArtifactViewModel({
    name: "TEST_GENERATION",
    status: "COMPLETED",
    requiresHumanApproval: true,
    output: {
      diff_patch: "diff should not be the selected test artifact",
      test_results: {
        status: "GENERATED",
        summary: "生成 TestAgent 单元测试",
        test_diff_patch:
          "diff --git a/tests/test_agent.py b/tests/test_agent.py\n" +
          "--- a/tests/test_agent.py\n" +
          "+++ b/tests/test_agent.py\n" +
          "@@ -0,0 +1,2 @@\n" +
          "+def test_agent():\n" +
          "+    assert True\n",
        test_files: [{ path: "tests/test_agent.py", framework: "pytest", purpose: "验证 Agent" }],
        test_commands: [{ command: "pytest tests/test_agent.py", purpose: "运行测试" }],
        execution_results: [{ command: "pytest tests/test_agent.py", status: "NOT_RUN", stderr: "补丁尚未应用" }],
      },
    },
  });

  assert.equal(view?.sourceKey, "test_results");
  assert.equal(view?.title, "测试生成结果");
  assert.equal(view?.sections.some((section) => section.title === "测试文件"), true);
  assert.equal(view?.sections.some((section) => section.title === "鎵ц缁撴灉"), false);
  assert.equal(JSON.stringify(view).includes("NOT_RUN"), false);
  assert.equal(JSON.stringify(view).includes("tests/test_agent.py"), true);
  assert.equal(JSON.stringify(view).includes("diff should not be the selected test artifact"), false);
}

function testBuildApplyAndRunTestsArtifactShowsManualAction(): void {
  const view = buildStageArtifactViewModel({
    name: "APPLY_AND_RUN_TESTS",
    status: "COMPLETED",
    requiresHumanApproval: false,
    output: {
      test_run_results: {
        status: "FAILED",
        summary: "等待人工应用补丁并运行测试",
        apply_strategy: "AUTO_APPLY_APPROVED_DIFFS",
        manual_steps: ["审查代码 diff", "审查测试 diff", "手动运行测试命令"],
        test_commands: [{ command: "python -m unittest discover -s tests", purpose: "运行后端测试" }],
        execution_results: [{ command: "python -m unittest discover -s tests", status: "FAILED", stderr: "boom" }],
        errors: ["boom"],
      },
    },
  });

  assert.equal(view?.sourceKey, "test_run_results");
  assert.equal(view?.title, "测试执行状态");
  assert.equal(view?.sections.some((section) => section.title === "人工步骤"), true);
  assert.equal(JSON.stringify(view).includes("FAILED"), true);
  assert.equal(JSON.stringify(view).includes("boom"), true);
}

function testBuildReviewArtifactShowsReviewReportOnly(): void {
  const view = buildStageArtifactViewModel({
    name: "CODE_REVIEW",
    status: "COMPLETED",
    requiresHumanApproval: false,
    output: {
      test_results: { summary: "should not be selected" },
      review_report: {
        status: "NEEDS_CHANGES",
        summary: "发现一个高风险问题。",
        findings: [
          {
            severity: "HIGH",
            file_path: "src/app.py",
            line: 42,
            description: "异常处理缺失。",
            recommendation: "补充失败分支。",
          },
        ],
        quality_gates: [{ name: "tests", status: "PASSED", evidence: "python -m unittest" }],
        risks: ["需要补充回归测试"],
        open_questions: ["是否需要兼容旧数据"],
        quality: { confidence: "MEDIUM" },
      },
    },
  });

  assert.equal(view?.sourceKey, "review_report");
  assert.equal(view?.title, "代码评审报告");
  assert.equal(view?.summaryFields.some((field) => field.label === "评审状态" && field.value === "NEEDS_CHANGES"), true);
  assert.equal(view?.sections.some((section) => section.title === "评审发现"), true);
  assert.equal(view?.sections.some((section) => section.title === "质量门禁"), true);
  assert.equal(JSON.stringify(view).includes("src/app.py"), true);
  assert.equal(JSON.stringify(view).includes("should not be selected"), false);
}

function testBuildDeliveryArtifactShowsDeliveryStatusOnly(): void {
  const view = buildStageArtifactViewModel({
    name: "DELIVERY_INTEGRATION",
    status: "COMPLETED",
    requiresHumanApproval: false,
    output: {
      review_report: { summary: "should not be selected" },
      delivery_status: {
        status: "READY",
        summary: "测试通过且评审批准，可以交付。",
        release_notes: ["新增交付集成 Agent。"],
        artifacts: [{ name: "code_diff", type: "diff", status: "READY" }],
        verification: [{ name: "unit_tests", status: "PASSED", evidence: "python -m unittest" }],
        handoff_checklist: [{ item: "确认评审结论", status: "DONE" }],
        risks: [],
        open_questions: [],
        quality: { confidence: "HIGH" },
      },
    },
  });

  assert.equal(view?.sourceKey, "delivery_status");
  assert.equal(view?.title, "交付集成状态");
  assert.equal(view?.summaryFields.some((field) => field.label === "交付状态" && field.value === "READY"), true);
  assert.equal(view?.sections.some((section) => section.title === "交付说明"), true);
  assert.equal(view?.sections.some((section) => section.title === "交接检查项"), true);
  assert.equal(JSON.stringify(view).includes("code_diff"), true);
  assert.equal(JSON.stringify(view).includes("should not be selected"), false);
}

function main(): void {
  testBuildCreateRequestOmitsEmptyRepository();
  testBuildCreateRequestNormalizesRepositoryContext();
  testSelectReviewStagePrefersCurrentStage();
  testFormattingHelpers();
  testBuildCodeContextViewModelFromStageOutput();
  testBuildRequirementArtifactShowsOnlyStructuredPrd();
  testBuildDesignArtifactShowsOnlyDesignDoc();
  testBuildCodeGenerationArtifactShowsDiffByFile();
  testBuildCodeGenerationArtifactShowsLlmDiagnostics();
  testParseUnifiedDiffForDisplayKeepsFullDiff();
  testBuildTestGenerationArtifactShowsCodeAndExecutionResults();
  testBuildApplyAndRunTestsArtifactShowsManualAction();
  testBuildReviewArtifactShowsReviewReportOnly();
  testBuildDeliveryArtifactShowsDeliveryStatusOnly();
}

main();

