package com.devflow.engine.api;

import java.util.List;

public record CreatePipelineRequest(
    String name,
    String requirement,
    List<String> stages,
    RepositoryContext repository,
    LlmRuntimeConfig llmConfig
) {
}
