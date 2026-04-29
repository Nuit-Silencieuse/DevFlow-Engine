# 阶段 0 研究与决策

基于《框架设计初稿》和赛题规范，本项目已对核心技术进行了预研，结果如下：

## 流水线调度引擎选型

**Decision**: 选用 Temporal
**Rationale**: 传统基于状态机或简单消息队列的架构难以处理长周期大语言模型任务的故障恢复与断点重试。Temporal 的持久化执行机制允许代码像写单线程阻塞应用一样处理长期任务，并且能原生地实现人工检查点的挂起与恢复，极大地降低了调度系统的复杂度。
**Alternatives considered**:
- Camunda: 过于重量级，更偏向于 BPMN，对于纯代码编排不够灵活。
- Redis + 状态机: 易于实现但难以维护长周期调度的分布式状态，且缺乏原生的信号唤醒机制。

## 多智能体协同架构选型

**Decision**: 选用 LangGraph (Python)
**Rationale**: Python 拥有最成熟的 AI 生态，而 LangGraph 提供了一种结构化的图（Graph）范式来管理多个 Agent 的状态流转。它的 Checkpointer 机制能够支持历史状态的时间旅行回溯，这完美契合了人类在检查点驳回代码时需要重做部分任务并注入反馈的场景。
**Alternatives considered**:
- AutoGen: 过于自由，难以定义严格的结构化拓扑来适配研发流程的固定环节。
- 纯 LangChain Chain: 缺乏复杂的循环和状态持久化图结构支持。

## 前端注入与沙箱修改机制

**Decision**: 使用 Vite/Babel 插件实现 AST 源码映射，通过 Content Script 获取 `__source` 进行本地修改
**Rationale**: 为了实现“所见即所得”且能在同一界面圈选编辑，必须将运行时的 DOM 节点映射回源代码的具体文件和行号。Babel 的 `@babel/plugin-transform-react-jsx-source` 提供了天然的支持。通过在开发环境中注入该属性，本地 Daemon 服务可以精准定位要修改的文件位置进行覆写，再配合 Vite 的 HMR 实现即时更新。
**Alternatives considered**:
- Chrome 扩展单独开发: 需要额外的扩展安装步骤，不够无缝。
- 依赖特定的组件库 ID: 不具备普适性，要求用户修改业务代码来配合。