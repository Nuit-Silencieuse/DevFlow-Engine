# 控制平面结构说明

本文档说明 Java 控制平面的代码结构、模块职责、核心对象字段含义，以及 `DevFlowWorkflowImpl`、`PipelineService` 等复杂模块的执行流程。

控制平面代码位于:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/`

## 总体职责

控制平面负责接收外部 API 请求、持久化流水线元数据、启动 Temporal Workflow、向 Workflow 发送人工审批 Signal，并对外提供流水线状态查询。

当前它不直接执行 AI Agent 逻辑。AI 阶段执行由 Python 执行平面注册的 Temporal Activity Worker 承担。

## 包结构

| 包 | 主要职责 |
|----|----------|
| `api` | REST Controller 和请求/响应 DTO |
| `config` | Spring Bean 配置，目前包含 Temporal Client 配置 |
| `model` | JPA 实体和状态枚举 |
| `repository` | Spring Data JPA Repository |
| `service` | 业务编排服务和 Temporal Gateway |
| `workflow` | Temporal Workflow、Activity 契约、Workflow 实现和传输对象 |

## 运行时调用链路

创建流水线:

```text
HTTP POST /api/v1/pipelines
  -> PipelineController.createPipeline
  -> PipelineService.createPipeline
  -> PipelineRepository.save
  -> TemporalPipelineGateway.startPipeline
  -> Temporal Server 创建 Workflow Execution
  -> DevFlowWorkflowImpl.start
  -> Python Activity Worker 执行各阶段 Activity
```

查询流水线:

```text
HTTP GET /api/v1/pipelines/{id}
  -> PipelineController.getPipeline
  -> PipelineService.getPipeline
  -> PipelineRepository.findById
  -> TemporalPipelineGateway.getStatus
  -> 同步 WorkflowStatusSnapshot 到 Pipeline/Stage 数据库快照
  -> PipelineStatusResponse
```

提交人工检查点:

```text
HTTP POST /api/v1/pipelines/{id}/checkpoints/{stageName}
  -> PipelineController.submitCheckpointDecision
  -> PipelineService.submitCheckpointDecision
  -> 同步当前阶段产物到 Stage.output_payload
  -> TemporalPipelineGateway.signalCheckpoint
  -> DevFlowWorkflowImpl.approveCheckpoint 或 rejectCheckpoint
```

## API 层

### `PipelineController`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/api/PipelineController.java`

职责:

- 暴露 `/api/v1/pipelines` 下的 REST API。
- 把 HTTP 请求转换为 service 调用。
- 把业务异常转换为标准错误响应。

字段:

| 字段 | 类型 | 含义 |
|------|------|------|
| `pipelineService` | `PipelineService` | 控制平面的业务服务入口 |

方法:

| 方法 | HTTP 契约 | 执行流程 |
|------|-----------|----------|
| `createPipeline` | `POST /api/v1/pipelines` | 接收 `CreatePipelineRequest`，调用 `PipelineService.createPipeline`，返回 `201 Created` |
| `getPipeline` | `GET /api/v1/pipelines/{id}` | 根据 UUID 查询流水线快照，返回 `200 OK` |
| `submitCheckpointDecision` | `POST /api/v1/pipelines/{id}/checkpoints/{stageName}` | 接收 `APPROVE/REJECT` 决策并转交 Service 发送 Signal |
| `handleNotFound` | 异常处理 | 把 `NoSuchElementException` 转为 `404 NOT_FOUND` |
| `handleBadRequest` | 异常处理 | 把 `IllegalArgumentException` 转为 `400 INVALID_REQUEST` |

### API DTO

| 类 | 字段 | 含义 |
|----|------|------|
| `CreatePipelineRequest` | `name` | 流水线名称 |
| `CreatePipelineRequest` | `requirement` | 原始需求文本 |
| `CreatePipelineRequest` | `stages` | 期望执行的阶段列表；为空时使用默认三阶段 |
| `CreatePipelineRequest` | `repository` | T018 新增代码库上下文；用于后续 Agent 路径驱动代码感知 |
| `CreatePipelineResponse` | `pipelineId` | 新建流水线 UUID |
| `CreatePipelineResponse` | `status` | 创建后的流水线状态，当前为 `RUNNING` |
| `PipelineStatusResponse` | `pipelineId` | 流水线 UUID |
| `PipelineStatusResponse` | `status` | 流水线状态 |
| `PipelineStatusResponse` | `currentStage` | 当前阶段名 |
| `PipelineStatusResponse` | `repository` | 创建时保存的代码库上下文，供 UI 展示和执行平面调试 |
| `PipelineStatusResponse` | `stages` | 阶段状态列表 |
| `StageStatusResponse` | `name` | 阶段名 |
| `StageStatusResponse` | `status` | 阶段状态 |
| `StageStatusResponse` | `requiresHumanApproval` | 是否需要人工审批 |
| `StageStatusResponse` | `output` | 阶段输出 JSON |
| `CheckpointDecisionRequest` | `decision` | `APPROVE` 或 `REJECT` |
| `CheckpointDecisionRequest` | `feedback` | 驳回或补充说明 |
| `CheckpointDecisionResponse` | `status` | Signal 接收后的流水线状态提示 |
| `CheckpointDecisionResponse` | `message` | 人类可读消息 |
| `CheckpointDecisionResponse` | `stageName` | 本次检查点对应的阶段名 |
| `CheckpointDecisionResponse` | `stageOutput` | 提交检查点前已同步的阶段产物，用于 UI 展示审批上下文 |
| `ErrorResponse` | `code` | 错误码 |
| `ErrorResponse` | `message` | 错误说明 |

### `RepositoryContext`

`RepositoryContext` 是 T018 新增的代码库上下文 DTO，控制平面只做校验、规范化和持久化，不直接读取目标代码库。

| 字段 | 含义 |
|------|------|
| `rootPath` | 目标仓库根目录；提供 `repository` 时必填 |
| `includePaths` | Agent 可探索的相对目录或文件 |
| `excludePaths` | Agent 必须跳过的目录或文件，如 `.git`、依赖目录、构建产物 |
| `targetFiles` | 用户明确指定的重点文件 |
| `maxFiles` | 单阶段最多读取或打包的文件数；默认 `200` |
| `maxBytes` | 单阶段最多打包的文本字节数；默认 `1048576` |
 
持久化位置:

`Pipeline.globalContext.repository`

## 数据模型层

### `Pipeline`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/model/Pipeline.java`

对应表:

`devflow_app.pipelines`

字段:

| 字段 | 数据库列 | 含义 |
|------|----------|------|
| `id` | `id` | 流水线 UUID 主键 |
| `name` | `name` | 流水线名称 |
| `status` | `status` | `PENDING/RUNNING/SUSPENDED/COMPLETED/FAILED` |
| `currentStage` | `current_stage` | 当前执行或等待的阶段 |
| `globalContext` | `global_context` | JSONB 上下文；当前保存 `original_requirement` 和 `requested_stages` |
| `createdAt` | `created_at` | 创建时间，由 `@PrePersist` 设置 |
| `updatedAt` | `updated_at` | 更新时间，由 `@PrePersist/@PreUpdate` 设置 |
| `stages` | 关联 `stages.pipeline_id` | 一个流水线包含多个阶段 |

关键方法:

| 方法 | 含义 |
|------|------|
| `prePersist` | 首次持久化前补齐 `id/createdAt/updatedAt` |
| `preUpdate` | 更新前刷新 `updatedAt` |
| `addStage` | 建立双向关联：把 `Stage` 加入列表并设置 `stage.pipeline` |
| `removeStage` | 移除双向关联 |

### `Stage`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/model/Stage.java`

对应表:

`devflow_app.stages`

字段:

| 字段 | 数据库列 | 含义 |
|------|----------|------|
| `id` | `id` | 阶段 UUID 主键 |
| `pipeline` | `pipeline_id` | 所属流水线 |
| `name` | `name` | 阶段名，如 `SYSTEM_DESIGN` |
| `agentRole` | `agent_role` | 执行该阶段的 Agent 角色标识 |
| `requiresHumanApproval` | `requires_human_approval` | 是否需要人工审批 |
| `status` | `status` | `PENDING/RUNNING/COMPLETED/FAILED/REJECTED` |
| `outputPayload` | `output_payload` | JSONB 阶段输出 |
| `createdAt` | `created_at` | 创建时间 |
| `updatedAt` | `updated_at` | 更新时间 |

数据库约束:

- `(pipeline_id, name)` 唯一，避免同一流水线下出现重复阶段名。

## Service 层

### `PipelineService`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/service/PipelineService.java`

职责:

- 校验和标准化 API 请求。
- 组装 `Pipeline` 与 `Stage` 实体。
- 持久化流水线元数据。
- 启动 Temporal Workflow。
- 查询流水线状态快照。
- 将人工审批请求转发为 Temporal Signal。

字段:

| 字段 | 类型 | 含义 |
|------|------|------|
| `DEFAULT_STAGES` | `List<String>` | 当请求未提供阶段时默认执行 `REQUIREMENT_ANALYSIS -> SYSTEM_DESIGN -> CODE_GENERATION` |
| `pipelineRepository` | `PipelineRepository` | 负责读写 `Pipeline` 聚合 |
| `temporalPipelineGateway` | `TemporalPipelineGateway` | 负责屏蔽 Temporal SDK 细节 |

#### `createPipeline`

输入:

`CreatePipelineRequest`

输出:

`CreatePipelineResponse`

执行流程:

```text
1. validateCreateRequest
   - 检查 request 不为空
   - 检查 name 和 requirement 不为空

2. normalizeStages
   - 如果请求没有 stages，使用 DEFAULT_STAGES
   - 如果请求里有空字符串，过滤并 trim

3. 创建 Pipeline
   - 设置随机 UUID
   - 设置 status = RUNNING
   - 设置 currentStage = 第一个阶段
   - 设置 globalContext:
     - original_requirement = request.requirement
     - requested_stages = 阶段顺序
     - repository = 规范化后的代码库上下文

4. 创建 Stage
   - 对每个阶段调用 createStage
   - SYSTEM_DESIGN 的 requiresHumanApproval = true
   - 其他阶段默认为 false

5. pipelineRepository.save
   - 持久化 Pipeline 和级联 Stage

6. temporalPipelineGateway.startPipeline
   - 构造 DevFlowWorkflowInput
   - 使用同一个 pipelineId 启动 Temporal Workflow

7. 返回 CreatePipelineResponse
   - pipelineId = saved.getId()
   - status = RUNNING
```

事务边界:

- 方法标注 `@Transactional`。
- 当前实现中数据库保存和 Temporal 启动在同一个 service 方法内顺序执行，但 Temporal 启动不是数据库事务的一部分。后续如果要增强一致性，应考虑 outbox 或启动失败补偿。

#### `getPipeline`

输入:

`UUID id`

输出:

`PipelineStatusResponse`

执行流程:

```text
1. pipelineRepository.findById
2. 找不到时抛出 NoSuchElementException，由 Controller 转成 404
3. readRequestedStageOrder
   - 从 globalContext.requested_stages 读取原始阶段顺序
   - 不存在时退回 DEFAULT_STAGES
4. 按阶段顺序排序 pipeline.stages
5. 每个 Stage 转为 StageStatusResponse
6. 返回 PipelineStatusResponse
```

#### T020 阶段产物同步

`getPipeline` 和 `submitCheckpointDecision` 都会先调用 `synchronizeWorkflowSnapshot`。该方法通过 `TemporalPipelineGateway.getStatus` 查询 Workflow 内部的 `WorkflowStatusSnapshot`，然后把快照同步到数据库聚合:

```text
WorkflowStatusSnapshot.status        -> Pipeline.status
WorkflowStatusSnapshot.currentStage  -> Pipeline.current_stage
StageExecutionResult.status          -> Stage.status
StageExecutionResult.outputPayload   -> Stage.output_payload
```

同步逻辑刻意放在 `PipelineService`，而不是放进 `DevFlowWorkflowImpl`。原因是 Temporal Workflow 代码需要保持确定性，普通数据库 IO 会受到网络、事务、重试时机影响，不适合直接写在 Workflow 线程里。Service 层读取 Workflow Query 后再落库，可以把 Temporal 的执行态转换成控制平面可展示、可查询、可复用的持久快照。

如果同一个阶段被多次执行，例如 `SYSTEM_DESIGN` 被驳回后重跑，Workflow 快照会包含多条同名 `StageExecutionResult`。数据库 `stages` 表按阶段名保留一行，因此同步时按快照顺序覆盖，最终展示该阶段最新一次结果。完整历史后续由 `CheckpointFeedback` 和审计日志扩展承载。

#### `submitCheckpointDecision`

输入:

- `pipelineId`
- `stageName`
- `CheckpointDecisionRequest`

输出:

`CheckpointDecisionResponse`

执行流程:

```text
1. pipelineRepository.existsById
   - 不存在时抛出 NoSuchElementException

2. parseDecision
   - 将字符串标准化为 APPROVE 或 REJECT
   - 非法值抛出 IllegalArgumentException

3. temporalPipelineGateway.signalCheckpoint
   - 转发给目标 Workflow
   - APPROVE -> approveCheckpoint
   - REJECT -> rejectCheckpoint

4. 返回 RUNNING + Signal received 消息
```

#### 关键私有方法

| 方法 | 作用 |
|------|------|
| `createStage` | 根据阶段名创建 `Stage`，并映射 Agent 角色 |
| `validateCreateRequest` | 校验创建请求必填字段 |
| `normalizeStages` | 清洗阶段列表并提供默认阶段 |
| `normalizeRepository` | 校验并规范化代码库上下文，补齐默认 `maxFiles/maxBytes` |
| `normalizePathList` | 清洗 include/exclude/target 路径列表 |
| `createGlobalContext` | 构造写入 `Pipeline.globalContext` 的初始 JSON |
| `repositoryToMap` | 将 `RepositoryContext` 转为 JSONB 可持久化的 Map |
| `readRepositoryContext` | 从 `Pipeline.globalContext.repository` 还原查询响应 DTO |
| `synchronizeWorkflowSnapshot` | 查询 Temporal Workflow 快照，并触发阶段产物落库 |
| `applyWorkflowSnapshot` | 将 Workflow 状态、当前阶段和阶段执行结果更新到 `Pipeline` 聚合 |
| `applyStageExecutionResult` | 将单个 `StageExecutionResult.outputPayload` 写入同名 `Stage.outputPayload` |
| `copyOutputPayload` | 复制 Activity 输出 Map，避免直接持有外部可变引用 |
| `readRequestedStageOrder` | 从 JSON 上下文读取阶段顺序 |
| `stageOrder` | 查询阶段排序权重 |
| `parsePipelineStatus` | 将 Workflow 状态字符串转换为 `PipelineStatus`，异常时保守回退为 `RUNNING` |
| `parseStageStatus` | 将 Activity 状态字符串转换为 `StageStatus`，异常时保守回退为 `RUNNING` |
| `parseDecision` | 把 API 字符串解析为 `CheckpointDecision` 枚举 |
| `agentRole` | 将阶段名映射到执行平面 Agent 角色 |

## Temporal Gateway

### `TemporalPipelineGateway`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/service/TemporalPipelineGateway.java`

这是控制平面对 Temporal 的抽象接口。Service 只依赖这个接口，便于单元测试中用 Mockito 替换真实 Temporal SDK。

方法:

| 方法 | 含义 |
|------|------|
| `startPipeline` | 根据 `DevFlowWorkflowInput` 启动一个 Workflow |
| `signalCheckpoint` | 向指定 Workflow 发送审批 Signal |
| `getStatus` | 查询指定 Workflow 的 `WorkflowStatusSnapshot`，供 Service 层同步数据库快照 |

### `TemporalPipelineGatewayImpl`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/service/TemporalPipelineGatewayImpl.java`

字段:

| 字段 | 含义 |
|------|------|
| `TASK_QUEUE` | 当前固定为 `DEVFLOW_TASK_QUEUE`，需要与 Worker 侧一致 |
| `workflowClient` | Temporal Java SDK 客户端 |

方法流程:

`startPipeline`:

```text
1. 根据 DevFlowWorkflow.class 创建 Workflow Stub
2. 设置 Task Queue = DEVFLOW_TASK_QUEUE
3. 设置 Workflow ID = devflow-pipeline-{pipelineId}
4. 使用 WorkflowClient.start 异步启动 DevFlowWorkflow.start(input)
```

`signalCheckpoint`:

```text
1. 用 Workflow ID 获取已有 Workflow Stub
2. 构造 CheckpointSignal
3. decision == APPROVE 时调用 workflow.approveCheckpoint
4. decision == REJECT 时调用 workflow.rejectCheckpoint
```

`getStatus`:

```text
1. 使用 devflow-pipeline-{pipelineId} 创建已有 Workflow Stub
2. 调用 workflow.getStatus 查询 Workflow 内部快照
3. 找不到 Workflow 时返回 Optional.empty
4. 其他情况下返回 Optional<WorkflowStatusSnapshot>
```

`workflowId`:

- 统一生成 `devflow-pipeline-{pipelineId}`。
- 这个约定让 API 层、Service 层和 Temporal 查询/Signal 能稳定定位同一个 Workflow。

## Workflow 层

### `DevFlowWorkflow`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflow.java`

Temporal Workflow 接口:

| 方法 | Temporal 注解 | 含义 |
|------|---------------|------|
| `start` | `@WorkflowMethod` | 启动一次流水线 |
| `approveCheckpoint` | `@SignalMethod` | 人工批准检查点 |
| `rejectCheckpoint` | `@SignalMethod` | 人工驳回检查点 |
| `getStatus` | `@QueryMethod` | 查询 Workflow 内部快照 |

### `DevFlowWorkflowImpl`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java`

职责:

- 在 Temporal Workflow 内编排阶段执行顺序。
- 调用 Python 执行平面注册的 Activity。
- 在 `SYSTEM_DESIGN` 后暂停等待人工审批。
- 处理驳回反馈并重新执行设计阶段。
- 维护 Workflow 内部查询快照。

字段:

| 字段 | 类型 | 含义 |
|------|------|------|
| `DEFAULT_STAGES` | `List<String>` | 默认三阶段：需求分析、系统设计、代码生成 |
| `activities` | `DevFlowActivities` | Temporal Activity Stub；默认构造函数通过 `Workflow.newActivityStub` 创建 |
| `stageResults` | `List<StageExecutionResult>` | Workflow 内存中的阶段执行结果列表 |
| `input` | `DevFlowWorkflowInput` | 本次 Workflow 的启动输入 |
| `status` | `WorkflowStatusSnapshot` | `getStatus` 返回的当前快照 |
| `checkpointSignal` | `CheckpointSignal` | 最近一次收到的审批 Signal；被 `Workflow.await` 等待和消费 |

#### 构造函数

默认构造函数:

```text
1. 创建 DevFlowActivities Activity Stub
2. 设置 Activity StartToCloseTimeout = 30 分钟
3. 交给 start 方法按阶段调用
```

测试构造函数:

```text
DevFlowWorkflowImpl(DevFlowActivities activities)
```

该构造函数允许测试注入 fake activities，避免真实连接 Temporal Activity Worker。

#### `start`

输入:

`DevFlowWorkflowInput`

输出:

`DevFlowWorkflowResult`

执行流程:

```text
1. 保存 input 到字段
2. 清空 stageResults

3. 构造 globalContext
   - 复制 input.globalContext
   - 如果没有 original_requirement，则补入 input.requirement

4. 决定阶段列表
   - input.stages 为空时使用 DEFAULT_STAGES
   - 否则使用调用方传入的 stages

5. 初始化 previousOutput = {}
6. updateStatus("RUNNING", 第一个阶段)

7. 遍历每个 stageName
   7.1 executeStage(stageName, globalContext, previousOutput)
   7.2 把 result.outputPayload 作为下一个阶段的 previousOutput

8. 如果当前阶段是 SYSTEM_DESIGN
   8.1 waitForDesignDecision，把状态置为 SUSPENDED 并等待 Signal
   8.2 如果收到 REJECT:
       - 追加一个 REJECTED 的 StageExecutionResult
       - 将 feedback 写入 globalContext.human_feedback
       - 将 rejected_stage 写入 globalContext.rejected_stage
       - 重新 executeStage("SYSTEM_DESIGN", ...)
       - 再次等待人工决策
   8.3 如果收到 APPROVE:
       - 继续后续阶段

9. 所有阶段完成后:
   - currentStage = 最后一个 stageResult.stageName
   - updateStatus("COMPLETED", currentStage)
   - 返回 DevFlowWorkflowResult
```

重要语义:

- `SYSTEM_DESIGN` 是当前唯一的人工检查点。
- `REJECT` 不会结束 Workflow，而是把反馈注入上下文并重跑设计阶段。
- `APPROVE` 表示继续执行后续阶段。

#### `approveCheckpoint`

执行流程:

```text
1. 接收 API 层经 Temporal Gateway 发来的 Signal
2. 创建新的 CheckpointSignal
3. 强制 decision = APPROVE
4. 写入 checkpointSignal 字段
5. 正在等待的 Workflow.await 条件被唤醒
```

#### `rejectCheckpoint`

执行流程:

```text
1. 接收 Signal
2. 创建新的 CheckpointSignal
3. 强制 decision = REJECT
4. 保留 feedbackReason
5. 写入 checkpointSignal 字段
```

#### `getStatus`

返回最近一次 `updateStatus` 写入的 `WorkflowStatusSnapshot`。

注意:

- 这是 Workflow 内部快照，不等同于数据库 `Pipeline.status`。
- 当前 API 查询仍读取数据库实体；后续如果要展示实时 Workflow 状态，需要在 Service 中调用 Workflow Query 或把 Workflow 事件同步回数据库。

#### `waitForCheckpointDecision`

执行流程:

```text
1. 清空 checkpointSignal
2. Workflow.await 等待:
   - checkpointSignal != null
   - checkpointSignal.stageName 等于当前等待的 stageName
3. 读取 signal
4. 清空 checkpointSignal，避免重复消费
5. 返回 signal
```

Temporal 约束:

- 这里使用 `Workflow.await`，符合 Temporal Workflow 的确定性执行模型。
- 不应在 Workflow 代码中使用普通线程 sleep、阻塞 IO 或随机数。

#### `executeStage`

执行流程:

```text
1. updateStatus("RUNNING", stageName)
2. 构造 StageExecutionRequest:
   - pipelineId
   - stageName
   - requirement
   - globalContext 的不可变拷贝
   - previousOutput 的不可变拷贝
3. 根据 stageName 分发到对应 Activity:
   - REQUIREMENT_ANALYSIS -> analyzeRequirement
   - SYSTEM_DESIGN -> designSystem
   - CODE_GENERATION -> generateCode
   - TEST_GENERATION -> generateTests
   - CODE_REVIEW -> reviewCode
   - DELIVERY_INTEGRATION -> integrateDelivery
4. 将 Activity 返回的 StageExecutionResult 加入 stageResults
5. updateStatus("RUNNING", stageName)
6. 返回 result
```

#### `waitForDesignDecision`

执行流程:

```text
1. updateStatus("SUSPENDED", stageName)
2. 调用 waitForCheckpointDecision(stageName)
```

#### `updateStatus`

执行流程:

```text
1. input 为空时直接返回
2. 创建新的 WorkflowStatusSnapshot:
   - pipelineId
   - statusName
   - currentStage
   - stageResults 的只读拷贝
3. 覆盖 status 字段
```

#### `safeFeedback`

把 `null` 反馈转换为空字符串，避免后续 `Map.of` 不接受 null。

## Workflow 与 Activity 数据契约

### `DevFlowWorkflowInput`

| 字段 | 含义 |
|------|------|
| `pipelineId` | 数据库流水线 UUID，也是 Workflow ID 的组成部分 |
| `name` | 流水线名称 |
| `requirement` | 原始需求文本 |
| `stages` | 本次执行阶段列表 |
| `globalContext` | 全局上下文 JSON |

### `StageExecutionRequest`

| 字段 | 含义 |
|------|------|
| `pipelineId` | 流水线 UUID |
| `stageName` | 当前要执行的阶段 |
| `requirement` | 原始需求 |
| `globalContext` | 跨阶段上下文，包含原始需求和人工反馈 |
| `previousOutput` | 上一个阶段的输出 |

### `StageExecutionResult`

| 字段 | 含义 |
|------|------|
| `stageName` | 完成的阶段 |
| `status` | 阶段执行结果，当前约定为 `COMPLETED` 或 `REJECTED` |
| `outputPayload` | 阶段输出 JSON，会传给后续阶段作为 `previousOutput` |

### `WorkflowStatusSnapshot`

| 字段 | 含义 |
|------|------|
| `pipelineId` | 流水线 UUID |
| `status` | Workflow 内部状态，如 `RUNNING/SUSPENDED/COMPLETED` |
| `currentStage` | 当前阶段 |
| `stages` | 已经产生的阶段结果列表 |

### `CheckpointSignal`

| 字段 | 含义 |
|------|------|
| `pipelineId` | 目标流水线 |
| `stageName` | 目标检查点阶段 |
| `decision` | `APPROVE` 或 `REJECT` |
| `feedbackReason` | 驳回原因或补充说明 |

## 配置层

### `TemporalClientConfig`

位置:

`control-plane/devflow-engine/src/main/java/com/devflow/engine/config/TemporalClientConfig.java`

Bean:

| Bean | 含义 |
|------|------|
| `WorkflowServiceStubs` | Temporal gRPC 连接，默认读取 `temporal.target` |
| `WorkflowClient` | Temporal Workflow Client，供 Gateway 使用 |

配置来源:

`control-plane/devflow-engine/src/main/resources/application.yml`

```yaml
temporal:
  target: localhost:7233
```

## 当前限制和后续演进

当前已完成:

- REST API 创建、查询、审批 Signal 转发。
- 数据库实体和 Flyway 表结构。
- Java Workflow 编排实现。
- Python Activity Worker 注册 Activity。

仍需后续补齐:

- 控制平面侧 Temporal Workflow Worker 的启动和注册。
- Workflow 状态与数据库 `Pipeline/Stage` 状态的同步。
- T018-T020 代码感知、上下文工具和阶段产物落库/展示基础能力。
- T021-T026 真实 Agent 节点逻辑。
- T027 LangGraph Checkpointer 与人工反馈回溯。
- 失败重试、幂等和 outbox 等一致性增强。
