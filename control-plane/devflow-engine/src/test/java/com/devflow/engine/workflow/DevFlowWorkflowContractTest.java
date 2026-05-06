package com.devflow.engine.workflow;

import static org.assertj.core.api.Assertions.assertThat;

import io.temporal.activity.ActivityInterface;
import io.temporal.activity.ActivityMethod;
import io.temporal.workflow.QueryMethod;
import io.temporal.workflow.SignalMethod;
import io.temporal.workflow.WorkflowInterface;
import io.temporal.workflow.WorkflowMethod;
import java.lang.reflect.Method;
import java.util.Arrays;
import org.junit.jupiter.api.Test;

class DevFlowWorkflowContractTest {
    @Test
    void workflowInterfaceDeclaresTemporalContract() throws Exception {
        assertThat(DevFlowWorkflow.class.isAnnotationPresent(WorkflowInterface.class)).isTrue();

        Method start = DevFlowWorkflow.class.getMethod("start", DevFlowWorkflowInput.class);
        Method approve = DevFlowWorkflow.class.getMethod("approveCheckpoint", CheckpointSignal.class);
        Method reject = DevFlowWorkflow.class.getMethod("rejectCheckpoint", CheckpointSignal.class);
        Method status = DevFlowWorkflow.class.getMethod("getStatus");

        assertThat(start.isAnnotationPresent(WorkflowMethod.class)).isTrue();
        assertThat(approve.isAnnotationPresent(SignalMethod.class)).isTrue();
        assertThat(reject.isAnnotationPresent(SignalMethod.class)).isTrue();
        assertThat(status.isAnnotationPresent(QueryMethod.class)).isTrue();
    }

    @Test
    void activityInterfaceCoversAllPlannedPipelineStages() {
        assertThat(DevFlowActivities.class.isAnnotationPresent(ActivityInterface.class)).isTrue();

        var javaMethodNames = Arrays.stream(DevFlowActivities.class.getMethods())
            .filter(method -> method.isAnnotationPresent(ActivityMethod.class))
            .map(Method::getName)
            .toList();

        assertThat(javaMethodNames).containsExactlyInAnyOrder(
            "analyzeRequirement",
            "designSystem",
            "generateCode",
            "generateTests",
            "applyAndRunTests",
            "reviewCode",
            "integrateDelivery"
        );

        var temporalActivityNames = Arrays.stream(DevFlowActivities.class.getMethods())
            .filter(method -> method.isAnnotationPresent(ActivityMethod.class))
            .map(method -> method.getAnnotation(ActivityMethod.class).name())
            .toList();

        assertThat(temporalActivityNames).containsExactlyInAnyOrder(
            "analyzeRequirement",
            "designSystem",
            "generateCode",
            "generateTests",
            "applyAndRunTests",
            "reviewCode",
            "integrateDelivery"
        );
    }
}
