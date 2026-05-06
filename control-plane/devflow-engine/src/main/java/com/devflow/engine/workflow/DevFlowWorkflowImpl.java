package com.devflow.engine.workflow;

import io.temporal.activity.ActivityOptions;
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
        "APPLY_AND_RUN_TESTS"
    );

    private final DevFlowActivities activities;
    private final List<StageExecutionResult> stageResults = new ArrayList<>();
    private DevFlowWorkflowInput input;
    private WorkflowStatusSnapshot status;
    private CheckpointSignal checkpointSignal;

    public DevFlowWorkflowImpl() {
        this(Workflow.newActivityStub(
            DevFlowActivities.class,
            ActivityOptions.newBuilder()
                .setStartToCloseTimeout(Duration.ofMinutes(30))
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

        List<String> stages = input.stages() == null || input.stages().isEmpty() ? DEFAULT_STAGES : input.stages();
        Map<String, Object> previousOutput = Map.of();
        updateStatus("RUNNING", stages.get(0));

        for (String stageName : stages) {
            StageExecutionResult result = executeStage(stageName, globalContext, previousOutput);
            previousOutput = result.outputPayload();

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
                    updateStatus("RUNNING", stageName);

                    result = executeStage(stageName, globalContext, previousOutput);
                    previousOutput = result.outputPayload();
                    decision = waitForHumanDecision(stageName);
                }
            }
        }

        String currentStage = stageResults.isEmpty() ? null : stageResults.get(stageResults.size() - 1).stageName();
        updateStatus("COMPLETED", currentStage);
        return new DevFlowWorkflowResult(
            input.pipelineId(),
            "COMPLETED",
            currentStage,
            List.copyOf(stageResults)
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
            || "TEST_GENERATION".equals(stageName);
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
}
