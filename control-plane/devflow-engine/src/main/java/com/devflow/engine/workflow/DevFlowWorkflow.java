package com.devflow.engine.workflow;

import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;

@WorkflowInterface
public interface DevFlowWorkflow {
    @WorkflowMethod
    DevFlowWorkflowResult start(DevFlowWorkflowInput input);

    @SignalMethod
    void approveCheckpoint(CheckpointSignal signal);

    @SignalMethod
    void rejectCheckpoint(CheckpointSignal signal);

    @SignalMethod
    void updateLlmConfig(LlmConfigSignal signal);

    @QueryMethod
    WorkflowStatusSnapshot getStatus();
}
