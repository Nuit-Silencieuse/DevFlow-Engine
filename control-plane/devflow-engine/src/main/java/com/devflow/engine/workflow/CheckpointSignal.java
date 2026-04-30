package com.devflow.engine.workflow;

import java.util.UUID;

public record CheckpointSignal(
    UUID pipelineId,
    String stageName,
    CheckpointDecision decision,
    String feedbackReason
) {
}
