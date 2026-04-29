# 数据模型与状态定义

## 核心实体模型 (PostgreSQL)

### 1. Pipeline (流水线实例)
表示一次研发任务的执行全流程。
- `id` (UUID): 唯一标识
- `name` (String): 流水线名称
- `status` (Enum): PENDING, RUNNING, SUSPENDED, COMPLETED, FAILED
- `current_stage` (String): 当前处于哪一个阶段
- `created_at`, `updated_at` (Timestamp)
- `global_context` (JSONB): 全局上下文信息，如存储需求描述、相关代码库信息等

### 2. Stage (执行阶段)
定义了流水线中的每个环节及其绑定的 Agent。
- `id` (UUID): 唯一标识
- `pipeline_id` (UUID): 关联的流水线
- `name` (String): 阶段名称 (如 REQUIREMENT_ANALYSIS, CODE_GENERATION)
- `agent_role` (String): 负责该阶段的 Agent 角色标识
- `requires_human_approval` (Boolean): 是否需要人工审批
- `status` (Enum): PENDING, RUNNING, COMPLETED, FAILED, REJECTED
- `output_payload` (JSONB): 当前阶段执行完毕后的输出数据

### 3. CheckpointFeedback (人工干预记录)
- `id` (UUID): 唯一标识
- `stage_id` (UUID): 关联的执行阶段
- `decision` (Enum): APPROVE, REJECT
- `feedback_reason` (Text): 当拒绝时，人类提供的修改意见
- `created_at` (Timestamp)

## LangGraph 共享状态类型 (TypedDict)

在 Python 执行平面流转的全局状态定义：
```python
class DevFlowState(TypedDict):
    original_requirement: str        # 初始需求
    structured_prd: dict             # 结构化需求分析结果
    design_doc: dict                 # 方案设计与文件清单
    diff_patch: str                  # 代码变更集
    test_results: dict               # 测试生成与运行结果
    review_report: dict              # 代码评审报告
    delivery_status: dict            # 交付集成结果(如 MR 链接)
    human_feedback: str              # 驳回时的修改意见
    current_step: str                # 当前图节点
    error_logs: list[str]            # 异常日志列表
```