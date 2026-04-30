---
description: "基于设计制品生成的开发任务列表"
---

# 任务列表: DevFlow Engine

**来源**: 根据 `/specs/001-devflow-engine/` 中的设计文档生成
**前置条件**: plan.md(项目结构), spec.md(功能规范), research.md, data-model.md, contracts/

## 阶段 1: 设置与基础设施 (Phase 1: Setup)

**目标**: 建立三个平面的基础项目结构和依赖环境

- [x] T001 初始化项目基础目录结构 (`control-plane/`, `execution-plane/`, `sandbox/`, `docker/`)
- [x] T002 在 `docker/docker-compose.yml` 中配置并编写 Postgres, Temporal Server, Milvus 启动脚本
- [x] T003 初始化 Java Spring Boot 工程于 `control-plane/` 目录，配置 Temporal Client 依赖
- [x] T004 [P] 初始化 Python FastAPI 工程于 `execution-plane/` 目录，配置 LangGraph 与 Temporal Worker 依赖
- [x] T005 [P] 初始化 Node.js 工程于 `sandbox/daemon/` 目录，配置基础依赖
- [x] T006 建立基础的数据库连接并确保所有服务（控制平面，执行平面）可以成功连接中间件

---

## 阶段 2: 基础模型与接口契约 (Phase 2: Base Models & Contracts)

**目标**: 建立核心实体模型，数据库迁移脚本和 API 契约骨架，阻断后续并行开发

- [x] T007 在 Postgres 中创建 Pipeline, Stage, CheckpointFeedback 数据表迁移脚本
- [x] T008 [P] 在 Java 控制平面 `control-plane/src/main/java/.../model/` 中建立对应的实体类 (Pipeline, Stage)
- [x] T009 [P] 在 Python 执行平面 `execution-plane/src/graph/` 中定义 `DevFlowState` TypedDict
- [ ] T010 [P] 在 Java 控制平面 `control-plane/src/main/java/.../api/` 中建立流水线管理 REST API 的骨架 (Controller)
- [ ] T011 [P] 在 Node Daemon `sandbox/daemon/` 中建立接收修改请求的 API 端点骨架
- [ ] T012 定义 Temporal Workflow 接口 `DevFlowWorkflow` 及各个 Activity 的接口契约于 `control-plane/src/main/java/.../workflow/`

---

## 阶段 3: 用户故事 1 - 端到端研发流水线执行 [US1] (优先级: P1)

**目标**: 实现需求->设计->代码的核心状态流转和人工检查点

**独立测试**: 能够启动流水线，并在人工驳回时正确回退或恢复流程。

### 核心调度与控制平面实现
- [ ] T013 [US1] 在 Java 控制平面实现 `DevFlowWorkflowImpl`，编排 RequirementAnalysis, SystemDesign, CodeGeneration 活动
- [ ] T014 [US1] 在 Java 控制平面实现人工审批（Approve/Reject）的 Signal 处理逻辑
- [ ] T015 [US1] 实现创建流水线和查询状态的 API 端点逻辑 (`control-plane/.../api/PipelineController.java`)

### 执行平面与 Agent 拓扑实现
- [ ] T016 [P] [US1] 在 Python 执行平面实现 Temporal Worker 并注册 Activities (`execution-plane/src/workers/`)
- [ ] T017 [US1] 在 Python 执行平面使用 LangGraph 构建端到端的状态图拓扑 (`execution-plane/src/graph/flow.py`)
- [ ] T018 [US1] 实现负责需求分析的 Agent 节点逻辑 (`execution-plane/src/agents/requirement_agent.py`)
- [ ] T019 [US1] 实现负责方案设计的 Agent 节点逻辑 (`execution-plane/src/agents/design_agent.py`)
- [ ] T020 [US1] 实现负责代码生成的 Agent 节点逻辑 (`execution-plane/src/agents/coder_agent.py`)
- [ ] T021 [US1] 实现负责测试生成的 Agent 节点逻辑 (`execution-plane/src/agents/test_agent.py`)
- [ ] T022 [US1] 实现负责代码评审的 Agent 节点逻辑 (`execution-plane/src/agents/review_agent.py`)
- [ ] T023 [US1] 实现负责交付集成的 Agent 节点逻辑 (`execution-plane/src/agents/delivery_agent.py`)
- [ ] T024 [US1] 集成 Checkpointer 支持 LangGraph 图状态的回溯和人工反馈注入

### 前端/控制台 (极简版)
- [ ] T025 [P] [US1] 初始化极简前端控制台应用 (`sandbox/frontend/`)，实现触发流水线和提交 Reject/Approve 反馈的 UI。

---

## 阶段 4: 用户故事 2 - 网页端“所见即所得”的修改与预览 [US2] (优先级: P1)

**目标**: 在目标页面通过悬浮框下发修改指令，本地自动修改代码并触发热更新

**独立测试**: 圈选测试页面的元素，输入自然语言指令，验证源码被修改且页面自动刷新。

### 浏览器端注入 (Sandbox Injector)
- [ ] T026 [US2] 开发 Vite/Babel 插件在目标网页的 DOM 元素上注入 `__source` 属性 (`sandbox/injector/vite-plugin-source.ts`)
- [ ] T027 [US2] 开发前端悬浮交互控件（Content Script 组件），实现 DOM 圈选与源码信息提取 (`sandbox/injector/content-script.tsx`)
- [ ] T028 [US2] 在悬浮控件中实现自然语言输入对话框，并调用本地 Daemon 的修改 API。

### 本地守护进程 (Local Daemon)
- [ ] T029 [P] [US2] 在 Node Daemon 中实现接收 AST 源码路径及指令的 API 逻辑 (`sandbox/daemon/src/api.ts`)
- [ ] T030 [US2] 实现 Daemon 读取本地文件，调用 LLM (或转发给执行平面) 生成对应代码节点的 Diff (`sandbox/daemon/src/coder.ts`)
- [ ] T031 [US2] 实现 Daemon 覆写本地文件系统，配合 Vite 触发 HMR (`sandbox/daemon/src/file_manager.ts`)
- [ ] T032 [US2] 实现修改确认后自动创建 Git 分支、Commit 及生成 MR 的逻辑 (`sandbox/daemon/src/git_ops.ts`)

---

## 阶段 5: 收尾与横切关注点 (Phase 5: Wrap-up)

**目标**: 文档、测试和演示准备。

- [ ] T033 编写集成测试脚本，模拟一个完整的六阶段流水线 API 调用链路
- [ ] T034 [P] 整理完整的技术方案设计文档，补充系统架构图
- [ ] T035 更新安装与运行指南 (`quickstart.md` 或 `README.md`)
- [ ] T036 准备并录制最终的端到端演示示例（满足赛题演示要求）

---

## 依赖图

1. 基础设施设置 (T001-T006)
2. 基础模型与接口 (T007-T012)
   - 依赖: 基础设施设置
3. 端到端流水线 [US1] (T013-T025)
   - 依赖: 基础模型与接口
4. 网页沙箱交互 [US2] (T026-T032)
   - 依赖: 无严格依赖，可在阶段 2 完成后独立与 US1 并行开发。
5. 收尾工作 (T033-T036)
   - 依赖: 所有功能阶段完成
