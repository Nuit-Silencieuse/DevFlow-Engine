package com.devflow.engine.api;

import java.util.UUID;

public record CreatePipelineResponse(
    UUID pipelineId,
    String workflowId,
    String status
) {
}
