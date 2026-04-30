package com.devflow.engine.api;

public record CheckpointDecisionRequest(
    String decision,
    String feedback
) {
}
