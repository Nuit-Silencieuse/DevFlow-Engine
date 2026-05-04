# T029 前端控制台设计与联调说明

## 目标

T029 在 `sandbox/frontend/` 初始化一个极简控制台应用，服务于端到端研发流水线的人工操作面:

- 触发控制平面 `POST /api/v1/pipelines` 创建流水线。
- 查询 `GET /api/v1/pipelines/{pipelineId}` 展示流水线状态、代码库上下文和阶段产物。
- 调用 `POST /api/v1/pipelines/{pipelineId}/checkpoints/{stageName}` 提交 `APPROVE` 或 `REJECT` 反馈。

该控制台不是营销页，而是面向演示和调试的工作台。首屏直接提供创建表单、状态列表、产物查看和人工检查点操作。

## 前端结构

```text
sandbox/frontend/
├── index.html
├── package.json
├── vite.config.ts
├── src/
│   ├── api.ts              # 控制平面 API 客户端
│   ├── api.test.ts         # API 调用契约测试
│   ├── main.ts             # DOM 控制台入口
│   ├── styles.css          # 控制台样式
│   ├── types.ts            # REST 契约类型
│   ├── viewModel.ts        # 表单规范化与阶段选择逻辑
│   └── viewModel.test.ts   # 视图模型测试
└── README.md
```

## API 对接方式

前端代码统一调用相对路径 `/api/v1`。开发环境由 Vite proxy 转发:

```ts
proxy: {
  "/api": {
    target: "http://localhost:8080",
    changeOrigin: true
  }
}
```

如需切换控制平面地址，可设置 `DEVFLOW_CONTROL_PLANE_URL`。这种方式避免在 T029 阶段修改后端 CORS，同时让浏览器页面和 API 请求保持同源。

## UI 状态流转

1. 用户填写流水线名称、需求、阶段列表和可选 repository 上下文。
2. 前端将表单值规范化为 `CreatePipelineRequest`，空 `rootPath` 时不传 `repository`。
3. 创建成功后，返回的 `pipelineId` 自动写入状态查询框。
4. 用户刷新状态时，前端展示 pipeline 状态、当前阶段、repository rootPath、阶段列表和产物标记。
5. 用户点击阶段后，右侧 JSON 产物区显示 `stages[].output`。
6. 用户提交 `APPROVE` 或 `REJECT` 时，前端把当前阶段名、决策和反馈文本提交给 checkpoint API，并展示后端返回的 `stageOutput`。

## 控制平面兼容性修复

真实联调时发现: 如果 Temporal 中 workflow execution 已创建，但当前没有启动承载 `DevFlowWorkflowImpl` 的 Java Workflow Worker，`Workflow Query` 可能长时间等待后失败，导致控制台状态查询超时。

因此 T029 同步补充了控制平面回退逻辑:

- `PipelineService.synchronizeWorkflowSnapshot` 尝试读取 Temporal 最新快照。
- 读取使用 3 秒短超时。
- 超时或失败时返回数据库中的 `Pipeline` / `Stage` 持久化快照。

该策略不会伪造阶段产物；它只是保证控制台至少能展示已创建流水线、当前阶段、repository 上下文和已有数据库快照。真实 Agent 产物仍需由 Workflow/Activity 执行后写入或同步。

## 测试策略

前端采用轻量 TDD:

- `api.test.ts` 使用注入的 `fetch` 验证 URL、JSON body、错误解析和 checkpoint 路径编码。
- `viewModel.test.ts` 验证表单规范化、默认阶段、repository 可选语义、阶段选择和 JSON 展示辅助逻辑。
- `npm.cmd run build` 验证 TypeScript 严格类型检查和 Vite 生产构建。

控制平面新增测试:

- `PipelineServiceTest.getPipelineFallsBackToPersistedSnapshotWhenTemporalQueryIsUnavailable`

该测试保证 Temporal 快照不可用时，状态查询仍返回数据库快照。

## 已验证联调链路

在 WSL Docker 中 Postgres/Temporal 已运行的前提下，实际启动:

- 控制平面: `mvn "-Dmaven.repo.local=C:\Users\12252\.m2\repository" spring-boot:run`
- 前端: `npm.cmd run dev -- --port 5173 --strictPort`

通过 `http://127.0.0.1:5173/api/v1` 完成:

- 创建流水线: 返回 `RUNNING` 和真实 `pipelineId`。
- 查询流水线: 返回 `RUNNING`、`REQUIREMENT_ANALYSIS`、3 个阶段和 repository 上下文。
- 提交 checkpoint: `SYSTEM_DESIGN` 返回 `RUNNING`。
