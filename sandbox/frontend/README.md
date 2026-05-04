# DevFlow 前端控制台

`sandbox/frontend` 是 T029 的极简控制台应用，用于触发控制平面流水线、查询阶段状态、展示阶段中间产物，并提交 `APPROVE` / `REJECT` 人工反馈。

## 本地运行

```powershell
npm.cmd install
npm.cmd run dev -- --port 5173
```

默认通过 Vite proxy 将 `/api` 转发到 `http://localhost:8080`。如果控制平面不在默认地址，可以设置:

```powershell
$env:DEVFLOW_CONTROL_PLANE_URL="http://localhost:8081"
npm.cmd run dev -- --port 5173
```

## 验证

```powershell
npm.cmd test
npm.cmd run build
```

测试重点覆盖:

- API 客户端是否按控制平面契约调用 `/api/v1/pipelines` 和 checkpoint API。
- 表单值是否被规范化为 `CreatePipelineRequest`，包括可选的 `repository` 代码库上下文。
- 阶段产物选择逻辑是否优先展示当前阶段，方便审批时查看上下文。
