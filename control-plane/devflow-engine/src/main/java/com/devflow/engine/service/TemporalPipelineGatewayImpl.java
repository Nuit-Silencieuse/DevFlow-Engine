package com.devflow.engine.service;

import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.CheckpointSignal;
import com.devflow.engine.workflow.DevFlowWorkflow;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
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

    static String workflowId(UUID pipelineId) {
        return "devflow-pipeline-" + pipelineId;
    }
}
