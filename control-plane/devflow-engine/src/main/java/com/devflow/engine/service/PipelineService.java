package com.devflow.engine.service;

import com.devflow.engine.api.CheckpointDecisionRequest;
import com.devflow.engine.api.CheckpointDecisionResponse;
import com.devflow.engine.api.CreatePipelineRequest;
import com.devflow.engine.api.CreatePipelineResponse;
import com.devflow.engine.api.PipelineStatusResponse;
import com.devflow.engine.api.RepositoryContext;
import com.devflow.engine.api.StageStatusResponse;
import com.devflow.engine.model.Pipeline;
import com.devflow.engine.model.PipelineStatus;
import com.devflow.engine.model.Stage;
import com.devflow.engine.model.StageStatus;
import com.devflow.engine.repository.PipelineRepository;
import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import com.devflow.engine.workflow.StageExecutionResult;
import com.devflow.engine.workflow.WorkflowStatusSnapshot;
import jakarta.transaction.Transactional;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.NoSuchElementException;
import java.util.Optional;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;
import java.util.UUID;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.stereotype.Service;
import org.springframework.util.StringUtils;

@Service
public class PipelineService {
    private static final Logger log = LoggerFactory.getLogger(PipelineService.class);
    private static final long WORKFLOW_SNAPSHOT_TIMEOUT_SECONDS = 3;

    private static final int DEFAULT_MAX_FILES = 200;
    private static final long DEFAULT_MAX_BYTES = 1_048_576L;

    private static final List<String> DEFAULT_STAGES = List.of(
        "REQUIREMENT_ANALYSIS",
        "SYSTEM_DESIGN",
        "CODE_GENERATION",
        "TEST_GENERATION",
        "APPLY_AND_RUN_TESTS"
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
        RepositoryContext repository = normalizeRepository(request.repository());

        Pipeline pipeline = new Pipeline(request.name());
        pipeline.setId(UUID.randomUUID());
        pipeline.setStatus(PipelineStatus.RUNNING);
        pipeline.setCurrentStage(requestedStages.get(0));
        pipeline.setGlobalContext(createGlobalContext(request.requirement(), requestedStages, repository));
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

        synchronizeWorkflowSnapshot(pipeline);
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
            readRepositoryContext(pipeline),
            stages
        );
    }

    @Transactional
    public CheckpointDecisionResponse submitCheckpointDecision(
        UUID pipelineId,
        String stageName,
        CheckpointDecisionRequest request
    ) {
        Pipeline pipeline = pipelineRepository.findById(pipelineId)
            .orElseThrow(() -> new NoSuchElementException("Pipeline not found: " + pipelineId));
        synchronizeWorkflowSnapshot(pipeline);

        CheckpointDecision decision = parseDecision(request.decision());
        temporalPipelineGateway.signalCheckpoint(pipelineId, stageName, decision, request.feedback());

        Map<String, Object> stageOutput = pipeline.getStages().stream()
            .filter(stage -> stage.getName().equals(stageName))
            .findFirst()
            .map(Stage::getOutputPayload)
            .orElse(Map.of());
        return new CheckpointDecisionResponse(
            "RUNNING",
            "Signal received. Pipeline resuming or re-routing.",
            stageName,
            stageOutput
        );
    }

    static Stage createStage(String stageName) {
        Stage stage = new Stage(stageName, agentRole(stageName));
        stage.setRequiresHumanApproval(requiresHumanApproval(stageName));
        return stage;
    }

    private static boolean requiresHumanApproval(String stageName) {
        return "SYSTEM_DESIGN".equals(stageName)
            || "CODE_GENERATION".equals(stageName)
            || "TEST_GENERATION".equals(stageName);
    }

    private void synchronizeWorkflowSnapshot(Pipeline pipeline) {
        /*
         * 控制台查询状态时应优先展示 Temporal Workflow 的最新快照，但不能把 UI 的可用性完全绑定到
         * Workflow Query 上。真实开发环境中可能只启动了控制平面和基础设施，还没有启动承载
         * DevFlowWorkflowImpl 的 Worker；这时 Temporal 会保留已启动的 workflow execution，但查询可能返回
         * “buffered query cleared” 或超时。这里退回到数据库里已经持久化的 Pipeline/Stage 快照，让前端仍能
         * 看到流水线已创建、当前阶段、repository 上下文和历史阶段产物。
         */
        try {
            readWorkflowSnapshotWithTimeout(pipeline.getId())
                .ifPresent(snapshot -> applyWorkflowSnapshot(pipeline, snapshot));
        } catch (RuntimeException ex) {
            log.warn(
                "Temporal workflow snapshot is unavailable for pipeline {}. Returning persisted database snapshot: {}",
                pipeline.getId(),
                ex.toString()
            );
            log.debug("Temporal workflow snapshot query failed.", ex);
        }
    }

    private Optional<WorkflowStatusSnapshot> readWorkflowSnapshotWithTimeout(UUID pipelineId) {
        /*
         * Temporal 的 Query 在 workflow execution 已创建但尚无 Worker 承载时，可能会等待服务端 buffered query
         * 清理后才失败。控制台的状态刷新不应被这个等待拖住，因此这里给“读取最新 Workflow 快照”设置短超时。
         * 超时并不代表流水线不存在，只代表本次无法拿到内存态快照；数据库快照仍然是有效的展示来源。
         */
        return CompletableFuture
            .supplyAsync(() -> temporalPipelineGateway.getStatus(pipelineId))
            .orTimeout(WORKFLOW_SNAPSHOT_TIMEOUT_SECONDS, TimeUnit.SECONDS)
            .exceptionally(ex -> {
                log.warn(
                    "Temporal workflow snapshot query timed out or failed for pipeline {}. Returning persisted database snapshot: {}",
                    pipelineId,
                    ex.toString()
                );
                log.debug("Temporal workflow snapshot query failed.", ex);
                return Optional.empty();
            })
            .join();
    }

    private void applyWorkflowSnapshot(Pipeline pipeline, WorkflowStatusSnapshot snapshot) {
        /*
         * T020 的核心是“阶段产物展示”，而阶段产物的原始来源是 Activity 返回的
         * StageExecutionResult.outputPayload。Temporal Workflow 会把这些结果保存在
         * WorkflowStatusSnapshot.stages 中；控制平面查询状态或提交检查点时，把这份快照
         * 同步进数据库的 Stage.output_payload。
         *
         * 这里没有在 Workflow 内直接写数据库，原因是 Temporal Workflow 代码必须保持确定性。
         * 普通数据库 IO 会受到网络、事务、重试时机影响，不适合放在 Workflow 线程中。
         * 因此同步逻辑放在 Spring Service 层，由 API 读写路径触发，并用数据库实体作为
         * UI 和后续查询的稳定快照。
         */
        if (snapshot.status() != null) {
            pipeline.setStatus(parsePipelineStatus(snapshot.status()));
        }
        if (snapshot.currentStage() != null) {
            pipeline.setCurrentStage(snapshot.currentStage());
        }

        if (snapshot.stages() != null) {
            snapshot.stages().forEach(result -> applyStageExecutionResult(pipeline, result));
        }
        pipelineRepository.save(pipeline);
    }

    private static void applyStageExecutionResult(Pipeline pipeline, StageExecutionResult result) {
        /*
         * Workflow 快照中的 stages 是按执行时间追加的日志，而数据库 stages 表按阶段名保持
         * 每个阶段一行。SYSTEM_DESIGN 被驳回后可能会出现多条同名结果；这里按快照顺序覆盖，
         * 让数据库保留“该阶段当前最新可展示产物”。历史驳回记录后续由 CheckpointFeedback
         * 和更完整的审计日志承担。
         */
        if (result == null || result.stageName() == null) {
            return;
        }
        pipeline.getStages().stream()
            .filter(stage -> stage.getName().equals(result.stageName()))
            .findFirst()
            .ifPresent(stage -> {
                stage.setStatus(parseStageStatus(result.status()));
                stage.setOutputPayload(copyOutputPayload(result.outputPayload()));
            });
    }

    private static Map<String, Object> copyOutputPayload(Map<String, Object> outputPayload) {
        if (outputPayload == null || outputPayload.isEmpty()) {
            return new LinkedHashMap<>();
        }
        return new LinkedHashMap<>(outputPayload);
    }

    private static PipelineStatus parsePipelineStatus(String value) {
        try {
            return PipelineStatus.valueOf(String.valueOf(value).trim().toUpperCase());
        } catch (RuntimeException ex) {
            return PipelineStatus.RUNNING;
        }
    }

    private static StageStatus parseStageStatus(String value) {
        try {
            return StageStatus.valueOf(String.valueOf(value).trim().toUpperCase());
        } catch (RuntimeException ex) {
            return StageStatus.RUNNING;
        }
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

    private static Map<String, Object> createGlobalContext(
        String requirement,
        List<String> stages,
        RepositoryContext repository
    ) {
        Map<String, Object> context = new LinkedHashMap<>();
        context.put("original_requirement", requirement);
        context.put("requested_stages", stages);
        if (repository != null) {
            context.put("repository", repositoryToMap(repository));
        }
        return context;
    }

    private static RepositoryContext normalizeRepository(RepositoryContext repository) {
        if (repository == null) {
            return null;
        }
        if (!StringUtils.hasText(repository.rootPath())) {
            throw new IllegalArgumentException("Repository rootPath is required when repository context is provided.");
        }
        return new RepositoryContext(
            repository.rootPath().trim(),
            normalizePathList(repository.includePaths()),
            normalizePathList(repository.excludePaths()),
            normalizePathList(repository.targetFiles()),
            repository.maxRounds(),
            repository.maxFiles() == null || repository.maxFiles() <= 0 ? DEFAULT_MAX_FILES : repository.maxFiles(),
            repository.maxBytes() == null || repository.maxBytes() <= 0 ? DEFAULT_MAX_BYTES : repository.maxBytes(),
            repository.maxSearchResults(),
            repository.privacyMode()
        );
    }

    private static List<String> normalizePathList(List<String> paths) {
        if (paths == null || paths.isEmpty()) {
            return List.of();
        }
        return paths.stream()
            .filter(StringUtils::hasText)
            .map(String::trim)
            .toList();
    }

    private static Map<String, Object> repositoryToMap(RepositoryContext repository) {
        Map<String, Object> value = new LinkedHashMap<>();
        value.put("rootPath", repository.rootPath());
        value.put("includePaths", repository.includePaths());
        value.put("excludePaths", repository.excludePaths());
        value.put("targetFiles", repository.targetFiles());
        value.put("maxRounds", repository.maxRounds());
        value.put("maxFiles", repository.maxFiles());
        value.put("maxBytes", repository.maxBytes());
        value.put("maxSearchResults", repository.maxSearchResults());
        value.put("privacyMode", repository.privacyMode());
        return value;
    }

    private static RepositoryContext readRepositoryContext(Pipeline pipeline) {
        Object value = pipeline.getGlobalContext().get("repository");
        if (!(value instanceof Map<?, ?> repository)) {
            return null;
        }
        return new RepositoryContext(
            stringValue(repository.get("rootPath")),
            stringList(repository.get("includePaths")),
            stringList(repository.get("excludePaths")),
            stringList(repository.get("targetFiles")),
            intValue(repository.get("maxRounds")),
            intValue(repository.get("maxFiles")),
            longValue(repository.get("maxBytes")),
            intValue(repository.get("maxSearchResults")),
            stringValue(repository.get("privacyMode"))
        );
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

    private static String stringValue(Object value) {
        return value == null ? null : String.valueOf(value);
    }

    private static List<String> stringList(Object value) {
        if (value instanceof List<?> list) {
            return list.stream().map(String::valueOf).toList();
        }
        return List.of();
    }

    private static Integer intValue(Object value) {
        if (value instanceof Number number) {
            return number.intValue();
        }
        if (value == null) {
            return null;
        }
        return Integer.valueOf(String.valueOf(value));
    }

    private static Long longValue(Object value) {
        if (value instanceof Number number) {
            return number.longValue();
        }
        if (value == null) {
            return null;
        }
        return Long.valueOf(String.valueOf(value));
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
            case "APPLY_AND_RUN_TESTS" -> "apply_and_run_tests_agent";
            case "CODE_REVIEW" -> "review_agent";
            case "DELIVERY_INTEGRATION" -> "delivery_agent";
            default -> "generic_agent";
        };
    }
}
