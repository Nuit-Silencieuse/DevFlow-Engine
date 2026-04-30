# API 契约设计

本文档描述 DevFlow Engine 当前阶段暴露的 HTTP API 契约。T010 和 T011 只交付接口骨架与请求/响应形状，真正的持久化、Temporal 调度、AST 定位、代码生成和文件写入会在后续任务中接入。

## 控制平面 API

控制平面由 Java Spring Boot 暴露，统一前缀为 `/api/v1`。

### 创建并启动流水线

- 方法: `POST`
- 路径: `/api/v1/pipelines`
- 当前阶段: 已提供控制器骨架，返回模拟的 `pipelineId` 和 `RUNNING` 状态；T015 会接入数据库和 Temporal Workflow。

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
  ]
}
```

响应 `201 Created`:

```json
{
  "pipelineId": "uuid-string",
  "status": "RUNNING"
}
```

### 查询流水线状态

- 方法: `GET`
- 路径: `/api/v1/pipelines/{id}`
- 当前阶段: 路由已存在，但业务查询尚未实现；T015 会返回真实状态和阶段输出。

当前响应 `501 Not Implemented`:

```json
{
  "code": "NOT_IMPLEMENTED",
  "message": "流水线状态查询将在 T015 接入持久化和调度逻辑后实现。"
}
```

目标响应 `200 OK`:

```json
{
  "pipelineId": "uuid-string",
  "status": "SUSPENDED",
  "currentStage": "SYSTEM_DESIGN",
  "stages": [
    {
      "name": "REQUIREMENT_ANALYSIS",
      "status": "COMPLETED",
      "output": {}
    },
    {
      "name": "SYSTEM_DESIGN",
      "status": "PENDING_APPROVAL",
      "output": {}
    }
  ]
}
```

### 提交人工检查点决策

- 方法: `POST`
- 路径: `/api/v1/pipelines/{id}/checkpoints/{stageName}`
- 当前阶段: 路由已存在，但尚未发送 Temporal Signal；T014 会接入批准/驳回后的 Workflow 继续执行或回退逻辑。

请求体:

```json
{
  "decision": "REJECT",
  "feedback": "补充数据库表结构说明。"
}
```

当前响应 `501 Not Implemented`:

```json
{
  "code": "NOT_IMPLEMENTED",
  "message": "Checkpoint signal handling will be implemented in T014."
}
```

目标响应 `200 OK`:

```json
{
  "status": "RUNNING",
  "message": "Signal received. Pipeline resuming or re-routing."
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

- Java 控制器骨架由 `PipelineControllerContractTest` 保护路由和响应形状。
- Node Daemon 骨架由 `src/api.test.ts` 保护接收请求和非法请求处理。
- 后续任务实现业务逻辑时必须先扩展这些契约测试，再改实现。
