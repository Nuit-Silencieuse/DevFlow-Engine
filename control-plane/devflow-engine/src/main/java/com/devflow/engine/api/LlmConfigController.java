package com.devflow.engine.api;

import com.devflow.engine.service.LlmCredentialService;
import com.devflow.engine.service.PipelineService;
import java.util.List;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.util.StringUtils;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PatchMapping;
import org.springframework.web.bind.annotation.PathVariable;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
@RequestMapping("/api/v1")
public class LlmConfigController {
    private final LlmCredentialService credentialService;
    private final PipelineService pipelineService;

    public LlmConfigController(LlmCredentialService credentialService, PipelineService pipelineService) {
        this.credentialService = credentialService;
        this.pipelineService = pipelineService;
    }

    @PostMapping("/llm/credentials")
    public ResponseEntity<LlmCredentialResponse> createCredential(@RequestBody LlmCredentialRequest request) {
        return ResponseEntity
            .status(HttpStatus.CREATED)
            .body(credentialService.createCredential(request.provider(), request.apiKey()));
    }

    @GetMapping("/llm/credentials")
    public ResponseEntity<List<LlmCredentialResponse>> listCredentials() {
        return ResponseEntity.ok(credentialService.listCredentials());
    }

    @GetMapping("/llm/credentials/{credentialId}/secret")
    public ResponseEntity<LlmCredentialSecretResponse> getCredentialSecret(@PathVariable String credentialId) {
        return ResponseEntity.ok(credentialService.getSecret(credentialId));
    }

    @PostMapping("/llm/test")
    public ResponseEntity<LlmConfigTestResponse> testConfig(@RequestBody LlmConfigTestRequest request) {
        LlmProviderConfig config = request.config();
        if (config == null || !StringUtils.hasText(config.provider())) {
            throw new IllegalArgumentException("LLM provider is required.");
        }
        if (!StringUtils.hasText(config.model())) {
            throw new IllegalArgumentException("LLM model is required.");
        }
        if (!StringUtils.hasText(config.apiKey()) && !StringUtils.hasText(config.credentialId())) {
            throw new IllegalArgumentException("LLM apiKey or credentialId is required for connection test.");
        }
        return ResponseEntity.ok(new LlmConfigTestResponse(
            "READY",
            "LLM config shape is valid. Runtime worker will perform the real provider request.",
            config.provider(),
            config.model()
        ));
    }

    @PatchMapping("/pipelines/{id}/llm-config")
    public ResponseEntity<LlmRuntimeConfig> updatePipelineLlmConfig(
        @PathVariable UUID id,
        @RequestBody LlmRuntimeConfig config
    ) {
        return ResponseEntity.ok(pipelineService.updateLlmConfig(id, config));
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
