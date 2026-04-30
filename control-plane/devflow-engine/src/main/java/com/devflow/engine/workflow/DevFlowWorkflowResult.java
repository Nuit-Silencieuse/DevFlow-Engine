package com.devflow.engine.workflow;

import java.util.List;
import java.util.UUID;

public record DevFlowWorkflowResult(
    UUID pipelineId,
    String status,
    String currentStage,
    List<StageExecutionResult> stageResults
) {
}
