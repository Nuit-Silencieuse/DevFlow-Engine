# T022 Requirement Agent 设计文档

本文档定义 T022 的实现方案。目标是在 `execution-plane/src/agents/requirement_agent.py` 中实现负责 `REQUIREMENT_ANALYSIS` 阶段的 LangGraph Agent，并替换 `execution-plane/src/graph/flow.py` 中当前的占位逻辑。

T022 依赖 T021 的可配置 LLM 调用客户端，不再设计或支持规则模板型 `RuleBasedRequirementAnalyzer`。

## 实现状态

T022 已完成首版实现:

- `execution-plane/src/agents/requirement_agent.py` 实现 `RequirementAgent`、内部 LangGraph 子图、上下文收集、LLM PRD 生成、结构校验和有界修复。
- `execution-plane/src/graph/flow.py` 的 `analyze_requirement_node` 已替换为 `RequirementAgent().run(state)`。
- `execution-plane/src/workers/activities.py` 的 `REQUIREMENT_ANALYSIS` 输出已包含 `structured_prd` 和 `code_context`。
- `execution-plane/tests/test_requirement_agent.py` 覆盖 Fake LLM、空需求、真实项目仓库上下文、flow 集成和 Activity 输出。
- 已使用当前项目代码库作为 `repository_context` 材料运行真实 LLM 测试，生成结构化 PRD，并记录实际检查文件。
- 支持 LLM Trace 输出 Requirement Agent 中间产物，包括 `context_pack`、`analysis_plan`、`draft_prd` 和 `validation_report`。
- `RequirementAgentEffectTest` 提供真实效果测试，终端输出摘要和 LLM 消息预览，完整 trace 写入 `logs/requirement-agent-effect.jsonl`，最终 PRD 写入 `logs/requirement-agent-effect-result.json`。

## 设计目标

Requirement Agent 的职责是把用户输入的自然语言需求转换为结构化 PRD。该 PRD 会进入 Temporal `StageExecutionResult.outputPayload`，再由 T020 同步到 `Stage.output_payload`，供状态查询、检查点展示和后续 Agent 使用。

目标输出必须满足以下要求:

- 可 JSON 序列化，能写入 Temporal payload 和 PostgreSQL JSONB。
- 字段结构稳定，后续 Design Agent 可以直接消费。
- 验收标准尽量可测试，避免只生成“体验良好”“性能优秀”这类不可验证描述。
- 保留足够的推理和证据摘要，便于人工审批、答辩和后续回溯。
- 在缺少信息时显式写入 `open_questions`，不要假装需求已经完整。

## 与现有架构的关系

外层流水线已经由 `execution-plane/src/graph/flow.py` 的主 LangGraph 管理:

```text
REQUIREMENT_ANALYSIS
  -> SYSTEM_DESIGN
  -> CODE_GENERATION
  -> TEST_GENERATION
  -> CODE_REVIEW
  -> DELIVERY_INTEGRATION
```

T022 不改变外层阶段顺序。它只把 `analyze_requirement_node(state)` 的内部实现替换为:

```python
def analyze_requirement_node(state: DevFlowState) -> DevFlowState:
    return RequirementAgent().run(state)
```

Requirement Agent 内部再使用一个小型 LangGraph 子图完成需求分析。这么做的原因是: 外层图负责流水线阶段编排，内层图负责单个 Agent 的“规划、工具调用、产物生成、校验、修复”。两者职责不同，不应该把所有细节都塞进外层 `flow.py`。

## 输入和输出

### 输入字段

| 字段 | 来源 | 用途 |
|------|------|------|
| `original_requirement` | Activity request 或 `globalContext.original_requirement` | 用户原始需求，是最核心输入 |
| `human_feedback` | 控制平面 Signal 后注入 | 后续回溯时用于修订需求理解；T022 首版可保留并写入修订说明 |
| `repository_context` | T018/T019 传入的仓库路径配置 | 可选，用于从 README、spec、package/pom 等文件中补充项目背景 |
| `code_context` | 之前阶段或上下文工具生成 | 可选，用于保留已检查文件和搜索词摘要 |
| `error_logs` | 前序状态 | 追加可恢复错误和诊断信息 |

### 输出字段

Requirement Agent 只写入以下状态增量:

```json
{
  "structured_prd": {},
  "code_context": {},
  "current_step": "REQUIREMENT_ANALYSIS",
  "error_logs": []
}
```

其中 `structured_prd` 建议结构如下:

```json
{
  "summary": "一句话需求摘要",
  "problem_statement": "用户为什么需要这个能力",
  "scope": {
    "in_scope": [],
    "out_of_scope": []
  },
  "user_stories": [
    {
      "role": "用户角色",
      "goal": "目标",
      "benefit": "价值"
    }
  ],
  "acceptance_criteria": [
    {
      "id": "AC-001",
      "description": "可验证的验收标准",
      "verification": "建议的验证方式"
    }
  ],
  "edge_cases": [],
  "non_functional_requirements": {
    "performance": [],
    "security": [],
    "reliability": [],
    "compatibility": []
  },
  "domain_terms": [],
  "assumptions": [],
  "open_questions": [],
  "evidence": {
    "inspected_files": [],
    "search_queries": [],
    "notes": []
  },
  "quality": {
    "testable_acceptance_criteria": true,
    "has_open_questions": false,
    "confidence": "HIGH|MEDIUM|LOW"
  },
  "source": "requirement_agent"
}
```

## Agent 内部状态

建议新增内部状态类型 `RequirementAgentState`，不要直接把所有临时字段写进 `DevFlowState`。外部 `DevFlowState` 是跨阶段契约，内部状态是 Agent 私有工作区。

```python
class RequirementAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    requirement_text: str
    feedback_text: str
    repository_context: dict[str, Any]
    context_pack: dict[str, Any]
    analysis_plan: dict[str, Any]
    draft_prd: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
```

字段含义:

| 字段 | 含义 |
|------|------|
| `devflow_state` | 原始 LangGraph 状态，只作为输入来源和最终合并依据 |
| `requirement_text` | 清洗后的用户需求文本 |
| `feedback_text` | 人工反馈或修订提示 |
| `repository_context` | 控制平面传入的仓库上下文配置 |
| `context_pack` | T019 工具打包出的项目背景摘要 |
| `analysis_plan` | 本次需求分析计划，例如需要识别哪些角色、约束和风险 |
| `draft_prd` | 初稿 PRD |
| `validation_report` | 结构和质量校验结果 |
| `attempts` | 修复次数，避免无限循环 |
| `errors` | Agent 内部可恢复错误 |

## LangGraph 子图设计

Requirement Agent 子图采用 Plan-and-Validate 范式，必要时加入一次修复循环。

```text
START
  -> prepare_input
  -> collect_context
  -> plan_analysis
  -> draft_prd
  -> validate_prd
  -> route_after_validation
       ├─ valid -> finalize
       ├─ repairable -> repair_prd -> validate_prd
       └─ invalid -> fail_soft
  -> END
```

### `prepare_input`

职责:

- 从 `DevFlowState.original_requirement` 读取需求。
- 去除多余空白，保留原始语言。
- 合并 `human_feedback`，但不让反馈覆盖原始需求。
- 如果需求为空，写入不可恢复错误。

输出:

- `requirement_text`
- `feedback_text`
- `errors`

### `collect_context`

职责:

- 如果存在 `repository_context`，调用 T019 上下文工具补充项目背景。
- 优先读取 `targetFiles`，其次搜索常见项目说明文件。
- 如果没有仓库上下文，直接跳过，不影响需求分析。

建议调用工具:

| 工具 | 调用时机 | 作用 |
|------|----------|------|
| `RepositoryContext.from_mapping` | 存在 `repository_context` 时 | 将控制平面 JSON 转换为执行平面对象 |
| `list_files` | 需要了解候选文件时 | 找 README、docs、spec、pom/package 等项目入口文件 |
| `search_text` | 需要搜索领域词时 | 搜索需求关键词、模块名、业务术语 |
| `read_file` | 需要读取明确文件时 | 读取 README、任务说明、目标文件片段 |
| `build_context_pack` | 生成最终上下文摘要时 | 在 `maxFiles/maxBytes` 预算内打包上下文 |

T022 不要求像 Coder Agent 那样深入代码实现。它只需要用仓库上下文判断项目类型、已有功能边界和术语，避免 PRD 与项目背景脱节。

### `plan_analysis`

职责:

- 生成需求分析计划，而不是直接写 PRD。
- 明确本次要抽取的信息类型:
  - 用户角色
  - 业务目标
  - 验收标准
  - 边界条件
  - 非功能约束
  - 开放问题
  - 项目上下文证据

推荐规划范式:

```text
Task-first decomposition
  1. 先判断需求类型: 新功能、修复、重构、交互修改、基础设施等
  2. 再按类型选择 PRD 章节模板
  3. 最后把缺失信息列为 open_questions，而不是任意补全
```

不建议在 T022 使用完全开放的 ReAct 循环。Requirement Agent 的工具需求有限，开放循环容易造成不必要的文件读取和不稳定输出。这里采用“固定节点 + 条件路由 + 有界修复”更适合当前项目阶段。

### `draft_prd`

职责:

- 根据 `analysis_plan`、`requirement_text`、`context_pack` 生成结构化 PRD。
- 必须通过 T021 提供的 LLM 调用客户端生成 PRD，不再设计或支持规则模板型生成器。
- 测试中通过 T021 的 Fake Provider 注入确定性响应，而不是引入 `RuleBasedRequirementAnalyzer`。

生成策略:

- 摘要: 从原始需求提炼一句话。
- 用户故事: 至少生成 1 条；角色不明确时使用“目标用户”，并写入 `assumptions`。
- 验收标准: 至少生成 2 条，优先使用“Given/When/Then”或“操作/期望结果”风格。
- 边界条件: 根据需求类型生成基本边界，例如空输入、权限不足、重复提交、网络失败。
- 非功能约束: 只在需求或项目上下文有依据时填写；否则写入 `open_questions`。
- 证据: 记录上下文工具检查过的文件和搜索词。

### `validate_prd`

职责:

- 校验结构完整性。
- 校验验收标准是否可测试。
- 校验是否保留了缺失信息。
- 校验输出是否只包含 JSON 可序列化值。

建议校验规则:

| 规则 | 失败处理 |
|------|----------|
| `summary` 非空 | 可修复 |
| `user_stories` 至少 1 条 | 可修复 |
| `acceptance_criteria` 至少 2 条 | 可修复 |
| 每条验收标准包含 `description` 和 `verification` | 可修复 |
| 不允许把未知信息写成事实 | 不可自动修复时进入 `open_questions` |
| JSON 可序列化 | 不可恢复，写入 `error_logs` |

### `repair_prd`

职责:

- 根据 `validation_report` 做一次有界修复。
- 修复次数建议最多 1 次或 2 次，避免 Agent 在 Temporal Activity 中长时间循环。
- 修复后回到 `validate_prd`。

修复示例:

- 缺少用户故事: 从 summary 生成默认用户故事。
- 验收标准不可测试: 拆成可操作步骤和期望结果。
- 缺少开放问题: 对不明确的权限、性能、兼容性要求补问题。

### `finalize`

职责:

- 输出外层 `DevFlowState` 增量。
- 保留 `error_logs` 中的可恢复错误。
- 写入 `current_step = REQUIREMENT_ANALYSIS`。
- 将上下文探索摘要写入 `code_context`，便于 T020 展示和后续 Design Agent 使用。

### `fail_soft`

职责:

- 对可诊断的输入问题给出结构化失败结果。
- 不建议直接吞掉严重异常。空需求这类明显输入错误可以返回 `error_logs`，真正的运行时异常交给 Temporal 重试。

## 工具调用策略

Requirement Agent 的工具调用要受控，避免读完整仓库。

默认策略:

```text
1. 有 targetFiles:
   - build_context_pack(paths=targetFiles)

2. 没有 targetFiles，但有 includePaths:
   - list_files
   - 优先选择 README、docs、specs、package.json、pom.xml、pyproject.toml

3. 有明显业务关键词:
   - search_text(关键词)
   - 将命中文件加入 build_context_pack

4. 没有 repository_context:
   - 不调用文件工具，只基于 original_requirement 生成 PRD
```

上下文预算:

- 继承 `RepositoryContext.maxFiles/maxBytes`。
- Requirement Agent 自身可以再设置更小软上限，例如最多 10 个文件、64KB 文本。
- 输出中只保存 `inspected_files`、`search_queries`、摘要 notes，不把大段源文件塞进 `structured_prd`。

## LLM 接入策略

T022 只使用 T021 的 LLM 调用客户端，不提供规则生成器作为生产路径。Requirement Agent 负责构造需求分析 Prompt、传入 JSON Schema、接收结构化输出并执行二次校验。

推荐接口:

```python
class RequirementAnalyzer(Protocol):
    def draft(self, request: RequirementDraftRequest) -> dict[str, Any]:
        ...
```

`RequirementAnalyzer` 内部应组合 T021 的 `LlmClient`。测试可以注入 Fake Provider 返回固定 JSON，生产运行时由配置选择真实 Provider。

## 效果保障

### 结构保障

- 使用固定输出 schema。
- `validate_prd` 做必填字段和 JSON 序列化检查。
- `source` 固定为 `requirement_agent`。

### 可测试性保障

- 验收标准必须包含 `verification`。
- 对“更快”“更安全”“体验更好”等模糊描述，要求转换为可观察结果或写入 `open_questions`。
- 单元测试覆盖登录注册、空需求、带仓库上下文、带人工反馈四类场景。

### 上下文真实性保障

- `evidence.inspected_files` 记录实际读取的文件。
- `evidence.search_queries` 记录实际搜索词。
- 如果没有读取仓库，明确 `notes` 中写明“未提供 repository_context”。

### 稳定性保障

- 工具调用固定顺序，不做无限 ReAct。
- 修复循环有最大次数。
- LLM 输出必须经过校验；校验失败时进入有界修复或返回 `error_logs`，不允许回退到规则模板生成器。

### 可解释性保障

- `assumptions` 写明 Agent 做出的假设。
- `open_questions` 写明后续需要用户确认的问题。
- `quality.confidence` 根据需求明确程度和上下文证据给出 HIGH/MEDIUM/LOW。

## 测试设计

建议新增 `execution-plane/tests/test_requirement_agent.py`。

测试用例:

| 用例 | 验证点 |
|------|--------|
| 普通登录注册需求 | 输出包含 summary、user_stories、acceptance_criteria |
| 空需求 | 返回 `error_logs` 或抛出明确异常 |
| 带 human_feedback | PRD 中保留反馈，并影响 open_questions 或 assumptions |
| 带 repository_context | 调用上下文工具，`structured_prd.evidence.inspected_files` 非空 |
| 验收标准质量 | 每条 acceptance criteria 都包含 `verification` |
| flow 集成 | `analyze_requirement_node` 调用 RequirementAgent 并写入 `current_step` |
| Activity 集成 | `analyzeRequirement` 的 `outputPayload.structured_prd.source` 为 `requirement_agent` |

测试原则:

- 不依赖真实 LLM；通过 T021 Fake Provider 注入固定响应。
- 仓库上下文测试使用临时目录或当前项目中稳定文件。
- 对输出做结构断言，不断言长文本逐字一致。

## 代码组织建议

```text
execution-plane/src/agents/
  __init__.py
  requirement_agent.py

execution-plane/tests/
  test_requirement_agent.py
```

`requirement_agent.py` 建议包含:

| 组件 | 责任 |
|------|------|
| `RequirementAgent` | 对外入口，暴露 `run(state)` |
| `RequirementAgentState` | 内部 LangGraph 状态 |
| `build_requirement_agent_graph` | 构建内部子图 |
| `RequirementAnalyzer` | 组合 T021 `LlmClient`，负责构造 Prompt 并请求结构化 PRD |
| `RequirementPrdValidator` | 输出结构和质量校验 |
| `RequirementContextCollector` | 封装 T019 上下文工具调用 |

## 与后续任务的接口

T023 Design Agent 将消费:

- `structured_prd.summary`
- `structured_prd.user_stories`
- `structured_prd.acceptance_criteria`
- `structured_prd.scope`
- `structured_prd.open_questions`
- `structured_prd.evidence`

因此 T022 必须保证这些字段稳定存在，即使某些列表为空，也应返回空列表而不是省略字段。

T020 展示通道会展示:

- `structured_prd.summary`
- `structured_prd.acceptance_criteria`
- `structured_prd.open_questions`
- `structured_prd.evidence.inspected_files`

因此这些字段应保持简洁，不应包含大段文件内容。

## 注释要求

后续实现 T022 时，关键代码需要写清楚“为什么这样做”，不只写“做了什么”。建议重点注释:

- 为什么 Requirement Agent 使用内部 LangGraph 子图，而不是单函数。
- 为什么上下文工具只读取有限文件。
- 为什么 LLM 输出必须经过校验。
- 为什么修复循环有次数上限。
- 为什么缺失信息进入 `open_questions`，而不是由 Agent 自行编造。

这些注释会帮助后续 T023-T027 维持同一设计风格。
