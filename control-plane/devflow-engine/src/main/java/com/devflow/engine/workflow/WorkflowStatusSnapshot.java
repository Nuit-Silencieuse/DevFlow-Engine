package com.devflow.engine.workflow;

import java.util.List;
import java.util.UUID;

public record WorkflowStatusSnapshot(
    UUID pipelineId,
    String status,
    String currentStage,
    List<StageExecutionResult> stages
) {
}
