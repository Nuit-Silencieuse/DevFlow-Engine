package com.devflow.engine.api;

import java.util.Map;
import java.util.UUID;

public record StageArtifactResponse(
    UUID pipelineId,
    String stageName,
    String status,
    boolean requiresHumanApproval,
    String artifactRevision,
    Map<String, Object> output
) {
}
