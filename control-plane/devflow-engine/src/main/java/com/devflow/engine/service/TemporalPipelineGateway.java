package com.devflow.engine.service;

import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import java.util.UUID;

public interface TemporalPipelineGateway {
    void startPipeline(DevFlowWorkflowInput input);

    void signalCheckpoint(UUID pipelineId, String stageName, CheckpointDecision decision, String feedback);
}
