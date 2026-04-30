package com.devflow.engine.api;

import java.util.List;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/pipelines")
public class PipelineController {
    @PostMapping
    public ResponseEntity<CreatePipelineResponse> createPipeline(@RequestBody CreatePipelineRequest request) {
        return ResponseEntity
            .status(HttpStatus.CREATED)
            .body(new CreatePipelineResponse(UUID.randomUUID(), "RUNNING"));
    }

    @GetMapping("/{id}")
    public ResponseEntity<ErrorResponse> getPipeline(@PathVariable UUID id) {
        return ResponseEntity
            .status(HttpStatus.NOT_IMPLEMENTED)
            .body(new ErrorResponse(
                "NOT_IMPLEMENTED",
                "Pipeline status query will be implemented in T015."
            ));
    }

    @PostMapping("/{id}/checkpoints/{stageName}")
    public ResponseEntity<ErrorResponse> submitCheckpointDecision(
        @PathVariable UUID id,
        @PathVariable String stageName,
        @RequestBody CheckpointDecisionRequest request
    ) {
        return ResponseEntity
            .status(HttpStatus.NOT_IMPLEMENTED)
            .body(new ErrorResponse(
                "NOT_IMPLEMENTED",
                "Checkpoint signal handling will be implemented in T014."
            ));
    }

    public record CreatePipelineRequest(
        String name,
        String requirement,
        List<String> stages
    ) {
    }

    public record CreatePipelineResponse(
        UUID pipelineId,
        String status
    ) {
    }

    public record CheckpointDecisionRequest(
        String decision,
        String feedback
    ) {
    }

    public record ErrorResponse(
        String code,
        String message
    ) {
    }
}
