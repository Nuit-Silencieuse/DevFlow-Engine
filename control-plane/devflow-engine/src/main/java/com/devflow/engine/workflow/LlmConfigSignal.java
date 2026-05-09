package com.devflow.engine.workflow;

import java.util.Map;
import java.util.UUID;

public record LlmConfigSignal(
    UUID pipelineId,
    Map<String, Object> llmConfig
) {
}
