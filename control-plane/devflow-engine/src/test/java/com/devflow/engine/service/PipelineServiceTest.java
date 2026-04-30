package com.devflow.engine.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.devflow.engine.api.CheckpointDecisionRequest;
import com.devflow.engine.api.CreatePipelineRequest;
import com.devflow.engine.model.Pipeline;
import com.devflow.engine.model.PipelineStatus;
import com.devflow.engine.model.StageStatus;
import com.devflow.engine.repository.PipelineRepository;
import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import java.util.List;
import java.util.Optional;
import java.util.UUID;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.ArgumentCaptor;
import org.mockito.InjectMocks;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

@ExtendWith(MockitoExtension.class)
class PipelineServiceTest {
    @Mock
    private PipelineRepository pipelineRepository;

    @Mock
    private TemporalPipelineGateway temporalPipelineGateway;

    @InjectMocks
    private PipelineService pipelineService;

    @Test
    void createPipelinePersistsStagesAndStartsWorkflow() {
        when(pipelineRepository.save(any(Pipeline.class))).thenAnswer(invocation -> invocation.getArgument(0));

        var response = pipelineService.createPipeline(new CreatePipelineRequest(
            "Add auth",
            "实现登录注册",
            List.of("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION")
        ));

        ArgumentCaptor<Pipeline> pipelineCaptor = ArgumentCaptor.forClass(Pipeline.class);
        ArgumentCaptor<DevFlowWorkflowInput> workflowInputCaptor = ArgumentCaptor.forClass(DevFlowWorkflowInput.class);
        verify(pipelineRepository).save(pipelineCaptor.capture());
        verify(temporalPipelineGateway).startPipeline(workflowInputCaptor.capture());

        Pipeline saved = pipelineCaptor.getValue();
        assertThat(response.pipelineId()).isEqualTo(saved.getId());
        assertThat(response.status()).isEqualTo("RUNNING");
        assertThat(saved.getStatus()).isEqualTo(PipelineStatus.RUNNING);
        assertThat(saved.getCurrentStage()).isEqualTo("REQUIREMENT_ANALYSIS");
        assertThat(saved.getGlobalContext()).containsEntry("original_requirement", "实现登录注册");
        assertThat(saved.getStages()).extracting("name")
            .containsExactly("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION");
        assertThat(saved.getStages().get(1).isRequiresHumanApproval()).isTrue();

        DevFlowWorkflowInput workflowInput = workflowInputCaptor.getValue();
        assertThat(workflowInput.pipelineId()).isEqualTo(saved.getId());
        assertThat(workflowInput.stages()).containsExactly("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION");
    }

    @Test
    void getPipelineReturnsStoredStageSnapshot() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        Pipeline pipeline = new Pipeline("Add auth");
        pipeline.setId(pipelineId);
        pipeline.setStatus(PipelineStatus.RUNNING);
        pipeline.setCurrentStage("SYSTEM_DESIGN");
        pipeline.addStage(PipelineService.createStage("SYSTEM_DESIGN"));
        pipeline.getStages().get(0).setStatus(StageStatus.PENDING);

        when(pipelineRepository.findById(pipelineId)).thenReturn(Optional.of(pipeline));

        var response = pipelineService.getPipeline(pipelineId);

        assertThat(response.pipelineId()).isEqualTo(pipelineId);
        assertThat(response.status()).isEqualTo("RUNNING");
        assertThat(response.currentStage()).isEqualTo("SYSTEM_DESIGN");
        assertThat(response.stages()).hasSize(1);
        assertThat(response.stages().get(0).name()).isEqualTo("SYSTEM_DESIGN");
    }

    @Test
    void submitCheckpointDecisionSignalsTemporalWorkflow() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        when(pipelineRepository.existsById(pipelineId)).thenReturn(true);

        var response = pipelineService.submitCheckpointDecision(
            pipelineId,
            "SYSTEM_DESIGN",
            new CheckpointDecisionRequest("REJECT", "补充数据库说明")
        );

        assertThat(response.status()).isEqualTo("RUNNING");
        verify(temporalPipelineGateway).signalCheckpoint(
            pipelineId,
            "SYSTEM_DESIGN",
            CheckpointDecision.REJECT,
            "补充数据库说明"
        );
    }
}
