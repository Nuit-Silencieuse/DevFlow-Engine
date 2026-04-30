# Temporal Workflow 契约设计

本文档描述 T012 定义的 Temporal Workflow 与 Activity 接口契约。当前阶段只定义接口边界，不实现具体编排逻辑；实现类将在 T013-T014 中补齐。

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

## Activity 接口

接口位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowActivities.java`

每个 Activity 都接收 `StageExecutionRequest`，返回 `StageExecutionResult`。

| 方法 | 阶段 |
|------|------|
| `analyzeRequirement` | 需求分析 |
| `designSystem` | 系统设计 |
| `generateCode` | 代码生成 |
| `generateTests` | 测试生成 |
| `reviewCode` | 代码评审 |
| `integrateDelivery` | 交付集成 |

## TDD 约束

- `DevFlowWorkflowContractTest` 通过反射检查 Workflow、Signal、Query 和 Activity 注解，防止后续实现破坏 Temporal 契约。
- T013-T014 增加 Workflow 实现前，应先补充 Workflow 行为测试，再实现编排和 Signal 处理。
