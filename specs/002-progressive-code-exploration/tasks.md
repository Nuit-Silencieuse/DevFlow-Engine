# 任务: 工具调用式渐进探索 Agent

**输入**: 来自 `/specs/002-progressive-code-exploration/` 的设计文档  
**前置条件**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md  
**测试策略**: 本功能按 TDD 执行。每个用户故事阶段先补充失败测试，再实现代码。  
**组织结构**: 任务按用户故事分组，保证每个故事可以独立实现、独立测试、增量交付。

## 格式: `[ID] [P?] [Story] 描述`

- **[P]**: 可并行运行，要求不同文件且不依赖未完成任务
- **[Story]**: 用户故事标签，例如 [US1]、[US2]、[US3]
- 每个任务描述都包含明确文件路径

---

## 阶段 1: 设置与测试夹具

**目的**: 为渐进探索工具准备可重复的本地测试仓库和测试入口。

- [X] T019-U001 [P] 在 `execution-plane/tests/fixtures/progressive_repo/README.md` 创建测试仓库说明，描述健康检查、控制平面、执行平面三个模拟模块
- [X] T019-U002 [P] 在 `execution-plane/tests/fixtures/progressive_repo/src/health_service.py` 创建可被业务关键词命中的代表性代码文件
- [X] T019-U003 [P] 在 `execution-plane/tests/fixtures/progressive_repo/src/temporal_worker.py` 创建 worker 状态相关代表性代码文件
- [X] T019-U004 [P] 在 `execution-plane/tests/fixtures/progressive_repo/node_modules/ignored.js` 创建默认排除目录样例文件
- [X] T019-U005 [P] 在 `execution-plane/tests/fixtures/progressive_repo/logs/runtime.log` 创建默认排除日志样例文件

---

## 阶段 2: 基础能力

**目的**: 建立所有用户故事共享的上下文数据结构、预算、安全边界和工具导出。

**关键**: 在此阶段完成之前，不能开始用户故事实现。

- [X] T019-U006 [P] 在 `execution-plane/tests/test_context_tools.py` 添加 RepositoryExplorationRequest、预算默认值、根目录逃逸防护的失败测试
- [X] T019-U007 在 `execution-plane/src/context/repository_context.py` 实现 RepositoryExplorationRequest、ExplorationSession、ExplorationStep、EvidenceItem、CodeContextSummary、SkippedFile、BudgetUsage 数据结构并添加中文注释说明字段语义
- [X] T019-U008 在 `execution-plane/src/context/repository_context.py` 实现路径规范化、rootPath 边界校验、默认排除规则和预算配置解析
- [X] T019-U009 在 `execution-plane/src/context/__init__.py` 导出渐进探索数据结构和工具函数，供 RequirementAgent 与测试直接复用
- [X] T019-U010 在 `execution-plane/tests/test_context_tools.py` 验证阶段 2 基础测试通过，并确保测试覆盖非法路径、默认预算和默认排除规则

**检查点**: 上下文请求模型、安全边界和预算基础就绪。

---

## 阶段 3: 用户故事 1 - 仅提供代码库根目录即可完成需求分析 (优先级: P1) MVP

**目标**: 用户只提供需求文本和 `repository.rootPath` 时，RequirementAgent 能自动发现、搜索、读取相关代码，并产出带证据的需求分析。

**独立测试**: 使用 `execution-plane/tests/fixtures/progressive_repo/` 作为目标代码库，不传 include/exclude，验证输出包含搜索词、已读文件、证据、预算使用和结构化需求分析结果。

### 用户故事 1 的测试

> 先编写这些测试，并确认它们在实现前失败。

- [X] T019-U011 [P] [US1] 在 `execution-plane/tests/test_context_tools.py` 添加 list_repository 默认跳过 node_modules/logs 且返回候选文件的失败测试
- [X] T019-U012 [P] [US1] 在 `execution-plane/tests/test_context_tools.py` 添加 search_text 按关键词返回文件路径、行号、预览片段和截断标记的失败测试
- [X] T019-U013 [P] [US1] 在 `execution-plane/tests/test_context_tools.py` 添加 read_file_range 按行范围读取、统计 bytesRead、处理超长片段的失败测试
- [X] T019-U014 [P] [US1] 在 `execution-plane/tests/test_requirement_agent.py` 添加仅传 rootPath 时 RequirementAgent 自动探索并生成 codeContext 的失败测试
- [X] T019-U015 [P] [US1] 在 `execution-plane/tests/test_requirement_agent.py` 添加 RequirementAgent 在无 repository 时退化为纯需求文本分析的失败测试

### 用户故事 1 的实施

- [X] T019-U016 [US1] 在 `execution-plane/src/context/repository_context.py` 实现 list_repository 工具，返回候选文件、语言提示、优先级提示和 skipped 列表
- [X] T019-U017 [US1] 在 `execution-plane/src/context/repository_context.py` 实现 search_text 工具，支持关键词搜索、默认文本后缀过滤、最大结果数和结构化 match 输出
- [X] T019-U018 [US1] 在 `execution-plane/src/context/repository_context.py` 实现 read_file_range 工具，支持行范围读取、字节预算、二进制/超大文件跳过和中文注释说明安全边界
- [X] T019-U019 [US1] 在 `execution-plane/src/agents/requirement_agent.py` 实现“规划 -> 文件发现 -> 搜索 -> 范围读取 -> 证据归纳 -> 充分性评估”的有限轮次循环，并添加中文注释解释每个阶段为什么存在
- [X] T019-U020 [US1] 在 `execution-plane/src/agents/requirement_agent.py` 将 CodeContextSummary 注入需求分析提示词，并保证 PRD 输出能引用 evidence 和 openQuestions
- [X] T019-U021 [US1] 在 `execution-plane/src/workers/activities.py` 确保 analyzeRequirement 活动返回的阶段产物包含 `codeContext` 字段
- [X] T019-U022 [US1] 使用 `execution-plane/tests/test_context_tools.py` 和 `execution-plane/tests/test_requirement_agent.py` 验证用户故事 1 测试通过

**检查点**: MVP 可演示。用户无需填写 include/exclude 即可获得基于代码证据的需求分析。

---

## 阶段 4: 用户故事 2 - 高级用户可限制探索范围 (优先级: P2)

**目标**: includePaths、excludePaths、targetFiles、预算和 privacyMode 作为高级选项生效，且不会破坏默认自动探索体验。

**独立测试**: 使用同一测试仓库分别运行默认探索和带高级约束的探索，验证排除路径绝不被读取，目标文件会被优先评估，小预算会产生 DEGRADED 或明确预算说明。

### 用户故事 2 的测试

> 先编写这些测试，并确认它们在实现前失败。

- [ ] T019-U023 [P] [US2] 在 `execution-plane/tests/test_context_tools.py` 添加 excludePaths 严格禁止搜索、读取、进入 evidence 的失败测试
- [ ] T019-U024 [P] [US2] 在 `execution-plane/tests/test_context_tools.py` 添加 targetFiles 优先进入候选集合且预算内仍可补充搜索的失败测试
- [ ] T019-U025 [P] [US2] 在 `execution-plane/tests/test_context_tools.py` 添加 maxRounds、maxFiles、maxBytes 触发预算耗尽和 skippedPaths 说明的失败测试
- [ ] T019-U026 [P] [US2] 在 `control-plane/devflow-engine/src/test/java/com/devflow/engine/api/PipelineControllerContractTest.java` 添加 repository 高级字段可选且 rootPath 是常规最小字段的契约测试
- [ ] T019-U027 [P] [US2] 在 `sandbox/frontend/src/api.test.ts` 添加 CreatePipelineRequest 支持高级 repository 选项且默认不要求 include/exclude 的测试

### 用户故事 2 的实施

- [ ] T019-U028 [US2] 在 `execution-plane/src/context/repository_context.py` 实现 includePaths、excludePaths、targetFiles 的合并、冲突处理和 exclude 优先规则
- [ ] T019-U029 [US2] 在 `execution-plane/src/context/repository_context.py` 实现预算耗尽时的 DEGRADED 摘要、skippedPaths 记录和隐私模式下的 excerpt 降级
- [ ] T019-U030 [US2] 在 `execution-plane/src/agents/requirement_agent.py` 接入高级约束配置，确保用户显式约束只收窄探索边界，不改变渐进探索主流程
- [ ] T019-U031 [US2] 在 `control-plane/devflow-engine/src/main/java/com/devflow/engine/api/RepositoryContext.java` 增加 targetFiles、maxRounds、maxFiles、maxBytes、maxSearchResults、privacyMode 字段
- [ ] T019-U032 [US2] 在 `control-plane/devflow-engine/src/main/java/com/devflow/engine/api/CreatePipelineRequest.java` 保持 repository 可选，并确保仅提供 rootPath 的请求仍可创建流水线
- [ ] T019-U033 [US2] 在 `sandbox/frontend/src/types.ts` 和 `sandbox/frontend/src/api.ts` 增加高级 repository 选项类型，保持 include/exclude 在 UI 层可省略
- [ ] T019-U034 [US2] 使用 `execution-plane/tests/test_context_tools.py`、`control-plane/devflow-engine/src/test/java/com/devflow/engine/api/PipelineControllerContractTest.java`、`sandbox/frontend/src/api.test.ts` 验证用户故事 2 测试通过

**检查点**: 高级约束可控生效，默认体验仍只需要 rootPath。

---

## 阶段 5: 用户故事 3 - 用户可审查探索过程和证据 (优先级: P3)

**目标**: 用户能看到 Agent 为什么搜索、为什么读取、哪些文件支撑结论、哪些问题仍未确认，并能在前端查看中间产物。

**独立测试**: 对一次需求分析结果检查 `explorationTrace`、`evidence`、`confidence`、`openQuestions` 和前端展示状态，确保关键结论可追溯。

### 用户故事 3 的测试

> 先编写这些测试，并确认它们在实现前失败。

- [ ] T019-U035 [P] [US3] 在 `execution-plane/tests/test_requirement_agent.py` 添加 explorationTrace 记录 PLAN、LIST_FILES、SEARCH_TEXT、READ_FILE、EVALUATE 步骤的失败测试
- [ ] T019-U036 [P] [US3] 在 `execution-plane/tests/test_requirement_agent.py` 添加信息不足时输出 openQuestions 和低 confidence 的失败测试
- [ ] T019-U037 [P] [US3] 在 `control-plane/devflow-engine/src/test/java/com/devflow/engine/service/PipelineServiceTest.java` 添加 Stage outputPayload 保留 codeContext 和 explorationTrace 的测试
- [ ] T019-U038 [P] [US3] 在 `sandbox/frontend/src/viewModel.test.ts` 添加 codeContext 状态、证据、预算和跳过路径展示模型测试
- [ ] T019-U039 [P] [US3] 在 `sandbox/frontend/src/api.test.ts` 添加 GET pipeline 响应解析 codeContext 和 explorationTrace 的测试

### 用户故事 3 的实施

- [ ] T019-U040 [US3] 在 `execution-plane/src/context/repository_context.py` 为每个工具调用生成 ExplorationStep，并在中文注释中说明 trace 与最终证据的区别
- [ ] T019-U041 [US3] 在 `execution-plane/src/agents/requirement_agent.py` 汇总 evidence、candidateFiles、skippedPaths、budgetUsage、confidence、openQuestions 和 explorationTrace
- [ ] T019-U042 [US3] 在 `execution-plane/src/llm/tracing.py` 记录渐进探索工具输入、结果摘要和 LLM 消息，避免写入疑似密钥原文
- [ ] T019-U043 [US3] 在 `control-plane/devflow-engine/src/main/java/com/devflow/engine/api/StageStatusResponse.java` 确保阶段输出可以透传 codeContext 和 explorationTrace
- [ ] T019-U044 [US3] 在 `sandbox/frontend/src/types.ts` 增加 CodeContextSummary、EvidenceItem、ExplorationStep、BudgetUsage 类型
- [ ] T019-U045 [US3] 在 `sandbox/frontend/src/viewModel.ts` 增加 codeContext 展示 view model，包含状态、已读文件、搜索词、证据、预算、跳过原因和开放问题
- [ ] T019-U046 [US3] 在 `sandbox/frontend/src/main.ts` 和 `sandbox/frontend/src/styles.css` 实现中间产物展示区域，避免证据卡片文本溢出或遮挡
- [ ] T019-U047 [US3] 使用 `execution-plane/tests/test_requirement_agent.py`、`control-plane/devflow-engine/src/test/java/com/devflow/engine/service/PipelineServiceTest.java`、`sandbox/frontend/src/viewModel.test.ts`、`sandbox/frontend/src/api.test.ts` 验证用户故事 3 测试通过

**检查点**: 用户可以审查探索过程、证据、预算和不确定问题。

---

## 阶段 6: 完善与横切关注点

**目的**: 补齐文档、真实仓库验证、全量测试和任务完成标记。

- [ ] T019-U048 [P] 在 `specs/002-progressive-code-exploration/quickstart.md` 补充实现后的本项目真实仓库 smoke test 步骤和预期 trace 输出字段
- [ ] T019-U049 [P] 在 `specs/001-devflow-engine/execution-plane.md` 补充渐进探索 Agent 的工具调用流程、状态字段和中间产物说明
- [ ] T019-U050 [P] 在 `specs/001-devflow-engine/agent-design.md` 补充 RequirementAgent 从路径驱动升级为渐进探索 Agent 的设计说明
- [ ] T019-U051 使用 `execution-plane/tests/test_context_tools.py` 和 `execution-plane/tests/test_requirement_agent.py` 运行 Python 单元测试，并记录失败修复到相关实现文件
- [ ] T019-U052 使用 `control-plane/devflow-engine/src/test/java/com/devflow/engine/api/PipelineControllerContractTest.java` 和 `control-plane/devflow-engine/src/test/java/com/devflow/engine/service/PipelineServiceTest.java` 运行 Maven 测试，并记录失败修复到相关实现文件
- [ ] T019-U053 使用 `sandbox/frontend/src/api.test.ts` 和 `sandbox/frontend/src/viewModel.test.ts` 运行前端测试与构建，并记录失败修复到相关实现文件
- [ ] T019-U054 使用 `execution-plane/scripts/llm_smoke_test.py` 或新增 `execution-plane/scripts/progressive_exploration_smoke.py` 以本项目代码库为目标执行真实上下文工具 smoke test，并确认输出包含搜索词、已读文件、证据和 trace 文件路径
- [ ] T019-U055 在 `specs/002-progressive-code-exploration/tasks.md` 将已完成任务勾选，并保持任务编号、故事标签和文件路径格式一致

---

## 依赖关系与执行顺序

### 阶段依赖关系

- **阶段 1 设置**: 无依赖，可立即开始。
- **阶段 2 基础**: 依赖阶段 1，用于建立共享数据结构、安全边界和预算解析。
- **US1 MVP**: 依赖阶段 2，是默认 rootPath 自动探索的最小可交付能力。
- **US2 高级约束**: 依赖阶段 2，可在 US1 后实施；如果团队并行，也可在基础工具稳定后与 US1 的 Agent 集成部分错开开发。
- **US3 可审查证据**: 依赖 US1 的 codeContext 基础输出，并与 US2 的约束输出集成。
- **阶段 6 完善**: 依赖目标用户故事完成。

### 用户故事依赖关系

- **US1 (P1)**: MVP，无其他用户故事依赖。
- **US2 (P2)**: 依赖基础工具模型，可独立测试高级约束；最终需要与 US1 的主流程集成。
- **US3 (P3)**: 依赖 US1 的探索结果结构；展示层可先用测试数据并行推进。

### 每个用户故事内部

- 测试任务必须先完成，并在实现前确认失败。
- 上下文工具实现先于 RequirementAgent 集成。
- 控制平面契约先于前端 API 类型联调。
- 每个故事完成后运行该故事列出的独立测试，再进入下一个优先级故事。

### 并行机会

- T019-U001-T019-U005 测试夹具文件互不冲突，可并行。
- T019-U011-T019-U015、T019-U023-T019-U027、T019-U035-T019-U039 都是测试文件任务，可在各自故事内并行编写。
- T019-U031-T019-U033 分别位于 Java DTO 和前端类型/API，完成测试后可并行实现。
- T019-U044-T019-U046 分别位于前端类型、view model、UI/CSS，但 T019-U046 依赖 T019-U044/T019-U045 的字段约定。
- T019-U048-T019-U050 文档任务互不冲突，可并行。

---

## 并行示例

### 用户故事 1

```text
任务: T019-U011 在 execution-plane/tests/test_context_tools.py 添加 list_repository 默认排除测试
任务: T019-U012 在 execution-plane/tests/test_context_tools.py 添加 search_text 结构化匹配测试
任务: T019-U014 在 execution-plane/tests/test_requirement_agent.py 添加 rootPath 自动探索测试
```

### 用户故事 2

```text
任务: T019-U023 在 execution-plane/tests/test_context_tools.py 添加 excludePaths 测试
任务: T019-U026 在 control-plane/devflow-engine/src/test/java/com/devflow/engine/api/PipelineControllerContractTest.java 添加 API 契约测试
任务: T019-U027 在 sandbox/frontend/src/api.test.ts 添加前端请求类型测试
```

### 用户故事 3

```text
任务: T019-U035 在 execution-plane/tests/test_requirement_agent.py 添加 explorationTrace 测试
任务: T019-U037 在 control-plane/devflow-engine/src/test/java/com/devflow/engine/service/PipelineServiceTest.java 添加阶段输出透传测试
任务: T019-U038 在 sandbox/frontend/src/viewModel.test.ts 添加中间产物展示模型测试
```

---

## 实施策略

### 仅 MVP

1. 完成阶段 1 和阶段 2。
2. 完成 US1 的测试和实现。
3. 使用本项目代码库执行 rootPath-only 需求分析 smoke test。
4. 若 codeContext 中包含搜索词、已读文件、证据、预算和置信度，即可演示 MVP。

### 增量交付

1. US1: 默认 rootPath 自动探索。
2. US2: 高级 include/exclude/targetFiles/预算控制。
3. US3: trace、证据、开放问题和前端中间产物展示。
4. 最后补齐文档、全量测试和真实仓库验证。

### 质量要求

- 每个复杂方法必须有清晰中文注释，说明状态转换、工具调用目的和安全边界。
- 所有路径读取必须经过 rootPath 校验。
- 输出不能编造未从需求文本或代码证据得到的事实。
- 不新增数据库迁移，除非实现时发现现有 Stage outputPayload 无法承载中间产物。

