package com.devflow.engine.api;

public record CheckpointDecisionResponse(
    String status,
    String message
) {
}
