package com.devflow.engine.workflow;

import static org.assertj.core.api.Assertions.assertThat;

import java.util.ArrayDeque;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Queue;
import java.util.UUID;
import org.junit.jupiter.api.Test;

class DevFlowWorkflowImplTest {
    @Test
    void startRunsRequirementDesignAndCodeGenerationActivities() {
        FakeActivities activities = new FakeActivities();
        TestableWorkflow workflow = new TestableWorkflow(
            activities,
            List.of(
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "CODE_GENERATION", CheckpointDecision.APPROVE, null)
            )
        );

        DevFlowWorkflowResult result = workflow.start(input());

        assertThat(activities.calls).containsExactly(
            "REQUIREMENT_ANALYSIS",
            "SYSTEM_DESIGN",
            "CODE_GENERATION"
        );
        assertThat(result.status()).isEqualTo("COMPLETED");
        assertThat(result.currentStage()).isEqualTo("CODE_GENERATION");
        assertThat(workflow.getStatus().status()).isEqualTo("COMPLETED");
    }

    @Test
    void codeAndTestGenerationRequireHumanApprovalBeforeContinuing() {
        FakeActivities activities = new FakeActivities();
        TestableWorkflow workflow = new TestableWorkflow(
            activities,
            List.of(
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "CODE_GENERATION", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "TEST_GENERATION", CheckpointDecision.APPROVE, null)
            )
        );

        DevFlowWorkflowResult result = workflow.start(new DevFlowWorkflowInput(
            UUID.fromString("00000000-0000-0000-0000-000000000001"),
            "Add auth",
            "实现登录注册",
            List.of("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION", "TEST_GENERATION", "APPLY_AND_RUN_TESTS"),
            new LinkedHashMap<>()
        ));

        assertThat(activities.calls).containsExactly(
            "REQUIREMENT_ANALYSIS",
            "SYSTEM_DESIGN",
            "CODE_GENERATION",
            "TEST_GENERATION",
            "APPLY_AND_RUN_TESTS"
        );
        assertThat(workflow.decisionsSeen).containsExactly("SYSTEM_DESIGN", "CODE_GENERATION", "TEST_GENERATION");
        assertThat(result.currentStage()).isEqualTo("APPLY_AND_RUN_TESTS");
    }

    @Test
    void rejectedSystemDesignInjectsFeedbackAndRerunsDesignBeforeCodeGeneration() {
        FakeActivities activities = new FakeActivities();
        DevFlowWorkflowImpl workflow = new TestableWorkflow(
            activities,
            List.of(
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.REJECT, "补充数据库表结构"),
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "CODE_GENERATION", CheckpointDecision.APPROVE, null)
            )
        );

        DevFlowWorkflowResult result = workflow.start(input());

        assertThat(activities.calls).containsExactly(
            "REQUIREMENT_ANALYSIS",
            "SYSTEM_DESIGN",
            "SYSTEM_DESIGN",
            "CODE_GENERATION"
        );
        assertThat(activities.requests.get(2).globalContext())
            .containsEntry("human_feedback", "补充数据库表结构")
            .containsEntry("rejected_stage", "SYSTEM_DESIGN");
        assertThat(result.status()).isEqualTo("COMPLETED");
    }

    @Test
    void rejectedCodeReviewRollsBackToCodeGenerationWithFeedbackAndExistingOutputs() {
        FakeActivities activities = new FakeActivities();
        DevFlowWorkflowImpl workflow = new TestableWorkflow(
            activities,
            List.of(
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "CODE_GENERATION", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "TEST_GENERATION", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "CODE_REVIEW", CheckpointDecision.REJECT, "è¯·åœ¨å·²åº”ç”¨çš„ä»£ç åŸºç¡€ä¸Šä¿®æ­£è¾¹ç•Œæƒ…å†µ"),
                new CheckpointSignal(UUID.randomUUID(), "CODE_GENERATION", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "TEST_GENERATION", CheckpointDecision.APPROVE, null),
                new CheckpointSignal(UUID.randomUUID(), "CODE_REVIEW", CheckpointDecision.APPROVE, null)
            )
        );

        DevFlowWorkflowResult result = workflow.start(new DevFlowWorkflowInput(
            UUID.fromString("00000000-0000-0000-0000-000000000001"),
            "Add auth",
            "ç€¹ç‚µå¹‡é§è¯²ç¶å¨‰ã„¥å”½",
            List.of(
                "REQUIREMENT_ANALYSIS",
                "SYSTEM_DESIGN",
                "CODE_GENERATION",
                "TEST_GENERATION",
                "APPLY_AND_RUN_TESTS",
                "CODE_REVIEW"
            ),
            new LinkedHashMap<>()
        ));

        assertThat(activities.calls).containsExactly(
            "REQUIREMENT_ANALYSIS",
            "SYSTEM_DESIGN",
            "CODE_GENERATION",
            "TEST_GENERATION",
            "APPLY_AND_RUN_TESTS",
            "CODE_REVIEW",
            "CODE_GENERATION",
            "TEST_GENERATION",
            "APPLY_AND_RUN_TESTS",
            "CODE_REVIEW"
        );
        StageExecutionRequest secondCodeGenerationRequest = activities.requests.get(6);
        assertThat(secondCodeGenerationRequest.globalContext())
            .containsEntry("human_feedback", "è¯·åœ¨å·²åº”ç”¨çš„ä»£ç åŸºç¡€ä¸Šä¿®æ­£è¾¹ç•Œæƒ…å†µ")
            .containsEntry("rejected_stage", "CODE_REVIEW");
        assertThat(secondCodeGenerationRequest.previousOutput())
            .containsKey("diff_patch")
            .containsKey("test_run_results")
            .containsKey("review_report");
        assertThat(result.currentStage()).isEqualTo("CODE_REVIEW");
    }

    private static DevFlowWorkflowInput input() {
        return new DevFlowWorkflowInput(
            UUID.fromString("00000000-0000-0000-0000-000000000001"),
            "Add auth",
            "实现登录注册",
            List.of("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"),
            new LinkedHashMap<>()
        );
    }

    private static class TestableWorkflow extends DevFlowWorkflowImpl {
        private final Queue<CheckpointSignal> decisions;
        private final List<String> decisionsSeen = new ArrayList<>();

        TestableWorkflow(DevFlowActivities activities, List<CheckpointSignal> decisions) {
            super(activities);
            this.decisions = new ArrayDeque<>(decisions);
        }

        @Override
        protected CheckpointSignal waitForCheckpointDecision(String stageName) {
            decisionsSeen.add(stageName);
            return decisions.remove();
        }
    }

    private static class FakeActivities implements DevFlowActivities {
        private final List<String> calls = new ArrayList<>();
        private final List<StageExecutionRequest> requests = new ArrayList<>();

        @Override
        public StageExecutionResult analyzeRequirement(StageExecutionRequest request) {
            return execute("REQUIREMENT_ANALYSIS", request);
        }

        @Override
        public StageExecutionResult designSystem(StageExecutionRequest request) {
            return execute("SYSTEM_DESIGN", request);
        }

        @Override
        public StageExecutionResult generateCode(StageExecutionRequest request) {
            return execute("CODE_GENERATION", request);
        }

        @Override
        public StageExecutionResult generateTests(StageExecutionRequest request) {
            return execute("TEST_GENERATION", request);
        }

        @Override
        public StageExecutionResult applyAndRunTests(StageExecutionRequest request) {
            return execute("APPLY_AND_RUN_TESTS", request);
        }

        @Override
        public StageExecutionResult reviewCode(StageExecutionRequest request) {
            return execute("CODE_REVIEW", request);
        }

        @Override
        public StageExecutionResult integrateDelivery(StageExecutionRequest request) {
            return execute("DELIVERY_INTEGRATION", request);
        }

        private StageExecutionResult execute(String stageName, StageExecutionRequest request) {
            calls.add(stageName);
            requests.add(request);
            return new StageExecutionResult(stageName, "COMPLETED", outputFor(stageName));
        }

        private Map<String, Object> outputFor(String stageName) {
            return switch (stageName) {
                case "REQUIREMENT_ANALYSIS" -> Map.of(
                    "structured_prd", Map.of("summary", "prd"),
                    "stage", stageName
                );
                case "SYSTEM_DESIGN" -> Map.of(
                    "design_doc", Map.of("summary", "design"),
                    "stage", stageName
                );
                case "CODE_GENERATION" -> Map.of(
                    "diff_patch", "diff --git a/a b/a",
                    "code_generation_report", Map.of("summary", "code"),
                    "stage", stageName
                );
                case "TEST_GENERATION" -> Map.of(
                    "test_results", Map.of("summary", "tests"),
                    "stage", stageName
                );
                case "APPLY_AND_RUN_TESTS" -> Map.of(
                    "test_run_results", Map.of("status", "PASSED"),
                    "stage", stageName
                );
                case "CODE_REVIEW" -> Map.of(
                    "review_report", Map.of("status", "CHANGES_REQUESTED"),
                    "stage", stageName
                );
                default -> Map.of("stage", stageName);
            };
        }
    }
}
