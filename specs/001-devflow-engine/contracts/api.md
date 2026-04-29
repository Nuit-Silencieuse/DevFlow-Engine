# API 契约设计 (控制平面)

RESTful API 由 Java Spring Boot 暴露，供前端单页应用（SPA）进行流水线生命周期管理及审批操作。

## 1. 流水线管理

### 创建并启动流水线
- **URL**: `POST /api/v1/pipelines`
- **Body**: 
  ```json
  {
    "name": "Add user authentication",
    "requirement": "现在我需要开发一个项目，要求在 @赛题.md 中，请完整地整理这些内容。",
    "stages": ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION", "TEST_GENERATION", "CODE_REVIEW", "DELIVERY_INTEGRATION"]
  }
  ```
- **Response** (201 Created):
  ```json
  {
    "pipelineId": "uuid-string",
    "status": "RUNNING"
  }
  ```

### 查询流水线状态
- **URL**: `GET /api/v1/pipelines/{id}`
- **Response** (200 OK):
  ```json
  {
    "pipelineId": "uuid-string",
    "status": "SUSPENDED",
    "currentStage": "SYSTEM_DESIGN",
    "stages": [
      {
        "name": "REQUIREMENT_ANALYSIS",
        "status": "COMPLETED",
        "output": "{ ... }"
      },
      {
        "name": "SYSTEM_DESIGN",
        "status": "PENDING_APPROVAL",
        "output": "{ ... }"
      }
    ]
  }
  ```

## 2. 人工干预检查点

### 提交审批决策 (Approve/Reject)
- **URL**: `POST /api/v1/pipelines/{id}/checkpoints/{stageName}`
- **Body**: 
  ```json
  {
    "decision": "REJECT",
    "feedback": "架构设计中未包含数据库表结构的定义，请补充。"
  }
  ```
- **Response** (200 OK):
  ```json
  {
    "status": "RUNNING",
    "message": "Signal received. Pipeline resuming or re-routing."
  }
  ```

## 3. 沙箱交互 API (本地 Daemon)

由 Node.js 守护进程暴露，接收来自 Content Script 的修改请求。

### 请求修改本地文件
- **URL**: `POST http://localhost:8080/api/sandbox/modify`
- **Body**:
  ```json
  {
    "fileName": "/src/components/Header.tsx",
    "lineNumber": 42,
    "userInstruction": "把这个按钮变成红色，并修改文案为 '立即体验'"
  }
  ```
- **Response** (200 OK):
  ```json
  {
    "status": "SUCCESS",
    "patchedFile": "/src/components/Header.tsx"
  }
  ```
