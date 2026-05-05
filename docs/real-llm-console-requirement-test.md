# 真实 LLM + 5173 流水线控制台测试说明

本文档用于做一次端到端用户测试：从 5173 前端控制台创建流水线，经控制平面提交 Temporal Workflow，由 Python Activity Worker 调用真实 LLM，只执行 `REQUIREMENT_ANALYSIS` 需求分析阶段。

## 测试目标

- 验证前端控制台 `http://127.0.0.1:5173` 可以创建只包含需求分析阶段的流水线。
- 验证控制平面、Temporal、Python Activity Worker 能连通。
- 验证 RequirementAgent 使用真实 LLM Provider，而不是 FakeProvider。
- 验证阶段产物里能看到 `structured_prd`、`codeContext`、`explorationTrace`。
- 验证日志文件里能看到 `llm.request`、`llm.response`、`requirement_agent.context_pack`。

## 前置条件

1. WSL Docker 可用，且本机能访问 Docker 暴露端口。
2. 本机 Maven 已可用。
3. `execution-plane/venv` 已安装执行平面依赖。
4. `sandbox/frontend/node_modules` 已安装前端依赖。
5. 已在 `execution-plane/.env.local` 或环境变量中配置真实 LLM API Key。

建议先确认 `execution-plane/.env.local` 至少包含以下内容。OpenAI-compatible 示例：

```env
DEVFLOW_LLM_PROVIDER=openai_compatible
DEVFLOW_LLM_OPENAI_API_KEY=你的真实APIKey
DEVFLOW_LLM_OPENAI_BASE_URL=https://api.openai.com/v1
DEVFLOW_LLM_OPENAI_DEFAULT_MODEL=gpt-4o-mini
DEVFLOW_LLM_MODEL=gpt-4o-mini
DEVFLOW_LLM_TRACE=1
DEVFLOW_LLM_TRACE_STDOUT=0
DEVFLOW_LLM_TRACE_FILE=./logs/real-llm-console-test.jsonl
DEVFLOW_LLM_TRACE_MAX_CHARS=120000
```

Anthropic-compatible 示例：

```env
DEVFLOW_LLM_PROVIDER=anthropic_compatible
DEVFLOW_LLM_ANTHROPIC_API_KEY=你的真实APIKey
DEVFLOW_LLM_ANTHROPIC_BASE_URL=https://api.anthropic.com/v1
DEVFLOW_LLM_ANTHROPIC_DEFAULT_MODEL=claude-3-5-haiku-latest
DEVFLOW_LLM_MODEL=claude-3-5-haiku-latest
DEVFLOW_LLM_TRACE=1
DEVFLOW_LLM_TRACE_STDOUT=0
DEVFLOW_LLM_TRACE_FILE=./logs/real-llm-console-test.jsonl
DEVFLOW_LLM_TRACE_MAX_CHARS=120000
```

`execution-plane/.env.local` 已被 `.gitignore` 忽略，不要把真实 Key 写入任何会提交的配置文件。

## 第一步：先验证真实 LLM 配置

在 PowerShell 中执行：

```powershell
cd D:\ZPY\Agent学习\DevFlow-Engine\execution-plane
.\venv\python.exe scripts\llm_smoke_test.py --config config\llm.local.json
```

预期输出类似：

```json
{"message":"llm smoke test passed","ok":true}
```

如果这里失败，先不要启动完整流水线。优先检查：

- `.env.local` 是否在 `execution-plane/.env.local`。
- `DEVFLOW_LLM_CONFIG_FILE` 或 `--config` 指向的 JSON 是否正确。
- `apiKeyEnv` 指向的环境变量名是否和 `.env.local` 中一致。
- Provider、base URL、model 是否和你的服务商兼容。

## 第二步：启动测试环境

从仓库根目录启动真实 LLM 模式：

```powershell
cd D:\ZPY\Agent学习\DevFlow-Engine
.\scripts\start-test-env.ps1 -UseRealLlm -FrontendPort 5173
```

启动完成后应看到：

- Temporal UI: `http://127.0.0.1:8234`
- Control plane: `http://127.0.0.1:8080`
- Frontend console: `http://127.0.0.1:5173`
- Logs: `.devflow-test-env`

`-UseRealLlm` 很关键。没有这个参数时，脚本会把 worker 强制切到 `fake` provider。

## 第三步：在 5173 控制台创建需求分析流水线

打开：

```text
http://127.0.0.1:5173
```

建议表单填写如下：

- 流水线名称：`真实 LLM 需求分析测试`
- 新需求：

```text
请基于当前 DevFlow-Engine 项目代码库，分析如何在前端流水线控制台中展示一次需求分析阶段的中间产物。要求说明用户如何创建流水线、控制平面如何提交 Temporal 工作流、执行平面 RequirementAgent 如何进行渐进式代码探索，并列出验收标准。
```

- 阶段：只勾选 `需求分析` / `REQUIREMENT_ANALYSIS`，取消其它阶段。
- 代码库根目录：填写当前仓库绝对路径，例如：

```text
D:/ZPY/Agent学习/DevFlow-Engine
```

- `includePaths`：留空。
- `excludePaths`：可留空；如想加速，可填写：

```text
execution-plane/venv
sandbox/frontend/node_modules
sandbox/daemon/node_modules
control-plane/devflow-engine/target
execution-plane/logs
```

- `targetFiles`：建议填写少量关键文件，帮助真实 LLM 测试更稳定：

```text
sandbox/frontend/src/main.ts
sandbox/frontend/src/viewModel.ts
execution-plane/src/agents/requirement_agent.py
execution-plane/src/context/repository_context.py
control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java
```

- 最大文件数：`20`
- 最大字节数：`180000`

点击 `启动` 后，控制台会显示 Pipeline ID。等待状态变成 `COMPLETED`。只执行需求分析阶段时，通常不需要人工审批。

## 第四步：检查前端产物

在左侧阶段列表中选择 `需求分析`。右侧应看到：

- `代码上下文` 面板。
- `已读文件` 数量大于 0。
- `搜索次数` 大于 0。
- `证据` 列表至少包含一个文件路径和行号。
- `探索轨迹` 包含 `PLAN`、`LIST_FILES`、`SEARCH_TEXT`、`READ_FILE`、`EVALUATE`。
- 原始 JSON 中包含：
  - `structured_prd`
  - `codeContext`
  - `explorationTrace`

如果 `codeContext.status` 是 `DEGRADED`，不一定代表失败。它通常表示预算不足或证据不够充分。真实验收时更关注是否有已读文件、证据、搜索词和 trace。

## 第五步：检查后端与 LLM 日志

Worker 和前端/后端进程日志在：

```text
.devflow-test-env/
```

重点查看：

```text
.devflow-test-env/execution-worker.out.log
.devflow-test-env/execution-worker.err.log
.devflow-test-env/control-plane.out.log
.devflow-test-env/control-plane.err.log
```

LLM trace 文件默认在：

```text
execution-plane/logs/real-llm-console-test.jsonl
```

trace 中至少应包含：

- `requirement_agent.context_pack`: 真实代码库探索结果，包括候选文件、已读文件、搜索词、证据和预算。
- `llm.request`: 发送给真实 LLM 的 messages、JSON schema、provider、model。
- `llm.response`: 真实 LLM 返回的文本、解析后的 JSON、usage、latency。
- `requirement_agent.validation_report`: 结构化 PRD 校验结果。

注意：trace 会记录 LLM 消息内容和代码片段，适合本地调试，不建议提交或分享。API Key、Authorization、token 等敏感字段会被脱敏。

## 可选：用脚本跑同一条链路

如果想在 UI 测试之外做一次可重复的 API 回归验证，可以在测试环境启动后执行：

```powershell
cd D:\ZPY\Agent学习\DevFlow-Engine
.\scripts\real-llm-requirement-analysis-test.ps1 -FrontendPort 5173
```

这个脚本会通过 5173 的 Vite proxy 调用 `/api/v1/pipelines`，创建只包含 `REQUIREMENT_ANALYSIS` 的流水线，并轮询到完成。输出结果会写入：

```text
.devflow-test-env/real-llm-requirement-analysis-result.json
```

脚本通过不代表 UI 人工体验完全通过；它只用于快速确认同一后端链路可用。

## 常见问题

### 8234 显示 No Workers Running

表示没有 worker 在轮询 `DEVFLOW_TASK_QUEUE`。请确认 `start-test-env.ps1 -UseRealLlm` 没有报错，并检查：

```text
.devflow-test-env/execution-worker.err.log
.devflow-test-env/control-plane.err.log
```

### 前端一直 RUNNING

通常是 Activity Worker 未启动、LLM 请求超时，或 Temporal task queue 没有 worker。先看 8234，再看 `.devflow-test-env` 日志。

### LLM smoke 通过，但流水线失败

重点检查 worker 启动时是否能读到同一个 `.env.local`。`start-test-env.ps1 -UseRealLlm` 的 worker 工作目录是 `execution-plane`，因此默认会读取 `execution-plane/.env.local`。

### codeContext 为空

检查控制台表单里的代码库根目录是否是本机绝对路径，并且该路径对 Python worker 可读。当前执行平面 worker 运行在本机 Python 进程中，不是在 WSL 容器内，因此应填写 Windows 可访问路径，例如 `D:/ZPY/Agent学习/DevFlow-Engine`。

### 想停掉测试环境

```powershell
cd D:\ZPY\Agent学习\DevFlow-Engine
.\scripts\stop-test-env.ps1
```
