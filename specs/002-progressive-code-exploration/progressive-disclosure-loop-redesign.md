# 渐进式披露循环重新设计

## 背景与问题

当前 `progressively_collect_context` 只是一个早期 smoke 实现，不能作为正式的代码感知方案。它存在三个核心问题：

1. 渐进式披露被写在 `RequirementAgent` 内部，导致能力无法被 Design、Coder、Test、Review 等 Agent 共享。
2. 循环结构过粗，先列出候选文件，再按固定顺序读取一批文件片段。真实代码库可能有几千到几十万个文件，这会快速推高 token 成本，并且容易读到低价值文件。
3. `extract_search_queries` 是简陋规则，不具备语义理解能力。关键词、文件路径模式、模块假设、负向信号都应该由 LLM 在工具循环中生成和修正。

因此，后续实现应把它重构为执行平面的通用“代码感知与渐进式披露服务”，所有 Agent 都通过同一套工具协议获取上下文。

## 设计目标

- 通用性：Requirement、Design、Coder、Test、Review、Delivery Agent 都使用同一套渐进式披露接口。
- 低 token 成本：默认只向 LLM 暴露仓库结构摘要、候选路径元数据和少量证据片段，不暴露所有候选文件内容。
- LLM 主导探索：由 LLM 生成搜索计划、选择工具、评估相关性和决定是否继续探索。
- 可审计：每一步工具调用都记录输入、原因、输出摘要、预算消耗和决策依据。
- 可控：通过 `maxRounds`、`maxToolCalls`、`maxFilesRead`、`maxBytesRead`、`maxPromptTokens`、`maxEvidenceItems` 限制成本。
- 可退化：LLM 查询规划失败时，可以退化到安全的最小文件树与用户显式路径，但不再把规则检索伪装成主方案。

## 总体架构

新增执行平面通用模块：

```text
execution-plane/src/context/
  exploration_service.py      # 通用渐进式披露循环
  exploration_tools.py        # 工具实现：目录、搜索、读取、符号、摘要
  exploration_protocol.py     # LLM 工具协议和状态模型
  relevance.py                # 候选排序、证据评分、充分性判断
```

Agent 不直接调用 `list_repository`、`search_text`、`read_file_range` 拼循环，而是调用：

```python
context_pack = ProgressiveDisclosureService(llm_client).explore(
    request=ExplorationRequest(...),
    task=AgentTask(
        stage_name="REQUIREMENT_ANALYSIS",
        goal="分析用户需求并形成 PRD",
        user_requirement=...,
        known_artifacts={...},
    ),
)
```

不同 Agent 的差异体现在 `AgentTask`：

- Requirement Agent：关注需求涉及的现有模块、API、用户界面、约束和术语。
- Design Agent：关注架构边界、已有服务、DTO、数据库表、外部依赖。
- Coder Agent：关注将要修改的文件、调用链、测试入口、局部上下文。
- Test Agent：关注已有测试风格、fixture、mock、边界用例。
- Review Agent：关注 diff 周边、风险模块、契约、回归测试证据。

## 核心状态模型

### ExplorationRequest

继承现有 `RepositoryExplorationRequest`，但新增更细粒度预算：

| 字段 | 含义 |
| --- | --- |
| `rootPath` | 代码库根路径 |
| `includePaths` | 高级选项，仅用于收窄范围 |
| `excludePaths` | 高级选项，优先级最高 |
| `targetFiles` | 用户或上游 Agent 明确关注的文件 |
| `maxRounds` | LLM 反思轮次上限 |
| `maxToolCalls` | 工具调用总数上限 |
| `maxSearches` | 搜索调用上限 |
| `maxFilesRead` | 实际读取内容的文件数上限 |
| `maxBytesRead` | 实际读取内容的字节上限 |
| `maxPromptTokens` | 单次发给 LLM 的上下文摘要上限 |
| `maxEvidenceItems` | 最终证据条数上限 |
| `privacyMode` | `standard` 或 `strict` |

### ExplorationState

```text
DISCOVERING -> PLANNING -> ACTING -> OBSERVING -> REFLECTING
                                         ^             |
                                         |-------------|
REFLECTING -> COMPLETE | DEGRADED | FAILED
```

关键字段：

- `repositoryMap`: 文件树和文件元数据，不包含文件正文。
- `hypotheses`: LLM 对相关模块的假设，例如“前端创建流水线逻辑可能在 sandbox/frontend/src/main.ts”。
- `queryPlan`: LLM 生成的关键词、符号名、文件名模式、路径模式、负向关键词。
- `candidateQueue`: 待评估文件队列，只包含路径、语言、大小、命中原因、分数。
- `observations`: 工具返回的结构化观察结果。
- `evidenceLedger`: 已确认可用于下游 Agent 的证据。
- `contextLedger`: 已经暴露给 LLM 的摘要，防止重复塞入同一内容。
- `openQuestions`: 证据不足时需要用户或后续 Agent 处理的问题。

## 工具协议

LLM 每轮只允许从白名单工具中选择下一步动作，工具返回结构化 observation。工具不得一次性返回大批文件正文。

### `inspect_tree`

用途：获取仓库局部结构。

输入：

```json
{
  "path": ".",
  "depth": 2,
  "limit": 200
}
```

输出只包含目录、文件名、大小、语言推断、是否被排除，不包含正文。

### `find_files`

用途：按 glob、路径关键词、语言筛选文件。

```json
{
  "patterns": ["**/*Controller.java", "**/main.ts", "**/*Agent*.py"],
  "limit": 100
}
```

### `search_text`

用途：全文搜索关键词或符号。

```json
{
  "queries": ["createPipeline", "REQUIREMENT_ANALYSIS", "codeContext"],
  "paths": ["control-plane", "execution-plane", "sandbox/frontend/src"],
  "limitPerQuery": 20
}
```

返回路径、行号、短预览、命中类型，不返回完整文件。

### `read_ranges`

用途：读取少量文件片段。只能读取候选队列中排名靠前或 LLM 明确解释理由的文件。

```json
{
  "ranges": [
    {"path": "sandbox/frontend/src/main.ts", "start": 180, "end": 260}
  ],
  "reason": "确认前端如何构造 CreatePipelineRequest"
}
```

### `summarize_file`

用途：当文件较长但可能重要时，先生成结构化摘要，而不是直接塞入全部正文。摘要应缓存。

输出：

```json
{
  "path": "...",
  "symbols": ["PipelineApiClient", "buildCreatePipelineRequest"],
  "responsibilities": ["创建流水线请求", "渲染阶段产物"],
  "importantRanges": [{"start": 184, "end": 240, "reason": "..."}]
}
```

### `evaluate_relevance`

用途：让 LLM 对一批候选文件元数据和短预览做相关性评估，输出排序和下一步建议。

输入只允许包含：

- 路径
- 文件名
- 语言
- 命中行短预览
- 已知符号名
- 上一轮假设

不得包含大段源码。

## LLM 查询规划

废弃 `extract_search_queries` 作为主流程。新的查询计划由 LLM 生成：

```json
{
  "concepts": ["流水线创建", "阶段产物展示", "需求分析 Agent", "代码上下文"],
  "keywords": ["createPipeline", "StageStatusResponse", "codeContext", "RequirementAgent"],
  "symbols": ["buildCreatePipelineRequest", "analyzeRequirement", "ProgressiveDisclosureService"],
  "pathGlobs": ["sandbox/frontend/src/**/*.ts", "execution-plane/src/**/*.py", "control-plane/**/*.java"],
  "negativeKeywords": ["node_modules", "target", "dist", "venv"],
  "hypotheses": [
    {
      "statement": "前端创建流水线请求可能由 viewModel 构造",
      "expectedEvidence": ["CreatePipelineRequest", "repository.rootPath"]
    }
  ]
}
```

LLM 规划 Prompt 必须包含：

- 当前 Agent 阶段和目标。
- 用户需求。
- 已有产物摘要。
- 仓库顶层结构摘要。
- 可用工具列表。
- 明确约束：不要请求读取所有候选文件；每轮最多读取少量高价值片段。

## 候选文件排序

候选分数由多路信号组成，而不是单纯关键词命中：

| 信号 | 说明 |
| --- | --- |
| `pathPrior` | 路径与当前 Agent 任务的匹配度，例如前端任务优先 `sandbox/frontend/src` |
| `symbolMatch` | 符号、类名、函数名命中 |
| `textMatch` | 搜索词命中 |
| `artifactLink` | 与上游产物引用的文件、模块、API 是否相关 |
| `recency` | 可选，结合 git diff 或最近修改文件 |
| `llmRelevance` | LLM 对候选元数据和短预览的相关性评分 |
| `diversity` | 防止只读取同一目录下大量相似文件 |
| `costPenalty` | 大文件、生成文件、低信噪比文件扣分 |

最终读取策略：

1. 每轮只读取 Top K 文件的必要范围，默认 K=3。
2. 同一轮读取的总字节数受 `roundByteBudget` 限制。
3. 读完后进入反思，不允许继续机械读取候选队列。

## 渐进式披露循环

推荐循环如下：

```text
1. Discover
   读取仓库顶层结构、配置文件名、主要目录，不读取源码正文。

2. Plan
   LLM 生成 queryPlan、hypotheses、expectedEvidence。

3. Act
   LLM 选择一个或多个工具调用：find_files / search_text / read_ranges / summarize_file。

4. Observe
   工具返回结构化 observation。系统更新 candidateQueue、evidenceLedger、budgetUsage。

5. Reflect
   LLM 判断：
   - 当前证据是否足以支持 Agent 任务？
   - 哪些假设已确认？
   - 哪些假设需要继续搜索？
   - 下一轮应搜索、读取还是停止？

6. Stop
   满足停止条件时输出 CodeContextSummary；预算不足但仍有不确定性时输出 DEGRADED 和 openQuestions。
```

停止条件：

- 已覆盖任务要求的关键概念。
- 至少有 N 条高置信证据，且来自不同关键模块。
- LLM 评估继续探索的边际收益低。
- 达到任一预算上限。
- 用户约束导致无法继续探索。

## Prompt 输入控制

每轮给 LLM 的上下文由 `ContextWindowBuilder` 构造，按优先级放入：

1. 当前 Agent 目标和用户需求。
2. 上一轮反思摘要。
3. 已确认 evidence 摘要。
4. 候选文件元数据 Top N。
5. 最近工具 observation 摘要。
6. 可用预算。

禁止放入：

- 全量候选文件列表。
- 全量文件内容。
- 重复文件片段。
- 被 exclude 的路径。
- 超过预算的大段日志、构建产物、依赖代码。

## 输出结构

`CodeContextSummary` 应保持下游兼容，但增强字段语义：

```json
{
  "status": "COMPLETE",
  "rootPath": "...",
  "taskGoal": "REQUIREMENT_ANALYSIS: 分析用户需求并形成 PRD",
  "hypotheses": [],
  "searchQueries": [],
  "candidateFiles": [],
  "inspectedFiles": [],
  "evidence": [],
  "skippedPaths": [],
  "budgetUsage": {},
  "confidence": 0.82,
  "openQuestions": [],
  "explorationTrace": []
}
```

`candidateFiles` 默认只保存 Top N 和被实际评估过的文件，不保存几千个候选路径。完整候选队列如需调试，应写入本地 trace 文件，而不是 Stage 产物。

## 与各 Agent 的集成方式

### Requirement Agent

目标：确认需求涉及哪些现有模块、用户入口、API、状态模型和约束。输出给 PRD Prompt 的只应是 evidence 摘要和少量关键片段。

### Design Agent

目标：在 PRD 基础上寻找架构边界、已有服务、DTO、数据表、接口契约。优先读取架构入口和契约文件，而不是重复读取 Requirement 已确认的文件。

### Coder Agent

目标：围绕设计文档中的文件计划，读取待修改文件及其邻近依赖。它需要更强的符号级工具，例如读取函数、类、调用者和测试文件。

### Test Agent

目标：寻找现有测试风格、fixture、mock 策略和断言模式。它的 `AgentTask` 应提高测试目录、契约测试、fixture 的优先级。

### Review Agent

目标：围绕 diff 和风险点做局部验证。它应默认从 changed files 开始，再扩展到调用链和契约边界。

## 失败与降级策略

- LLM 规划失败：输出 `DEGRADED`，只使用用户显式 `targetFiles` 和仓库顶层结构，不做规则伪装的深度探索。
- 搜索无结果：要求 LLM 改写查询计划，最多重试 1-2 轮。
- 候选过多：先做 LLM relevance 批评估和多样性采样，再读取片段。
- 预算耗尽：停止读取，输出已确认事实和 `openQuestions`。
- 路径越界或敏感路径：直接跳过并写入 `skippedPaths`。

## 测试策略

后续实现应先写失败测试：

1. 千文件 fixture：确认只读取 Top K 文件，不会 excerpt 所有候选文件。
2. LLM 查询规划：FakeProvider 返回 queryPlan，工具按该计划搜索，不调用 `extract_search_queries`。
3. LLM 相关性评估：候选文件很多时，FakeProvider 返回排序，系统只读取排序靠前且理由充分的文件。
4. 多 Agent 复用：RequirementAgent 和 DesignAgent 使用同一个 `ProgressiveDisclosureService`。
5. 预算停止：达到 `maxToolCalls` 或 `maxBytesRead` 后返回 `DEGRADED` 和 `openQuestions`。
6. 上下文窗口控制：单轮 Prompt 不包含完整候选文件列表和大段源码。
7. trace 审计：每个工具调用、LLM plan、LLM reflect 都写入 trace。

## 迁移计划

1. 保留现有 `RepositoryExplorationRequest`、`EvidenceItem`、`ExplorationStep`、`CodeContextSummary`，避免破坏前后端契约。
2. 新增 `ProgressiveDisclosureService` 和工具协议。
3. 将 `RequirementAgent.collect_context` 改为调用通用服务。
4. 删除或降级 `extract_search_queries`：只允许作为 LLM 不可用时的最小 fallback，且输出必须标记 `DEGRADED`。
5. 将 Design/Coder/Test/Review Agent 接入同一服务。
6. 更新 smoke test：真实仓库测试必须证明候选文件很多时不会读取所有候选，只读取 LLM 选择的少量证据文件。

## 结论

正确的渐进式披露不是“先列出文件再读一批片段”，而是一个受预算约束的 LLM 工具循环：先理解任务，生成探索假设，调用工具获取观察，再反思是否继续。工具负责安全和结构化访问，LLM 负责语义规划和相关性判断，所有 Agent 共享同一套上下文获取能力。
