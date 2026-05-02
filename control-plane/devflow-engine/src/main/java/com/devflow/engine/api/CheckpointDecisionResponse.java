package com.devflow.engine.api;

import java.util.Map;

/*
 * 检查点响应不仅告诉前端 Signal 是否已被控制平面接收，也会回带当前阶段产物。
 *
 * 这里的 stageOutput 不是重新执行 Agent 得到的新结果，而是 PipelineService 在发送
 * approve/reject Signal 之前，从 Temporal Workflow 快照同步到 Stage.output_payload
 * 的数据库快照。这样 UI 在用户点击“通过/驳回”后，仍然可以展示刚才审批的设计文档、
 * 评审报告或测试结果，不需要立即再发一次状态查询请求。
 */
public record CheckpointDecisionResponse(
    String status,
    String message,
    String stageName,
    Map<String, Object> stageOutput
) {
    public CheckpointDecisionResponse(String status, String message) {
        this(status, message, null, Map.of());
    }

    public CheckpointDecisionResponse {
        stageOutput = stageOutput == null ? Map.of() : stageOutput;
    }
}
