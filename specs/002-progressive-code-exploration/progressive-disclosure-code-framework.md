# 渐进式披露循环代码框架设计

## 目标

本文档定义“渐进式披露循环”的代码框架。它不是 `RequirementAgent` 的私有方法，而是执行平面所有 Agent 共享的上下文获取子图。

核心要求：

- 使用 LangGraph 条件路由维护循环。
- LLM 负责规划、选择工具、反思和停止判断。
- 工具层负责安全访问代码库、预算控制和结构化 observation。
- 不读取所有候选文件，不把全量候选列表或大段源码塞进 Prompt。
- 输出统一的 `CodeContextSummary`，供 Requirement、Design、Coder、Test、Review 等 Agent 使用。

## 模块位置

建议新增模块：

```text
execution-plane/src/context/
  progressive_disclosure/
    __init__.py
    state.py          # 状态模型
    graph.py          # LangGraph 子图
    nodes.py          # 节点方法
    tools.py          # 工具适配层
    prompts.py        # LLM plan/reflect prompt
    budget.py         # 预算与退出判断
    summary.py        # CodeContextSummary 构造
```

上层 Agent 只依赖一个入口：

```python
context_summary = ProgressiveDisclosureRunner(llm_client).run(
    task=agent_task,
    request=exploration_request,
)
```

## 核心输入

### `AgentTask`

描述当前 Agent 为什么需要代码上下文。

```python
@dataclass(frozen=True)
class AgentTask:
    stage_name: str
    goal: str
    user_requirement: str
    known_artifacts: dict[str, Any]
    preferred_evidence_types: tuple[str, ...]
```

字段含义：

| 字段 | 作用 |
| --- | --- |
| `stage_name` | 当前阶段，例如 `REQUIREMENT_ANALYSIS`、`SYSTEM_DESIGN` |
| `goal` | 本轮代码感知目标，例如“确认需求涉及的已有模块” |
| `user_requirement` | 用户原始需求 |
| `known_artifacts` | 上游阶段产物，例如 PRD、设计文档、diff |
| `preferred_evidence_types` | 当前 Agent 更偏好的证据类型，例如 API、UI、测试、数据模型 |

### `ExplorationRequest`

描述仓库边界和预算。

```python
@dataclass(frozen=True)
class ExplorationRequest:
    root_path: Path
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    target_files: tuple[str, ...] = ()
    max_rounds: int = 5
    max_tool_calls: int = 20
    max_searches: int = 8
    max_files_read: int = 8
    max_bytes_read: int = 160_000
    max_prompt_tokens: int = 24_000
    max_evidence_items: int = 12
    privacy_mode: Literal["standard", "strict"] = "standard"
```

关键原则：

- `root_path` 是硬边界，所有工具读取都必须落在该目录内。
- `exclude_paths` 优先级最高，命中后不能搜索、不能读取、不能进入 evidence。
- `include_paths` 只用于收窄范围，不是常规用户必须填写项。
- `target_files` 只提高优先级，不关闭自动探索。

## 核心状态

LangGraph 子图流转一个显式 state：

```python
class ProgressiveDisclosureState(TypedDict, total=False):
    task: AgentTask
    request: ExplorationRequest

    status: Literal[
        "DISCOVERING",
        "PLANNING",
        "ACTING",
        "OBSERVING",
        "REFLECTING",
        "COMPLETE",
        "DEGRADED",
        "FAILED",
    ]

    round_index: int
    repository_map: RepositoryMap
    query_plan: QueryPlan
    hypotheses: list[ExplorationHypothesis]
    candidate_queue: list[CandidateFile]
    next_actions: list[ToolAction]
    observations: list[ToolObservation]
    evidence_ledger: list[EvidenceItem]
    skipped_paths: list[SkippedFile]
    budget_usage: BudgetUsage
    open_questions: list[str]
    trace: list[ExplorationStep]
    errors: list[str]
    final_summary: CodeContextSummary
```

状态中允许进入 Prompt 的内容必须经过压缩。`candidate_queue` 可以在内存中很大，但每轮只向 LLM 暴露 Top N 元数据和最近 observation 摘要。

## LangGraph 结构

```text
initialize
  -> inspect_repository
  -> plan_exploration
  -> route_after_plan

route_after_plan:
  if failed              -> finalize_failed
  if no_actions          -> finalize_degraded
  else                   -> execute_tools

execute_tools
  -> observe_tools
  -> reflect_progress
  -> route_after_reflect

route_after_reflect:
  if complete            -> finalize_complete
  if budget_exhausted    -> finalize_degraded
  if needs_replan        -> plan_exploration
  if has_next_actions    -> execute_tools
  else                   -> plan_exploration
```

代码骨架：

```python
def build_progressive_disclosure_graph() -> CompiledGraph:
    graph = StateGraph(ProgressiveDisclosureState)

    graph.add_node("initialize", initialize)
    graph.add_node("inspect_repository", inspect_repository)
    graph.add_node("plan_exploration", plan_exploration)
    graph.add_node("execute_tools", execute_tools)
    graph.add_node("observe_tools", observe_tools)
    graph.add_node("reflect_progress", reflect_progress)
    graph.add_node("finalize_complete", finalize_complete)
    graph.add_node("finalize_degraded", finalize_degraded)
    graph.add_node("finalize_failed", finalize_failed)

    graph.set_entry_point("initialize")
    graph.add_edge("initialize", "inspect_repository")
    graph.add_edge("inspect_repository", "plan_exploration")
    graph.add_conditional_edges("plan_exploration", route_after_plan)
    graph.add_edge("execute_tools", "observe_tools")
    graph.add_edge("observe_tools", "reflect_progress")
    graph.add_conditional_edges("reflect_progress", route_after_reflect)

    return graph.compile()
```

## 节点方法

### `initialize(state)`

作用：初始化运行状态、预算、trace 和根路径校验。

输入：

- `task`
- `request`

输出更新：

- `status = DISCOVERING`
- `round_index = 0`
- `budget_usage = empty`
- `trace += INIT`

失败：

- `root_path` 不存在或不可读时，进入 `FAILED`。

### `inspect_repository(state)`

作用：构建一次 compact repository map，作为整个渐进式披露循环的初始地图。这里不是只读取仓库顶层结构，也不是递归展开所有文件，而是在预算内做分层结构探测，返回足够 LLM 规划第一轮探索的仓库摘要。

调用工具：

- `inspect_compact_repository_map(root_path, include_paths, exclude_paths, budget)`
- `find_files(patterns=request.target_files)`，仅验证目标文件存在性并提高候选优先级

输出更新：

- `repository_map`
- 初始 `candidate_queue`
- `skipped_paths`
- `trace += LIST_TREE`

注意：

- 这里可以返回文件路径、大小、语言、目录摘要、入口文件、高信号文件。
- 不得对候选文件做源码 excerpt。
- 不得把完整文件树塞入 Prompt。
- compact repository map 在一次 Pipeline 内可以共享给后续 Agent，暂不引入 DirectorySnapshot / FileSummary 三层缓存。

#### compact repository map 的粒度

只读取顶层目录过粗，例如只看到 `control-plane/`、`execution-plane/`、`sandbox/`，LLM 很难规划出 `PipelineController.java`、`requirement_agent.py`、`viewModel.ts` 等具体搜索方向。因此 `inspect_repository` 应生成“粗地图 + 高信号路径”的组合：

```json
{
  "topLevel": ["control-plane", "execution-plane", "sandbox", "specs"],
  "languageStats": {"Java": 42, "Python": 31, "TypeScript": 18},
  "entrypoints": [
    "control-plane/devflow-engine/src/main/java/com/devflow/engine/DevflowEngineApplication.java",
    "execution-plane/src/workers/worker.py",
    "sandbox/frontend/src/main.ts"
  ],
  "highSignalFiles": [
    "sandbox/frontend/src/viewModel.ts",
    "execution-plane/src/agents/requirement_agent.py",
    "control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java"
  ],
  "directorySummaries": [
    {
      "path": "execution-plane/src",
      "children": ["agents", "context", "graph", "llm", "workers"],
      "fileCount": 28,
      "languageHints": ["Python"]
    }
  ]
}
```

生成策略：

1. 读取顶层目录和主要模块的 1-2 层结构。
2. 识别入口文件：`README`、`pom.xml`、`package.json`、`docker-compose.yml`、`main.*`、`Application.java`。
3. 识别高信号文件：`*Controller*`、`*Service*`、`*Workflow*`、`*Agent*`、`types.ts`、`viewModel.ts`、测试入口文件。
4. 统计语言分布、文件数量、主要目录文件数量。
5. 应用默认排除和用户 `excludePaths`，被排除路径只记录摘要，不进入候选队列。

预算建议：

| 参数 | 建议默认值 | 说明 |
| --- | --- | --- |
| `maxMapFiles` | 800 | 最多收集多少个文件元数据 |
| `maxDirectoryDepth` | 3 | 自动展开目录深度 |
| `maxChildrenPerDirectory` | 80 | 单目录最多暴露多少个 child |
| `maxHighSignalFiles` | 120 | 高信号文件上限 |
| `maxMapPromptItems` | 150 | 进入 LLM Prompt 的 map item 上限 |

后续阶段可以复用这份 map，节省重复 IO 和重复 token。但这份 map 只是初始地图，不要求覆盖所有细节；如果某个 Agent 需要更细粒度信息，应通过 `inspect_tree`、`find_files`、`search_text` 在循环中按需获取，而不是把 compact map 做成全量索引。

### `plan_exploration(state)`

作用：让 LLM 基于任务目标、仓库结构摘要、已有 observation 生成下一步探索计划。

调用：

```python
llm_client.complete_json(
    LlmRequest(
        task="progressive_disclosure_plan",
        messages=build_plan_messages(state),
        json_schema=QUERY_PLAN_SCHEMA,
    )
)
```

LLM 输出：

```json
{
  "hypotheses": [],
  "queries": [],
  "pathGlobs": [],
  "symbols": [],
  "negativeSignals": [],
  "actions": [
    {
      "type": "search_text",
      "args": {"queries": ["createPipeline", "codeContext"], "limitPerQuery": 20},
      "reason": "定位前端创建流水线和中间产物展示逻辑"
    }
  ]
}
```

输出更新：

- `query_plan`
- `hypotheses`
- `next_actions`
- `status = ACTING`
- `trace += PLAN`

失败：

- LLM 返回非法 JSON：允许一次 repair；仍失败则进入 fallback plan。

### `execute_tools(state)`

作用：执行 LLM 选择的工具调用，同时做安全和预算拦截。

支持工具：

- `inspect_tree`
- `find_files`
- `search_text`
- `read_ranges`
- `summarize_file`
- `evaluate_relevance`

输出更新：

- 原始工具结果暂存到 `observations`
- `budget_usage.tool_calls += n`
- `trace += TOOL_CALL`

硬规则：

- 工具调用超预算时不执行，写入 `skipped_paths`。
- 读取路径越界时不执行。
- `read_ranges` 每轮默认最多读取 3 个文件片段。
- `search_text` 只能返回短预览和行号，不返回完整文件。

### `observe_tools(state)`

作用：把工具结果转为候选文件、证据或开放问题。

输入：

- 最新 `ToolObservation`

输出更新：

- `candidate_queue`
- `evidence_ledger`
- `skipped_paths`
- `budget_usage.files_read`
- `budget_usage.bytes_read`
- `trace += OBSERVE`

候选队列更新策略：

```text
new_score =
  pathPrior
  + symbolMatch
  + textMatch
  + artifactLink
  + llmRelevance
  + diversityBonus
  - costPenalty
```

#### score 的来源

`observe_tools` 的 score 不应只依赖 LLM，也不应只依赖本地规则。推荐使用“两阶段评分”：

1. 本地快速评分：处理大量候选，便宜、稳定、可扩展。
2. LLM relevance rerank：只对 Top N 候选元数据和短预览做语义重排。

本地评分先把搜索结果、文件发现结果、compact repository map 中的高信号文件合并为候选队列。候选项只包含元数据：

```json
{
  "path": "sandbox/frontend/src/main.ts",
  "language": "TypeScript",
  "sizeBytes": 18420,
  "signals": ["entrypoint", "textMatch:codeContext", "pathPrior:frontend"],
  "matchPreviews": [
    {"line": 184, "preview": "createForm.addEventListener(...)"}
  ]
}
```

本地分数示例：

```text
localScore =
  0.20 * pathPrior
  + 0.20 * symbolMatch
  + 0.18 * textMatch
  + 0.14 * artifactLink
  + 0.10 * filenameSignal
  + 0.10 * targetFileBoost
  + 0.08 * diversityBonus
  - 0.15 * costPenalty
  - 0.30 * generatedFilePenalty
```

各项含义：

| 信号 | 来源 | 作用 |
| --- | --- | --- |
| `pathPrior` | `AgentTask.stage_name`、`preferred_evidence_types`、目录名 | 当前 Agent 的路径偏好，例如 UI 任务优先 `sandbox/frontend/src` |
| `symbolMatch` | 搜索结果、文件名、轻量符号提取 | 类名、函数名、接口名是否命中，例如 `RequirementAgent`、`CreatePipelineRequest` |
| `textMatch` | `search_text` observation | 搜索词命中次数、命中行密度、命中位置 |
| `artifactLink` | `known_artifacts` | 上游 PRD、design、diff 是否显式引用该路径或符号 |
| `filenameSignal` | 文件名规则 | `Controller`、`Service`、`Workflow`、`Agent`、`types`、`viewModel` 等高信号名称 |
| `targetFileBoost` | 用户输入 | 用户显式 `targetFiles` 加权，但仍受 exclude 约束 |
| `diversityBonus` | 候选队列统计 | 防止 Top K 全部来自同一目录或同一文件类型 |
| `costPenalty` | 文件大小、行数、类型 | 大文件、日志、低信噪比文件扣分 |
| `generatedFilePenalty` | 路径和文件名规则 | 生成文件、构建产物、依赖文件强扣分或直接排除 |

#### LLM relevance rerank

当候选数量较多时，本地评分只取 Top 30-50 给 LLM 做语义重排。输入仍然只是元数据和短预览，不包含源码正文：

```json
[
  {
    "path": "sandbox/frontend/src/main.ts",
    "language": "TypeScript",
    "localScore": 0.82,
    "signals": ["pathPrior:ui", "textMatch:codeContext"],
    "matchPreviews": [
      {"line": 184, "preview": "createForm.addEventListener(...)"}
    ]
  }
]
```

LLM 输出：

```json
{
  "rankedCandidates": [
    {
      "path": "sandbox/frontend/src/main.ts",
      "relevance": 0.93,
      "reason": "contains UI submit flow for creating pipelines",
      "suggestedRanges": [{"start": 184, "end": 240}]
    }
  ]
}
```

最终分数：

```text
finalScore = 0.65 * localScore + 0.35 * llmRelevance
```

如果 LLM relevance 不可用，则只使用 `localScore`，并在 trace 中标记 `rerankSkipped=true`。此时每轮读取范围要更保守，例如只读 Top 1-2 个候选。

#### 准确度、性能和 token 成本

准确度考虑：

- 本地评分负责高召回和稳定排序，避免 LLM 在过多候选中迷失。
- LLM rerank 负责语义判断，弥补关键词无法理解任务意图的问题。
- `reflect_progress` 会在读取少量范围后再次判断证据是否足够，避免一次排序错误导致流程结束。

性能考虑：

- compact repository map 只构建一次，后续 Agent 可复用。
- 大量候选只在本地内存中排序，不进入 Prompt。
- LLM rerank 默认只处理 Top 30-50 个候选摘要。
- 每轮 `read_ranges` 默认只读取 Top 2-3 个文件范围。

token 成本考虑：

- `observe_tools` 不把完整候选队列放进 Prompt。
- `search_text` observation 只保留短 preview。
- `evaluate_relevance` 输入是候选元数据，不是源码。
- `reflect_progress` 输入是 evidence 摘要、最近 observation 摘要和 Top N 候选，而不是全部已读内容。

因此完整流程应是：

```text
compact repository map
-> LLM plan
-> cheap local search/find_files
-> observe_tools local scoring
-> optional LLM rerank small batch
-> read a few ranges
-> LLM reflect
-> continue or stop
```

不是：

```text
list all files
-> read many candidate files
-> send all excerpts to LLM
```

### `reflect_progress(state)`

作用：让 LLM 判断当前证据是否足够，以及下一步应该继续搜索、读取、重规划还是停止。

调用：

```python
llm_client.complete_json(
    LlmRequest(
        task="progressive_disclosure_reflect",
        messages=build_reflect_messages(state),
        json_schema=REFLECTION_SCHEMA,
    )
)
```

LLM 输出：

```json
{
  "decision": "continue|complete|degraded|replan",
  "confidence": 0.82,
  "confirmedHypotheses": [],
  "missingEvidence": [],
  "nextActions": [],
  "openQuestions": [],
  "reason": "已经确认前端、控制平面和执行平面的关键入口"
}
```

输出更新：

- `confidence`
- `open_questions`
- `next_actions`
- `status`
- `round_index += 1`
- `trace += REFLECT`

注意：

- 反思 Prompt 只能包含 evidence 摘要、Top N 候选元数据、最近 observation 摘要。
- 不得把全部已读源码重复放入 Prompt。

### `route_after_reflect(state)`

作用：LangGraph 条件路由。

路由规则：

```python
def route_after_reflect(state: ProgressiveDisclosureState) -> str:
    if state["status"] == "COMPLETE":
        return "finalize_complete"
    if has_fatal_error(state):
        return "finalize_failed"
    if is_budget_exhausted(state):
        return "finalize_degraded"
    if state["round_index"] >= state["request"].max_rounds:
        return "finalize_degraded"
    if state.get("next_actions"):
        return "execute_tools"
    return "plan_exploration"
```

### `finalize_complete(state)`

作用：构造成功的 `CodeContextSummary`。

输出：

- `status = COMPLETE`
- 高置信 evidence
- 已读文件
- 搜索词
- trace
- 预算摘要

### `finalize_degraded(state)`

作用：构造降级但可用的 `CodeContextSummary`。

触发原因：

- 预算耗尽。
- LLM 规划失败但已有部分观察。
- 搜索结果不足。
- 达到最大轮次。
- 用户约束过窄。

输出：

- `status = DEGRADED`
- 已确认 evidence
- `openQuestions`
- 降级原因写入 `notes`

### `finalize_failed(state)`

作用：构造失败产物。

触发原因：

- 根路径无效。
- 所有工具不可用。
- 安全边界错误不可恢复。

输出：

- `status = FAILED`
- `errors`
- 空 evidence

## 一次典型调用的时间顺序

### T0：上层 Agent 发起请求

RequirementAgent 收到用户需求：

```text
请分析 5173 控制台如何展示需求分析阶段中间产物。
```

调用：

```python
context = ProgressiveDisclosureRunner(llm_client).run(
    task=AgentTask(
        stage_name="REQUIREMENT_ANALYSIS",
        goal="确认需求涉及的前端、控制平面、执行平面代码入口",
        user_requirement=requirement_text,
        known_artifacts={},
        preferred_evidence_types=("ui", "api", "agent", "workflow"),
    ),
    request=ExplorationRequest(root_path=repo_root),
)
```

### T1：初始化和仓库概览

`initialize` 校验 rootPath。

`inspect_repository` 返回：

```json
{
  "topLevelDirs": ["control-plane", "execution-plane", "sandbox", "specs"],
  "targetFiles": [],
  "skippedPaths": [".git", "node_modules", "target"]
}
```

### T2：LLM 生成查询计划

`plan_exploration` 让 LLM 输出：

```json
{
  "queries": ["CreatePipelineRequest", "codeContext", "REQUIREMENT_ANALYSIS", "analyzeRequirement"],
  "pathGlobs": [
    "sandbox/frontend/src/**/*.ts",
    "execution-plane/src/**/*.py",
    "control-plane/devflow-engine/src/main/java/**/*.java"
  ],
  "actions": [
    {"type": "find_files", "args": {"patterns": ["sandbox/frontend/src/*.ts"]}},
    {"type": "search_text", "args": {"queries": ["codeContext", "REQUIREMENT_ANALYSIS"]}}
  ]
}
```

### T3：执行搜索工具

`execute_tools` 执行 `find_files` 和 `search_text`。

`observe_tools` 更新候选队列：

```text
sandbox/frontend/src/main.ts                  score 0.91
sandbox/frontend/src/viewModel.ts             score 0.88
execution-plane/src/agents/requirement_agent.py score 0.84
control-plane/.../DevFlowWorkflowImpl.java    score 0.79
```

此时仍然没有读取完整文件。

### T4：LLM 评估候选并选择读取范围

`reflect_progress` 判断需要读取少量片段，输出：

```json
{
  "decision": "continue",
  "nextActions": [
    {
      "type": "read_ranges",
      "args": {
        "ranges": [
          {"path": "sandbox/frontend/src/main.ts", "start": 184, "end": 240},
          {"path": "sandbox/frontend/src/viewModel.ts", "start": 36, "end": 70},
          {"path": "execution-plane/src/agents/requirement_agent.py", "start": 78, "end": 136}
        ]
      }
    }
  ]
}
```

### T5：读取少量证据

`execute_tools` 只读取 3 个范围。

`observe_tools` 生成 evidence：

```json
[
  {
    "filePath": "sandbox/frontend/src/main.ts",
    "lineStart": 184,
    "lineEnd": 240,
    "relevanceReason": "确认前端创建流水线请求和提交入口",
    "supports": ["pipeline creation", "repository context"]
  }
]
```

### T6：反思是否足够

`reflect_progress` 发现还缺控制平面 workflow 证据，继续一轮：

```json
{
  "decision": "continue",
  "missingEvidence": ["Temporal workflow stage execution"],
  "nextActions": [
    {
      "type": "read_ranges",
      "args": {
        "ranges": [
          {
            "path": "control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java",
            "start": 118,
            "end": 148
          }
        ]
      }
    }
  ]
}
```

### T7：满足停止条件

下一次 `reflect_progress` 输出：

```json
{
  "decision": "complete",
  "confidence": 0.86,
  "reason": "已覆盖前端请求构造、控制平面工作流提交、执行平面需求分析 Agent"
}
```

`route_after_reflect` 进入 `finalize_complete`。

### T8：输出产物

最终输出：

```json
{
  "status": "COMPLETE",
  "rootPath": "...",
  "searchQueries": ["CreatePipelineRequest", "codeContext", "REQUIREMENT_ANALYSIS"],
  "inspectedFiles": [
    "sandbox/frontend/src/main.ts",
    "sandbox/frontend/src/viewModel.ts",
    "execution-plane/src/agents/requirement_agent.py",
    "control-plane/devflow-engine/src/main/java/com/devflow/engine/workflow/DevFlowWorkflowImpl.java"
  ],
  "evidence": [],
  "budgetUsage": {
    "roundsUsed": 3,
    "toolCallsUsed": 7,
    "filesRead": 4,
    "bytesRead": 32000
  },
  "confidence": 0.86,
  "openQuestions": [],
  "explorationTrace": []
}
```

## 退出条件

循环退出只允许由 `route_after_reflect` 和预算守卫决定。

### 正常完成

进入 `finalize_complete` 的条件：

- LLM reflection 输出 `decision = complete`。
- 高置信 evidence 数量达到阈值。
- 关键假设已确认。
- 未触发预算耗尽。

### 降级完成

进入 `finalize_degraded` 的条件：

- `round_index >= max_rounds`
- `tool_calls >= max_tool_calls`
- `bytes_read >= max_bytes_read`
- `files_read >= max_files_read`
- 搜索多轮无结果
- LLM 规划失败但仍有部分 evidence
- 用户 include/exclude 导致范围过窄

### 失败

进入 `finalize_failed` 的条件：

- rootPath 不存在或越界。
- 工具层发生不可恢复错误。
- 安全策略拒绝继续执行。

## Fallback 策略

### LLM plan 失败

策略：

1. 使用 JSON repair 重试一次。
2. 仍失败时进入 minimal fallback。
3. minimal fallback 只读取：
   - 用户显式 `targetFiles`
   - 仓库顶层结构
   - 上游产物明确引用的文件
4. 输出 `DEGRADED`，并在 `openQuestions` 中说明“LLM 查询规划失败”。

不允许：

- 用 `extract_search_queries` 继续伪装成正常语义规划。
- 扫描并读取大量候选文件。

### LLM relevance 失败

策略：

1. 使用本地候选分数排序。
2. 每轮最多读取 Top 1-2 个文件范围。
3. 输出 `DEGRADED` 或低置信度，提示相关性评估不可用。

### 搜索无结果

策略：

1. 让 LLM 改写查询计划。
2. 增加路径 glob 或符号名搜索。
3. 最多重试 2 轮。
4. 仍无结果则停止，输出开放问题。

### 预算耗尽

策略：

1. 停止所有读取。
2. 保留已确认 evidence。
3. 输出 `DEGRADED`。
4. 在 `notes` 中记录耗尽的预算项。

## 上层 Agent 如何使用输出

RequirementAgent 示例：

```python
def collect_context(self, state: RequirementAgentState) -> RequirementAgentState:
    context_summary = self.context_runner.run(
        task=AgentTask(
            stage_name="REQUIREMENT_ANALYSIS",
            goal="收集支持 PRD 的代码证据",
            user_requirement=state["requirement_text"],
            known_artifacts={},
            preferred_evidence_types=("api", "ui", "workflow", "agent"),
        ),
        request=RepositoryExplorationRequest.from_mapping(
            state["devflow_state"].get("repository_context") or {}
        ),
    )
    return {"context_pack": context_summary}
```

DesignAgent 示例：

```python
context_summary = context_runner.run(
    task=AgentTask(
        stage_name="SYSTEM_DESIGN",
        goal="确认系统设计需要复用或修改的架构边界",
        user_requirement=state["original_requirement"],
        known_artifacts={"structured_prd": state["structured_prd"]},
        preferred_evidence_types=("architecture", "api", "data_model"),
    ),
    request=request,
)
```

## 与 Temporal 的边界

Temporal 维护外层工作流：

- Pipeline 是否运行。
- 当前 Stage。
- Activity 重试。
- 人工 checkpoint。

LangGraph 维护 Agent 内部循环：

- 何时搜索。
- 搜索什么。
- 读取哪些范围。
- 是否需要重规划。
- 是否证据充分。
- 何时降级停止。

两者不冲突。Temporal 不适合维护这种细粒度认知状态；LangGraph 更适合表达 Agent 的工具循环和条件路由。
