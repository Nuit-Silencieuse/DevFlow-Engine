package com.devflow.engine.api;

import java.time.OffsetDateTime;
import java.util.List;
import java.util.UUID;

public record PipelineSummaryResponse(
    UUID pipelineId,
    String workflowId,
    String status,
    String currentStage,
    RepositoryContext repository,
    OffsetDateTime updatedAt,
    List<StageSummaryResponse> stages
) {
}
