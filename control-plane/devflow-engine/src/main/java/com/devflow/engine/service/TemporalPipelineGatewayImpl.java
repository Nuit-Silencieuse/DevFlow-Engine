package com.devflow.engine.service;

import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.CheckpointSignal;
import com.devflow.engine.workflow.DevFlowWorkflow;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import com.devflow.engine.workflow.LlmConfigSignal;
import com.devflow.engine.workflow.WorkflowStatusSnapshot;
import io.temporal.api.common.v1.WorkflowExecution;
import io.temporal.api.enums.v1.WorkflowExecutionStatus;
import io.temporal.api.workflowservice.v1.DescribeWorkflowExecutionRequest;
import io.temporal.client.WorkflowNotFoundException;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Optional;
import java.util.UUID;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.stereotype.Component;

@Component
public class TemporalPipelineGatewayImpl implements TemporalPipelineGateway {
    private final WorkflowClient workflowClient;
    private final String taskQueue;

    public TemporalPipelineGatewayImpl(
        WorkflowClient workflowClient,
        @Value("${devflow.task-queue:DEVFLOW_TASK_QUEUE}") String taskQueue
    ) {
        this.workflowClient = workflowClient;
        this.taskQueue = taskQueue;
    }

    @Override
    public void startPipeline(DevFlowWorkflowInput input) {
        DevFlowWorkflow workflow = workflowClient.newWorkflowStub(
            DevFlowWorkflow.class,
            WorkflowOptions.newBuilder()
                .setTaskQueue(taskQueue)
                .setWorkflowId(workflowId(input.pipelineId(), input.name()))
                .build()
        );
        WorkflowClient.start(workflow::start, input);
    }

    @Override
    public void signalCheckpoint(String workflowId, UUID pipelineId, String stageName, CheckpointDecision decision, String feedback) {
        DevFlowWorkflow workflow = workflowClient.newWorkflowStub(DevFlowWorkflow.class, workflowId);
        CheckpointSignal signal = new CheckpointSignal(pipelineId, stageName, decision, feedback);
        if (decision == CheckpointDecision.APPROVE) {
            workflow.approveCheckpoint(signal);
        } else {
            workflow.rejectCheckpoint(signal);
        }
    }

    @Override
    public void updateLlmConfig(String workflowId, UUID pipelineId, Object llmConfig) {
        DevFlowWorkflow workflow = workflowClient.newWorkflowStub(DevFlowWorkflow.class, workflowId);
        workflow.updateLlmConfig(new LlmConfigSignal(pipelineId, copyMap(llmConfig)));
    }

    @Override
    public Optional<WorkflowStatusSnapshot> getStatus(String workflowId) {
        /*
         * 控制平面自身持有数据库快照，但真正的阶段执行结果首先产生在 Temporal Workflow 中。
         * 这里通过 Workflow Query 读取内存中的 WorkflowStatusSnapshot，然后由 PipelineService
         * 决定如何落库。这样可以保持两个边界:
         *
         * 1. Workflow 代码不直接访问数据库，避免破坏 Temporal 对 Workflow 确定性的要求。
         * 2. 查询失败或 Workflow 尚未创建时，不影响数据库里已有快照的读取能力。
         */
        try {
            Optional<WorkflowStatusSnapshot> terminalFailure = describeTerminalFailure(workflowId);
            if (terminalFailure.isPresent()) {
                return terminalFailure;
            }
            DevFlowWorkflow workflow = workflowClient.newWorkflowStub(DevFlowWorkflow.class, workflowId);
            return Optional.ofNullable(workflow.getStatus());
        } catch (WorkflowNotFoundException ex) {
            return Optional.empty();
        } catch (RuntimeException ex) {
            return describeClosedWorkflow(workflowId);
        }
    }

    private Optional<WorkflowStatusSnapshot> describeTerminalFailure(String workflowId) {
        try {
            WorkflowExecutionStatus status = describeWorkflowStatus(workflowId);
            if (isFailedTerminalStatus(status)) {
                return Optional.of(new WorkflowStatusSnapshot(null, "FAILED", null, List.of()));
            }
            return Optional.empty();
        } catch (WorkflowNotFoundException notFound) {
            return Optional.empty();
        }
    }

    private Optional<WorkflowStatusSnapshot> describeClosedWorkflow(String workflowId) {
        try {
            WorkflowExecutionStatus status = describeWorkflowStatus(workflowId);
            String pipelineStatus = switch (status) {
                case WORKFLOW_EXECUTION_STATUS_COMPLETED -> "COMPLETED";
                case WORKFLOW_EXECUTION_STATUS_FAILED,
                    WORKFLOW_EXECUTION_STATUS_CANCELED,
                    WORKFLOW_EXECUTION_STATUS_TERMINATED,
                    WORKFLOW_EXECUTION_STATUS_TIMED_OUT -> "FAILED";
                default -> "RUNNING";
            };
            return Optional.of(new WorkflowStatusSnapshot(null, pipelineStatus, null, List.of()));
        } catch (WorkflowNotFoundException notFound) {
            return Optional.empty();
        }
    }

    private WorkflowExecutionStatus describeWorkflowStatus(String workflowId) {
        return workflowClient.getWorkflowServiceStubs()
            .blockingStub()
            .describeWorkflowExecution(DescribeWorkflowExecutionRequest.newBuilder()
                .setNamespace(workflowClient.getOptions().getNamespace())
                .setExecution(WorkflowExecution.newBuilder()
                    .setWorkflowId(workflowId)
                    .build())
                .build())
            .getWorkflowExecutionInfo()
            .getStatus();
    }

    private static boolean isFailedTerminalStatus(WorkflowExecutionStatus status) {
        return status == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_FAILED
            || status == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_CANCELED
            || status == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_TERMINATED
            || status == WorkflowExecutionStatus.WORKFLOW_EXECUTION_STATUS_TIMED_OUT;
    }

    static String workflowId(UUID pipelineId, String pipelineName) {
        String prefix = sanitizeWorkflowPrefix(pipelineName);
        return "devflow-" + prefix + "-" + pipelineId;
    }

    static String sanitizeWorkflowPrefix(String pipelineName) {
        if (pipelineName == null || pipelineName.isBlank()) {
            return "pipeline";
        }
        StringBuilder builder = new StringBuilder();
        pipelineName.trim().codePoints().forEach(codePoint -> {
            if (Character.isLetterOrDigit(codePoint)) {
                builder.appendCodePoint(codePoint);
            } else if (builder.length() == 0 || builder.charAt(builder.length() - 1) != '-') {
                builder.append('-');
            }
        });
        String value = builder.toString().replaceAll("^-+|-+$", "");
        if (value.isBlank()) {
            return "pipeline";
        }
        return value.length() <= 40 ? value : value.substring(0, 40);
    }

    private static Map<String, Object> copyMap(Object value) {
        if (!(value instanceof Map<?, ?> map)) {
            return Map.of();
        }
        Map<String, Object> result = new LinkedHashMap<>();
        map.forEach((key, item) -> result.put(String.valueOf(key), item));
        return result;
    }
}
