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

当前尚未完成:

- T018-T020 的代码感知、上下文工具和阶段产物落库/展示基础能力。
- T021-T026 的真实 Agent 业务逻辑。
- T027 的 LangGraph Checkpointer 和人工反馈回溯。
- 与真实 LLM、代码仓库、测试运行器、MR 平台的集成。

## 包结构

| 路径 | 职责 |
|------|------|
| `src/graph/state.py` | 定义 `DevFlowState`，作为 LangGraph 共享状态 |
| `src/graph/flow.py` | 定义阶段常量、节点函数、状态图构建和单阶段执行入口 |
| `src/graph/__init__.py` | 导出状态类型、阶段顺序和图构建函数 |
| `src/workers/activities.py` | 注册 Python Temporal Activities，并把 Activity 请求转换为 LangGraph 状态执行 |
| `src/workers/worker.py` | 创建并启动 Temporal Activity Worker |
| `src/workers/__init__.py` | 导出 Worker 工厂和 Activity 注册函数 |
| `tests/test_flow.py` | 验证 LangGraph 拓扑和人工反馈注入 |
| `tests/test_worker.py` | 验证 Activity 注册名和返回契约 |

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
| `analyze_requirement_node` | `structured_prd`, `current_step`, `error_logs` | 把原始需求写入 PRD 摘要，占位等待 T021 |
| `design_system_node` | `design_doc`, `current_step`, `error_logs` | 读取 `structured_prd` 和 `human_feedback`，生成设计占位文档，等待 T022 |
| `generate_code_node` | `diff_patch`, `current_step`, `error_logs` | 写入代码生成占位文本，等待 T023 |
| `generate_tests_node` | `test_results`, `current_step`, `error_logs` | 写入测试生成占位状态，等待 T024 |
| `review_code_node` | `review_report`, `current_step`, `error_logs` | 写入代码评审占位状态，等待 T025 |
| `integrate_delivery_node` | `delivery_status`, `current_step`, `error_logs` | 写入交付集成占位状态，等待 T026 |

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
- T021: 将 `analyze_requirement_node` 替换为需求分析 Agent。
- T022: 将 `design_system_node` 替换为系统设计 Agent。
- T023: 将 `generate_code_node` 替换为代码生成 Agent。
- T024: 将 `generate_tests_node` 替换为测试生成 Agent。
- T025: 将 `review_code_node` 替换为代码评审 Agent。
- T026: 将 `integrate_delivery_node` 替换为交付集成 Agent。
- T027: 引入 Checkpointer，支持图状态持久化、回溯和人工反馈注入。
