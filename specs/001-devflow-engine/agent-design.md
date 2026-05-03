# Agent 设计总览

本文档定义执行平面的 LLM 调用基础能力与六类业务 Agent。T021 先实现可配置 LLM 调用客户端，T022-T027 再逐步把 `execution-plane/src/graph/flow.py` 中的占位节点替换为真实 Agent。

当前 T016-T017 已建立 Temporal Activity 与 LangGraph 拓扑，T018-T020 已补齐代码感知和中间产物展示基础能力。后续 Agent 必须复用这些基础能力，不再支持 `RuleBasedRequirementAnalyzer` 这类规则或模板式需求分析实现。

## 设计原则

1. Agent 输入和输出必须基于 `DevFlowState`，避免引入与图状态分离的隐式上下文。
2. 每个 Agent 只负责一个流水线阶段，不跨阶段直接修改不属于自己的产物。
3. Agent 输出必须可序列化为 JSON，能够进入 Temporal `outputPayload` 和数据库 JSONB 字段。
4. Agent 必须保留足够的中间解释信息，便于人工审批和后续回溯。
5. Agent 失败时应写入 `error_logs`，并返回可诊断的失败上下文。
6. 所有真实业务 Agent 统一通过 T021 的 `LlmClient` 调用模型，不直接依赖某个 Provider SDK。
7. 测试中通过可控 Fake Provider 或固定响应验证行为，不把规则分析器作为生产兜底路径。

## 状态字段归属

| 阶段 | Agent | 主要输入 | 主要输出 |
|------|-------|----------|----------|
| `REQUIREMENT_ANALYSIS` | Requirement Agent | `original_requirement`, `repository_context` | `structured_prd`, `code_context` |
| `SYSTEM_DESIGN` | Design Agent | `structured_prd`, `code_context`, `human_feedback` | `design_doc` |
| `CODE_GENERATION` | Coder Agent | `design_doc`, `structured_prd`, `code_context` | `diff_patch` |
| `TEST_GENERATION` | Test Agent | `diff_patch`, `design_doc`, `code_context` | `test_results` |
| `CODE_REVIEW` | Review Agent | `diff_patch`, `test_results`, `design_doc` | `review_report` |
| `DELIVERY_INTEGRATION` | Delivery Agent | `review_report`, `test_results`, `diff_patch` | `delivery_status` |

## T021: LLM Client

详细设计见 [t021-llm-client-design.md](./t021-llm-client-design.md)。

目标文件:

`execution-plane/src/llm/`

绑定节点:

无。它是 T022-T027 业务 Agent 的公共调用基础设施。

职责:

- 定义统一的 `LlmClient`、`LlmRequest`、`LlmResponse`、`LlmProvider` 契约。
- 至少支持两个可配置 Provider，例如 OpenAI-compatible 和 Anthropic-compatible。
- 支持运行时按请求切换 Provider 和模型，而不是只在进程启动时固定。
- 支持结构化 JSON 输出、有限 JSON 修复、重试和超时控制。
- 提供 Fake Provider 供 TDD 和离线测试使用。
- 禁止把 Fake Provider 或规则模板实现作为生产环境自动兜底。

验收测试建议:

- 默认 Provider 来自环境变量，单次请求可覆盖 Provider 和模型。
- 两个不同 Provider 的响应能被统一转换为 `LlmResponse`。
- JSON 响应解析失败时执行有限修复；仍失败时抛出可诊断异常。
- 日志和异常不能泄露 API Key、Authorization Header 等敏感信息。

## T022: Requirement Agent

详细设计见 [t022-requirement-agent-design.md](./t022-requirement-agent-design.md)。

目标文件:

`execution-plane/src/agents/requirement_agent.py`

绑定节点:

`REQUIREMENT_ANALYSIS`

职责:

- 通过 T021 `LlmClient` 将自然语言需求整理为结构化 PRD。
- 使用 T019 代码库上下文工具做路径驱动的渐进式探索。
- 提取用户故事、验收标准、边界条件、非功能约束。
- 识别缺失信息，并标记后续需要人工确认的点。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `original_requirement` | 是 | 用户原始需求 |
| `repository_context` | 否 | 代码库根路径、包含路径、排除路径和目标文件 |
| `human_feedback` | 否 | 人工补充说明；通常来自后续回溯 |
| `error_logs` | 否 | 历史错误上下文 |

输出:

写入 `structured_prd`:

```json
{
  "summary": "需求摘要",
  "user_stories": [
    {
      "role": "用户角色",
      "goal": "目标",
      "benefit": "价值"
    }
  ],
  "acceptance_criteria": [
    "可测试的验收标准"
  ],
  "edge_cases": [
    "边界场景"
  ],
  "non_functional_requirements": {
    "performance": [],
    "security": [],
    "reliability": []
  },
  "open_questions": [],
  "source": "requirement_agent"
}
```

执行流程:

```text
1. 读取 original_requirement 和 repository_context
2. 根据路径配置调用 context 工具，逐步收集相关代码上下文
3. 组装包含需求、上下文摘要和输出 schema 的 LLM 请求
4. 调用 T021 LlmClient 获取结构化 JSON 响应
5. 校验 user_stories、acceptance_criteria、open_questions 等关键字段
6. 返回 structured_prd、code_context 和 current_step
```

验收测试建议:

- 输入一段登录注册需求，输出至少包含 summary、user_stories、acceptance_criteria。
- 存在 `repository_context` 时，应记录被检查文件和搜索词。
- 当需求为空时，写入 `error_logs` 或抛出可控异常。
- 测试使用 Fake Provider 注入固定 LLM 响应，不依赖真实模型服务。

## T023: Design Agent

目标文件:

`execution-plane/src/agents/design_agent.py`

绑定节点:

`SYSTEM_DESIGN`

职责:

- 根据 `structured_prd` 和代码上下文生成系统设计方案。
- 输出模块拆分、数据模型、API 设计、文件变更计划。
- 吸收人工驳回反馈并修订设计。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `structured_prd` | 是 | 需求分析结果 |
| `code_context` | 否 | Requirement Agent 或上下文工具生成的代码上下文 |
| `human_feedback` | 否 | 人工驳回反馈 |
| `original_requirement` | 否 | 原始需求补充上下文 |

输出:

写入 `design_doc`:

```json
{
  "summary": "设计摘要",
  "modules": [
    {
      "name": "模块名",
      "responsibility": "职责",
      "dependencies": []
    }
  ],
  "api_contracts": [],
  "data_changes": [],
  "file_plan": [
    {
      "path": "需要修改的文件",
      "operation": "create|update",
      "reason": "修改原因"
    }
  ],
  "risks": [],
  "feedback": "人工反馈",
  "source": "design_agent"
}
```

执行流程:

```text
1. 读取 structured_prd
2. 如果 human_feedback 存在，优先分析反馈中要求修正的点
3. 根据 code_context 判断现有模块、接口和数据结构
4. 通过 LlmClient 生成模块设计和文件计划
5. 校验设计产物字段完整性
6. 返回 design_doc 和 current_step
```

## T024: Coder Agent

目标文件:

`execution-plane/src/agents/coder_agent.py`

绑定节点:

`CODE_GENERATION`

职责:

- 根据 `design_doc` 生成代码修改方案。
- 按文件计划继续读取目标仓库上下文，定位需要修改的文件。
- 生成可审查的 diff patch。

约束:

- 不直接提交 Git。
- 不直接覆盖文件，文件写入应由后续沙箱或明确的文件管理模块执行。
- diff 必须尽量小，避免无关格式化。

## T025: Test Agent

目标文件:

`execution-plane/src/agents/test_agent.py`

绑定节点:

`TEST_GENERATION`

职责:

- 根据 `diff_patch`、`design_doc` 和验收标准生成或更新测试。
- 规划测试命令。
- 在允许的环境中运行测试并汇总结论。

输出写入 `test_results`，其中必须包含测试状态、命令、摘要和失败详情。

## T026: Review Agent

目标文件:

`execution-plane/src/agents/review_agent.py`

绑定节点:

`CODE_REVIEW`

职责:

- 审查 `diff_patch` 的正确性、安全性和可维护性。
- 结合 `test_results` 判断变更是否可进入交付。
- 输出按严重程度排序的审查报告。

输出写入 `review_report`，其中 `status` 可以是 `APPROVED`、`CHANGES_REQUESTED` 或 `BLOCKED`。

## T027: Delivery Agent

目标文件:

`execution-plane/src/agents/delivery_agent.py`

绑定节点:

`DELIVERY_INTEGRATION`

职责:

- 根据评审结果整理交付状态。
- 生成交付摘要、变更清单和后续操作建议。
- 为后续 MR/PR 创建逻辑提供结构化输入。

输出写入 `delivery_status`，其中 `status` 可以是 `READY`、`BLOCKED` 或 `PENDING`。

## Agent 与 LangGraph 的集成方式

后续实现时保持 `flow.py` 的公共接口稳定:

```python
def analyze_requirement_node(state: DevFlowState) -> DevFlowState:
    return RequirementAgent(llm_client=build_default_llm_client()).run(state)
```

每个 Agent 建议提供统一接口:

```python
class RequirementAgent:
    def run(self, state: DevFlowState) -> DevFlowState:
        ...
```

这样测试可以分别覆盖:

- LLM Client 的 Provider 选择、结构化输出和错误处理。
- Agent 本身的输入输出。
- `flow.py` 节点是否调用正确 Agent。
- Temporal Activity 是否正确把请求转换为状态并返回契约结果。

## 错误处理约定

Agent 遇到可恢复业务错误时，应返回:

```python
{
    "error_logs": [*state.get("error_logs", []), "错误说明"],
    "current_step": "当前阶段"
}
```

遇到不可恢复错误时，可以抛出异常，让 Temporal Activity 失败并交给 Temporal 重试策略处理。接入真实 LLM、文件系统和测试运行器时，应区分:

- 输入缺失: 通常是不可恢复错误。
- LLM 超时或限流: 可重试错误。
- LLM 输出 JSON 不合法: 先做有限修复，仍失败则写入诊断信息。
- 测试失败: 业务结果，应写入 `test_results`，不一定抛异常。
- 代码评审发现问题: 业务结果，应写入 `review_report`，不应抛异常。

## 文档和测试要求

每完成一个任务，应同步补充:

- 对应模块的单元测试。
- `execution-plane-architecture.md` 中对应模块的实现状态。
- 如输出结构变化，更新本文档和 `contracts/workflow.md`。
- 关键代码和复杂方法需要中文注释，说明设计意图、状态流转和失败处理策略。
