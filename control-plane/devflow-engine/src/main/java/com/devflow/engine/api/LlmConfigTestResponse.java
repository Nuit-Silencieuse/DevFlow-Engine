package com.devflow.engine.api;

public record LlmConfigTestResponse(
    String status,
    String message,
    String provider,
    String model
) {
}
