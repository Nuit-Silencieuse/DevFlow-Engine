package com.devflow.engine.api;

import com.devflow.engine.service.PipelineService;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1/pipelines")
public class PipelineController {
    private final PipelineService pipelineService;

    public PipelineController(PipelineService pipelineService) {
        this.pipelineService = pipelineService;
    }

    @PostMapping
    public ResponseEntity<CreatePipelineResponse> createPipeline(@RequestBody CreatePipelineRequest request) {
        return ResponseEntity
            .status(HttpStatus.CREATED)
            .body(pipelineService.createPipeline(request));
    }

    @GetMapping("/{id}")
    public ResponseEntity<PipelineStatusResponse> getPipeline(@PathVariable UUID id) {
        return ResponseEntity.ok(pipelineService.getPipeline(id));
    }

    @PostMapping("/{id}/checkpoints/{stageName}")
    public ResponseEntity<CheckpointDecisionResponse> submitCheckpointDecision(
        @PathVariable UUID id,
        @PathVariable String stageName,
        @RequestBody CheckpointDecisionRequest request
    ) {
        return ResponseEntity.ok(pipelineService.submitCheckpointDecision(id, stageName, request));
    }

    @ExceptionHandler(NoSuchElementException.class)
    public ResponseEntity<ErrorResponse> handleNotFound(NoSuchElementException ex) {
        return ResponseEntity
            .status(HttpStatus.NOT_FOUND)
            .body(new ErrorResponse("NOT_FOUND", ex.getMessage()));
    }

    @ExceptionHandler(IllegalArgumentException.class)
    public ResponseEntity<ErrorResponse> handleBadRequest(IllegalArgumentException ex) {
        return ResponseEntity
            .badRequest()
            .body(new ErrorResponse("INVALID_REQUEST", ex.getMessage()));
    }
}
