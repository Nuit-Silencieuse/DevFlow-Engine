# 实施计划: DevFlow Engine

**分支**: `001-devflow-engine` | **日期**: 2026-04-27 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/001-devflow-engine/spec.md` 的功能规范

## 摘要

本项目旨在构建一个高度容错、可追溯且具备极致交互体验的 AI 驱动软件研发流水线引擎（DevFlow Engine）。核心采用“控制平面 + 执行平面 + 本地沙箱平面”的架构，利用 Temporal 实现基于持久化执行的流水线调度与状态管理，利用 LangGraph 实现复杂的多智能体拓扑与状态回溯，利用“路径驱动 + 工具调用式渐进探索”实现项目代码感知，并利用 Babel 注入和本地守护进程实现网页端“所见即所得”的代码修改与热更新预览。

## 技术背景

**语言/版本**: Java 17+ (控制平面), Python 3.10+ (执行平面), TypeScript (沙箱前端/Daemon)
**主要依赖**: Spring Boot, Temporal Java SDK, FastAPI, LangGraph, Vite, Babel
**存储**: PostgreSQL (Temporal/流水线状态/阶段产物持久化), Milvus (可选向量存储), Elasticsearch (可选代码检索增强)
**测试**: JUnit 5, PyTest, Jest
**目标平台**: Linux 容器化部署 (服务端), 现代浏览器端 (沙箱交互)
**项目类型**: Web 服务 + CLI 守护进程 + 前端应用
**性能目标**: 流水线状态流转延迟 < 1s, 网页端圈选交互及热更新生效 < 3s
**约束条件**: 需要能处理长周期（可能长达数小时）的大语言模型生成任务而不丢失状态；Agent 必须至少支持通过目录/文件路径感知目标代码库上下文
**规模/范围**: 支持多并发流水线实例，支持复杂的多智能体协作，前端注入需兼容主流 React 项目

## 章程检查

*门控: 必须在阶段 0 研究前通过. 阶段 1 设计后重新检查. *

1. **思考先行**: 是。技术选型（Temporal + LangGraph）充分考虑了长周期 AI 任务的脆弱性，放弃了简单的手写状态机。
2. **简洁至上**: 是。虽然引入了 Temporal，但它是解决持久化执行和自动回溯的最简单、最成熟的方案，避免了自行造轮子实现复杂的状态持久化和重试逻辑。
3. **手术刀式变更**: 是。项目是全新开发，模块划分清晰，互不干扰。
4. **目标驱动执行**: 是。规范和计划中已明确了端到端流转和前端圈选两个核心用户场景的验收标准。

## 项目结构

### 文档(此功能)

```
specs/001-devflow-engine/
├── plan.md              # 此文件
├── research.md          # 阶段 0 输出
├── data-model.md        # 阶段 1 输出
├── quickstart.md        # 阶段 1 输出
├── control-plane-architecture.md
├── execution-plane-architecture.md
├── agent-design.md
├── contracts/           # 阶段 1 输出
└── tasks.md             # 阶段 2 输出 (由 /speckit.tasks 生成)
```

### 源代码(仓库根目录)

```
DevFlow-Engine/
├── control-plane/       # Java Spring Boot + Temporal Client (引擎核心)
│   ├── src/main/java/
│   │   ├── workflow/    # Temporal 工作流定义
│   │   ├── service/     # Pipeline 服务、Temporal Gateway、产物同步逻辑
│   │   ├── repository/  # JPA 仓库
│   │   ├── model/       # Pipeline/Stage 等持久化实体
│   │   └── api/         # 供前端调用的 RESTful API
├── execution-plane/     # Python FastAPI + LangGraph (AI 智能体)
│   ├── src/
│   │   ├── agents/      # 多智能体定义
│   │   ├── context/     # 路径驱动代码感知、文件读取、文本搜索、上下文打包
│   │   ├── graph/       # 状态拓扑图定义
│   │   └── workers/     # Temporal Worker 实现
├── sandbox/             # 本地交互与沙箱
│   ├── frontend/        # 控制台 UI 与 SPA
│   ├── injector/        # Vite 插件与 Content Script (Babel AST 处理)
│   └── daemon/          # Node.js 本地守护进程 (文件修改与 GitOps)
└── docker/              # 部署脚本 (包含 Temporal, Postgres, Milvus 等)
```

**结构决策**: 采用标准的微服务结构，按技术栈和职责划分为三大独立模块，通过 Temporal 和 REST API 进行通信解耦。

## 代码感知方案

### 设计目标

赛题要求 Agent 能感知代码库上下文，并且至少支持通过目录/文件路径提供上下文。因此本项目采用分层策略:

1. **路径驱动上下文(MVP 必做)**: 用户在创建流水线时显式传入代码库根目录、包含路径、排除路径和目标文件列表。
2. **工具调用式渐进探索(主方案)**: Agent 不一次性读取整个仓库，而是通过受控工具按需遍历目录、读取文件、搜索文本和打包上下文。
3. **索引式混合检索(后续增强)**: 当仓库变大后，再引入 Elasticsearch 的 BM25/符号检索，并可选接入 Milvus 做语义召回。

当前优先实现 1 和 2。Elasticsearch/Milvus 作为 Good-to-have，不阻塞端到端演示。

### 控制平面契约

创建流水线 API 需要支持 `repository` 上下文，并持久化到 `Pipeline.global_context.repository`:

```json
{
  "name": "Add login feature",
  "requirement": "增加用户登录功能",
  "repository": {
    "rootPath": "D:/projects/demo-app",
    "includePaths": ["src", "package.json", "README.md"],
    "excludePaths": ["node_modules", "dist", "target", ".git"],
    "targetFiles": ["src/App.tsx"],
    "maxFiles": 200,
    "maxBytes": 1048576
  }
}
```

字段含义:

| 字段 | 含义 |
|------|------|
| `rootPath` | 目标代码库根目录，所有读取操作必须限制在该目录内 |
| `includePaths` | 允许 Agent 探索的目录或文件 |
| `excludePaths` | 必须跳过的目录或文件，如依赖目录、构建产物和 `.git` |
| `targetFiles` | 用户明确指定的重点文件 |
| `maxFiles` | 单阶段最多读取或打包的文件数 |
| `maxBytes` | 单阶段最多打包的文本字节数 |

控制平面只负责保存、校验和透传这些约束，不直接读取目标仓库文件。真正的文件访问由执行平面或本地沙箱平面在受控工具中完成。

### 执行平面工具

执行平面新增 `execution-plane/src/context/`，提供以下工具:

| 工具 | 作用 |
|------|------|
| `list_files` | 按 `rootPath/includePaths/excludePaths` 列出候选文件 |
| `read_file` | 读取指定文件或行范围，禁止越过 `rootPath` |
| `search_text` | 在允许路径内做关键词搜索 |
| `build_context_pack` | 根据任务、目标文件和搜索结果生成有限大小的上下文包 |

Agent 使用这些工具进行渐进式探索，并在中间产物中记录 `inspected_files`、`search_queries` 和 `relevant_symbols`，方便 UI 展示和技术答辩解释。

T019 已落地第一版路径驱动上下文工具:

- `RepositoryContext.from_mapping` 负责把控制平面 `repository` JSON 转换为执行平面对象。
- `list_files` 负责在 `includePaths` 和 `excludePaths` 约束下遍历仓库。
- `read_file` 负责安全读取仓库内文件，并支持按行截取。
- `search_text` 负责在允许路径内做大小写不敏感文本搜索。
- `build_context_pack` 负责按 `targetFiles`、显式路径和搜索命中文件构造受 `maxFiles`/`maxBytes` 限制的上下文包。
- 测试包含真实项目仓库检索，验证执行平面可以在当前代码库中找到 `RepositoryContext.java`。

## 中间产物落库与展示

### 产物类型

每个流水线阶段会生成结构化中间产物:

| 阶段 | 产物字段 |
|------|----------|
| 需求分析 | `structured_prd` |
| 方案设计 | `design_doc` |
| 代码生成 | `diff_patch` |
| 测试生成 | `test_results` |
| 代码评审 | `review_report` |
| 交付集成 | `delivery_status` |

### 落库策略

短期使用现有 `Stage.output_payload` JSONB 字段保存阶段产物，并同步更新:

| 字段 | 更新时机 |
|------|----------|
| `Pipeline.current_stage` | 阶段开始或暂停时 |
| `Pipeline.status` | 运行、暂停、完成或失败时 |
| `Stage.status` | 阶段开始、完成、失败或被驳回时 |
| `Stage.output_payload` | Activity 返回 `StageExecutionResult.outputPayload` 后 |

由于 Temporal Workflow 不应直接执行普通数据库 IO，控制平面应通过受 Temporal 管理的 Activity 或专门的状态同步服务完成数据库更新。这样可以保留 Temporal 的可重试语义，并避免 Workflow 代码违反确定性约束。

T020 采用“查询触发的状态同步服务”实现短期落库策略:

- `TemporalPipelineGateway.getStatus` 通过 Workflow Query 读取 `WorkflowStatusSnapshot`。
- `PipelineService.synchronizeWorkflowSnapshot` 在状态查询和检查点提交前同步快照。
- `StageExecutionResult.outputPayload` 写入同名 `Stage.output_payload`。
- 同名阶段多次执行时以最新结果覆盖，用于 UI 展示当前可审批或可查看的阶段产物。
- 检查点响应新增 `stageName` 和 `stageOutput`，避免前端提交审批后丢失当前审批上下文。

### 展示策略

控制平面查询 API 继续以 `PipelineStatusResponse` 返回阶段列表，并在每个 `StageStatusResponse.output` 中展示对应中间产物。检查点 API/UI 应在等待人工审批时展示当前阶段产物，例如:

- `SYSTEM_DESIGN` 检查点展示 `design_doc` 和代码上下文引用。
- `CODE_REVIEW` 检查点展示 `review_report`、`test_results` 和 diff 摘要。

后续前端控制台任务需要基于这些字段展示阶段产物、代码感知过程和 Approve/Reject 操作。

## LLM 调用客户端方案

T021 新增执行平面的可配置 LLM 调用客户端，详细设计见 [t021-llm-client-design.md](./t021-llm-client-design.md)。该能力是 T022-T027 真实 Agent 的公共基础设施，不绑定某个流水线阶段，也不直接替代 Temporal 或 LangGraph 的状态职责。

设计目标:

1. **Provider 可配置**: 至少支持两个不同模型提供商，首版建议实现 OpenAI-compatible 和 Anthropic-compatible Provider。
2. **运行时可切换**: 默认 Provider 和模型来自环境变量，单次 `LlmRequest` 可以覆盖 Provider 和模型，便于不同 Agent 使用不同模型策略。
3. **统一调用契约**: Agent 只依赖 `LlmClient`、`LlmRequest`、`LlmResponse` 和 `LlmProvider`，不直接依赖具体 SDK。
4. **结构化输出**: 支持 JSON schema 提示、响应解析、有限 JSON 修复和明确错误返回，保证阶段产物可以稳定写入 `outputPayload` 与 JSONB 字段。
5. **可测试性**: 提供 Fake Provider 用于 TDD 和离线测试，但 Fake Provider 只能通过显式测试配置启用，不能作为生产自动兜底。

执行平面新增目录:

```text
execution-plane/src/llm/
├── __init__.py
├── client.py      # LlmClient 门面，负责 Provider 选择、重试、结构化解析
├── config.py      # 环境变量与运行时配置
├── messages.py    # LlmMessage、LlmRequest、LlmResponse 等数据结构
├── providers.py   # OpenAI-compatible、Anthropic-compatible、Fake Provider
└── errors.py      # 统一异常类型，避免泄露密钥和底层实现细节
```

后续 Agent 接入方式:

- T022 Requirement Agent 使用 LLM Client 生成 `structured_prd`，不再支持 `RuleBasedRequirementAnalyzer`。
- T023 Design Agent 使用 LLM Client 生成 `design_doc`。
- T024-T027 继续复用同一客户端，根据任务类型选择 Provider、模型、温度和 JSON 输出 schema。
- Temporal 仍负责持久化调度、重试和人工回溯；LangGraph 仍负责单次 Activity 内的节点编排和状态增量；LLM Client 只负责模型调用、Provider 切换和结构化响应处理。

## 复杂度跟踪

> **仅在章程检查有必须证明的违规时填写**

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|-----------|------------|-------------------------------------|
| 引入多种技术栈和中间件 | 业务场景涉及复杂的流程调度(需Java/Temporal)和AI应用(需Python/LangGraph) | 如果仅用单体语言（如全 Python 或全 Java），要么在流程调度上难以实现强一致性和容错，要么在 AI 生态上缺乏成熟的组件（如 LangGraph）支持。 |
