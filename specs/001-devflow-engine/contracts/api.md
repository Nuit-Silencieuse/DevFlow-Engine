# API 契约设计

本文档描述 DevFlow Engine 当前阶段暴露的 HTTP API 契约。T015 已将控制平面的流水线创建和查询接入 JPA 持久化，并通过 Temporal Gateway 启动 Workflow；T014 已将人工检查点决策转发为 Temporal Signal。AST 定位、代码生成和文件写入仍由后续任务接入。

## 控制平面 API

控制平面由 Java Spring Boot 暴露，统一前缀为 `/api/v1`。

### 创建并启动流水线

- 方法: `POST`
- 路径: `/api/v1/pipelines`
- 当前阶段: 已实现业务逻辑。接口会创建 `Pipeline` 记录、初始化阶段列表、写入 `global_context`，并通过 `TemporalPipelineGateway` 启动对应 Workflow。T018 已支持 `repository` 代码库上下文，并会写入 `Pipeline.global_context.repository` 后透传给 Workflow。

请求体:

```json
{
  "name": "Add user authentication",
  "requirement": "实现用户登录、注册和鉴权",
  "stages": [
    "REQUIREMENT_ANALYSIS",
    "SYSTEM_DESIGN",
    "CODE_GENERATION",
    "TEST_GENERATION",
    "CODE_REVIEW",
    "DELIVERY_INTEGRATION"
  ],
  "repository": {
    "rootPath": "D:/projects/demo-app",
    "includePaths": ["src", "README.md"],
    "excludePaths": ["node_modules", "dist", ".git"],
    "targetFiles": ["src/App.tsx"],
    "maxFiles": 50,
    "maxBytes": 65536
  }
}
```

响应 `201 Created`:

```json
{
  "pipelineId": "uuid-string",
  "status": "RUNNING"
}
```

创建后的持久化约定:

| 字段 | 当前写入规则 |
|------|--------------|
| `Pipeline.status` | `RUNNING` |
| `Pipeline.current_stage` | 请求阶段列表的第一个阶段，默认 `REQUIREMENT_ANALYSIS` |
| `Pipeline.global_context.original_requirement` | 原始需求文本 |
| `Pipeline.global_context.requested_stages` | 本次请求的阶段顺序 |
| `Pipeline.global_context.repository` | 代码库上下文，包含 `rootPath/includePaths/excludePaths/targetFiles/maxFiles/maxBytes` |
| `Stage.requires_human_approval` | `SYSTEM_DESIGN` 为 `true`，其他默认 `false` |

`repository` 规则:

| 字段 | 规则 |
|------|------|
| `rootPath` | 提供 `repository` 时必填；控制平面仅保存和透传，不读取文件系统 |
| `includePaths` | 可选，空值会规范化为空列表 |
| `excludePaths` | 可选，空值会规范化为空列表 |
| `targetFiles` | 可选，空值会规范化为空列表 |
| `maxFiles` | 可选，未提供或小于等于 0 时默认为 `200` |
| `maxBytes` | 可选，未提供或小于等于 0 时默认为 `1048576` |

Temporal Workflow ID 约定:

`devflow-pipeline-{pipelineId}`

### 查询流水线状态

- 方法: `GET`
- 路径: `/api/v1/pipelines/{id}`
- 当前阶段: 已实现业务查询。接口从数据库读取流水线和阶段快照，按 `requested_stages` 顺序返回阶段状态。

响应 `200 OK`:

```json
{
  "pipelineId": "uuid-string",
  "status": "SUSPENDED",
  "currentStage": "SYSTEM_DESIGN",
  "repository": {
    "rootPath": "D:/projects/demo-app",
    "includePaths": ["src", "README.md"],
    "excludePaths": ["node_modules", "dist", ".git"],
    "targetFiles": ["src/App.tsx"],
    "maxFiles": 50,
    "maxBytes": 65536
  },
  "stages": [
    {
      "name": "REQUIREMENT_ANALYSIS",
      "status": "PENDING",
      "requiresHumanApproval": false,
      "output": {}
    },
    {
      "name": "SYSTEM_DESIGN",
      "status": "PENDING",
      "requiresHumanApproval": true,
      "output": {}
    }
  ]
}
```

未找到响应 `404 Not Found`:

```json
{
  "code": "NOT_FOUND",
  "message": "Pipeline not found: uuid-string"
}
```

### 提交人工检查点决策

- 方法: `POST`
- 路径: `/api/v1/pipelines/{id}/checkpoints/{stageName}`
- 当前阶段: 已实现 Signal 转发。接口验证流水线存在后，将 `APPROVE` 映射到 `approveCheckpoint`，将 `REJECT` 映射到 `rejectCheckpoint`。

请求体:

```json
{
  "decision": "REJECT",
  "feedback": "补充数据库表结构说明。"
}
```

响应 `200 OK`:

```json
{
  "status": "RUNNING",
  "message": "Signal received. Pipeline resuming or re-routing."
}
```

非法决策响应 `400 Bad Request`:

```json
{
  "code": "INVALID_REQUEST",
  "message": "Checkpoint decision must be APPROVE or REJECT."
}
```

## 本地 Daemon API

本地 Daemon 由 Node.js/Express 暴露，用于接收浏览器注入层发来的“修改本地文件”请求。T011 只完成接收端点骨架和请求校验。

### 接收修改请求

- 方法: `POST`
- 路径: `http://localhost:8080/api/sandbox/modify`

请求体:

```json
{
  "fileName": "/src/components/Header.tsx",
  "lineNumber": 42,
  "userInstruction": "把按钮改成红色，并修改文案为立即体验"
}
```

当前响应 `202 Accepted`:

```json
{
  "requestId": "uuid-string",
  "status": "ACCEPTED",
  "fileName": "/src/components/Header.tsx",
  "message": "修改请求已接收，AST 定位、代码生成和文件写入将在后续任务中接入。"
}
```

非法请求响应 `400 Bad Request`:

```json
{
  "code": "INVALID_REQUEST",
  "message": "请求体必须包含 fileName、lineNumber 和 userInstruction。"
}
```

## 测试约束

- Java API 由 `PipelineControllerContractTest` 保护路由和响应形状。
- 流水线创建、查询、Signal 转发由 `PipelineServiceTest` 保护业务契约。
- `repository` 上下文的 JSON 反序列化、`global_context` 写入和状态查询回显由 Java 测试覆盖。
- Node Daemon 骨架由 `src/api.test.ts` 保护接收请求和非法请求处理。
- 后续任务实现业务逻辑时必须先扩展这些契约测试，再改实现。
