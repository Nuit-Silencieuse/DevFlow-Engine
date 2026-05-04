# 实施计划: 工具调用式渐进探索 Agent

**分支**: `002-progressive-code-exploration` | **日期**: 2026-05-04 | **规格**: `specs/002-progressive-code-exploration/spec.md`
**输入**: 来自 `/specs/002-progressive-code-exploration/spec.md` 的功能规格

## 摘要

将 T019 的“路径驱动代码上下文工具”升级为“工具调用式渐进探索 Agent”。用户在常规使用中只需要提供需求文本和目标代码库根目录；Agent 基于需求自主执行文件发现、关键词检索、定向读取、证据归纳和充分性判断。`includePaths`、`excludePaths`、预算上限等参数保留为高级选项，用于限制范围、提升速度或保护隐私。

本计划不引入向量库或 Elasticsearch 作为主路径。第一阶段采用本地文件系统工具 + LLM 规划/反思循环，优先保证可解释、可测试、低基础设施依赖，并与现有 RequirementAgent、LLM Trace、Temporal 活动和控制平面阶段输出对齐。

## 技术上下文

**Language/Version**: Python 3.10+, Java 17+, TypeScript  
**Primary Dependencies**: LangGraph, Temporal Python SDK, project LLM Client, Spring Boot, Temporal Java SDK, JPA, Vite  
**Storage**: Existing Pipeline/Stage output payload, LLM trace files, H2/PostgreSQL through existing control plane  
**Testing**: Python unittest, JUnit/Mockito, Vitest  
**Target Platform**: Windows local development with WSL Docker infrastructure  
**Project Type**: web + workflow engine + execution agent  

**语言/版本**: Python 3.10+（执行平面、LangGraph Agent、上下文工具）；Java 17+（控制平面、Spring Boot、Temporal Java SDK）；TypeScript/Vite（沙箱前端）  
**主要依赖**: LangGraph、Temporal Python SDK、项目内 LLM Client、Spring Boot、Temporal Java SDK、JPA/H2/PostgreSQL、Vite  
**存储**: 复用现有 Pipeline/Stage 输出载荷；探索过程作为阶段中间产物和 LLM trace 输出，不要求新增数据库迁移  
**测试**: Python `unittest` 为主；Java JUnit/Mockito 验证 API 合约和阶段输出；前端 Vitest 验证展示逻辑  
**目标平台**: 本地 Windows + WSL Docker 测试环境；执行平面读取本地仓库目录  
**项目类型**: 多模块工作流系统，包含控制平面、执行平面和沙箱前端  
**性能目标**: 默认探索预算在中等仓库上可控完成；单次需求分析默认不超过配置的轮次、文件数和字节数  
**约束**: 必须限制在用户指定仓库根目录内；默认排除依赖、构建产物、缓存、日志、密钥文件；输出必须包含可追踪证据  
**规模/范围**: 面向 1 个目标代码库的一次需求分析探索；后续可扩展为跨仓库或索引增强检索

## 章程检查

当前 `.specify/memory/constitution.md` 仍是模板内容，没有定义可执行的项目章程门禁。因此本计划采用项目已经形成的工程约束：

- 以 TDD 推进实现：先补充上下文工具与 RequirementAgent 行为测试，再实现代码。
- 真实验证：至少使用本项目代码库作为目标仓库执行一次渐进探索测试。
- 可观测性：输出 LLM 消息、工具调用轨迹、读取文件清单、证据摘要和预算消耗。
- 安全边界：所有路径解析必须在仓库根目录内完成，高级 include/exclude 只收窄范围，不扩大权限。

## 项目结构

### 本功能制品

```text
specs/002-progressive-code-exploration/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── api.md
│   └── context-tools.md
└── checklists/
    └── requirements.md
```

### 预计代码影响范围

```text
execution-plane/
├── src/
│   ├── agents/
│   │   └── requirement_agent.py        # 将一次性上下文注入升级为规划-工具-反思循环
│   ├── context/
│   │   ├── repository_context.py       # 扩展文件发现/搜索/范围读取/预算统计
│   │   └── __init__.py
│   └── graph/
│       └── state.py                    # 如有必要，补充探索轨迹字段
└── tests/
    ├── test_repository_context.py
    └── test_requirement_agent.py

control-plane/devflow-engine/
├── src/main/java/com/devflow/engine/api/
│   ├── CreatePipelineRequest.java      # repository 仅要求 rootPath，include/exclude 为高级选项
│   └── RepositoryContext.java
└── src/test/java/com/devflow/engine/
    └── ...                             # API 合约与阶段输出测试

sandbox/frontend/
└── src/
    ├── api.ts                          # 类型随 API 合约调整
    └── ...                             # 展示探索中间产物、证据和工具轨迹
```

## 复杂度跟踪

| 风险 | 采用原因 | 简化方案 |
|------|----------|----------|
| LLM 工具循环比路径聚合复杂 | 用户明确要求仿照 Codex/Claude Code 的渐进式披露，避免手写 include/exclude | 先实现有限轮次、有限工具集、可观测 trace，不做长期记忆和索引服务 |
| 不引入 Elasticsearch/向量库可能影响超大仓库召回 | 当前目标是基础能力，减少测试环境依赖 | 后续在工具层增加可选索引工具，不改变 Agent 状态机 |
| 自动读取代码存在隐私/性能风险 | 默认自动探索是核心体验 | 根目录沙箱、默认排除、预算上限、trace 可审计 |

## 阶段产物

- Phase 0: `research.md`，记录渐进探索、检索策略、预算和安全决策。
- Phase 1: `data-model.md`、`contracts/api.md`、`contracts/context-tools.md`、`quickstart.md`。
- Phase 2: 后续 `/speckit-tasks` 将拆分为 TDD 测试、上下文工具升级、RequirementAgent 集成、控制平面/前端展示和真实仓库验证任务。
