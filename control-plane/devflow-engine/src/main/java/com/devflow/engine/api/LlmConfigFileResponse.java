package com.devflow.engine.api;

import java.util.Map;

public record LlmConfigFileResponse(
    String path,
    Map<String, Object> config
) {
}
