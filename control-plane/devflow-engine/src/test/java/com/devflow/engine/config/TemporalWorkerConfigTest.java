package com.devflow.engine.config;

import static org.mockito.Mockito.mock;
import static org.mockito.Mockito.verify;

import com.devflow.engine.workflow.DevFlowWorkflowImpl;
import io.temporal.worker.Worker;
import org.junit.jupiter.api.Test;

class TemporalWorkerConfigTest {
    @Test
    void registersDevFlowWorkflowImplementationOnWorker() {
        Worker worker = mock(Worker.class);

        TemporalWorkerConfig.registerWorkflowImplementation(worker);

        verify(worker).registerWorkflowImplementationTypes(DevFlowWorkflowImpl.class);
    }
}
