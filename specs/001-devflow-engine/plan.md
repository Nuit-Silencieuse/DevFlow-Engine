# 实施计划: DevFlow Engine

**分支**: `001-devflow-engine` | **日期**: 2026-04-27 | **规范**: [spec.md](./spec.md)
**输入**: 来自 `/specs/001-devflow-engine/spec.md` 的功能规范

## 摘要

本项目旨在构建一个高度容错、可追溯且具备极致交互体验的 AI 驱动软件研发流水线引擎（DevFlow Engine）。核心采用“控制平面 + 执行平面 + 本地沙箱平面”的架构，利用 Temporal 实现基于持久化执行的流水线调度与状态管理，利用 LangGraph 实现复杂的多智能体拓扑与状态回溯，利用 Babel 注入和本地守护进程实现网页端“所见即所得”的代码修改与热更新预览。

## 技术背景

**语言/版本**: Java 17+ (控制平面), Python 3.10+ (执行平面), TypeScript (沙箱前端/Daemon)
**主要依赖**: Spring Boot, Temporal Java SDK, FastAPI, LangGraph, Vite, Babel
**存储**: PostgreSQL (Temporal/状态持久化), Milvus (向量存储), Elasticsearch (代码检索)
**测试**: JUnit 5, PyTest, Jest
**目标平台**: Linux 容器化部署 (服务端), 现代浏览器端 (沙箱交互)
**项目类型**: Web 服务 + CLI 守护进程 + 前端应用
**性能目标**: 流水线状态流转延迟 < 1s, 网页端圈选交互及热更新生效 < 3s
**约束条件**: 需要能处理长周期（可能长达数小时）的大语言模型生成任务而不丢失状态
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
├── contracts/           # 阶段 1 输出
└── tasks.md             # 阶段 2 输出 (由 /speckit.tasks 生成)
```

### 源代码(仓库根目录)

```
DevFlow-Engine/
├── control-plane/       # Java Spring Boot + Temporal Client (引擎核心)
│   ├── src/main/java/
│   │   ├── workflow/    # Temporal 工作流定义
│   │   ├── activity/    # 活动接口契约
│   │   └── api/         # 供前端调用的 RESTful API
├── execution-plane/     # Python FastAPI + LangGraph (AI 智能体)
│   ├── src/
│   │   ├── agents/      # 多智能体定义
│   │   ├── graph/       # 状态拓扑图定义
│   │   └── workers/     # Temporal Worker 实现
├── sandbox/             # 本地交互与沙箱
│   ├── frontend/        # 控制台 UI 与 SPA
│   ├── injector/        # Vite 插件与 Content Script (Babel AST 处理)
│   └── daemon/          # Node.js 本地守护进程 (文件修改与 GitOps)
└── docker/              # 部署脚本 (包含 Temporal, Postgres, Milvus 等)
```

**结构决策**: 采用标准的微服务结构，按技术栈和职责划分为三大独立模块，通过 Temporal 和 REST API 进行通信解耦。

## 复杂度跟踪

> **仅在章程检查有必须证明的违规时填写**

| 违规 | 为什么需要 | 拒绝更简单替代方案的原因 |
|-----------|------------|-------------------------------------|
| 引入多种技术栈和中间件 | 业务场景涉及复杂的流程调度(需Java/Temporal)和AI应用(需Python/LangGraph) | 如果仅用单体语言（如全 Python 或全 Java），要么在流程调度上难以实现强一致性和容错，要么在 AI 生态上缺乏成熟的组件（如 LangGraph）支持。 |
