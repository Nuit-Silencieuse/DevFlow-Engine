package com.devflow.engine.api;

public record LlmCredentialResponse(
    String credentialId,
    String provider,
    String maskedApiKey
) {
}
