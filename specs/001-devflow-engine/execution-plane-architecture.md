# 执行平面结构说明

本文档说明 Python 执行平面的代码结构、Temporal Activity Worker、LangGraph 状态图实现，以及它与 Java 控制平面的数据契约。

执行平面代码位于:

`execution-plane/src/`

## 总体职责

执行平面负责承载 AI 研发流水线中的实际阶段执行逻辑。控制平面通过 Temporal Workflow 调度阶段，执行平面通过 Temporal Activity Worker 接收任务，并把每个阶段映射到 LangGraph 状态图节点。

当前已完成:

- `DevFlowState` 共享状态类型。
- 六阶段 LangGraph 拓扑。
- 与 Java Activity 契约一致的 Python Temporal Activities。
- Python Activity Worker 启动入口。
- T019 路径驱动代码库上下文工具。
- T020 阶段产物落库/展示基础能力由控制平面同步快照提供。
- T021 可配置 LLM 调用客户端。
- T022 Requirement Agent。

当前尚未完成:

- T023-T027 的真实 Agent 业务逻辑。
- T028 的 LangGraph Checkpointer 和人工反馈回溯。
- 与真实 LLM、代码仓库、测试运行器、MR 平台的集成。

## 包结构

| 路径 | 职责 |
|------|------|
| `src/context/repository_context.py` | 提供路径驱动代码库上下文工具，支持目录遍历、文件读取、文本搜索和上下文打包 |
| `src/agents/requirement_agent.py` | 实现需求分析 Agent，调用 LLM Client 生成结构化 PRD，并写入代码上下文证据 |
| `src/llm/` | 提供可配置 LLM 调用客户端、Provider 抽象、结构化 JSON 输出解析、Fake Provider 测试能力 |
| `src/graph/state.py` | 定义 `DevFlowState`，作为 LangGraph 共享状态 |
| `src/graph/flow.py` | 定义阶段常量、节点函数、状态图构建和单阶段执行入口 |
| `src/graph/__init__.py` | 导出状态类型、阶段顺序和图构建函数 |
| `src/workers/activities.py` | 注册 Python Temporal Activities，并把 Activity 请求转换为 LangGraph 状态执行 |
| `src/workers/worker.py` | 创建并启动 Temporal Activity Worker |
| `src/workers/__init__.py` | 导出 Worker 工厂和 Activity 注册函数 |
| `tests/test_flow.py` | 验证 LangGraph 拓扑和人工反馈注入 |
| `tests/test_worker.py` | 验证 Activity 注册名和返回契约 |
| `tests/test_context_tools.py` | 验证代码库上下文工具，并覆盖真实项目仓库检索 |
| `tests/test_llm_client.py` | 验证 LLM Client 的 Provider 切换、结构化 JSON、错误处理、重试和请求适配 |
| `tests/test_requirement_agent.py` | 验证 Requirement Agent 的结构化 PRD、空需求诊断、真实项目仓库上下文、flow 和 Activity 集成 |

## LLM 调用客户端

T021 已在 `execution-plane/src/llm/` 中实现统一 LLM 调用客户端。这个模块不维护流水线阶段状态，也不决定 LangGraph 的路由；它只处理模型调用本身，包括 Provider 选择、请求转换、响应解析、重试和错误归一化。

核心组件:

| 组件 | 职责 |
|------|------|
| `LlmClient` | Agent 使用的门面对象，根据请求参数或默认配置选择 Provider，并统一处理重试、超时和结构化输出 |
| `LlmRequest` | 描述一次模型调用，包含 messages、provider、model、temperature、json_schema、timeout 等字段 |
| `LlmResponse` | 统一返回文本、结构化 JSON、Provider 名称、模型名、Token 用量和原始元数据摘要 |
| `LlmProvider` | Provider 抽象接口，屏蔽 OpenAI-compatible、Anthropic-compatible 等不同 HTTP/API 形状 |
| `FakeProvider` | 测试专用 Provider，用于 TDD 中注入固定响应和异常，不作为生产兜底实现 |

当前文件结构:

| 文件 | 实现说明 |
|------|----------|
| `src/llm/messages.py` | 定义 `LlmMessage`、`LlmRequest`、`LlmResponse`，作为 Agent 与 Provider 之间的稳定数据契约 |
| `src/llm/config.py` | 从环境变量构造 `LlmClientConfig`，并为单次请求派生 Provider/模型 override，避免并发调用污染全局配置 |
| `src/llm/client.py` | 实现 `LlmClient.complete` 与 `complete_json`，统一执行 Provider 选择、有限重试、Markdown JSON 提取和格式修复 |
| `src/llm/providers.py` | 实现 OpenAI-compatible、Anthropic-compatible 和 Fake Provider；真实 Provider 使用标准库 HTTP transport，测试可注入 fake transport |
| `src/llm/errors.py` | 定义统一异常类型和错误文本脱敏函数，避免 API Key 被写入日志或阶段产物 |
| `src/llm/__init__.py` | 导出 T022-T027 Agent 后续需要依赖的公共 API |
| `config/llm.test.example.json` | 测试联调用配置模板，只引用 API Key 环境变量名 |
| `config/llm.production.example.json` | 生产部署配置模板，建议由容器或密钥系统注入真实 API Key |
| `.env.local.example` | 本地真实 API Key 与真实网络测试开关模板；真实 `.env.local` 被忽略 |
| `scripts/llm_smoke_test.py` | 手动真实网络冒烟测试入口，输出模型返回的结构化 JSON |

运行时配置:

| 环境变量 | 作用 |
|----------|------|
| `DEVFLOW_LLM_PROVIDER` | 默认 Provider，默认值为 `openai_compatible` |
| `DEVFLOW_LLM_MODEL` | 全局默认模型；单次 `LlmRequest.model` 可覆盖 |
| `DEVFLOW_LLM_TEMPERATURE` | 默认采样温度 |
| `DEVFLOW_LLM_TIMEOUT_SECONDS` | 单次请求默认超时 |
| `DEVFLOW_LLM_MAX_RETRIES` | 客户端内部短周期重试次数 |
| `DEVFLOW_LLM_JSON_REPAIR_ATTEMPTS` | JSON 格式修复尝试次数 |
| `DEVFLOW_LLM_CONFIG_FILE` | 可选 JSON 配置文件路径；环境变量会覆盖文件中的同名默认值 |
| `DEVFLOW_LLM_ENV_FILE` | 可选 dotenv 密钥文件路径；未设置时默认读取 `execution-plane/.env.local` |
| `DEVFLOW_LLM_OPENAI_API_KEY` / `DEVFLOW_LLM_OPENAI_BASE_URL` / `DEVFLOW_LLM_OPENAI_DEFAULT_MODEL` | OpenAI-compatible Provider 配置 |
| `DEVFLOW_LLM_ANTHROPIC_API_KEY` / `DEVFLOW_LLM_ANTHROPIC_BASE_URL` / `DEVFLOW_LLM_ANTHROPIC_DEFAULT_MODEL` | Anthropic-compatible Provider 配置 |

真实网络测试默认关闭。只有设置 `DEVFLOW_LLM_INTEGRATION_TEST=1` 并提供对应 Provider 的 API Key 与默认模型时，`tests.test_llm_client.LlmClientRealNetworkTest` 才会访问外部模型服务。

设计边界:

- Temporal 继续负责长周期调度、持久化重试、人工审批和回溯。
- LangGraph 继续负责单次 Activity 内的节点执行和状态增量合并。
- LLM Client 只负责模型 I/O，不承担阶段状态转移。
- T022-T027 Agent 只能通过 `LlmClient` 调用模型，不直接依赖具体 Provider SDK。
- 不再设计或支持 `RuleBasedRequirementAnalyzer` 作为需求分析实现方式。

## 运行时调用链路

```text
Java DevFlowWorkflowImpl.executeStage
  -> 调用 DevFlowActivities 中的 Activity Type
  -> Temporal Server 派发 Activity Task
  -> Python Worker 监听 DEVFLOW_TASK_QUEUE
  -> activities.py 中对应 @activity.defn 函数接收请求
  -> _state_from_request 合并 globalContext 和 previousOutput
  -> run_stage 执行 LangGraph 节点函数
  -> _output_payload_for_stage 组装 StageExecutionResult
  -> Java Workflow 获取 outputPayload 并传给下一个阶段
```

## 状态模型

### `DevFlowState`

位置:

`execution-plane/src/graph/state.py`

`DevFlowState` 是 LangGraph 在节点之间传递的共享状态，当前使用 `TypedDict(total=False)`，代表字段可以逐步补齐。

| 字段 | 类型 | 含义 | 主要写入阶段 |
|------|------|------|--------------|
| `original_requirement` | `str` | 原始需求文本 | Activity 请求初始化 |
| `repository_context` | `dict[str, Any]` | 控制平面 `globalContext.repository` 传入的代码库路径配置 | Activity 请求初始化 |
| `code_context` | `dict[str, Any]` | 执行平面上下文工具生成的文件引用、搜索词和上下文包摘要 | 代码感知工具或后续 Agent |
| `structured_prd` | `dict[str, Any]` | 结构化需求分析结果 | `REQUIREMENT_ANALYSIS` |
| `design_doc` | `dict[str, Any]` | 系统设计文档和文件规划 | `SYSTEM_DESIGN` |
| `diff_patch` | `str` | 代码变更补丁或摘要 | `CODE_GENERATION` |
| `test_results` | `dict[str, Any]` | 测试生成与运行结果 | `TEST_GENERATION` |
| `review_report` | `dict[str, Any]` | 代码评审报告 | `CODE_REVIEW` |
| `delivery_status` | `dict[str, Any]` | 交付集成结果 | `DELIVERY_INTEGRATION` |
| `human_feedback` | `str` | 人工驳回反馈 | 控制平面 Signal 注入到 `globalContext` 后由 Activity 合并 |
| `current_step` | `str` | 当前图节点名称 | 每个节点 |
| `error_logs` | `list[str]` | 异常记录 | 各节点或未来错误处理中间件 |

设计理由:

- 用一个共享状态承载跨阶段上下文，方便 LangGraph 节点只返回增量更新。
- `total=False` 允许早期阶段只拥有部分字段，符合流水线逐步产出的特征。
- 字段名与数据库 `global_context`、Temporal `outputPayload` 保持一致，降低跨语言映射成本。

## LangGraph 实现

### 核心组件

位置:

`execution-plane/src/graph/flow.py`

| 组件 | 类型 | 含义 |
|------|------|------|
| `GraphNode` | `Callable[[DevFlowState], DevFlowState]` | 节点函数类型，输入状态并返回状态增量 |
| `REQUIREMENT_ANALYSIS` 等常量 | `str` | 阶段名，必须与 Java Workflow 中的 `stageName` 保持一致 |
| `STAGE_ORDER` | `list[str]` | 六阶段顺序，用于测试、文档和未来 UI 展示 |
| `STAGE_NODES` | `dict[str, GraphNode]` | 阶段名到节点函数的映射表 |
| `build_devflow_graph` | 函数 | 构建并编译完整 LangGraph 状态图 |
| `run_stage` | 函数 | 执行单个阶段节点，供 Temporal Activity 调用 |

### 阶段拓扑

```text
REQUIREMENT_ANALYSIS
  -> SYSTEM_DESIGN
  -> CODE_GENERATION
  -> TEST_GENERATION
  -> CODE_REVIEW
  -> DELIVERY_INTEGRATION
  -> END
```

当前拓扑是线性图，先保证端到端契约稳定。后续 T027 引入 Checkpointer 和反馈回溯后，可以把 `SYSTEM_DESIGN` 的人工反馈扩展为条件边或恢复点。

### `build_devflow_graph`

执行流程:

```text
1. 创建 StateGraph(DevFlowState)
2. 遍历 STAGE_NODES，调用 builder.add_node 注册节点
3. 设置入口节点 REQUIREMENT_ANALYSIS
4. 添加六阶段顺序边
5. 将 DELIVERY_INTEGRATION 指向 END
6. builder.compile 返回可执行图
```

使用方式:

```python
graph = build_devflow_graph()
result = graph.invoke({"original_requirement": "实现用户登录、注册和鉴权"})
```

适用场景:

- 本地端到端图验证。
- 未来在执行平面内部一次性运行整条流水线。
- 未来为 Checkpointer 接入图级状态快照。

### `run_stage`

执行流程:

```text
1. 根据 stage_name 从 STAGE_NODES 找节点函数
2. 找不到则抛出 ValueError
3. 调用节点函数得到 updates
4. 将原始 state 和 updates 合并
5. 返回合并后的新状态
```

使用方式:

```python
result_state = run_stage("SYSTEM_DESIGN", state)
```

适用场景:

- 当前 Temporal Activity 每次只执行一个阶段，因此 `activities.py` 调用的是 `run_stage`。
- 这种做法避免每个 Activity 都重复构造完整图，也让 Activity 和节点函数保持一一对应。

### 节点函数实现

| 节点函数 | 写入字段 | 当前实现 |
|----------|----------|----------|
| `analyze_requirement_node` | `structured_prd`, `code_context`, `current_step`, `error_logs` | 已调用 T022 Requirement Agent，通过 LLM Client 生成结构化 PRD，并记录上下文证据 |
| `design_system_node` | `design_doc`, `current_step`, `error_logs` | 读取 `structured_prd` 和 `human_feedback`，生成设计占位文档，等待 T023 Design Agent |
| `generate_code_node` | `diff_patch`, `current_step`, `error_logs` | 写入代码生成占位文本，等待 T024 Coder Agent |
| `generate_tests_node` | `test_results`, `current_step`, `error_logs` | 写入测试生成占位状态，等待 T025 Test Agent |
| `review_code_node` | `review_report`, `current_step`, `error_logs` | 写入代码评审占位状态，等待 T026 Review Agent |
| `integrate_delivery_node` | `delivery_status`, `current_step`, `error_logs` | 写入交付集成占位状态，等待 T027 Delivery Agent |

当前每个节点都返回增量字典，而不是直接修改输入对象。这样更符合 LangGraph 的状态更新模型，也便于后续替换为真实 Agent。

## Temporal Activity Worker

### `activities.py`

位置:

`execution-plane/src/workers/activities.py`

职责:

- 用 Python Temporal SDK 注册 Activity Type。
- 接收 Java `StageExecutionRequest` 形状的字典。
- 将请求转换为 `DevFlowState`。
- 调用 LangGraph 单阶段节点。
- 返回 Java `StageExecutionResult` 形状的字典。

### Activity 注册表

| Java Activity Type | Python 函数 | 调用阶段 |
|--------------------|-------------|----------|
| `analyzeRequirement` | `analyze_requirement` | `REQUIREMENT_ANALYSIS` |
| `designSystem` | `design_system` | `SYSTEM_DESIGN` |
| `generateCode` | `generate_code` | `CODE_GENERATION` |
| `generateTests` | `generate_tests` | `TEST_GENERATION` |
| `reviewCode` | `review_code` | `CODE_REVIEW` |
| `integrateDelivery` | `integrate_delivery` | `DELIVERY_INTEGRATION` |

`@activity.defn(name="...")` 中的 `name` 必须与 Java `DevFlowActivities` 的方法名一致，因为 Temporal 按 Activity Type 匹配跨语言调用。

### `_execute_stage`

执行流程:

```text
1. _state_from_request(request)
   - 从 requirement/globalContext/previousOutput 构造 DevFlowState

2. run_stage(stage_name, state)
   - 执行对应 LangGraph 节点

3. _output_payload_for_stage(stage_name, result_state)
   - 按阶段挑选对后续阶段有意义的字段

4. 返回 StageExecutionResult:
   - stageName
   - status = COMPLETED
   - outputPayload
```

### `_state_from_request`

输入来源:

| 来源字段 | 说明 |
|----------|------|
| `request.requirement` | 优先作为 `original_requirement` |
| `request.globalContext` | 控制平面传入的全局上下文，包含 `original_requirement`、`human_feedback` 等 |
| `request.previousOutput` | 上一个阶段的输出，由 Java Workflow 传递 |

合并规则:

```text
1. 初始化 original_requirement 和 error_logs
2. 将 globalContext 中已知状态字段合并进 DevFlowState
3. 将 previousOutput 中已知状态字段合并进 DevFlowState
4. 如果 globalContext.human_feedback 存在，强制写入 human_feedback
```

这样设计的原因:

- `globalContext` 表示跨阶段、跨重试长期存在的上下文。
- `previousOutput` 表示上一个阶段直接交给下一个阶段的产物。
- 人工反馈来自控制平面 Signal，需要优先保留，供设计阶段重跑使用。

### `_merge_known_state`

该函数只合并白名单字段，避免把 Temporal 请求里的无关字段直接写进 LangGraph 状态。

当前白名单:

```text
structured_prd
design_doc
diff_patch
test_results
review_report
delivery_status
human_feedback
repository_context
code_context
current_step
error_logs
```

### `_output_payload_for_stage`

每个阶段只输出后续阶段需要的字段:

| 阶段 | 输出字段 |
|------|----------|
| `REQUIREMENT_ANALYSIS` | `structured_prd`, `current_step` |
| `SYSTEM_DESIGN` | `structured_prd`, `design_doc`, `human_feedback`, `current_step` |
| `CODE_GENERATION` | `design_doc`, `diff_patch`, `current_step` |
| `TEST_GENERATION` | `diff_patch`, `test_results`, `current_step` |
| `CODE_REVIEW` | `test_results`, `review_report`, `current_step` |
| `DELIVERY_INTEGRATION` | `review_report`, `delivery_status`, `current_step` |

这个裁剪避免 `outputPayload` 随流水线推进无限膨胀。

### `registered_activities`

返回 Worker 要注册的 Activity 函数列表。测试会读取每个函数上的 Temporal 元数据，验证注册名与 Java 契约一致。

## Worker 入口

### `worker.py`

位置:

`execution-plane/src/workers/worker.py`

字段:

| 字段 | 含义 |
|------|------|
| `TASK_QUEUE` | `DEVFLOW_TASK_QUEUE`，需要与 Java `TemporalPipelineGatewayImpl.TASK_QUEUE` 一致 |
| `DEFAULT_TEMPORAL_TARGET` | `localhost:7233` |

方法:

| 方法 | 执行流程 |
|------|----------|
| `create_worker` | 接收 Temporal `Client`，创建监听指定 Task Queue 的 `Worker`，注册 `registered_activities()` |
| `run_worker` | 读取 `TEMPORAL_TARGET`，连接 Temporal，创建 Worker 并开始轮询 |

启动命令:

```powershell
.\venv\python.exe -m src.workers.worker
```

## 与控制平面的契约

### 输入: `StageExecutionRequest`

Python 侧以 `dict[str, Any]` 接收 Java record 序列化后的结构。

| 字段 | 来源 | 说明 |
|------|------|------|
| `pipelineId` | Java Workflow | 流水线 ID |
| `stageName` | Java Workflow | 当前阶段名 |
| `requirement` | Java Workflow | 原始需求 |
| `globalContext` | Java Workflow | 全局上下文 |
| `previousOutput` | Java Workflow | 前一个阶段输出 |

### 输出: `StageExecutionResult`

Python 返回:

```json
{
  "stageName": "SYSTEM_DESIGN",
  "status": "COMPLETED",
  "outputPayload": {
    "current_step": "SYSTEM_DESIGN",
    "design_doc": {}
  }
}
```

Java `DevFlowWorkflowImpl` 会把 `outputPayload` 作为下一阶段的 `previousOutput`。

## 测试覆盖

| 测试 | 覆盖内容 |
|------|----------|
| `tests/test_flow.py` | 完整图执行、六阶段状态写入、人工反馈进入设计文档 |
| `tests/test_worker.py` | Activity Type 注册名、Task Queue 名称、Activity 返回结构 |

运行:

```powershell
.\venv\python.exe -m unittest discover -s tests
```

## 当前限制和后续演进

当前实现是“契约优先”的执行平面骨架，已经能被 Temporal Worker 注册和调用，但各节点还是占位逻辑。

后续任务:

- T018: 控制平面接收并保存 `repository` 上下文。
- T019: 执行平面提供路径驱动的代码库上下文工具。
- T020: 控制平面提供阶段产物落库和展示通道。
- T021: 已实现可配置 LLM 调用客户端，支持至少两个 Provider、运行时切换和结构化 JSON 输出。
- T022: 已将 `analyze_requirement_node` 替换为需求分析 Agent。
- T023: 已将 `design_system_node` 替换为系统设计 Agent。
- T024: 将 `generate_code_node` 替换为代码生成 Agent。
- T025: 将 `generate_tests_node` 替换为测试生成 Agent。
- T026: 将 `review_code_node` 替换为代码评审 Agent。
- T027: 将 `integrate_delivery_node` 替换为交付集成 Agent。
- T028: 引入 Checkpointer，支持图状态持久化、回溯和人工反馈注入。

## T023 DesignAgent 子图实现

`execution-plane/src/agents/design_agent.py` 现在承载 `SYSTEM_DESIGN` 阶段的真实业务逻辑。它和 RequirementAgent 一样使用阶段内 LangGraph 子图，但不重新扫描代码库；它复用前序阶段写入的 `structured_prd`、`code_context` 和 `pipeline_context`，生成可供后续 CoderAgent 消费的 `design_doc`。

### 子图节点

| 节点 | 作用 |
|------|------|
| `prepare_input` | 读取 `structured_prd`、`code_context`、`pipeline_context` 和 `human_feedback`；缺少 PRD 时写入错误并降级 |
| `plan_design` | 构造设计章节计划，记录是否有代码上下文、验收标准数量和输出章节 |
| `draft_design` | 调用 `LlmClient.complete_json` 生成结构化设计文档 |
| `validate_design` | 校验 `summary`、`modules` 和 `file_plan` 等关键字段 |
| `repair_design` | 只补齐 JSON 结构缺口，降低 confidence，并要求人工复核 |
| `finalize` | 返回 `design_doc`，并把设计产物写入 `pipeline_context.artifact_index.SYSTEM_DESIGN` |
| `fail_soft` | 输入不完整时返回诊断型 `design_doc`，避免后续阶段误认为设计已完成 |

### 调试日志

DesignAgent 在以下位置输出 warning，方便本地调试和真实 LLM 调用排查：

- 缺少 `structured_prd`：说明 RequirementAgent 产物没有传到设计阶段。
- 缺少 `code_context`：说明本次设计只能基于 PRD，不能引用代码证据。
- 存在 `human_feedback`：说明当前是人工驳回后的修订路径。
- 调用 LLM 前：记录 inspected file 数量和是否存在反馈。
- 结构校验失败：记录缺失字段列表。
- 结构修复：记录修复次数和校验问题。

### 输出产物

`design_doc` 包含：

- `summary`
- `modules`
- `api_contracts`
- `data_changes`
- `file_plan`
- `risks`
- `open_questions`
- `feedback`
- `code_context_summary`
- `quality`
- `source = design_agent`

`SYSTEM_DESIGN` Activity 输出会继续携带 `pipeline_context`，保证后续 `CODE_GENERATION` 阶段可以复用需求分析和设计阶段的共享上下文。

## 代码库上下文工具

T019 已在 `execution-plane/src/context/` 中实现路径驱动的代码库上下文工具，供后续 T022-T027 的真实 Agent 通过工具调用式渐进探索目标仓库。

### 模块结构

| 路径 | 说明 |
|------|------|
| `src/context/repository_context.py` | 定义 `RepositoryContext`、文件匹配、搜索命中、上下文包等数据结构，并实现目录遍历、文件读取、文本搜索和上下文打包 |
| `src/context/__init__.py` | 对外导出上下文工具，避免 Agent 直接依赖内部文件名 |
| `tests/test_context_tools.py` | 覆盖临时仓库能力测试、越界路径拒绝测试、真实项目仓库上下文工具测试 |

### 核心数据结构

| 字段/类型 | 含义 |
|----------|------|
| `RepositoryContext.root_path` | 用户传入的目标仓库根目录，所有工具调用都必须限制在该目录内 |
| `include_paths` | 允许遍历的目录或文件路径；为空时默认从仓库根目录开始遍历 |
| `exclude_paths` | 需要排除的目录或文件前缀，例如 `.git`、`node_modules`、`target`、`venv` |
| `target_files` | 用户或控制平面明确指定的关键文件，会优先进入上下文打包候选集 |
| `max_files` | 单次遍历或打包最多处理的文件数量 |
| `max_bytes` | 单次上下文包的最大字节预算，防止一次性塞入过多代码 |
| `ContextPack.inspected_files` | 本次上下文打包实际检查过的文件列表，可作为中间产物展示依据 |
| `ContextPack.search_queries` | 本次打包使用过的搜索词，可展示 Agent 的探索过程 |

### 工具执行流程

`list_files(context)`:

```text
1. 校验 root_path 必须存在且是目录
2. 将 include_paths 解析为 root_path 内部路径
3. 递归遍历目录，遇到 exclude_paths 前缀立即跳过
4. 合并 target_files 中明确指定的文件
5. 按相对路径稳定排序，并按 max_files 截断
```

`read_file(context, path, start_line, end_line)`:

```text
1. 将 path 解析到 root_path 内部，禁止通过 .. 逃逸到仓库外
2. 如果路径命中 exclude_paths，则拒绝读取
3. 使用 UTF-8 读取文本，无法识别的字符使用替代字符保留上下文连续性
4. 如果传入行号，则按 1-based 闭区间返回片段
```

`search_text(context, query)`:

```text
1. 基于 list_files 得到允许搜索的文件集合
2. 逐文件读取文本并按行做大小写不敏感匹配
3. 返回相对路径、行号和命中行，供 Agent 决定下一步读取哪些文件
```

`build_context_pack(context, paths, search_queries)`:

```text
1. 优先加入 target_files 和调用方显式 paths
2. 对 search_queries 调用 search_text，并把命中文件加入候选集
3. 按 max_files 和 max_bytes 控制预算
4. 返回 ContextPack，包含文件内容、截断标记、已检查文件和搜索词
```

### 与 LangGraph 状态的关系

`DevFlowState` 已增加:

- `repository_context`: 控制平面传入并持久化的仓库上下文配置。
- `code_context`: 执行平面上下文工具生成的上下文包或探索摘要。

Temporal 仍负责流水线阶段的持久调度和失败重试；上下文工具只负责在某个 Activity 执行期间受控读取代码库，为 LangGraph 节点和 Agent 提供输入材料。
## 渐进式代码库探索 Agent

T019 升级后，执行平面的 RequirementAgent 不再要求用户手写 `includePaths` / `excludePaths` 才能感知项目代码。常规入口只需要 `repository.rootPath`，Agent 会在有限预算内按“规划 -> 发现文件 -> 搜索文本 -> 读取片段 -> 评估充分性”的顺序调用上下文工具。Temporal 仍负责外层工作流和 Activity 重试，LangGraph 负责 RequirementAgent 内部这些细粒度节点的状态推进。

### 工具调用流程

1. `PLAN`: 从自然语言需求抽取搜索词，并确定本轮探索目标。
2. `LIST_FILES`: 调用 `list_repository(request)`，在 `rootPath` 边界内列出候选文件，同时应用默认排除规则和用户高级约束。
3. `SEARCH_TEXT`: 调用 `search_text(request, query, max_results)`，用需求关键词定位代码信号，结果只返回路径、行号和预览片段。
4. `READ_FILE`: 调用 `read_file_range(request, path, line_start, line_end, max_bytes)`，只读取少量高相关文件片段，不把整仓库塞进 Prompt。
5. `EVALUATE`: 汇总 evidence、预算消耗和开放问题，判断 `COMPLETE` 或 `DEGRADED`。

### 状态字段

- `RepositoryExplorationRequest`: 用户输入和高级约束，包含 `rootPath`、`includePaths`、`excludePaths`、`targetFiles`、`maxRounds`、`maxFiles`、`maxBytes`、`maxSearchResults`、`privacyMode`。
- `ExplorationSession`: 单次探索过程的运行态容器，保存请求、候选文件、已读文件、证据和预算。
- `ExplorationStep`: 面向审计的轨迹记录，字段包括 `stepIndex`、`roundIndex`、`actionType`、`reason`、`input`、`resultSummary`、`selectedFiles`。
- `EvidenceItem`: 最终可引用的代码证据，字段包括 `filePath`、`lineStart`、`lineEnd`、`excerpt`、`relevanceReason`、`supports`。
- `BudgetUsage`: 预算消耗摘要，包含 `roundsUsed`、`filesRead`、`bytesRead`、`searchesUsed`。
- `CodeContextSummary`: 阶段产物中的代码上下文摘要，包含 `status`、`rootPath`、`inspectedFiles`、`searchQueries`、`candidateFiles`、`evidence`、`skippedPaths`、`budgetUsage`、`confidence`、`openQuestions`、`explorationTrace`。

### 中间产物

RequirementAgent 的 Activity 输出同时保留蛇形命名和前端友好的驼峰命名：

- `code_context` / `codeContext`: 用于后续 Design/Coder/Test/Review Agent 复用的代码上下文摘要。
- `exploration_trace` / `explorationTrace`: 用于前端展示和排查“为什么读了这些文件”的过程轨迹。
- `structured_prd`: LLM 基于需求文本和 `CodeContextSummary` 生成的结构化 PRD。

这些字段被写入控制平面的 Stage `outputPayload`。前端通过阶段状态接口读取后，会把搜索词、已读文件、证据、预算、跳过路径和开放问题展示为中间产物面板。

### 安全边界

所有路径读取都先解析到 `rootPath` 内，任何目录逃逸都会被拒绝。默认排除规则覆盖 `.git`、依赖目录、构建产物、缓存、日志、`.env*`、`*secret*` 等敏感或低价值路径。`excludePaths` 优先级高于 `targetFiles`，用于用户主动限制范围、保护隐私或提升速度。`privacyMode=strict` 时 evidence 中只保留定位信息和摘要，不写入原始代码片段。
