package com.devflow.engine.api;

import java.util.Map;

public record StageStatusResponse(
    String name,
    String status,
    boolean requiresHumanApproval,
    Map<String, Object> output
) {
}
