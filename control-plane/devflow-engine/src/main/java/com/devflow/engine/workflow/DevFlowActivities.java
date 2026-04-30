package com.devflow.engine.workflow;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface DevFlowActivities {
    @ActivityMethod
    StageExecutionResult analyzeRequirement(StageExecutionRequest request);

    @ActivityMethod
    StageExecutionResult designSystem(StageExecutionRequest request);

    @ActivityMethod
    StageExecutionResult generateCode(StageExecutionRequest request);

    @ActivityMethod
    StageExecutionResult generateTests(StageExecutionRequest request);

    @ActivityMethod
    StageExecutionResult reviewCode(StageExecutionRequest request);

    @ActivityMethod
    StageExecutionResult integrateDelivery(StageExecutionRequest request);
}
