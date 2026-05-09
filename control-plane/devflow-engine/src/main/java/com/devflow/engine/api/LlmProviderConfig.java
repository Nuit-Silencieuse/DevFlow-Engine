package com.devflow.engine.api;

public record LlmProviderConfig(
    String provider,
    String baseUrl,
    String apiKey,
    String credentialId,
    String model,
    Integer timeoutSeconds,
    Integer maxTokens,
    Double temperature
) {
}
