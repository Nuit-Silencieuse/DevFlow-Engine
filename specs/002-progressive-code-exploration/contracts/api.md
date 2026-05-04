# API 契约: 渐进探索代码上下文

## 创建流水线请求

`POST /api/v1/pipelines`

### 请求体

```json
{
  "requirement": "为测试环境增加健康检查页面，展示 Temporal worker、控制平面和执行平面状态。",
  "repository": {
    "rootPath": "D:/ZPY/Agent学习/DevFlow-Engine",
    "includePaths": [],
    "excludePaths": [],
    "targetFiles": [],
    "maxRounds": 4,
    "maxFiles": 12,
    "maxBytes": 80000,
    "privacyMode": "standard"
  }
}
```

### 字段规则

- `repository` 可选；不提供时 RequirementAgent 只基于需求文本分析。
- `repository.rootPath` 是代码感知的唯一常规必填字段。
- `includePaths`、`excludePaths`、`targetFiles`、预算字段是高级选项；前端默认不强制用户填写。
- `excludePaths` 与系统默认排除规则合并，不能取消默认安全排除。
- 路径字段可以是 Windows 或 WSL/Unix 风格路径，但服务端或执行平面必须规范化后校验根目录边界。

## 阶段输出中的中间产物

`GET /api/v1/pipelines/{pipelineId}`

`REQUIREMENT_ANALYSIS` 阶段的 `outputPayload` 应包含渐进探索摘要。

```json
{
  "requirementAnalysis": {
    "summary": "需求分析摘要",
    "userStories": [],
    "acceptanceCriteria": [],
    "risks": []
  },
  "codeContext": {
    "status": "COMPLETE",
    "rootPath": "D:/ZPY/Agent学习/DevFlow-Engine",
    "searchQueries": ["health check", "Temporal worker", "PipelineController"],
    "inspectedFiles": [
      "control-plane/devflow-engine/src/main/java/com/devflow/engine/api/PipelineController.java",
      "execution-plane/src/workers/worker.py"
    ],
    "candidateFiles": [],
    "evidence": [
      {
        "filePath": "execution-plane/src/workers/worker.py",
        "lineStart": 1,
        "lineEnd": 40,
        "symbolName": "main",
        "excerpt": "启动 Temporal worker 的关键逻辑摘要",
        "relevanceReason": "用于判断健康检查是否能覆盖执行平面 worker 状态",
        "supports": ["执行平面状态展示"]
      }
    ],
    "skippedPaths": [],
    "budgetUsage": {
      "roundsUsed": 2,
      "filesRead": 5,
      "bytesRead": 24576,
      "searchesUsed": 4
    },
    "confidence": 0.82,
    "openQuestions": []
  },
  "explorationTrace": [
    {
      "stepIndex": 1,
      "roundIndex": 1,
      "actionType": "PLAN",
      "reason": "识别健康检查相关模块",
      "resultSummary": "优先搜索 worker、controller、frontend api"
    }
  ]
}
```

## 前端展示要求

前端应至少展示：

- 探索状态：`COMPLETE`、`DEGRADED` 或 `FAILED`。
- 已读取文件列表。
- 搜索词和候选文件数量。
- 代码证据卡片：文件、行号、摘要、相关原因。
- 预算消耗。
- 降级或失败时的 open questions 和跳过原因。
