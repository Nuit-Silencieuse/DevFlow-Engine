package com.devflow.engine.api;

import java.util.Map;

public record LlmRuntimeConfig(
    LlmProviderConfig defaultConfig,
    Map<String, LlmProviderConfig> stageOverrides
) {
}
