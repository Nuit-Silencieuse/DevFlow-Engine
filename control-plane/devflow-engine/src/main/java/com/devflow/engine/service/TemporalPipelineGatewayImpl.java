package com.devflow.engine.service;

import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.CheckpointSignal;
import com.devflow.engine.workflow.DevFlowWorkflow;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import com.devflow.engine.workflow.WorkflowStatusSnapshot;
import io.temporal.client.WorkflowNotFoundException;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import java.util.Optional;
import java.util.UUID;
import org.springframework.stereotype.Component;

@Component
public class TemporalPipelineGatewayImpl implements TemporalPipelineGateway {
    public static final String TASK_QUEUE = "DEVFLOW_TASK_QUEUE";

    private final WorkflowClient workflowClient;

    public TemporalPipelineGatewayImpl(WorkflowClient workflowClient) {
        this.workflowClient = workflowClient;
    }

    @Override
    public void startPipeline(DevFlowWorkflowInput input) {
        DevFlowWorkflow workflow = workflowClient.newWorkflowStub(
            DevFlowWorkflow.class,
            WorkflowOptions.newBuilder()
                .setTaskQueue(TASK_QUEUE)
                .setWorkflowId(workflowId(input.pipelineId()))
                .build()
        );
        WorkflowClient.start(workflow::start, input);
    }

    @Override
    public void signalCheckpoint(UUID pipelineId, String stageName, CheckpointDecision decision, String feedback) {
        DevFlowWorkflow workflow = workflowClient.newWorkflowStub(DevFlowWorkflow.class, workflowId(pipelineId));
        CheckpointSignal signal = new CheckpointSignal(pipelineId, stageName, decision, feedback);
        if (decision == CheckpointDecision.APPROVE) {
            workflow.approveCheckpoint(signal);
        } else {
            workflow.rejectCheckpoint(signal);
        }
    }

    @Override
    public Optional<WorkflowStatusSnapshot> getStatus(UUID pipelineId) {
        /*
         * 控制平面自身持有数据库快照，但真正的阶段执行结果首先产生在 Temporal Workflow 中。
         * 这里通过 Workflow Query 读取内存中的 WorkflowStatusSnapshot，然后由 PipelineService
         * 决定如何落库。这样可以保持两个边界:
         *
         * 1. Workflow 代码不直接访问数据库，避免破坏 Temporal 对 Workflow 确定性的要求。
         * 2. 查询失败或 Workflow 尚未创建时，不影响数据库里已有快照的读取能力。
         */
        try {
            DevFlowWorkflow workflow = workflowClient.newWorkflowStub(DevFlowWorkflow.class, workflowId(pipelineId));
            return Optional.ofNullable(workflow.getStatus());
        } catch (WorkflowNotFoundException ex) {
            return Optional.empty();
        }
    }

    static String workflowId(UUID pipelineId) {
        return "devflow-pipeline-" + pipelineId;
    }
}
