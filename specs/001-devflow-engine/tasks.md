---
description: "基于设计制品生成的开发任务列表"
---

# 任务列表: DevFlow Engine

**来源**: 根据 `/specs/001-devflow-engine/` 中的设计文档生成
**前置条件**: plan.md、spec.md、research.md、data-model.md、contracts/

## 阶段 1: 设置与基础设施 (Phase 1: Setup)

**目标**: 建立三个平面的基础项目结构和依赖环境。

- [x] T001 初始化项目基础目录结构 (`control-plane/`, `execution-plane/`, `sandbox/`, `docker/`)
- [x] T002 在 `docker/docker-compose.yml` 中配置并编写 Postgres、Temporal Server、Milvus 启动脚本
- [x] T003 初始化 Java Spring Boot 工程于 `control-plane/` 目录，配置 Temporal Client 依赖
- [x] T004 [P] 初始化 Python FastAPI 工程于 `execution-plane/` 目录，配置 LangGraph 与 Temporal Worker 依赖
- [x] T005 [P] 初始化 Node.js 工程于 `sandbox/daemon/` 目录，配置基础依赖
- [x] T006 建立基础数据库连接，并确保控制平面和执行平面可以成功连接中间件

---

## 阶段 2: 基础模型与接口契约 (Phase 2: Base Models & Contracts)

**目标**: 建立核心实体模型、数据库迁移脚本和 API 契约骨架，阻断后续并行开发。

- [x] T007 在 Postgres 中创建 Pipeline、Stage、CheckpointFeedback 数据表迁移脚本
- [x] T008 [P] 在 Java 控制平面 `control-plane/src/main/java/.../model/` 中建立对应实体类 (Pipeline, Stage)
- [x] T009 [P] 在 Python 执行平面 `execution-plane/src/graph/` 中定义 `DevFlowState` TypedDict
- [x] T010 [P] 在 Java 控制平面 `control-plane/src/main/java/.../api/` 中建立流水线管理 REST API 的骨架 (Controller)
- [x] T011 [P] 在 Node Daemon `sandbox/daemon/` 中建立接收修改请求的 API 端点骨架
- [x] T012 定义 Temporal Workflow 接口 `DevFlowWorkflow` 及各个 Activity 的接口契约于 `control-plane/src/main/java/.../workflow/`

---

## 阶段 3: 用户故事 1 - 端到端研发流水线执行 [US1] (优先级: P1)

**目标**: 实现需求到设计再到代码的核心状态流转和人工检查点。

**独立测试**: 能够启动流水线，并在人工驳回时正确回退或恢复流程。

### 核心调度与控制平面实现

- [x] T013 [US1] 在 Java 控制平面实现 `DevFlowWorkflowImpl`，编排 RequirementAnalysis、SystemDesign、CodeGeneration 活动
- [x] T014 [US1] 在 Java 控制平面实现人工审批 (Approve/Reject) 的 Signal 处理逻辑
- [x] T015 [US1] 实现创建流水线和查询状态的 API 端点逻辑 (`control-plane/.../api/PipelineController.java`)

### 执行平面与 Agent 拓扑实现

- [x] T016 [P] [US1] 在 Python 执行平面实现 Temporal Worker 并注册 Activities (`execution-plane/src/workers/`)
- [x] T017 [US1] 在 Python 执行平面使用 LangGraph 构建端到端的状态图拓扑 (`execution-plane/src/graph/flow.py`)
- [x] T018 [US1] 在 Java 控制平面扩展流水线创建/查询契约，支持 `repository` 上下文 (`rootPath`, `includePaths`, `excludePaths`, `targetFiles`, `maxFiles`, `maxBytes`) 并写入 `Pipeline.global_context`
- [x] T019 [US1] 在 Python 执行平面实现路径驱动的代码库上下文工具 (`execution-plane/src/context/`)，支持目录遍历、文件读取、文本搜索和上下文打包
- [x] T020 [US1] 在 Java 控制平面实现阶段中间产物落库与展示通道，将 Activity `outputPayload` 同步到 `Stage.output_payload` 并通过状态查询/检查点 API 暴露
- [x] T021 [US1] 在 Python 执行平面实现可配置 LLM 调用客户端 (`execution-plane/src/llm/`)，支持至少两个 Provider、运行时切换、结构化 JSON 输出和可测试的 Fake Provider
- [x] T022 [US1] 实现负责需求分析的 Agent 节点逻辑 (`execution-plane/src/agents/requirement_agent.py`)
- [ ] T023 [US1] 实现负责方案设计的 Agent 节点逻辑 (`execution-plane/src/agents/design_agent.py`)
- [ ] T024 [US1] 实现负责代码生成的 Agent 节点逻辑 (`execution-plane/src/agents/coder_agent.py`)
- [ ] T025 [US1] 实现负责测试生成的 Agent 节点逻辑 (`execution-plane/src/agents/test_agent.py`)
- [ ] T026 [US1] 实现负责代码评审的 Agent 节点逻辑 (`execution-plane/src/agents/review_agent.py`)
- [ ] T027 [US1] 实现负责交付集成的 Agent 节点逻辑 (`execution-plane/src/agents/delivery_agent.py`)
- [ ] T028 [US1] 集成 Checkpointer 支持 LangGraph 图状态的回溯和人工反馈注入

### 前端/控制台极简版

- [ ] T029 [P] [US1] 初始化极简前端控制台应用 (`sandbox/frontend/`)，实现触发流水线、展示阶段产物并提交 Reject/Approve 反馈的 UI

---

## 阶段 4: 用户故事 2 - 网页端“所见即所得”的修改与预览 [US2] (优先级: P1)

**目标**: 在目标页面通过悬浮框下发修改指令，本地自动修改代码并触发热更新。

**独立测试**: 圈选测试页面的元素，输入自然语言指令，验证源码被修改且页面自动刷新。

### 浏览器端注入 (Sandbox Injector)

- [ ] T030 [US2] 开发 Vite/Babel 插件，在目标网页的 DOM 元素上注入 `__source` 属性 (`sandbox/injector/vite-plugin-source.ts`)
- [ ] T031 [US2] 开发前端悬浮交互控件 (Content Script 组件)，实现 DOM 圈选与源码信息提取 (`sandbox/injector/content-script.tsx`)
- [ ] T032 [US2] 在悬浮控件中实现自然语言输入对话框，并调用本地 Daemon 的修改 API

### 本地守护进程 (Local Daemon)

- [ ] T033 [P] [US2] 在 Node Daemon 中实现接收 AST 源码路径及指令的 API 逻辑 (`sandbox/daemon/src/api.ts`)
- [ ] T034 [US2] 实现 Daemon 读取本地文件，调用 LLM 或转发给执行平面生成对应代码节点的 Diff (`sandbox/daemon/src/coder.ts`)
- [ ] T035 [US2] 实现 Daemon 覆写本地文件系统，配合 Vite 触发 HMR (`sandbox/daemon/src/file_manager.ts`)
- [ ] T036 [US2] 实现修改确认后自动创建 Git 分支、Commit 及生成 MR 的逻辑 (`sandbox/daemon/src/git_ops.ts`)

---

## 阶段 5: 收尾与横切关注点 (Phase 5: Wrap-up)

**目标**: 文档、测试和演示准备。

- [ ] T037 编写集成测试脚本，模拟一个完整的六阶段流水线 API 调用链路
- [ ] T038 [P] 整理完整的技术方案设计文档，补充系统架构图
- [ ] T039 更新安装与运行指南 (`quickstart.md` 或 `README.md`)
- [ ] T040 准备并录制最终的端到端演示示例，满足赛题演示要求

---

## 依赖图

1. 基础设施设置 (T001-T006)
2. 基础模型与接口契约 (T007-T012)
   - 依赖: 基础设施设置
3. 端到端流水线 [US1] (T013-T029)
   - 依赖: 基础模型与接口契约
   - 代码感知、产物展示和 LLM 客户端基础能力 (T018-T021) 必须先于真实 Agent 逻辑 (T022-T027)
4. 网页沙箱交互 [US2] (T030-T036)
   - 依赖: 阶段 2 完成后即可并行开发
5. 收尾工作 (T037-T040)
   - 依赖: 所有功能阶段完成
