import type {
  BudgetUsage,
  CodeContextSummary,
  CreatePipelineRequest,
  EvidenceItem,
  ExplorationStep,
  PipelineStatusResponse,
  RepositoryContext,
  SkippedPath,
  StageStatusResponse,
} from "./types";

export const STAGE_OPTIONS = [
  { name: "APPLY_AND_RUN_TESTS", label: "应用并运行测试" },
  { name: "REQUIREMENT_ANALYSIS", label: "需求分析" },
  { name: "SYSTEM_DESIGN", label: "方案设计" },
  { name: "CODE_GENERATION", label: "代码生成" },
  { name: "TEST_GENERATION", label: "测试生成" },
  { name: "CODE_REVIEW", label: "代码评审" },
  { name: "DELIVERY_INTEGRATION", label: "交付集成" },
] as const;

export const DEFAULT_STAGE_NAMES = [
  "REQUIREMENT_ANALYSIS",
  "SYSTEM_DESIGN",
  "CODE_GENERATION",
  "TEST_GENERATION",
  "APPLY_AND_RUN_TESTS",
];

export interface PipelineFormValues {
  name: string;
  requirement: string;
  stages: string[];
  rootPath: string;
  includePaths: string;
  excludePaths: string;
  targetFiles: string;
  maxFiles: string;
  maxBytes: string;
}

export function buildCreatePipelineRequest(values: PipelineFormValues): CreatePipelineRequest {
  const request: CreatePipelineRequest = {
    name: values.name.trim(),
    requirement: values.requirement.trim(),
    stages: values.stages.length > 0 ? values.stages : [...DEFAULT_STAGE_NAMES],
  };

  const repository = buildRepositoryContext(values);
  if (repository) {
    request.repository = repository;
  }
  return request;
}

export function buildRepositoryContext(values: PipelineFormValues): RepositoryContext | undefined {
  /*
   * repository 是可选能力：用户没有填写 rootPath 时，前端不传空对象给后端。
   * 这样可以区分“本次不提供代码库上下文”和“提供了上下文但字段非法”两个语义，
   * 后者交给控制平面继续按契约返回 400，避免 UI 自己复制后端校验规则。
   */
  const rootPath = values.rootPath.trim();
  if (!rootPath) {
    return undefined;
  }
  return {
    rootPath,
    includePaths: parsePathList(values.includePaths),
    excludePaths: parsePathList(values.excludePaths),
    targetFiles: parsePathList(values.targetFiles),
    maxFiles: parsePositiveInteger(values.maxFiles, 200),
    maxBytes: parsePositiveInteger(values.maxBytes, 1_048_576),
  };
}

export function parsePathList(value: string): string[] {
  return value
    .split(/[\n,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

export function parsePositiveInteger(value: string, fallback: number): number {
  const parsed = Number.parseInt(value, 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function selectReviewStage(
  pipeline: PipelineStatusResponse | null,
  preferredStageName?: string | null,
): StageStatusResponse | null {
  if (!pipeline || pipeline.stages.length === 0) {
    return null;
  }
  if (preferredStageName) {
    const preferred = pipeline.stages.find((stage) => stage.name === preferredStageName);
    if (preferred) {
      return preferred;
    }
  }
  const current = pipeline.stages.find((stage) => stage.name === pipeline.currentStage);
  if (current) {
    return current;
  }
  return pipeline.stages.find((stage) => stage.requiresHumanApproval) ?? pipeline.stages[0];
}

export function hasArtifact(stage: StageStatusResponse | null): boolean {
  return !!stage && !!stage.output && Object.keys(stage.output).length > 0;
}

export function formatJson(value: unknown): string {
  return JSON.stringify(value ?? {}, null, 2);
}

export function stageLabel(stageName: string): string {
  if (stageName === "APPLY_AND_RUN_TESTS") {
    return "应用代码并运行测试";
  }
  return STAGE_OPTIONS.find((stage) => stage.name === stageName)?.label ?? stageName;
}

export function statusTone(status: string): "neutral" | "active" | "success" | "warning" | "danger" {
  switch (status) {
    case "RUNNING":
      return "active";
    case "COMPLETED":
      return "success";
    case "SUSPENDED":
      return "warning";
    case "FAILED":
    case "REJECTED":
      return "danger";
    default:
      return "neutral";
  }
}

export interface CodeContextViewModel {
  status: string;
  inspectedFiles: string[];
  searchQueries: string[];
  evidence: EvidenceItem[];
  skippedPaths: SkippedPath[];
  budgetUsage: Required<BudgetUsage>;
  confidence: number;
  openQuestions: string[];
  trace: ExplorationStep[];
}

export interface DisplayField {
  label: string;
  value: string;
}

export interface DisplaySection {
  title: string;
  items: DisplayField[][];
}

export interface CodeDiffLine {
  type: "add" | "delete" | "hunk" | "context" | "meta";
  text: string;
}

export interface CodeDiffFile {
  path: string;
  displayPath: string;
  header: string;
  additions: number;
  deletions: number;
  lines: CodeDiffLine[];
}

export interface StageArtifactViewModel {
  title: string;
  sourceKey: string;
  description: string;
  summaryFields: DisplayField[];
  sections: DisplaySection[];
  codeDiffFiles?: CodeDiffFile[];
  raw: unknown;
}

const REQUIREMENT_ARTIFACT_KEYS = ["structured_prd", "structuredPrd"];
const DESIGN_ARTIFACT_KEYS = ["design_doc", "designDoc"];
const CODE_ARTIFACT_KEYS = ["diff_patch", "diffPatch"];
const TEST_ARTIFACT_KEYS = ["test_results", "testResults"];
const TEST_RUN_ARTIFACT_KEYS = ["test_run_results", "testRunResults"];

export function buildStageArtifactViewModel(stage: StageStatusResponse | null): StageArtifactViewModel | null {
  if (!stage) {
    return null;
  }

  const selected = selectCoreArtifact(stage);
  if (!selected) {
    return {
      title: stageLabel(stage.name),
      sourceKey: "output",
      description: "当前阶段尚未产生可展示的核心产物。",
      summaryFields: [],
      sections: [],
      raw: {},
    };
  }

  if (stage.name === "REQUIREMENT_ANALYSIS") {
    return buildRequirementArtifact(selected.key, selected.value);
  }
  if (stage.name === "SYSTEM_DESIGN") {
    return buildDesignArtifact(selected.key, selected.value);
  }
  if (stage.name === "CODE_GENERATION") {
    return buildCodeGenerationArtifact(selected.key, selected.value, stage.output);
  }
  if (stage.name === "TEST_GENERATION") {
    return buildTestGenerationArtifact(selected.key, selected.value);
  }
  if (stage.name === "APPLY_AND_RUN_TESTS") {
    return buildTestRunArtifact(selected.key, selected.value);
  }
  return buildGenericArtifact(stageLabel(stage.name), selected.key, selected.value);
}

function selectCoreArtifact(stage: StageStatusResponse): { key: string; value: unknown } | null {
  const output = stage.output ?? {};
  const preferredKeys =
    stage.name === "REQUIREMENT_ANALYSIS"
      ? REQUIREMENT_ARTIFACT_KEYS
      : stage.name === "SYSTEM_DESIGN"
        ? DESIGN_ARTIFACT_KEYS
        : stage.name === "CODE_GENERATION"
          ? CODE_ARTIFACT_KEYS
          : stage.name === "TEST_GENERATION"
            ? TEST_ARTIFACT_KEYS
            : stage.name === "APPLY_AND_RUN_TESTS"
              ? TEST_RUN_ARTIFACT_KEYS
        : [];

  for (const key of preferredKeys) {
    const value = output[key];
    if (value !== undefined && value !== null) {
      return { key, value };
    }
  }

  if (
    stage.name === "REQUIREMENT_ANALYSIS" ||
    stage.name === "SYSTEM_DESIGN" ||
    stage.name === "CODE_GENERATION" ||
    stage.name === "TEST_GENERATION" ||
    stage.name === "APPLY_AND_RUN_TESTS"
  ) {
    return null;
  }
  return Object.keys(output).length > 0 ? { key: "output", value: output } : null;
}

function buildRequirementArtifact(sourceKey: string, value: unknown): StageArtifactViewModel {
  const record = asRecord(value);
  return {
    title: "结构化需求 PRD",
    sourceKey,
    description: "只展示需求分析阶段产出的 structured_prd，隐藏代码上下文和探索轨迹等调试字段。",
    summaryFields: compactFields([
      ["需求摘要", record.summary],
      ["问题陈述", record.problem_statement],
      ["置信度", asRecord(record.quality).confidence],
    ]),
    sections: compactSections([
      objectListSection("用户故事", record.user_stories),
      objectListSection("验收条件", record.acceptance_criteria),
      textListSection("假设", record.assumptions),
      textListSection("边界场景", record.edge_cases),
      textListSection("开放问题", record.open_questions),
      objectListSection("非功能需求", record.non_functional_requirements),
      objectListSection("证据摘要", record.evidence),
    ]),
    raw: value,
  };
}

function buildDesignArtifact(sourceKey: string, value: unknown): StageArtifactViewModel {
  const record = asRecord(value);
  return {
    title: "方案设计文档",
    sourceKey,
    description: "只展示方案设计阶段产出的 design_doc，便于在人工检查点快速审阅设计结构。",
    summaryFields: compactFields([
      ["方案摘要", record.summary],
      ["对应 PRD", record.prd_summary],
      ["置信度", asRecord(record.quality).confidence],
    ]),
    sections: compactSections([
      objectListSection("模块设计", record.modules),
      objectListSection("文件计划", record.file_plan),
      textListSection("实现步骤", record.implementation_steps),
      objectListSection("风险与缓解", record.risks),
      textListSection("开放问题", record.open_questions),
      textListSection("假设", record.assumptions),
      objectListSection("质量信息", record.quality),
    ]),
    raw: value,
  };
}

function buildCodeGenerationArtifact(
  sourceKey: string,
  value: unknown,
  output: StageStatusResponse["output"],
): StageArtifactViewModel {
  const diffText = String(value ?? "");
  const files = parseUnifiedDiff(diffText);
  const report = asRecord(output.code_generation_report);
  return {
    title: "代码 Diff",
    sourceKey,
    description: "展示代码生成阶段产出的 diff_patch。这里不会应用补丁，只用于人工审查和后续沙箱处理。",
    summaryFields: compactFields([
      ["生成摘要", report.summary],
      ["修改文件数", String(files.length)],
      ["新增行", String(files.reduce((sum, file) => sum + file.additions, 0))],
      ["删除行", String(files.reduce((sum, file) => sum + file.deletions, 0))],
    ]),
    sections: compactSections([
      {
        title: "Diff 文件",
        items: files.map((file) => [
          { label: "文件", value: file.path },
          { label: "新增行", value: String(file.additions) },
          { label: "删除行", value: String(file.deletions) },
          { label: "片段", value: file.preview },
        ]),
      },
      objectListSection("生成报告", report),
    ]),
    raw: diffText,
  };
}

function buildTestGenerationArtifact(sourceKey: string, value: unknown): StageArtifactViewModel {
  const record: Record<string, unknown> = { ...asRecord(value), execution_results: undefined, executionResults: undefined };
  const testDiff = String(record.test_diff_patch ?? record.testDiffPatch ?? "");
  const diffFiles = parseUnifiedDiffForDisplay(testDiff);
  const passedCount = 0;
  const failedCount = 0;
  return {
    title: "测试生成结果",
    sourceKey,
    description: "展示测试生成阶段产出的测试代码 diff、测试文件计划和执行结果。测试补丁不会在控制台自动应用。",
    summaryFields: compactFields([
      ["状态", record.status],
      ["摘要", record.summary],
      ["测试文件数", String(normalizeDisplayArray(record.test_files ?? record.testFiles).length || diffFiles.length)],
      ["测试命令数", String(normalizeDisplayArray(record.test_commands ?? record.testCommands).length)],
      ["通过", passedCount ? String(passedCount) : undefined],
      ["失败", failedCount ? String(failedCount) : undefined],
    ]),
    sections: compactSections([
      objectListSection("测试文件", record.test_files ?? record.testFiles),
      objectListSection("测试命令", record.test_commands ?? record.testCommands),
      objectListSection("执行结果", record.execution_results ?? record.executionResults),
      textListSection("覆盖重点", record.coverage_focus ?? record.coverageFocus),
      textListSection("风险", record.risks),
      textListSection("开放问题", record.open_questions ?? record.openQuestions),
    ]),
    raw: record,
  };
}

function buildTestRunArtifact(sourceKey: string, value: unknown): StageArtifactViewModel {
  const record = asRecord(value);
  return {
    title: "测试执行状态",
    sourceKey,
    description: "当前未启用沙箱执行。本阶段用于展示人工应用补丁、手动运行测试和回填真实结果的操作要求。",
    summaryFields: compactFields([
      ["状态", record.status],
      ["摘要", record.summary],
      ["应用策略", record.apply_strategy ?? record.applyStrategy],
      ["命令数", String(normalizeDisplayArray(record.test_commands ?? record.testCommands).length)],
    ]),
    sections: compactSections([
      textListSection("人工步骤", record.manual_steps ?? record.manualSteps),
      objectListSection("测试命令", record.test_commands ?? record.testCommands),
      objectListSection("执行结果", record.execution_results ?? record.executionResults),
      textListSection("需要审批的阶段", record.required_approvals ?? record.requiredApprovals),
    ]),
    raw: value,
  };
}

function buildGenericArtifact(title: string, sourceKey: string, value: unknown): StageArtifactViewModel {
  return {
    title,
    sourceKey,
    description: "该阶段尚未定义专用产物视图，以下按通用结构展示。",
    summaryFields: [],
    sections: [objectListSection("阶段输出", value)].filter((section): section is DisplaySection => !!section),
    raw: value,
  };
}

interface DiffFileSummary {
  path: string;
  additions: number;
  deletions: number;
  preview: string;
}

function parseUnifiedDiff(diffText: string): DiffFileSummary[] {
  const files: DiffFileSummary[] = [];
  let current: DiffFileSummary | null = null;
  const previewLimit = 24;

  for (const line of diffText.split(/\r?\n/)) {
    const gitHeader = /^diff --git\s+a\/(.+?)\s+b\/(.+)$/.exec(line);
    if (gitHeader) {
      current = {
        path: gitHeader[2],
        additions: 0,
        deletions: 0,
        preview: "",
      };
      files.push(current);
      continue;
    }

    const plusHeader = /^\+\+\+\s+b\/(.+)$/.exec(line);
    if (!current && plusHeader) {
      current = {
        path: plusHeader[1],
        additions: 0,
        deletions: 0,
        preview: "",
      };
      files.push(current);
      continue;
    }

    if (!current) {
      continue;
    }
    if (line.startsWith("+") && !line.startsWith("+++")) {
      current.additions += 1;
    } else if (line.startsWith("-") && !line.startsWith("---")) {
      current.deletions += 1;
    }

    if (
      (line.startsWith("@@") || line.startsWith("+") || line.startsWith("-") || line.startsWith(" ")) &&
      current.preview.split("\n").filter(Boolean).length < previewLimit
    ) {
      current.preview = [current.preview, line].filter(Boolean).join("\n");
    }
  }

  if (files.length === 0 && diffText.trim()) {
    return [
      {
        path: "未识别文件",
        additions: (diffText.match(/^\+(?!\+\+)/gm) ?? []).length,
        deletions: (diffText.match(/^-(?!--)/gm) ?? []).length,
        preview: diffText.split(/\r?\n/).slice(0, previewLimit).join("\n"),
      },
    ];
  }
  return files;
}

function objectListSection(title: string, value: unknown): DisplaySection | null {
  if (Array.isArray(value)) {
    return value.length > 0
      ? {
          title,
          items: value.map((item) => normalizeDisplayFields(item)),
        }
      : null;
  }

  if (isRecord(value)) {
    const fields = normalizeDisplayFields(value);
    return fields.length > 0 ? { title, items: [fields] } : null;
  }

  if (value === undefined || value === null || value === "") {
    return null;
  }

  return {
    title,
    items: [[{ label: "内容", value: toDisplayText(value) }]],
  };
}

function textListSection(title: string, value: unknown): DisplaySection | null {
  if (!Array.isArray(value) || value.length === 0) {
    return null;
  }
  return {
    title,
    items: value.map((item, index) => [
      {
        label: String(index + 1),
        value: toDisplayText(item),
      },
    ]),
  };
}

function compactFields(entries: Array<[string, unknown]>): DisplayField[] {
  return entries
    .filter(([, value]) => value !== undefined && value !== null && value !== "")
    .map(([label, value]) => ({ label, value: toDisplayText(value) }));
}

function compactSections(sections: Array<DisplaySection | null>): DisplaySection[] {
  return sections.filter((section): section is DisplaySection => !!section && section.items.length > 0);
}

function normalizeDisplayArray(value: unknown): Array<Record<string, unknown>> {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => (isRecord(item) ? item : { value: item }))
    .filter((item) => Object.keys(item).length > 0);
}

function normalizeDisplayFields(value: unknown): DisplayField[] {
  if (!isRecord(value)) {
    return [{ label: "内容", value: toDisplayText(value) }];
  }

  return Object.entries(value)
    .filter(([, fieldValue]) => fieldValue !== undefined && fieldValue !== null && fieldValue !== "")
    .map(([key, fieldValue]) => ({
      label: readableLabel(key),
      value: toDisplayText(fieldValue),
    }));
}

function toDisplayText(value: unknown): string {
  if (Array.isArray(value)) {
    return value.map(toDisplayText).join("\n");
  }
  if (isRecord(value)) {
    return Object.entries(value)
      .filter(([, nestedValue]) => nestedValue !== undefined && nestedValue !== null && nestedValue !== "")
      .map(([key, nestedValue]) => `${readableLabel(key)}: ${toDisplayText(nestedValue)}`)
      .join("\n");
  }
  return String(value ?? "");
}

function readableLabel(key: string): string {
  const labels: Record<string, string> = {
    id: "ID",
    title: "标题",
    name: "名称",
    summary: "摘要",
    description: "描述",
    requirement: "需求",
    priority: "优先级",
    verification: "验证方式",
    rationale: "理由",
    owner: "责任方",
    path: "路径",
    file: "文件",
    action: "动作",
    risk: "风险",
    case: "边界场景",
    handling: "处理方式",
    mitigation: "缓解措施",
    confidence: "置信度",
    source: "来源",
  };
  return labels[key] ?? key.replaceAll("_", " ");
}

function asRecord(value: unknown): Record<string, unknown> {
  return isRecord(value) ? value : {};
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

export function buildCodeContextViewModel(output: StageStatusResponse["output"] | null): CodeContextViewModel | null {
  const codeContext = output?.codeContext;
  if (!codeContext) {
    return null;
  }
  return {
    status: codeContext.status ?? "UNKNOWN",
    inspectedFiles: codeContext.inspectedFiles ?? [],
    searchQueries: codeContext.searchQueries ?? [],
    evidence: codeContext.evidence ?? [],
    skippedPaths: codeContext.skippedPaths ?? [],
    budgetUsage: normalizeBudgetUsage(codeContext.budgetUsage),
    confidence: typeof codeContext.confidence === "number" ? codeContext.confidence : 0,
    openQuestions: codeContext.openQuestions ?? [],
    trace: output.explorationTrace ?? codeContext.explorationTrace ?? [],
  };
}

export function normalizeBudgetUsage(value: CodeContextSummary["budgetUsage"]): Required<BudgetUsage> {
  return {
    roundsUsed: value?.roundsUsed ?? 0,
    filesRead: value?.filesRead ?? 0,
    bytesRead: value?.bytesRead ?? 0,
    searchesUsed: value?.searchesUsed ?? 0,
  };
}

export function parseUnifiedDiffForDisplay(diffText: string): CodeDiffFile[] {
  const files: CodeDiffFile[] = [];
  let current: CodeDiffFile | null = null;

  for (const line of diffText.split(/\r?\n/)) {
    const gitHeader = /^diff --git\s+a\/(.+?)\s+b\/(.+)$/.exec(line);
    if (gitHeader) {
      current = {
        path: gitHeader[2],
        displayPath: gitHeader[2].replaceAll("/", "\\"),
        header: "",
        additions: 0,
        deletions: 0,
        lines: [],
      };
      files.push(current);
      continue;
    }

    const plusHeader = /^\+\+\+\s+b\/(.+)$/.exec(line);
    if (!current && plusHeader) {
      current = {
        path: plusHeader[1],
        displayPath: plusHeader[1].replaceAll("/", "\\"),
        header: "",
        additions: 0,
        deletions: 0,
        lines: [],
      };
      files.push(current);
    }

    if (!current) {
      continue;
    }

    const type = classifyDiffLineForDisplay(line);
    if (type === "add") {
      current.additions += 1;
    } else if (type === "delete") {
      current.deletions += 1;
    }
    current.lines.push({ type, text: line });
  }

  if (files.length === 0 && diffText.trim()) {
    files.push({
      path: "unknown",
      displayPath: "unknown",
      header: "",
      additions: (diffText.match(/^\+(?!\+\+)/gm) ?? []).length,
      deletions: (diffText.match(/^-(?!--)/gm) ?? []).length,
      lines: diffText.split(/\r?\n/).map((line) => ({
        type: classifyDiffLineForDisplay(line),
        text: line,
      })),
    });
  }

  for (const file of files) {
    file.header = `Edited ${file.displayPath} (+${file.additions} -${file.deletions})`;
  }
  return files;
}

function classifyDiffLineForDisplay(line: string): CodeDiffLine["type"] {
  if (line.startsWith("+") && !line.startsWith("+++")) {
    return "add";
  }
  if (line.startsWith("-") && !line.startsWith("---")) {
    return "delete";
  }
  if (line.startsWith("@@")) {
    return "hunk";
  }
  if (line.startsWith("diff --git") || line.startsWith("index ") || line.startsWith("---") || line.startsWith("+++")) {
    return "meta";
  }
  return "context";
}
