import { PipelineApiClient, PipelineApiError } from "./api";
import "./styles.css";
import type {
  CheckpointDecision,
  PipelineStatusResponse,
  PipelineSummaryResponse,
  StageArtifactResponse,
  StageOutput,
  StageStatusResponse,
} from "./types";
import {
  buildCodeContextViewModel,
  buildCreatePipelineRequest,
  buildStageArtifactViewModel,
  DEFAULT_STAGE_NAMES,
  formatJson,
  hasArtifact,
  parseUnifiedDiffForDisplay,
  selectReviewStage,
  STAGE_OPTIONS,
  stageLabel,
  statusTone,
} from "./viewModel";
import type { CodeDiffFile } from "./viewModel";

interface AppState {
  pipeline: PipelineStatusResponse | null;
  selectedStageName: string | null;
  lastCheckpointOutput: Record<string, unknown> | null;
  checkpointOutputExpanded: boolean;
  loadedArtifactRevisions: Record<string, string>;
  loading: boolean;
  message: string | null;
  error: string | null;
}

const api = new PipelineApiClient();
const app = document.querySelector<HTMLDivElement>("#app");
const AUTO_REFRESH_INTERVAL_MS = 3000;

const state: AppState = {
  pipeline: null,
  selectedStageName: null,
  lastCheckpointOutput: null,
  checkpointOutputExpanded: false,
  loadedArtifactRevisions: {},
  loading: false,
  message: null,
  error: null,
};
let autoRefreshInFlight = false;

if (!app) {
  throw new Error("App root not found.");
}

app.innerHTML = `
  <main class="console-shell">
    <section class="toolbar-band">
      <div>
        <p class="eyebrow">DevFlow Engine</p>
        <h1>流水线控制台</h1>
      </div>
      <div class="status-strip" aria-live="polite">
        <span id="globalStatus" class="status-pill neutral">未连接</span>
        <span id="globalMessage" class="message-text">等待创建或查询流水线</span>
      </div>
    </section>

    <section class="workbench">
      <form id="createForm" class="panel create-panel">
        <div class="panel-heading">
          <h2>创建流水线</h2>
          <button id="createButton" class="primary-button" type="submit">启动</button>
        </div>

        <label class="field">
          <span>流水线名称</span>
          <input name="name" value="DevFlow 插件开发" autocomplete="off" required />
        </label>

        <label class="field">
          <span>新需求</span>
          <textarea name="requirement" required>完成 DevFlow-Engine 阶段 4 的 T030 任务。请在根目录的demo文件夹下生成一个简单的测试网页和插件代码，不需要很复杂，完成框架即可。</textarea>
        </label>

        <fieldset class="field stage-field">
          <legend>阶段</legend>
          <div id="stageOptions" class="stage-options"></div>
        </fieldset>

        <div class="repository-grid">
          <label class="field">
            <span>代码库根目录</span>
            <input name="rootPath" value="D:/ZPY/Agent学习/DevFlow-Engine" autocomplete="off" />
          </label>
          <label class="field">
            <span>最大文件数</span>
            <input name="maxFiles" value="800" inputmode="numeric" />
          </label>
          <label class="field">
            <span>最大字节数</span>
            <input name="maxBytes" value="262144" inputmode="numeric" />
          </label>
        </div>

        <div class="path-grid">
          <label class="field">
            <span>包含路径</span>
            <textarea name="includePaths">control-plane/devflow-engine/src/main/java
sandbox/frontend
specs/001-devflow-engine</textarea>
          </label>
          <label class="field">
            <span>排除路径</span>
            <textarea name="excludePaths">node_modules
target
dist
.git</textarea>
          </label>
          <label class="field">
            <span>重点文件</span>
            <textarea name="targetFiles">specs/001-devflow-engine/tasks.md
control-plane/devflow-engine/src/main/java/com/devflow/engine/api/PipelineController.java
sandbox/frontend/src/main.ts</textarea>
          </label>
        </div>
      </form>

      <section class="panel monitor-panel">
        <div class="panel-heading">
          <h2>状态与产物</h2>
          <button id="refreshButton" class="secondary-button" type="button">刷新</button>
        </div>
        <label class="field inline-field">
          <span>Pipeline ID</span>
          <input id="pipelineIdInput" autocomplete="off" placeholder="创建后自动填入，或手动粘贴已有 ID" />
        </label>

        <div id="pipelineSummary" class="summary-grid"></div>
        <div id="stageList" class="stage-list"></div>
      </section>

      <section class="panel artifact-panel">
        <div class="panel-heading">
          <h2>阶段产物</h2>
          <span id="artifactStage" class="subtle-text">未选择阶段</span>
        </div>
        <div id="codeContextPanel" class="code-context-panel"></div>
        <div id="artifactOutput" class="artifact-output artifact-view"></div>
      </section>

      <section class="panel checkpoint-panel">
        <div class="panel-heading">
          <h2>人工检查点</h2>
          <span id="checkpointHint" class="subtle-text">等待可审批阶段</span>
        </div>
        <div class="decision-row" role="group" aria-label="审批决定">
          <label><input type="radio" name="decision" value="APPROVE" checked /> Approve</label>
          <label><input type="radio" name="decision" value="REJECT" /> Reject</label>
        </div>
        <label class="field">
          <span>反馈</span>
          <textarea id="feedbackInput" placeholder="Reject 时建议写明需要补充或重做的内容；Approve 可留空。"></textarea>
        </label>
        <button id="submitDecisionButton" class="primary-button full-button" type="button">提交反馈</button>
        <details id="checkpointOutputDetails" class="checkpoint-output-details">
          <summary>查看提交响应</summary>
          <pre id="checkpointOutput" class="checkpoint-output"></pre>
        </details>
      </section>
    </section>
  </main>
`;

const createForm = mustGet<HTMLFormElement>("createForm");
const createButton = mustGet<HTMLButtonElement>("createButton");
const refreshButton = mustGet<HTMLButtonElement>("refreshButton");
const pipelineIdInput = mustGet<HTMLInputElement>("pipelineIdInput");
const globalStatus = mustGet<HTMLSpanElement>("globalStatus");
const globalMessage = mustGet<HTMLSpanElement>("globalMessage");
const pipelineSummary = mustGet<HTMLDivElement>("pipelineSummary");
const stageList = mustGet<HTMLDivElement>("stageList");
const artifactStage = mustGet<HTMLSpanElement>("artifactStage");
const codeContextPanel = mustGet<HTMLDivElement>("codeContextPanel");
const artifactOutput = mustGet<HTMLDivElement>("artifactOutput");
const checkpointHint = mustGet<HTMLSpanElement>("checkpointHint");
const submitDecisionButton = mustGet<HTMLButtonElement>("submitDecisionButton");
const feedbackInput = mustGet<HTMLTextAreaElement>("feedbackInput");
const checkpointOutputDetails = mustGet<HTMLDetailsElement>("checkpointOutputDetails");
const checkpointOutput = mustGet<HTMLPreElement>("checkpointOutput");
const stageOptions = mustGet<HTMLDivElement>("stageOptions");

stageOptions.replaceChildren(
  ...[...STAGE_OPTIONS].sort((left, right) => stageOptionOrder(left.name) - stageOptionOrder(right.name)).map((stage) => {
    const label = document.createElement("label");
    label.className = "check-option";
    const checked = DEFAULT_STAGE_NAMES.includes(stage.name);
    label.innerHTML = `<input type="checkbox" name="stages" value="${stage.name}" ${checked ? "checked" : ""} /> <span>${stageLabel(stage.name)}</span>`;
    return label;
  }),
);

function stageOptionOrder(stageName: string): number {
  const defaultIndex = DEFAULT_STAGE_NAMES.indexOf(stageName);
  if (defaultIndex >= 0) {
    return defaultIndex;
  }
  const optionIndex = STAGE_OPTIONS.findIndex((stage) => stage.name === stageName);
  return DEFAULT_STAGE_NAMES.length + (optionIndex >= 0 ? optionIndex : 999);
}

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await withLoading(async () => {
    const request = buildCreatePipelineRequest(readCreateForm());
    const response = await api.createPipeline(request);
    pipelineIdInput.value = response.pipelineId;
    state.selectedStageName = null;
    state.lastCheckpointOutput = null;
    state.loadedArtifactRevisions = {};
    state.message = `已创建流水线：${response.pipelineId}${response.workflowId ? ` / Workflow: ${response.workflowId}` : ""}`;
    await refreshPipeline();
  });
});

refreshButton.addEventListener("click", () => withLoading(refreshPipeline));

submitDecisionButton.addEventListener("click", async () => {
  await withLoading(async () => {
    const pipelineId = pipelineIdInput.value.trim();
    const stage = selectReviewStage(state.pipeline, state.selectedStageName);
    if (!pipelineId || !stage) {
      throw new Error("请先创建或查询流水线，并选择一个阶段。");
    }

    const response = await api.submitCheckpointDecision(
      pipelineId,
      stage.name,
      readDecision(),
      feedbackInput.value.trim(),
    );
    state.lastCheckpointOutput = response.stageOutput;
    state.checkpointOutputExpanded = false;
    state.message = response.message;
    await refreshPipeline();
  });
});

render();
checkpointOutputDetails.addEventListener("toggle", () => {
  state.checkpointOutputExpanded = checkpointOutputDetails.open;
  renderCheckpoint(selectReviewStage(state.pipeline, state.selectedStageName), state.pipeline);
});
window.setInterval(() => {
  void autoRefreshPipeline();
}, AUTO_REFRESH_INTERVAL_MS);

async function refreshPipeline(): Promise<void> {
  const pipelineId = pipelineIdInput.value.trim();
  if (!pipelineId) {
    throw new Error("请输入 Pipeline ID。");
  }
  state.pipeline = await api.getPipeline(pipelineId);
  state.loadedArtifactRevisions = Object.fromEntries(
    state.pipeline.stages
      .filter((stage) => stage.artifactRevision && hasArtifact(stage))
      .map((stage) => [stage.name, String(stage.artifactRevision)]),
  );
  const selected = selectReviewStage(state.pipeline, state.selectedStageName);
  state.selectedStageName = selected?.name ?? null;
  state.message = "流水线状态已刷新。";
}

function readCreateForm() {
  const data = new FormData(createForm);
  return {
    name: String(data.get("name") ?? ""),
    requirement: String(data.get("requirement") ?? ""),
    stages: data.getAll("stages").map(String),
    rootPath: String(data.get("rootPath") ?? ""),
    includePaths: String(data.get("includePaths") ?? ""),
    excludePaths: String(data.get("excludePaths") ?? ""),
    targetFiles: String(data.get("targetFiles") ?? ""),
    maxFiles: String(data.get("maxFiles") ?? ""),
    maxBytes: String(data.get("maxBytes") ?? ""),
  };
}

async function autoRefreshPipeline(): Promise<void> {
  const pipelineId = pipelineIdInput.value.trim();
  if (!pipelineId || state.loading || autoRefreshInFlight) {
    return;
  }
  if (state.pipeline?.status === "COMPLETED" || state.pipeline?.status === "FAILED") {
    return;
  }
  autoRefreshInFlight = true;
  try {
    state.pipeline = mergePipelineSummary(state.pipeline, await api.getPipelineSummary(pipelineId));
    const selected = selectReviewStage(state.pipeline, state.selectedStageName);
    state.selectedStageName = selected?.name ?? null;
    state.error = null;
    render({ preserveArtifact: true });
  } catch (error) {
    state.error = describeError(error);
    render({ preserveArtifact: true });
  } finally {
    autoRefreshInFlight = false;
  }
}

async function selectStage(stageName: string): Promise<void> {
  state.selectedStageName = stageName;
  const pipelineId = pipelineIdInput.value.trim();
  const stage = state.pipeline?.stages.find((item) => item.name === stageName) ?? null;
  if (pipelineId && shouldLoadStageArtifact(stage)) {
    try {
      mergeStageArtifact(await api.getStageArtifact(pipelineId, stageName));
      state.error = null;
    } catch (error) {
      state.error = describeError(error);
    }
  }
  render();
}

function shouldLoadStageArtifact(stage: StageStatusResponse | null): boolean {
  if (!stage?.outputAvailable) {
    return false;
  }
  return state.loadedArtifactRevisions[stage.name] !== String(stage.artifactRevision ?? "");
}

function mergePipelineSummary(
  current: PipelineStatusResponse | null,
  summary: PipelineSummaryResponse,
): PipelineStatusResponse {
  const previousStages = new Map((current?.stages ?? []).map((stage) => [stage.name, stage]));
  return {
    pipelineId: summary.pipelineId,
    workflowId: summary.workflowId,
    status: summary.status,
    currentStage: summary.currentStage,
    repository: summary.repository,
    stages: summary.stages.map((stage) => {
      const previous = previousStages.get(stage.name);
      return {
        name: stage.name,
        status: stage.status,
        requiresHumanApproval: stage.requiresHumanApproval,
        outputAvailable: stage.outputAvailable,
        artifactRevision: stage.artifactRevision,
        output: previous?.output ?? {},
      };
    }),
  };
}

function mergeStageArtifact(artifact: StageArtifactResponse): void {
  if (!state.pipeline) {
    return;
  }
  state.pipeline = {
    ...state.pipeline,
    stages: state.pipeline.stages.map((stage) =>
      stage.name === artifact.stageName
        ? {
            ...stage,
            status: artifact.status,
            requiresHumanApproval: artifact.requiresHumanApproval,
            artifactRevision: artifact.artifactRevision,
            outputAvailable: true,
            output: (artifact.output ?? {}) as StageOutput,
          }
        : stage,
    ),
  };
  state.loadedArtifactRevisions[artifact.stageName] = String(artifact.artifactRevision ?? "");
}

function readDecision(): CheckpointDecision {
  const checked = document.querySelector<HTMLInputElement>('input[name="decision"]:checked');
  return checked?.value === "REJECT" ? "REJECT" : "APPROVE";
}

async function withLoading(action: () => Promise<void>): Promise<void> {
  state.loading = true;
  state.error = null;
  render();
  try {
    await action();
  } catch (error) {
    state.error = describeError(error);
  } finally {
    state.loading = false;
    render();
  }
}

function render(options: { preserveArtifact?: boolean } = {}): void {
  /*
   * 状态展示以控制平面的 PipelineStatusResponse 为唯一事实来源。
   * Workflow 运行细节、Activity 中间产物和人工检查点结果都已经由后端压缩成阶段快照，
   * 前端只负责把这些快照稳定地摆出来，并在用户确认后把 Approve/Reject 决策发回后端。
   */
  const pipeline = state.pipeline;
  const selectedStage = selectReviewStage(pipeline, state.selectedStageName);
  renderGlobalStatus(pipeline);
  renderSummary(pipeline);
  renderStageList(pipeline, selectedStage);
  if (!options.preserveArtifact) {
    renderArtifact(selectedStage);
  }
  renderCheckpoint(selectedStage, pipeline);

  createButton.disabled = state.loading;
  refreshButton.disabled = state.loading;
  submitDecisionButton.disabled = state.loading || !selectedStage;
}

function renderGlobalStatus(pipeline: PipelineStatusResponse | null): void {
  const status = state.error ? "ERROR" : pipeline?.status ?? "READY";
  globalStatus.className = `status-pill ${state.error ? "danger" : statusTone(status)}`;
  globalStatus.textContent = state.loading ? "处理中" : status;
  globalMessage.textContent = state.error ?? state.message ?? "等待创建或查询流水线";
}

function renderSummary(pipeline: PipelineStatusResponse | null): void {
  pipelineSummary.replaceChildren();
  const items = [
    ["Pipeline ID", pipeline?.pipelineId ?? "-"],
    ["Workflow ID", pipeline?.workflowId ?? "-"],
    ["当前阶段", pipeline?.currentStage ? stageLabel(pipeline.currentStage) : "-"],
    ["阶段数", pipeline ? String(pipeline.stages.length) : "0"],
    ["代码库", pipeline?.repository?.rootPath ?? "未提供"],
  ];

  for (const [label, value] of items) {
    const item = document.createElement("div");
    item.className = "summary-item";
    const title = document.createElement("span");
    title.textContent = label;
    const content = document.createElement("strong");
    content.textContent = value;
    item.append(title, content);
    pipelineSummary.append(item);
  }
}

function renderStageList(
  pipeline: PipelineStatusResponse | null,
  selectedStage: StageStatusResponse | null,
): void {
  stageList.replaceChildren();
  if (!pipeline) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "创建或查询流水线后显示阶段列表。";
    stageList.append(empty);
    return;
  }

  for (const stage of pipeline.stages) {
    const reviewState = checkpointReviewState(stage, pipeline);
    const button = document.createElement("button");
    button.type = "button";
    button.className = `stage-row ${selectedStage?.name === stage.name ? "selected" : ""} ${reviewState === "required" ? "needs-review" : ""} ${reviewState === "reviewed" ? "reviewed" : ""} ${reviewState === "rejected" ? "rejected" : ""}`;
    button.addEventListener("click", () => {
      void selectStage(stage.name);
    });

    const name = document.createElement("span");
    name.className = "stage-name";
    name.textContent = stageLabel(stage.name);
    const meta = document.createElement("span");
    meta.className = "stage-meta";
    meta.textContent = `${stage.status}${reviewState === "required" ? " / 需要人工检查" : ""}${reviewState === "reviewed" ? " / 已审查" : ""}${stage.requiresHumanApproval && reviewState === "none" ? " / 需审查" : ""}${hasArtifact(stage) ? " / 有产物" : ""}`;
    meta.textContent = stageMetaText(stage, reviewState);
    button.append(name, meta);
    stageList.append(button);
  }
}

function renderArtifact(stage: StageStatusResponse | null): void {
  artifactStage.textContent = stage ? `${stageLabel(stage.name)} · ${stage.status}` : "未选择阶段";
  renderCodeContext(stage);
  renderStageArtifact(stage);
}

function stageMetaText(
  stage: StageStatusResponse,
  reviewState: "required" | "reviewed" | "rejected" | "none",
): string {
  const parts = [stage.status];
  if (reviewState === "required") {
    parts.push("需要人工检查");
  }
  if (reviewState === "reviewed") {
    parts.push("已审查");
  }
  if (reviewState === "rejected") {
    parts.push("已 Reject，退回该阶段重新执行");
  }
  if (stage.requiresHumanApproval && reviewState === "none") {
    parts.push("需审查");
  }
  if (stage.outputAvailable || hasArtifact(stage)) {
    parts.push("有产物");
  }
  return parts.join(" / ");
}

function renderStageArtifact(stage: StageStatusResponse | null): void {
  artifactOutput.replaceChildren();
  const view = buildStageArtifactViewModel(stage);
  if (!view) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "选择阶段后显示核心阶段产物。";
    artifactOutput.append(empty);
    return;
  }

  const header = document.createElement("div");
  header.className = "artifact-header";
  const title = document.createElement("div");
  title.className = "artifact-title";
  const heading = document.createElement("strong");
  heading.textContent = view.title;
  const source = document.createElement("span");
  source.textContent = view.sourceKey;
  title.append(heading, source);
  const description = document.createElement("p");
  description.textContent = view.description;
  header.append(title, description);
  artifactOutput.append(header);

  if (view.summaryFields.length > 0) {
    const summary = document.createElement("div");
    summary.className = "artifact-summary";
    for (const field of view.summaryFields) {
      summary.append(renderField(field.label, field.value));
    }
    artifactOutput.append(summary);
  }

  const diffText = typeof view.raw === "string" ? view.raw : null;
  const isCodeDiffArtifact = diffText !== null && (view.sourceKey === "diff_patch" || view.sourceKey === "diffPatch");
  if (isCodeDiffArtifact) {
    artifactOutput.append(renderDiffFiles(parseUnifiedDiffForDisplay(diffText)));
  }
  const testDiffText = readTestDiffPatch(view.raw);
  const isTestResultArtifact = testDiffText !== null && (view.sourceKey === "test_results" || view.sourceKey === "testResults");
  if (isTestResultArtifact) {
    artifactOutput.append(renderDiffFiles(parseUnifiedDiffForDisplay(testDiffText), "测试代码 Diff"));
  }

  const visibleSections = isCodeDiffArtifact
    ? view.sections.filter((section) => !section.title.includes("Diff"))
    : view.sections;

  if (visibleSections.length === 0 && !isCodeDiffArtifact && !isTestResultArtifact) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "该核心产物暂无可展开字段。";
    artifactOutput.append(empty);
  }

  for (const section of visibleSections) {
    const sectionElement = document.createElement("section");
    sectionElement.className = "artifact-section";
    sectionElement.append(sectionTitle(section.title));

    for (const item of section.items) {
      const card = document.createElement("div");
      card.className = "artifact-card";
      for (const field of item) {
        card.append(renderField(field.label, field.value));
      }
      sectionElement.append(card);
    }
    artifactOutput.append(sectionElement);
  }

  const details = document.createElement("details");
  details.className = "artifact-raw";
  const summary = document.createElement("summary");
  summary.textContent = "查看原始 JSON";
  const raw = document.createElement("pre");
  raw.textContent = typeof view.raw === "string" ? view.raw : formatJson(view.raw);
  details.append(summary, raw);
  artifactOutput.append(details);
}

function renderField(label: string, value: string): HTMLElement {
  const row = document.createElement("div");
  row.className = "artifact-field";
  const labelElement = document.createElement("span");
  labelElement.textContent = label;
  const valueElement = document.createElement("p");
  const toneClass = artifactValueToneClass(label, value);
  if (toneClass) {
    valueElement.classList.add("artifact-value-pill", toneClass);
  }
  valueElement.textContent = value;
  row.append(labelElement, valueElement);
  return row;
}

function artifactValueToneClass(label: string, value: string): string | null {
  const normalizedLabel = label.toLowerCase();
  const normalizedValue = value.toUpperCase();
  const isStatusLike =
    normalizedLabel.includes("status") ||
    normalizedLabel.includes("状态") ||
    normalizedLabel.includes("严重") ||
    normalizedLabel.includes("severity");
  if (!isStatusLike || value.includes("\n")) {
    return null;
  }
  if (["READY", "APPROVED", "PASSED", "DONE", "GENERATED"].includes(normalizedValue)) {
    return "tone-success";
  }
  if (["NEEDS_CHANGES", "BLOCKED", "SUSPENDED", "NOT_RUN", "MANUAL_ACTION_REQUIRED", "MEDIUM"].includes(normalizedValue)) {
    return "tone-warning";
  }
  if (["FAILED", "REJECTED", "HIGH"].includes(normalizedValue)) {
    return "tone-danger";
  }
  if (["LOW"].includes(normalizedValue)) {
    return "tone-neutral";
  }
  return null;
}

function renderDiffFiles(files: CodeDiffFile[], title = "Diff 文件"): HTMLElement {
  const section = document.createElement("section");
  section.className = "artifact-section diff-section";
  section.append(sectionTitle(title));

  if (files.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "没有可展示的 diff。";
    section.append(empty);
    return section;
  }

  for (const file of files) {
    const fileBlock = document.createElement("article");
    fileBlock.className = "diff-file";

    const header = document.createElement("div");
    header.className = "diff-file-heading";
    header.textContent = file.header;

    const code = document.createElement("code");
    code.className = "diff-code";
    for (const line of file.lines) {
      const row = document.createElement("span");
      row.className = `diff-line ${line.type}`;
      row.textContent = line.text || " ";
      code.append(row);
    }

    const pre = document.createElement("pre");
    pre.append(code);
    fileBlock.append(header, pre);
    section.append(fileBlock);
  }
  return section;
}

function readTestDiffPatch(raw: unknown): string | null {
  if (!raw || typeof raw !== "object" || Array.isArray(raw)) {
    return null;
  }
  const record = raw as Record<string, unknown>;
  const value = record.test_diff_patch ?? record.testDiffPatch;
  return typeof value === "string" && value.trim() ? value : null;
}

function renderCodeContext(stage: StageStatusResponse | null): void {
  codeContextPanel.replaceChildren();
  if (stage?.name === "REQUIREMENT_ANALYSIS" || stage?.name === "SYSTEM_DESIGN") {
    return;
  }
  const view = buildCodeContextViewModel(stage?.output ?? null);
  if (!view) {
    return;
  }

  const header = document.createElement("div");
  header.className = "code-context-header";
  header.innerHTML = `
    <span class="status-pill ${view.status === "COMPLETE" ? "success" : "warning"}">${view.status}</span>
    <strong>代码上下文</strong>
    <span>${Math.round(view.confidence * 100)}% confidence</span>
  `;

  const stats = document.createElement("div");
  stats.className = "code-context-stats";
  for (const [label, value] of [
    ["已读文件", String(view.budgetUsage.filesRead)],
    ["搜索次数", String(view.budgetUsage.searchesUsed)],
    ["读取字节", String(view.budgetUsage.bytesRead)],
    ["轮次", String(view.budgetUsage.roundsUsed)],
  ]) {
    const item = document.createElement("span");
    item.textContent = `${label}: ${value}`;
    stats.append(item);
  }

  const files = document.createElement("div");
  files.className = "code-context-list";
  files.append(sectionTitle("已读文件"));
  files.append(...listItems(view.inspectedFiles));

  const evidence = document.createElement("div");
  evidence.className = "evidence-list";
  evidence.append(sectionTitle("证据"));
  for (const item of view.evidence.slice(0, 6)) {
    const card = document.createElement("div");
    card.className = "evidence-card";
    const location = [item.filePath, item.lineStart ? `:${item.lineStart}` : ""].join("");
    card.innerHTML = `
      <strong>${escapeText(location)}</strong>
      <span>${escapeText(item.relevanceReason ?? "")}</span>
      <p>${escapeText(item.excerpt ?? "")}</p>
    `;
    evidence.append(card);
  }

  const trace = document.createElement("div");
  trace.className = "trace-list";
  trace.append(sectionTitle("探索轨迹"));
  for (const step of view.trace.slice(0, 8)) {
    const row = document.createElement("div");
    row.className = "trace-row";
    row.textContent = `${step.stepIndex ?? "-"} · ${step.actionType} · ${step.resultSummary ?? step.reason ?? ""}`;
    trace.append(row);
  }

  const questions = document.createElement("div");
  questions.className = "code-context-list";
  questions.append(sectionTitle("开放问题"));
  questions.append(...listItems(view.openQuestions));

  codeContextPanel.append(header, stats, files, evidence, trace, questions);
}

function sectionTitle(text: string): HTMLElement {
  const title = document.createElement("h3");
  title.className = "mini-heading";
  title.textContent = text;
  return title;
}

function listItems(values: string[]): HTMLElement[] {
  if (values.length === 0) {
    const empty = document.createElement("p");
    empty.className = "empty-text";
    empty.textContent = "无";
    return [empty];
  }
  return values.slice(0, 8).map((value) => {
    const item = document.createElement("p");
    item.className = "context-list-item";
    item.textContent = value;
    return item;
  });
}

function escapeText(value: string): string {
  return value
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function renderCheckpoint(stage: StageStatusResponse | null, pipeline: PipelineStatusResponse | null): void {
  const reviewState = checkpointReviewState(stage, pipeline);
  if (!stage) {
    checkpointHint.textContent = "等待可审查阶段";
  } else if (reviewState === "required") {
    checkpointHint.textContent = `${stageLabel(stage.name)} 需要人工检查：请审查阶段产物后选择 Approve 或 Reject。`;
  } else if (reviewState === "rejected") {
    checkpointHint.textContent = `${stageLabel(stage.name)} 已被 Reject，流水线已退回该阶段重新执行；请等待新的阶段产物后再次审查。`;
  } else if (reviewState === "reviewed") {
    checkpointHint.textContent = `${stageLabel(stage.name)} 已经审查，流水线可继续执行后续阶段。`;
  } else if (stage.requiresHumanApproval) {
    checkpointHint.textContent = `${stageLabel(stage.name)} 可提交人工反馈`;
  } else {
    checkpointHint.textContent = `${stageLabel(stage.name)} 默认不是人工检查点。`;
  }
  checkpointOutputDetails.open = state.checkpointOutputExpanded;
  checkpointOutput.textContent = state.checkpointOutputExpanded && state.lastCheckpointOutput
    ? formatJson(state.lastCheckpointOutput)
    : "";
}

function checkpointReviewState(
  stage: StageStatusResponse | null,
  pipeline: PipelineStatusResponse | null,
): "required" | "reviewed" | "rejected" | "none" {
  if (!stage?.requiresHumanApproval) {
    return "none";
  }
  if (pipeline?.status === "SUSPENDED" && pipeline.currentStage === stage.name) {
    return "required";
  }
  if (stage.status === "REJECTED") {
    return "rejected";
  }
  if (stage.status === "COMPLETED") {
    return "reviewed";
  }
  return "none";
}
function describeError(error: unknown): string {
  if (error instanceof PipelineApiError) {
    return `${error.status}: ${error.message}`;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return String(error);
}

function mustGet<T extends HTMLElement>(id: string): T {
  const element = document.getElementById(id);
  if (!element) {
    throw new Error(`Element not found: ${id}`);
  }
  return element as T;
}
