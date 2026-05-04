# Quickstart: 渐进探索代码上下文

## 目标

验证用户不手写 include/exclude 时，RequirementAgent 能以本项目代码库为目标，自动发现、搜索、读取和归纳相关代码，并把中间产物输出到终端、日志文件和阶段输出。

## 前置条件

- Python 虚拟环境已安装项目依赖。
- LLM 配置可使用 fake provider 或真实 provider。
- 目标仓库路径存在，例如 `D:/ZPY/Agent学习/DevFlow-Engine`。

## 最小请求

用户常规请求只需要需求文本和仓库根目录：

```json
{
  "requirement": "为测试环境增加健康检查页面，展示 Temporal worker、控制平面和执行平面状态。",
  "repository": {
    "rootPath": "D:/ZPY/Agent学习/DevFlow-Engine"
  }
}
```

Agent 应自动完成：

1. 根据需求生成搜索计划。
2. 扫描仓库目录轮廓。
3. 搜索 controller、worker、frontend api、health 等相关信号。
4. 读取关键文件片段。
5. 输出 EvidenceItem 和 CodeContextSummary。
6. 将探索轨迹写入 LLM trace 或阶段输出。

## 高级约束示例

当用户希望限制范围、加快速度或保护隐私时，可以额外传入：

```json
{
  "repository": {
    "rootPath": "D:/ZPY/Agent学习/DevFlow-Engine",
    "includePaths": ["control-plane", "execution-plane", "sandbox/frontend/src"],
    "excludePaths": ["sandbox/frontend/node_modules"],
    "maxRounds": 3,
    "maxFiles": 8,
    "maxBytes": 60000,
    "privacyMode": "strict"
  }
}
```

## 预期测试

实现任务时应先补充测试，再写代码。

```powershell
.\venv\python.exe -m unittest tests.test_repository_context tests.test_requirement_agent
```

如涉及控制平面 API 合约：

```powershell
mvn "-Dmaven.repo.local=C:\Users\12252\.m2\repository" test
```

如涉及前端展示：

```powershell
cd sandbox\frontend
npm.cmd test
npm.cmd run build
```

## 验收标准

- 不传 `includePaths` 和 `excludePaths` 时，Agent 仍能产出代码上下文摘要。
- 输出包含搜索词、已读文件、证据、跳过路径、预算消耗和置信度。
- 预算耗尽时返回 `DEGRADED`，而不是静默失败。
- 所有读取路径都在 `rootPath` 内。
- 默认排除规则覆盖依赖、构建产物、缓存、日志和密钥类文件。
- 使用本项目代码库作为目标仓库完成一次真实上下文工具测试。
