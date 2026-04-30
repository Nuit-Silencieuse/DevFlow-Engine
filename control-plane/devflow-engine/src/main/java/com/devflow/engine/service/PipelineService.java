package com.devflow.engine.service;

import com.devflow.engine.api.CheckpointDecisionRequest;
import com.devflow.engine.api.CheckpointDecisionResponse;
import com.devflow.engine.api.CreatePipelineRequest;
import com.devflow.engine.api.CreatePipelineResponse;
import com.devflow.engine.api.PipelineStatusResponse;
import com.devflow.engine.api.StageStatusResponse;
import com.devflow.engine.model.Pipeline;
import com.devflow.engine.model.PipelineStatus;
import com.devflow.engine.model.Stage;
import com.devflow.engine.repository.PipelineRepository;
import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import jakarta.transaction.Transactional;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.UUID;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class PipelineService {
    private static final List<String> DEFAULT_STAGES = List.of(
        "REQUIREMENT_ANALYSIS",
        "SYSTEM_DESIGN",
        "CODE_GENERATION"
    );

    private final PipelineRepository pipelineRepository;
    private final TemporalPipelineGateway temporalPipelineGateway;

    public PipelineService(PipelineRepository pipelineRepository, TemporalPipelineGateway temporalPipelineGateway) {
        this.pipelineRepository = pipelineRepository;
        this.temporalPipelineGateway = temporalPipelineGateway;
    }

    @Transactional
    public CreatePipelineResponse createPipeline(CreatePipelineRequest request) {
        validateCreateRequest(request);
        List<String> requestedStages = normalizeStages(request.stages());

        Pipeline pipeline = new Pipeline(request.name());
        pipeline.setId(UUID.randomUUID());
        pipeline.setStatus(PipelineStatus.RUNNING);
        pipeline.setCurrentStage(requestedStages.get(0));
        pipeline.setGlobalContext(createGlobalContext(request.requirement(), requestedStages));
        requestedStages.forEach(stageName -> pipeline.addStage(createStage(stageName)));

        Pipeline saved = pipelineRepository.save(pipeline);
        temporalPipelineGateway.startPipeline(new DevFlowWorkflowInput(
            saved.getId(),
            saved.getName(),
            request.requirement(),
            requestedStages,
            saved.getGlobalContext()
        ));

        return new CreatePipelineResponse(saved.getId(), saved.getStatus().name());
    }

    @Transactional
    public PipelineStatusResponse getPipeline(UUID id) {
        Pipeline pipeline = pipelineRepository.findById(id)
            .orElseThrow(() -> new NoSuchElementException("Pipeline not found: " + id));

        List<String> requestedOrder = readRequestedStageOrder(pipeline);
        List<StageStatusResponse> stages = pipeline.getStages().stream()
            .sorted((left, right) -> Integer.compare(
                stageOrder(requestedOrder, left.getName()),
                stageOrder(requestedOrder, right.getName())
            ))
            .map(stage -> new StageStatusResponse(
                stage.getName(),
                stage.getStatus().name(),
                stage.isRequiresHumanApproval(),
                stage.getOutputPayload()
            ))
            .toList();

        return new PipelineStatusResponse(
            pipeline.getId(),
            pipeline.getStatus().name(),
            pipeline.getCurrentStage(),
            stages
        );
    }

    @Transactional
    public CheckpointDecisionResponse submitCheckpointDecision(
        UUID pipelineId,
        String stageName,
        CheckpointDecisionRequest request
    ) {
        if (!pipelineRepository.existsById(pipelineId)) {
            throw new NoSuchElementException("Pipeline not found: " + pipelineId);
        }
        CheckpointDecision decision = parseDecision(request.decision());
        temporalPipelineGateway.signalCheckpoint(pipelineId, stageName, decision, request.feedback());
        return new CheckpointDecisionResponse("RUNNING", "Signal received. Pipeline resuming or re-routing.");
    }

    static Stage createStage(String stageName) {
        Stage stage = new Stage(stageName, agentRole(stageName));
        stage.setRequiresHumanApproval("SYSTEM_DESIGN".equals(stageName));
        return stage;
    }

    private static void validateCreateRequest(CreatePipelineRequest request) {
        if (request == null || !StringUtils.hasText(request.name()) || !StringUtils.hasText(request.requirement())) {
            throw new IllegalArgumentException("Pipeline name and requirement are required.");
        }
    }

    private static List<String> normalizeStages(List<String> stages) {
        if (stages == null || stages.isEmpty()) {
            return DEFAULT_STAGES;
        }
        List<String> normalized = stages.stream()
            .filter(StringUtils::hasText)
            .map(String::trim)
            .toList();
        if (normalized.isEmpty()) {
            return DEFAULT_STAGES;
        }
        return normalized;
    }

    private static Map<String, Object> createGlobalContext(String requirement, List<String> stages) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("original_requirement", requirement);
        context.put("requested_stages", stages);
        return context;
    }

    @SuppressWarnings("unchecked")
    private static List<String> readRequestedStageOrder(Pipeline pipeline) {
        Object value = pipeline.getGlobalContext().get("requested_stages");
        if (value instanceof List<?> list) {
            return list.stream().map(String::valueOf).toList();
        }
        return DEFAULT_STAGES;
    }

    private static int stageOrder(List<String> requestedOrder, String stageName) {
        int index = requestedOrder.indexOf(stageName);
        return index >= 0 ? index : Integer.MAX_VALUE;
    }

    private static CheckpointDecision parseDecision(String value) {
        try {
            return CheckpointDecision.valueOf(String.valueOf(value).trim().toUpperCase());
        } catch (RuntimeException ex) {
            throw new IllegalArgumentException("Checkpoint decision must be APPROVE or REJECT.");
        }
    }

    private static String agentRole(String stageName) {
        return switch (stageName) {
            case "REQUIREMENT_ANALYSIS" -> "requirement_agent";
            case "SYSTEM_DESIGN" -> "design_agent";
            case "CODE_GENERATION" -> "coder_agent";
            case "TEST_GENERATION" -> "test_agent";
            case "CODE_REVIEW" -> "review_agent";
            case "DELIVERY_INTEGRATION" -> "delivery_agent";
            default -> "generic_agent";
        };
    }
}
