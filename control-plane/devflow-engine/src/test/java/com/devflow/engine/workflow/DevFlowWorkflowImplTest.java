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
        DevFlowWorkflowImpl workflow = new TestableWorkflow(
            activities,
            List.of(new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.APPROVE, null))
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
    void rejectedSystemDesignInjectsFeedbackAndRerunsDesignBeforeCodeGeneration() {
        FakeActivities activities = new FakeActivities();
        DevFlowWorkflowImpl workflow = new TestableWorkflow(
            activities,
            List.of(
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.REJECT, "补充数据库表结构"),
                new CheckpointSignal(UUID.randomUUID(), "SYSTEM_DESIGN", CheckpointDecision.APPROVE, null)
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

        TestableWorkflow(DevFlowActivities activities, List<CheckpointSignal> decisions) {
            super(activities);
            this.decisions = new ArrayDeque<>(decisions);
        }

        @Override
        protected CheckpointSignal waitForCheckpointDecision(String stageName) {
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
            return new StageExecutionResult(stageName, "COMPLETED", Map.of("stage", stageName));
        }
    }
}
