package com.devflow.engine.service;

import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;

import com.devflow.engine.api.CheckpointDecisionRequest;
import com.devflow.engine.api.CreatePipelineRequest;
import com.devflow.engine.api.RepositoryContext;
import com.devflow.engine.model.Pipeline;
import com.devflow.engine.model.PipelineStatus;
import com.devflow.engine.model.StageStatus;
import com.devflow.engine.repository.PipelineRepository;
import com.devflow.engine.workflow.CheckpointDecision;
import com.devflow.engine.workflow.DevFlowWorkflowInput;
import com.devflow.engine.workflow.StageExecutionResult;
import com.devflow.engine.workflow.WorkflowStatusSnapshot;
import java.util.List;
import java.util.Map;
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
            List.of("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"),
            new RepositoryContext(
                "D:/projects/demo-app",
                List.of("src", "README.md"),
                List.of("node_modules", "dist", ".git"),
                List.of("src/App.tsx"),
                4,
                50,
                65_536L,
                25,
                "standard"
            )
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
        assertThat(saved.getGlobalContext()).containsKey("repository");
        assertThat(repositoryMap(saved.getGlobalContext().get("repository")))
            .containsEntry("rootPath", "D:/projects/demo-app")
            .containsEntry("maxRounds", 4)
            .containsEntry("maxFiles", 50)
            .containsEntry("maxBytes", 65_536L)
            .containsEntry("maxSearchResults", 25)
            .containsEntry("privacyMode", "standard");
        assertThat(saved.getStages()).extracting("name")
            .containsExactly("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION");
        assertThat(saved.getStages().get(1).isRequiresHumanApproval()).isTrue();

        DevFlowWorkflowInput workflowInput = workflowInputCaptor.getValue();
        assertThat(workflowInput.pipelineId()).isEqualTo(saved.getId());
        assertThat(workflowInput.stages()).containsExactly("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION");
        assertThat(repositoryMap(workflowInput.globalContext().get("repository")))
            .containsEntry("rootPath", "D:/projects/demo-app");
    }

    @Test
    void getPipelineReturnsStoredStageSnapshot() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        Pipeline pipeline = new Pipeline("Add auth");
        pipeline.setId(pipelineId);
        pipeline.setStatus(PipelineStatus.RUNNING);
        pipeline.setCurrentStage("SYSTEM_DESIGN");
        pipeline.setGlobalContext(Map.of(
            "requested_stages", List.of("SYSTEM_DESIGN"),
            "repository", Map.of(
                "rootPath", "D:/projects/demo-app",
                "includePaths", List.of("src"),
                "excludePaths", List.of("node_modules", ".git"),
                "targetFiles", List.of("src/App.tsx"),
                "maxFiles", 50,
                "maxBytes", 65_536L
            )
        ));
        pipeline.addStage(PipelineService.createStage("SYSTEM_DESIGN"));
        pipeline.getStages().get(0).setStatus(StageStatus.PENDING);

        when(pipelineRepository.findById(pipelineId)).thenReturn(Optional.of(pipeline));

        var response = pipelineService.getPipeline(pipelineId);

        assertThat(response.pipelineId()).isEqualTo(pipelineId);
        assertThat(response.status()).isEqualTo("RUNNING");
        assertThat(response.currentStage()).isEqualTo("SYSTEM_DESIGN");
        assertThat(response.repository().rootPath()).isEqualTo("D:/projects/demo-app");
        assertThat(response.repository().targetFiles()).containsExactly("src/App.tsx");
        assertThat(response.stages()).hasSize(1);
        assertThat(response.stages().get(0).name()).isEqualTo("SYSTEM_DESIGN");
    }

    @Test
    void getPipelineSynchronizesWorkflowOutputsIntoStageSnapshots() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000020");
        Pipeline pipeline = new Pipeline("Add auth");
        pipeline.setId(pipelineId);
        pipeline.setStatus(PipelineStatus.RUNNING);
        pipeline.setCurrentStage("REQUIREMENT_ANALYSIS");
        pipeline.setGlobalContext(Map.of(
            "requested_stages", List.of("REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN")
        ));
        pipeline.addStage(PipelineService.createStage("REQUIREMENT_ANALYSIS"));
        pipeline.addStage(PipelineService.createStage("SYSTEM_DESIGN"));

        when(pipelineRepository.findById(pipelineId)).thenReturn(Optional.of(pipeline));
        when(temporalPipelineGateway.getStatus(pipelineId)).thenReturn(Optional.of(new WorkflowStatusSnapshot(
            pipelineId,
            "SUSPENDED",
            "SYSTEM_DESIGN",
            List.of(
                new StageExecutionResult(
                    "REQUIREMENT_ANALYSIS",
                    "COMPLETED",
                    Map.of(
                        "structured_prd", Map.of("summary", "登录注册"),
                        "codeContext", Map.of("status", "COMPLETE"),
                        "explorationTrace", List.of(Map.of("actionType", "PLAN"))
                    )
                ),
                new StageExecutionResult(
                    "SYSTEM_DESIGN",
                    "COMPLETED",
                    Map.of("design_doc", Map.of("tables", List.of("users")))
                )
            )
        )));

        var response = pipelineService.getPipeline(pipelineId);

        assertThat(pipeline.getStatus()).isEqualTo(PipelineStatus.SUSPENDED);
        assertThat(pipeline.getCurrentStage()).isEqualTo("SYSTEM_DESIGN");
        assertThat(response.stages()).hasSize(2);
        assertThat(response.stages().get(0).status()).isEqualTo("COMPLETED");
        assertThat(response.stages().get(0).output()).containsKey("structured_prd");
        assertThat(response.stages().get(0).output()).containsKey("codeContext");
        assertThat(response.stages().get(0).output()).containsKey("explorationTrace");
        assertThat(response.stages().get(1).output()).containsKey("design_doc");
        verify(pipelineRepository).save(pipeline);
    }

    @Test
    void getPipelineFallsBackToPersistedSnapshotWhenTemporalQueryIsUnavailable() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000030");
        Pipeline pipeline = new Pipeline("Add auth");
        pipeline.setId(pipelineId);
        pipeline.setStatus(PipelineStatus.RUNNING);
        pipeline.setCurrentStage("REQUIREMENT_ANALYSIS");
        pipeline.setGlobalContext(Map.of(
            "requested_stages", List.of("REQUIREMENT_ANALYSIS")
        ));
        pipeline.addStage(PipelineService.createStage("REQUIREMENT_ANALYSIS"));

        when(pipelineRepository.findById(pipelineId)).thenReturn(Optional.of(pipeline));
        when(temporalPipelineGateway.getStatus(pipelineId)).thenThrow(new RuntimeException("Temporal query unavailable"));

        var response = pipelineService.getPipeline(pipelineId);

        assertThat(response.pipelineId()).isEqualTo(pipelineId);
        assertThat(response.status()).isEqualTo("RUNNING");
        assertThat(response.currentStage()).isEqualTo("REQUIREMENT_ANALYSIS");
        assertThat(response.stages()).hasSize(1);
        assertThat(response.stages().get(0).status()).isEqualTo("PENDING");
    }

    @Test
    void submitCheckpointDecisionSignalsTemporalWorkflow() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        Pipeline pipeline = new Pipeline("Add auth");
        pipeline.setId(pipelineId);
        pipeline.setGlobalContext(Map.of(
            "requested_stages", List.of("SYSTEM_DESIGN")
        ));
        pipeline.addStage(PipelineService.createStage("SYSTEM_DESIGN"));
        when(pipelineRepository.findById(pipelineId)).thenReturn(Optional.of(pipeline));

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

    @Test
    void submitCheckpointDecisionReturnsCurrentStageOutputForReviewUi() {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000021");
        Pipeline pipeline = new Pipeline("Add auth");
        pipeline.setId(pipelineId);
        pipeline.setStatus(PipelineStatus.SUSPENDED);
        pipeline.setCurrentStage("SYSTEM_DESIGN");
        pipeline.setGlobalContext(Map.of(
            "requested_stages", List.of("SYSTEM_DESIGN")
        ));
        pipeline.addStage(PipelineService.createStage("SYSTEM_DESIGN"));

        when(pipelineRepository.findById(pipelineId)).thenReturn(Optional.of(pipeline));
        when(temporalPipelineGateway.getStatus(pipelineId)).thenReturn(Optional.of(new WorkflowStatusSnapshot(
            pipelineId,
            "SUSPENDED",
            "SYSTEM_DESIGN",
            List.of(new StageExecutionResult(
                "SYSTEM_DESIGN",
                "COMPLETED",
                Map.of("design_doc", Map.of("summary", "需要审批的方案"))
            ))
        )));

        var response = pipelineService.submitCheckpointDecision(
            pipelineId,
            "SYSTEM_DESIGN",
            new CheckpointDecisionRequest("APPROVE", "")
        );

        assertThat(response.stageName()).isEqualTo("SYSTEM_DESIGN");
        assertThat(response.stageOutput()).containsKey("design_doc");
        verify(temporalPipelineGateway).signalCheckpoint(
            pipelineId,
            "SYSTEM_DESIGN",
            CheckpointDecision.APPROVE,
            ""
        );
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> repositoryMap(Object value) {
        return (Map<String, Object>) value;
    }
}
