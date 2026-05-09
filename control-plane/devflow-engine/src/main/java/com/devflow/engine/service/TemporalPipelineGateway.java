package com.devflow.engine.service;

import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import com.devflow.engine.workflow.WorkflowStatusSnapshot;
import java.util.Optional;
import java.util.UUID;

public interface TemporalPipelineGateway {
    void startPipeline(DevFlowWorkflowInput input);

    void signalCheckpoint(String workflowId, UUID pipelineId, String stageName, CheckpointDecision decision, String feedback);

    void updateLlmConfig(String workflowId, UUID pipelineId, Object llmConfig);

    /*
     * WorkflowStatusSnapshot 是控制平面把 Temporal 内部执行进度转换为数据库快照的桥。
     *
     * 返回 Optional 的原因是: API 查询不应该因为 Workflow 暂时不可查、尚未创建或已经被
     * Temporal 清理而彻底失败。Service 层拿不到快照时会继续返回数据库里已有的 Pipeline/Stage
     * 状态；拿到快照时才执行 T020 的 outputPayload 同步。
     */
    Optional<WorkflowStatusSnapshot> getStatus(String workflowId);
}
