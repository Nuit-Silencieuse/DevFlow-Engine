package com.devflow.engine.service;

import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.CheckpointSignal;
import com.devflow.engine.workflow.DevFlowWorkflow;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import com.devflow.engine.workflow.LlmConfigSignal;
import com.devflow.engine.workflow.WorkflowStatusSnapshot;
import io.temporal.client.WorkflowNotFoundException;
import io.temporal.client.WorkflowClient;
import io.temporal.client.WorkflowOptions;
import java.util.LinkedHashMap;
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
            DevFlowWorkflow workflow = workflowClient.newWorkflowStub(DevFlowWorkflow.class, workflowId);
            return Optional.ofNullable(workflow.getStatus());
        } catch (WorkflowNotFoundException ex) {
            return Optional.empty();
        }
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
