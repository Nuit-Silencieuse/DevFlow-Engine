package com.devflow.engine.workflow;

import java.util.Map;

public record StageExecutionResult(
    String stageName,
    String status,
    Map<String, Object> outputPayload
) {
}
