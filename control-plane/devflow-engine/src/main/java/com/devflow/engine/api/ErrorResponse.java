package com.devflow.engine.api;

public record ErrorResponse(
    String code,
    String message
) {
}
