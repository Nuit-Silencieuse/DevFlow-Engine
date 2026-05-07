package com.devflow.engine.api;

public record StageSummaryResponse(
    String name,
    String status,
    boolean requiresHumanApproval,
    boolean outputAvailable,
    String artifactRevision
) {
}
