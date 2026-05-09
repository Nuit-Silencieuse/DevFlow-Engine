package com.devflow.engine.api;

public record LlmCredentialSecretResponse(
    String credentialId,
    String provider,
    String apiKey
) {
}
