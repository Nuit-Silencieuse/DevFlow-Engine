package com.devflow.engine.config;

import com.devflow.engine.workflow.DevFlowWorkflowImpl;
import io.temporal.client.WorkflowClient;
import io.temporal.worker.Worker;
import io.temporal.worker.WorkerFactory;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.autoconfigure.condition.ConditionalOnProperty;
import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;

@Configuration
public class TemporalWorkerConfig {
    public static final String DEFAULT_TASK_QUEUE = "DEVFLOW_TASK_QUEUE";

    @Bean(destroyMethod = "shutdown")
    @ConditionalOnProperty(
        name = "devflow.workflow-worker.enabled",
        havingValue = "true",
        matchIfMissing = true
    )
    WorkerFactory devFlowWorkflowWorkerFactory(
        WorkflowClient workflowClient,
        @Value("${devflow.task-queue:" + DEFAULT_TASK_QUEUE + "}") String taskQueue
    ) {
        /*
         * 控制平面承担 Workflow orchestration，因此必须有一个 Java Workflow Worker 轮询同一个 Task Queue。
         * 之前只有 WorkflowClient 负责 start/signal/query，Temporal 能创建 workflow execution，但没有 Worker
         * 真正执行 DevFlowWorkflowImpl，所以 Temporal UI 会显示 “No Workers Running”。
         *
         * 这里仅注册 Workflow implementation，不注册 Activity implementation。Activity 仍由执行平面的
         * Python Worker 承载；两类 Worker 使用同一个 DEVFLOW_TASK_QUEUE，Temporal 会按 task 类型分发。
         */
        WorkerFactory factory = WorkerFactory.newInstance(workflowClient);
        Worker worker = factory.newWorker(taskQueue);
        registerWorkflowImplementation(worker);
        factory.start();
        return factory;
    }

    static void registerWorkflowImplementation(Worker worker) {
        worker.registerWorkflowImplementationTypes(DevFlowWorkflowImpl.class);
    }
}
