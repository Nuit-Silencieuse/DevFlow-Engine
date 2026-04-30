package com.devflow.engine.workflow;

import java.util.Map;
import java.util.UUID;

public record StageExecutionRequest(
    UUID pipelineId,
    String stageName,
    String requirement,
    Map<String, Object> globalContext,
    Map<String, Object> previousOutput
) {
}
