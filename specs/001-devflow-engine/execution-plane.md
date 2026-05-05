# 执行平面补充说明：渐进式代码库探索

执行平面的主体文档位于 [execution-plane-architecture.md](./execution-plane-architecture.md)。本文件作为任务 T019-U049 的稳定入口，记录 RequirementAgent 渐进式代码库探索的工具流、状态字段和中间产物。

## 工具流

RequirementAgent 在 `REQUIREMENT_ANALYSIS` 阶段内部使用 LangGraph 管理细粒度状态：

1. `PLAN`: 根据需求文本抽取关键词并确定探索目标。
2. `LIST_FILES`: 在 `repository.rootPath` 内列出候选文件，应用默认排除和高级约束。
3. `SEARCH_TEXT`: 用关键词搜索相关代码信号。
4. `READ_FILE`: 只读取高相关文件的有限行范围。
5. `EVALUATE`: 生成证据、预算摘要、置信度和开放问题。

Temporal 只维护外层 Pipeline/Stage/Activity 的生命周期，不替代 Agent 内部的搜索、读取和评估决策。

## 状态与产物

- `RepositoryExplorationRequest`: 仓库路径和高级约束。
- `ExplorationStep`: 每次工具调用的审计轨迹。
- `EvidenceItem`: 可追溯到文件和行号的需求分析证据。
- `BudgetUsage`: 文件数、字节数、搜索次数等预算消耗。
- `CodeContextSummary`: 最终输出给 LLM、控制平面和前端的代码上下文摘要。

Activity 输出保留 `code_context` / `codeContext` 和 `exploration_trace` / `explorationTrace`，控制平面将其存入 Stage `outputPayload`，前端中间产物面板负责展示。
