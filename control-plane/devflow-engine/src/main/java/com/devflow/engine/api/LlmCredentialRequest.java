package com.devflow.engine.api;

public record LlmCredentialRequest(
    String provider,
    String apiKey
) {
}
