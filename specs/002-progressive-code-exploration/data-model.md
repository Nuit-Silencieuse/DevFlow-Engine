# 数据模型: 工具调用式渐进探索 Agent

## RepositoryExplorationRequest

表示一次代码感知请求。

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| `rootPath` | string | 是 | 目标代码库根目录或用户提供的文件/目录路径。所有工具访问都必须限制在该根内。 |
| `includePaths` | string[] | 否 | 高级选项。为空时默认探索整个 `rootPath`。存在时只作为收窄范围。 |
| `excludePaths` | string[] | 否 | 高级选项。与默认排除规则合并。 |
| `targetFiles` | string[] | 否 | 高级选项。用户明确希望优先阅读的文件。 |
| `maxRounds` | int | 否 | 最大探索轮次。 |
| `maxFiles` | int | 否 | 最大读取文件数。 |
| `maxBytes` | int | 否 | 最大读取字节数。 |
| `maxSearchResults` | int | 否 | 单次搜索最大返回结果数。 |
| `privacyMode` | enum | 否 | `standard` 或 `strict`。严格模式下减少原文片段输出。 |

### 校验规则

- `rootPath` 必须存在。
- `includePaths`、`excludePaths`、`targetFiles` 解析后不得逃逸 `rootPath`。
- 预算字段必须为正整数；为空时使用系统默认值。
- include/exclude 冲突时 exclude 优先。

## ExplorationSession

表示一次 Agent 渐进探索的运行实例。

| 字段 | 类型 | 说明 |
|------|------|------|
| `sessionId` | string | 探索会话标识，可由 pipelineId + stageName 派生。 |
| `pipelineId` | string | 所属流水线。 |
| `stageName` | string | 通常为 `REQUIREMENT_ANALYSIS`。 |
| `requirement` | string | 用户需求文本。 |
| `status` | enum | `PLANNED`、`DISCOVERING`、`READING`、`EVALUATING`、`COMPLETE`、`DEGRADED`、`FAILED`。 |
| `budget` | ExplorationBudget | 本次预算配置。 |
| `budgetUsage` | BudgetUsage | 已消耗轮次、文件数、字节数、搜索次数。 |
| `confidence` | float | Agent 对上下文充分性的置信度，范围 0 到 1。 |
| `createdAt` | datetime | 创建时间。 |
| `completedAt` | datetime | 完成时间。 |

### 状态流转

```text
PLANNED
  -> DISCOVERING
  -> READING
  -> EVALUATING
  -> COMPLETE

EVALUATING -> DISCOVERING   # 证据不足且预算未耗尽
EVALUATING -> DEGRADED      # 证据不足但预算耗尽
任意状态 -> FAILED          # 工具错误不可恢复或 rootPath 非法
```

## ExplorationStep

记录一次工具调用或一次 Agent 判断。

| 字段 | 类型 | 说明 |
|------|------|------|
| `stepIndex` | int | 从 1 开始递增。 |
| `roundIndex` | int | 所属探索轮次。 |
| `actionType` | enum | `PLAN`、`LIST_FILES`、`SEARCH_TEXT`、`READ_FILE`、`SUMMARIZE`、`EVALUATE`。 |
| `reason` | string | Agent 为什么执行该动作。 |
| `input` | object | 工具输入，例如 query、path、line range。 |
| `resultSummary` | string | 结果摘要，避免输出大量原文。 |
| `selectedFiles` | string[] | 本步选中的文件。 |
| `skippedFiles` | SkippedFile[] | 本步跳过的文件及原因。 |
| `error` | string | 可恢复错误信息。 |

## EvidenceItem

表示需求分析引用的代码证据。

| 字段 | 类型 | 说明 |
|------|------|------|
| `filePath` | string | 相对仓库根目录的路径。 |
| `lineStart` | int | 起始行，可为空。 |
| `lineEnd` | int | 结束行，可为空。 |
| `symbolName` | string | 类、函数、配置项或 API 名称，可为空。 |
| `excerpt` | string | 短片段或严格模式下的摘要。 |
| `relevanceReason` | string | 为什么该证据与需求相关。 |
| `supports` | string[] | 支持的需求点、约束或风险。 |

## CodeContextSummary

RequirementAgent 注入 PRD 生成提示词的最终上下文摘要。

| 字段 | 类型 | 说明 |
|------|------|------|
| `rootPath` | string | 探索根路径。 |
| `searchQueries` | string[] | 实际使用的搜索词。 |
| `inspectedFiles` | string[] | 实际读取过的文件。 |
| `candidateFiles` | string[] | 搜索命中但未读取的候选文件。 |
| `evidence` | EvidenceItem[] | 可追溯代码证据。 |
| `skippedPaths` | SkippedFile[] | 因默认排除、预算、二进制或权限跳过的路径。 |
| `budgetUsage` | BudgetUsage | 预算消耗。 |
| `confidence` | float | 上下文充分性评分。 |
| `openQuestions` | string[] | Agent 仍无法从代码中确认的问题。 |

## SkippedFile

| 字段 | 类型 | 说明 |
|------|------|------|
| `path` | string | 相对路径。 |
| `reason` | enum | `EXCLUDED`、`BINARY`、`TOO_LARGE`、`OUT_OF_SCOPE`、`BUDGET_EXHAUSTED`、`READ_ERROR`。 |
| `detail` | string | 补充说明。 |

## ExplorationBudget / BudgetUsage

| 字段 | 类型 | 说明 |
|------|------|------|
| `maxRounds` / `roundsUsed` | int | 最大/已用探索轮次。 |
| `maxFiles` / `filesRead` | int | 最大/已读文件数。 |
| `maxBytes` / `bytesRead` | int | 最大/已读字节数。 |
| `maxSearches` / `searchesUsed` | int | 最大/已用搜索次数。 |
