package com.devflow.engine.workflow;

import java.util.List;
import java.util.Map;
import java.util.UUID;

public record DevFlowWorkflowInput(
    UUID pipelineId,
    String name,
    String requirement,
    List<String> stages,
    Map<String, Object> globalContext
) {
}
