package com.devflow.engine.api;

import java.util.List;
import java.util.UUID;

public record PipelineStatusResponse(
    UUID pipelineId,
    String status,
    String currentStage,
    List<StageStatusResponse> stages
) {
}
