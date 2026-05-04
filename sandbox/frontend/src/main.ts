import { PipelineApiClient, PipelineApiError } from "./api";
import "./styles.css";
import type { CheckpointDecision, PipelineStatusResponse, StageStatusResponse } from "./types";
import {
  buildCreatePipelineRequest,
  DEFAULT_STAGE_NAMES,
  formatJson,
  hasArtifact,
  selectReviewStage,
  STAGE_OPTIONS,
  stageLabel,
  statusTone,
} from "./viewModel";

interface AppState {
  pipeline: PipelineStatusResponse | null;
  selectedStageName: string | null;
  lastCheckpointOutput: Record<string, unknown> | null;
  loading: boolean;
  message: string | null;
  error: string | null;
}

const api = new PipelineApiClient();
const app = document.querySelector<HTMLDivElement>("#app");

const state: AppState = {
  pipeline: null,
  selectedStageName: null,
  lastCheckpointOutput: null,
  loading: false,
  message: null,
  error: null,
};

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
          <input name="name" value="DevFlow 控制台联调" autocomplete="off" required />
        </label>

        <label class="field">
          <span>新需求</span>
          <textarea name="requirement" required>以当前 DevFlow-Engine 项目为材料，分析前端控制台如何触发流水线、展示阶段产物并提交人工反馈。</textarea>
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
            <input name="maxFiles" value="80" inputmode="numeric" />
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
        <pre id="artifactOutput" class="artifact-output">{}</pre>
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
        <pre id="checkpointOutput" class="checkpoint-output">{}</pre>
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
const artifactOutput = mustGet<HTMLPreElement>("artifactOutput");
const checkpointHint = mustGet<HTMLSpanElement>("checkpointHint");
const submitDecisionButton = mustGet<HTMLButtonElement>("submitDecisionButton");
const feedbackInput = mustGet<HTMLTextAreaElement>("feedbackInput");
const checkpointOutput = mustGet<HTMLPreElement>("checkpointOutput");
const stageOptions = mustGet<HTMLDivElement>("stageOptions");

stageOptions.replaceChildren(
  ...STAGE_OPTIONS.map((stage) => {
    const label = document.createElement("label");
    label.className = "check-option";
    const checked = DEFAULT_STAGE_NAMES.includes(stage.name);
    label.innerHTML = `<input type="checkbox" name="stages" value="${stage.name}" ${checked ? "checked" : ""} /> <span>${stage.label}</span>`;
    return label;
  }),
);

createForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  await withLoading(async () => {
    const request = buildCreatePipelineRequest(readCreateForm());
    const response = await api.createPipeline(request);
    pipelineIdInput.value = response.pipelineId;
    state.selectedStageName = null;
    state.lastCheckpointOutput = null;
    state.message = `流水线已启动：${response.pipelineId}`;
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
    state.message = response.message;
    await refreshPipeline();
  });
});

render();

async function refreshPipeline(): Promise<void> {
  const pipelineId = pipelineIdInput.value.trim();
  if (!pipelineId) {
    throw new Error("请输入 Pipeline ID。");
  }
  state.pipeline = await api.getPipeline(pipelineId);
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

function render(): void {
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
  renderArtifact(selectedStage);
  renderCheckpoint(selectedStage);

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
    const button = document.createElement("button");
    button.type = "button";
    button.className = `stage-row ${selectedStage?.name === stage.name ? "selected" : ""}`;
    button.addEventListener("click", () => {
      state.selectedStageName = stage.name;
      render();
    });

    const name = document.createElement("span");
    name.className = "stage-name";
    name.textContent = stageLabel(stage.name);
    const meta = document.createElement("span");
    meta.className = "stage-meta";
    meta.textContent = `${stage.status}${stage.requiresHumanApproval ? " / 需审批" : ""}${hasArtifact(stage) ? " / 有产物" : ""}`;
    button.append(name, meta);
    stageList.append(button);
  }
}

function renderArtifact(stage: StageStatusResponse | null): void {
  artifactStage.textContent = stage ? `${stageLabel(stage.name)} · ${stage.status}` : "未选择阶段";
  artifactOutput.textContent = stage ? formatJson(stage.output) : "{}";
}

function renderCheckpoint(stage: StageStatusResponse | null): void {
  if (!stage) {
    checkpointHint.textContent = "等待可审批阶段";
  } else if (stage.requiresHumanApproval) {
    checkpointHint.textContent = `${stageLabel(stage.name)} 可提交人工反馈`;
  } else {
    checkpointHint.textContent = `${stageLabel(stage.name)} 默认不是人工检查点，但仍可按后端契约提交 Signal`;
  }
  checkpointOutput.textContent = formatJson(state.lastCheckpointOutput ?? {});
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
