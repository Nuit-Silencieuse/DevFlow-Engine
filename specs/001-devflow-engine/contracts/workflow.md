# Temporal Workflow 契约设计

本文档描述 Temporal Workflow 与 Activity 接口契约。T012 定义接口边界；T013-T014 已补齐 `DevFlowWorkflowImpl`，实现前三个核心阶段编排和人工审批 Signal 处理。

## Workflow 接口

接口位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflow.java`

### `start`

- 注解: `@WorkflowMethod`
- 输入: `DevFlowWorkflowInput`
- 输出: `DevFlowWorkflowResult`
- 作用: 启动一次端到端 DevFlow 流水线。

输入字段:

| 字段 | 含义 |
|------|------|
| `pipelineId` | 数据库中的流水线 ID |
| `name` | 流水线名称 |
| `requirement` | 原始需求文本 |
| `stages` | 需要执行的阶段列表 |
| `globalContext` | 全局上下文，如仓库信息、约束、需求分析结果 |

### `approveCheckpoint`

- 注解: `@SignalMethod`
- 输入: `CheckpointSignal`
- 作用: 人工批准某个检查点，使 Workflow 继续执行。

### `rejectCheckpoint`

- 注解: `@SignalMethod`
- 输入: `CheckpointSignal`
- 作用: 人工驳回某个检查点，并把反馈注入后续流程。

### `getStatus`

- 注解: `@QueryMethod`
- 输出: `WorkflowStatusSnapshot`
- 作用: 查询当前 Workflow 快照，供 API 层或调试工具读取。

## Workflow 实现

实现位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java`

当前 T013-T014 行为:

1. `start` 初始化状态为 `RUNNING`，默认执行阶段为 `REQUIREMENT_ANALYSIS -> SYSTEM_DESIGN -> CODE_GENERATION`。
2. `REQUIREMENT_ANALYSIS` 调用 `DevFlowActivities.analyzeRequirement`。
3. `SYSTEM_DESIGN` 调用 `DevFlowActivities.designSystem`，完成后将 Workflow 快照置为 `SUSPENDED` 并等待人工检查点 Signal。
4. 收到 `APPROVE` 后继续执行 `CODE_GENERATION`。
5. 收到 `REJECT` 后记录驳回结果，把 `human_feedback` 和 `rejected_stage` 注入 `globalContext`，重新执行 `SYSTEM_DESIGN`，然后再次等待审批。
6. `CODE_GENERATION` 调用 `DevFlowActivities.generateCode`。
7. 全部阶段完成后返回 `DevFlowWorkflowResult(status=COMPLETED)`，`getStatus` 返回同样的最终快照。

当前实现同时支持 `TEST_GENERATION`、`CODE_REVIEW`、`DELIVERY_INTEGRATION` 的 Activity 分发；这些阶段的执行平面实际能力将在 T016 之后逐步补齐。

Temporal 配置:

| 项 | 值 |
|----|----|
| Task Queue | `DEVFLOW_TASK_QUEUE` |
| Workflow ID | `devflow-pipeline-{pipelineId}` |
| Temporal Target | `application.yml` 中的 `temporal.target`，默认 `localhost:7233` |

## Activity 接口

接口位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowActivities.java`

Python 执行平面实现位置:

`execution-plane/src/workers/activities.py`

每个 Activity 都接收 `StageExecutionRequest`，返回 `StageExecutionResult`。

| 方法 | 阶段 |
|------|------|
| `analyzeRequirement` | 需求分析 |
| `designSystem` | 系统设计 |
| `generateCode` | 代码生成 |
| `generateTests` | 测试生成 |
| `reviewCode` | 代码评审 |
| `integrateDelivery` | 交付集成 |

T016 已在 Python Worker 中注册与 Java 方法名一致的 Activity Type:

| Java Activity Type | Python 函数 | 输出字段 |
|--------------------|-------------|----------|
| `analyzeRequirement` | `analyze_requirement` | `stageName`, `status`, `outputPayload.structured_prd` |
| `designSystem` | `design_system` | `stageName`, `status`, `outputPayload.design_doc` |
| `generateCode` | `generate_code` | `stageName`, `status`, `outputPayload.diff_patch` |
| `generateTests` | `generate_tests` | `stageName`, `status`, `outputPayload.test_results` |
| `reviewCode` | `review_code` | `stageName`, `status`, `outputPayload.review_report` |
| `integrateDelivery` | `integrate_delivery` | `stageName`, `status`, `outputPayload.delivery_status` |

T026/T027 后，`reviewCode` 和 `integrateDelivery` 已不再是占位输出。`reviewCode` 输出结构化 `review_report`，状态取值为 `APPROVED`、`NEEDS_CHANGES`、`BLOCKED`；`integrateDelivery` 输出结构化 `delivery_status`，状态取值为 `READY`、`BLOCKED`、`FAILED`。这两个 Activity 都通过执行平面 Agent 产生产物，不在 Activity 内提交 Git 或发布系统。

Worker 入口:

`execution-plane/src/workers/worker.py`

运行约定:

| 项 | 值 |
|----|----|
| Task Queue | `DEVFLOW_TASK_QUEUE` |
| Temporal Target | 环境变量 `TEMPORAL_TARGET`，默认 `localhost:7233` |

## LangGraph 拓扑

实现位置:

`execution-plane/src/graph/flow.py`

T017 已建立执行平面状态图拓扑:

```text
REQUIREMENT_ANALYSIS
  -> SYSTEM_DESIGN
  -> CODE_GENERATION
  -> TEST_GENERATION
  -> CODE_REVIEW
  -> DELIVERY_INTEGRATION
  -> END
```

当前节点实现是稳定的占位逻辑，用于打通 Temporal Activity 到 LangGraph 状态流转的契约；T018-T020 会先补齐代码感知和阶段产物展示基础能力，T021-T026 会逐步替换为真实 Agent 节点。

人工驳回反馈约定:

- 控制平面驳回时会把 `human_feedback` 放入 `globalContext`。
- Python `designSystem` Activity 会把该字段合并进 `DevFlowState`，并写入 `design_doc.feedback`。

## TDD 约束

- `DevFlowWorkflowContractTest` 通过反射检查 Workflow、Signal、Query 和 Activity 注解，防止后续实现破坏 Temporal 契约。
- `DevFlowWorkflowImplTest` 先定义行为红灯，再实现编排和 Signal 处理，覆盖批准继续执行、驳回后注入反馈并重跑设计阶段。
- `execution-plane/tests/test_worker.py` 验证 Python Worker 注册的 Activity Type 与 Java 契约一致，并验证返回 `StageExecutionResult` 形状。
- `execution-plane/tests/test_flow.py` 验证 LangGraph 六阶段拓扑和人工反馈注入。
