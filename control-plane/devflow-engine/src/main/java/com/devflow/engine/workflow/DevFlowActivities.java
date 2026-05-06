package com.devflow.engine.workflow;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;

@ActivityInterface
public interface DevFlowActivities {
    @ActivityMethod(name = "analyzeRequirement")
    StageExecutionResult analyzeRequirement(StageExecutionRequest request);

    @ActivityMethod(name = "designSystem")
    StageExecutionResult designSystem(StageExecutionRequest request);

    @ActivityMethod(name = "generateCode")
    StageExecutionResult generateCode(StageExecutionRequest request);

    @ActivityMethod(name = "generateTests")
    StageExecutionResult generateTests(StageExecutionRequest request);

    @ActivityMethod(name = "applyAndRunTests")
    StageExecutionResult applyAndRunTests(StageExecutionRequest request);

    @ActivityMethod(name = "reviewCode")
    StageExecutionResult reviewCode(StageExecutionRequest request);

    @ActivityMethod(name = "integrateDelivery")
    StageExecutionResult integrateDelivery(StageExecutionRequest request);
}
