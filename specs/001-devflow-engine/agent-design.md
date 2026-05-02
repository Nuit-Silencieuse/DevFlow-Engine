# Agent 设计文档

本文档定义 T021-T026 需要实现的六类执行平面 Agent。当前 T016-T017 已建立 Temporal Activity 和 LangGraph 拓扑，T018-T020 会先补齐代码感知和产物展示基础能力，随后这些 Agent 将逐步替换 `execution-plane/src/graph/flow.py` 中的占位节点逻辑。

## 设计原则

1. Agent 输入和输出必须基于 `DevFlowState`，避免引入与图状态分离的隐式上下文。
2. 每个 Agent 只负责一个阶段，不跨阶段直接修改不属于自己的产物。
3. Agent 输出必须可序列化为 JSON，能够进入 Temporal `outputPayload` 和数据库 JSONB 字段。
4. Agent 必须保留足够的中间解释信息，便于人工审批和后续回溯。
5. Agent 失败时应写入 `error_logs`，并返回可诊断的失败上下文。

## 状态字段归属

| 阶段 | Agent | 主要输入 | 主要输出 |
|------|-------|----------|----------|
| `REQUIREMENT_ANALYSIS` | Requirement Agent | `original_requirement` | `structured_prd` |
| `SYSTEM_DESIGN` | Design Agent | `structured_prd`, `human_feedback` | `design_doc` |
| `CODE_GENERATION` | Coder Agent | `design_doc`, `structured_prd` | `diff_patch` |
| `TEST_GENERATION` | Test Agent | `diff_patch`, `design_doc` | `test_results` |
| `CODE_REVIEW` | Review Agent | `diff_patch`, `test_results` | `review_report` |
| `DELIVERY_INTEGRATION` | Delivery Agent | `review_report`, `test_results`, `diff_patch` | `delivery_status` |

## T021: Requirement Agent

目标文件:

`execution-plane/src/agents/requirement_agent.py`

绑定节点:

`REQUIREMENT_ANALYSIS`

职责:

- 将自然语言需求整理为结构化 PRD。
- 提取用户故事、验收标准、边界条件、非功能约束。
- 识别缺失信息，并标记后续需要人工确认的点。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `original_requirement` | 是 | 用户原始需求 |
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
1. 读取 original_requirement
2. 清洗空白和格式
3. 调用需求分析策略或 LLM
4. 生成 structured_prd
5. 校验验收标准是否可测试
6. 返回状态增量:
   - structured_prd
   - current_step = REQUIREMENT_ANALYSIS
```

验收测试建议:

- 输入一段登录注册需求，输出至少包含 summary、user_stories、acceptance_criteria。
- 当需求为空时，写入 `error_logs` 或抛出可控异常。
- 如果存在 `human_feedback`，应体现在 `open_questions` 或修订说明中。

## T022: Design Agent

目标文件:

`execution-plane/src/agents/design_agent.py`

绑定节点:

`SYSTEM_DESIGN`

职责:

- 根据 `structured_prd` 生成系统设计方案。
- 输出模块拆分、数据模型、API 设计、文件变更计划。
- 吸收人工驳回反馈并修订设计。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `structured_prd` | 是 | 需求分析结果 |
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
3. 生成模块设计和文件计划
4. 标记风险和待确认项
5. 返回 design_doc 和 current_step
```

人工检查点:

- 控制平面当前在 `SYSTEM_DESIGN` 后暂停等待审批。
- 如果用户 `REJECT`，Java Workflow 会把反馈写入 `globalContext.human_feedback`，执行平面再次运行 Design Agent。
- Design Agent 必须把反馈写入 `design_doc.feedback`，并体现在修订后的方案中。

验收测试建议:

- 有 `structured_prd` 时生成模块和文件计划。
- 有 `human_feedback` 时输出中必须保留反馈文本，并改变设计说明。
- 输出必须能被 JSON 序列化。

## T023: Coder Agent

目标文件:

`execution-plane/src/agents/coder_agent.py`

绑定节点:

`CODE_GENERATION`

职责:

- 根据 `design_doc` 生成代码修改方案。
- 读取目标仓库上下文，定位需要修改的文件。
- 生成可审查的 diff patch。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `design_doc` | 是 | 系统设计和文件计划 |
| `structured_prd` | 否 | 需求上下文 |
| `human_feedback` | 否 | 对代码生成有影响的人工反馈 |

输出:

写入 `diff_patch`:

```text
diff --git a/path/to/file b/path/to/file
...
```

可选地在后续扩展中写入:

```json
{
  "changed_files": [],
  "implementation_notes": [],
  "risk_notes": []
}
```

执行流程:

```text
1. 读取 design_doc.file_plan
2. 加载目标文件内容或代码索引
3. 生成代码修改
4. 将修改标准化为 unified diff
5. 返回 diff_patch 和 current_step
```

约束:

- 不直接提交 Git。
- 不直接覆盖文件，文件写入应由后续沙箱或明确的文件管理模块执行。
- diff 必须尽量小，避免无关格式化。

验收测试建议:

- 输入包含文件计划的 `design_doc`，输出非空 `diff_patch`。
- 不存在文件计划时返回错误上下文。
- diff 中应包含目标文件路径。

## T024: Test Agent

目标文件:

`execution-plane/src/agents/test_agent.py`

绑定节点:

`TEST_GENERATION`

职责:

- 根据 `diff_patch` 和 `design_doc` 生成或更新测试。
- 规划测试命令。
- 在允许的环境中运行测试并汇总结果。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `diff_patch` | 是 | 代码变更 |
| `design_doc` | 是 | 设计目标 |
| `structured_prd` | 否 | 验收标准 |

输出:

写入 `test_results`:

```json
{
  "generated_tests": [
    {
      "path": "测试文件",
      "purpose": "覆盖目标"
    }
  ],
  "commands": [
    "mvn test"
  ],
  "status": "PASS|FAIL|PENDING",
  "summary": "测试摘要",
  "failures": []
}
```

执行流程:

```text
1. 从 structured_prd.acceptance_criteria 提取测试目标
2. 从 diff_patch 判断受影响模块
3. 生成测试文件计划或测试 diff
4. 运行可用测试命令
5. 汇总 PASS/FAIL/PENDING
6. 返回 test_results 和 current_step
```

验收测试建议:

- 能根据验收标准生成测试计划。
- 能记录执行命令和状态。
- 测试失败时保留失败摘要，不吞掉错误。

## T025: Review Agent

目标文件:

`execution-plane/src/agents/review_agent.py`

绑定节点:

`CODE_REVIEW`

职责:

- 审查 `diff_patch` 的正确性、安全性、可维护性。
- 结合 `test_results` 判断变更是否可进入交付。
- 输出按严重程度排序的审查报告。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `diff_patch` | 是 | 待审查代码变更 |
| `test_results` | 是 | 测试结果 |
| `design_doc` | 否 | 设计意图 |

输出:

写入 `review_report`:

```json
{
  "status": "APPROVED|CHANGES_REQUESTED|BLOCKED",
  "findings": [
    {
      "severity": "HIGH|MEDIUM|LOW",
      "file": "文件路径",
      "line": 1,
      "message": "问题说明",
      "recommendation": "修复建议"
    }
  ],
  "test_assessment": "测试充分性判断",
  "summary": "评审摘要"
}
```

执行流程:

```text
1. 解析 diff_patch
2. 对照 design_doc 检查实现是否偏离设计
3. 检查安全、错误处理、边界条件、兼容性
4. 结合 test_results 判断风险
5. 输出 findings 和最终 status
```

验收测试建议:

- 当测试失败时，`status` 不应为 `APPROVED`。
- findings 应按严重程度排序。
- 无问题时明确输出空 findings 和批准状态。

## T026: Delivery Agent

目标文件:

`execution-plane/src/agents/delivery_agent.py`

绑定节点:

`DELIVERY_INTEGRATION`

职责:

- 根据评审结果整理交付状态。
- 生成交付摘要、变更清单和后续操作建议。
- 为后续 MR/PR 创建逻辑提供结构化输入。

输入:

| 字段 | 必需 | 说明 |
|------|------|------|
| `review_report` | 是 | 代码评审报告 |
| `test_results` | 是 | 测试结果 |
| `diff_patch` | 是 | 代码变更 |

输出:

写入 `delivery_status`:

```json
{
  "status": "READY|BLOCKED|PENDING",
  "summary": "交付摘要",
  "changed_files": [],
  "release_notes": [],
  "next_actions": [],
  "mr": {
    "url": "",
    "status": "NOT_CREATED"
  }
}
```

执行流程:

```text
1. 读取 review_report.status
2. 读取 test_results.status
3. 如果评审或测试阻塞，则 delivery_status.status = BLOCKED
4. 否则生成交付摘要和 release notes
5. 返回 delivery_status 和 current_step
```

验收测试建议:

- 评审通过且测试通过时输出 `READY`。
- 评审要求修改或测试失败时输出 `BLOCKED`。
- 输出必须包含可展示给用户的摘要。

## Agent 与 LangGraph 的集成方式

建议后续实现时保持 `flow.py` 的公共接口稳定:

```python
def analyze_requirement_node(state: DevFlowState) -> DevFlowState:
    return RequirementAgent().run(state)
```

每个 Agent 建议提供统一接口:

```python
class RequirementAgent:
    def run(self, state: DevFlowState) -> DevFlowState:
        ...
```

这样测试可以分别覆盖:

- Agent 本身的输入输出。
- `flow.py` 节点是否调用正确 Agent。
- Temporal Activity 是否正确把请求转换为状态并返回契约结果。

## 错误处理约定

Agent 遇到可恢复错误时，应返回:

```python
{
    "error_logs": [*state.get("error_logs", []), "错误说明"],
    "current_step": "当前阶段"
}
```

遇到不可恢复错误时，可以抛出异常，让 Temporal Activity 失败并交给 Temporal 重试策略处理。后续接入真实 LLM 和文件系统时，应区分:

- 输入缺失: 通常是不可恢复错误。
- LLM 超时: 可重试错误。
- 测试失败: 业务结果，应写入 `test_results`，不一定抛异常。
- 代码评审发现问题: 业务结果，应写入 `review_report`，不应抛异常。

## 文档和测试要求

每完成一个 Agent 任务，应同步补充:

- Agent 单元测试。
- `execution-plane-architecture.md` 中对应节点的实现状态。
- 如输出结构变化，更新本文件和 `contracts/workflow.md`。
