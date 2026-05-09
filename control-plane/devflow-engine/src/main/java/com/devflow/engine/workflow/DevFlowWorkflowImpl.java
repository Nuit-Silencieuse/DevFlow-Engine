package com.devflow.engine.workflow;

import io.temporal.activity.ActivityOptions;
import io.temporal.common.RetryOptions;
import io.temporal.workflow.Workflow;
import java.time.Duration;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

public class DevFlowWorkflowImpl implements DevFlowWorkflow {
    private static final List<String> DEFAULT_STAGES = List.of(
        "REQUIREMENT_ANALYSIS",
        "SYSTEM_DESIGN",
        "CODE_GENERATION",
        "TEST_GENERATION",
        "APPLY_AND_RUN_TESTS",
        "CODE_REVIEW"
    );

    private final DevFlowActivities activities;
    private final List<StageExecutionResult> stageResults = new ArrayList<>();
    private DevFlowWorkflowInput input;
    private WorkflowStatusSnapshot status;
    private CheckpointSignal checkpointSignal;
    private Map<String, Object> latestLlmConfig = new LinkedHashMap<>();

    public DevFlowWorkflowImpl() {
        this(Workflow.newActivityStub(
            DevFlowActivities.class,
            ActivityOptions.newBuilder()
                .setStartToCloseTimeout(Duration.ofMinutes(30))
                .setRetryOptions(RetryOptions.newBuilder()
                    .setMaximumAttempts(5)
                    .build())
                .build()
        ));
    }

    public DevFlowWorkflowImpl(DevFlowActivities activities) {
        this.activities = activities;
    }

    @Override
    public DevFlowWorkflowResult start(DevFlowWorkflowInput input) {
        this.input = input;
        stageResults.clear();

        Map<String, Object> globalContext = new LinkedHashMap<>();
        if (input.globalContext() != null) {
            globalContext.putAll(input.globalContext());
        }
        globalContext.putIfAbsent("original_requirement", input.requirement());
        latestLlmConfig = copyMap(globalContext.get("llm_config"));

        List<String> stages = input.stages() == null || input.stages().isEmpty() ? DEFAULT_STAGES : input.stages();
        Map<String, Object> previousOutput = Map.of();
        Map<String, Object> accumulatedOutput = new LinkedHashMap<>();
        updateStatus("RUNNING", stages.get(0));

        for (int stageIndex = 0; stageIndex < stages.size(); stageIndex++) {
            String stageName = stages.get(stageIndex);
            StageExecutionResult result = executeStage(stageName, globalContext, previousOutput);
            previousOutput = result.outputPayload();
            accumulatedOutput.putAll(result.outputPayload());

            if (requiresHumanApproval(stageName)) {
                CheckpointSignal decision = waitForHumanDecision(stageName);
                while (decision.decision() == CheckpointDecision.REJECT) {
                    stageResults.add(new StageExecutionResult(
                        stageName,
                        "REJECTED",
                        Map.of("feedback", safeFeedback(decision.feedbackReason()))
                    ));
                    globalContext.put("human_feedback", safeFeedback(decision.feedbackReason()));
                    globalContext.put("rejected_stage", stageName);

                    if ("CODE_REVIEW".equals(stageName)) {
                        stageIndex = rollbackToCodeGeneration(stages);
                        previousOutput = Map.copyOf(accumulatedOutput);
                        updateStatus("RUNNING", "CODE_GENERATION");
                        break;
                    }

                    result = executeStage(stageName, globalContext, previousOutput);
                    previousOutput = result.outputPayload();
                    accumulatedOutput.putAll(result.outputPayload());
                    decision = waitForHumanDecision(stageName);
                }
            }
        }

        String currentStage = stageResults.isEmpty() ? null : stageResults.get(stageResults.size() - 1).stageName();
        updateStatus("COMPLETED", currentStage);
        return new DevFlowWorkflowResult(
            input.pipelineId(),
            "COMPLETED",
            currentStage
        );
    }

    @Override
    public void approveCheckpoint(CheckpointSignal signal) {
        checkpointSignal = new CheckpointSignal(
            signal.pipelineId(),
            signal.stageName(),
            CheckpointDecision.APPROVE,
            signal.feedbackReason()
        );
    }

    @Override
    public void rejectCheckpoint(CheckpointSignal signal) {
        checkpointSignal = new CheckpointSignal(
            signal.pipelineId(),
            signal.stageName(),
            CheckpointDecision.REJECT,
            signal.feedbackReason()
        );
    }

    @Override
    public void updateLlmConfig(LlmConfigSignal signal) {
        latestLlmConfig = copyMap(signal.llmConfig());
    }

    @Override
    public WorkflowStatusSnapshot getStatus() {
        return status;
    }

    protected CheckpointSignal waitForCheckpointDecision(String stageName) {
        checkpointSignal = null;
        Workflow.await(() -> checkpointSignal != null && stageName.equals(checkpointSignal.stageName()));
        CheckpointSignal signal = checkpointSignal;
        checkpointSignal = null;
        return signal;
    }

    private StageExecutionResult executeStage(
        String stageName,
        Map<String, Object> globalContext,
        Map<String, Object> previousOutput
    ) {
        updateStatus("RUNNING", stageName);
        if (latestLlmConfig == null || latestLlmConfig.isEmpty()) {
            globalContext.remove("llm_config");
        } else {
            globalContext.put("llm_config", Map.copyOf(latestLlmConfig));
        }
        StageExecutionRequest request = new StageExecutionRequest(
            input.pipelineId(),
            stageName,
            input.requirement(),
            Map.copyOf(globalContext),
            previousOutput == null ? Map.of() : Map.copyOf(previousOutput)
        );
        StageExecutionResult result = switch (stageName) {
            case "REQUIREMENT_ANALYSIS" -> activities.analyzeRequirement(request);
            case "SYSTEM_DESIGN" -> activities.designSystem(request);
            case "CODE_GENERATION" -> activities.generateCode(request);
            case "TEST_GENERATION" -> activities.generateTests(request);
            case "APPLY_AND_RUN_TESTS" -> activities.applyAndRunTests(request);
            case "CODE_REVIEW" -> activities.reviewCode(request);
            case "DELIVERY_INTEGRATION" -> activities.integrateDelivery(request);
            default -> throw new IllegalArgumentException("Unsupported workflow stage: " + stageName);
        };
        stageResults.add(result);
        updateStatus("RUNNING", stageName);
        return result;
    }

    private CheckpointSignal waitForHumanDecision(String stageName) {
        updateStatus("SUSPENDED", stageName);
        return waitForCheckpointDecision(stageName);
    }

    private static boolean requiresHumanApproval(String stageName) {
        return "SYSTEM_DESIGN".equals(stageName)
            || "CODE_GENERATION".equals(stageName)
            || "TEST_GENERATION".equals(stageName)
            || "CODE_REVIEW".equals(stageName);
    }

    private static int rollbackToCodeGeneration(List<String> stages) {
        int codeGenerationIndex = stages.indexOf("CODE_GENERATION");
        if (codeGenerationIndex < 0) {
            throw new IllegalStateException("CODE_REVIEW reject requires CODE_GENERATION stage in workflow");
        }
        return codeGenerationIndex - 1;
    }

    private void updateStatus(String statusName, String currentStage) {
        if (input == null) {
            return;
        }
        status = new WorkflowStatusSnapshot(
            input.pipelineId(),
            statusName,
            currentStage,
            List.copyOf(stageResults)
        );
    }

    private static String safeFeedback(String feedback) {
        return feedback == null ? "" : feedback;
    }

    private static Map<String, Object> copyMap(Object value) {
        if (!(value instanceof Map<?, ?> map)) {
            return new LinkedHashMap<>();
        }
        Map<String, Object> result = new LinkedHashMap<>();
        map.forEach((key, item) -> result.put(String.valueOf(key), item));
        return result;
    }
}
