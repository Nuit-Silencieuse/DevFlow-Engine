package com.devflow.engine.api;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.blankOrNullString;
import static org.assertj.core.api.Assertions.assertThat;
import static org.mockito.ArgumentMatchers.any;
import static org.mockito.Mockito.verify;
import static org.mockito.Mockito.when;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import com.devflow.engine.service.PipelineService;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.time.OffsetDateTime;
import org.junit.jupiter.api.Test;
import org.mockito.ArgumentCaptor;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.boot.test.mock.mockito.MockBean;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(PipelineController.class)
class PipelineControllerContractTest {
    @Autowired
    private MockMvc mockMvc;

    @MockBean
    private PipelineService pipelineService;

    @Test
    void createPipelineAcceptsContractRequest() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000101");
        when(pipelineService.createPipeline(any(CreatePipelineRequest.class)))
            .thenReturn(new CreatePipelineResponse(pipelineId, "devflow-Add-auth-" + pipelineId, "RUNNING"));

        mockMvc.perform(post("/api/v1/pipelines")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "name": "Add user authentication",
                      "requirement": "实现用户登录、注册和鉴权",
                      "stages": ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"],
                      "repository": {
                        "rootPath": "D:/projects/demo-app",
                        "includePaths": ["src", "README.md"],
                        "excludePaths": ["node_modules", "dist", ".git"],
                        "targetFiles": ["src/App.tsx"],
                        "maxRounds": 4,
                        "maxFiles": 50,
                        "maxBytes": 65536,
                        "maxSearchResults": 25,
                        "privacyMode": "strict"
                      }
                    }
                    """))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.pipelineId", not(blankOrNullString())))
            .andExpect(jsonPath("$.status").value("RUNNING"));

        ArgumentCaptor<CreatePipelineRequest> requestCaptor = ArgumentCaptor.forClass(CreatePipelineRequest.class);
        verify(pipelineService).createPipeline(requestCaptor.capture());
        RepositoryContext repository = requestCaptor.getValue().repository();
        assertThat(repository.rootPath()).isEqualTo("D:/projects/demo-app");
        assertThat(repository.includePaths()).containsExactly("src", "README.md");
        assertThat(repository.excludePaths()).containsExactly("node_modules", "dist", ".git");
        assertThat(repository.targetFiles()).containsExactly("src/App.tsx");
        assertThat(repository.maxRounds()).isEqualTo(4);
        assertThat(repository.maxFiles()).isEqualTo(50);
        assertThat(repository.maxBytes()).isEqualTo(65_536L);
        assertThat(repository.maxSearchResults()).isEqualTo(25);
        assertThat(repository.privacyMode()).isEqualTo("strict");
    }

    @Test
    void createPipelineTreatsRepositoryAdvancedFieldsAsOptional() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000102");
        when(pipelineService.createPipeline(any(CreatePipelineRequest.class)))
            .thenReturn(new CreatePipelineResponse(pipelineId, "devflow-Add-auth-" + pipelineId, "RUNNING"));

        mockMvc.perform(post("/api/v1/pipelines")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "name": "Root only repository context",
                      "requirement": "Analyze health check requirement",
                      "stages": ["REQUIREMENT_ANALYSIS"],
                      "repository": {
                        "rootPath": "D:/projects/demo-app"
                      }
                    }
                    """))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.pipelineId", not(blankOrNullString())))
            .andExpect(jsonPath("$.status").value("RUNNING"));

        ArgumentCaptor<CreatePipelineRequest> requestCaptor = ArgumentCaptor.forClass(CreatePipelineRequest.class);
        verify(pipelineService).createPipeline(requestCaptor.capture());
        RepositoryContext repository = requestCaptor.getValue().repository();
        assertThat(repository.rootPath()).isEqualTo("D:/projects/demo-app");
        assertThat(repository.includePaths()).isNull();
        assertThat(repository.excludePaths()).isNull();
        assertThat(repository.targetFiles()).isNull();
        assertThat(repository.maxRounds()).isNull();
        assertThat(repository.maxFiles()).isNull();
        assertThat(repository.maxBytes()).isNull();
        assertThat(repository.maxSearchResults()).isNull();
        assertThat(repository.privacyMode()).isNull();
    }

    @Test
    void getPipelineReturnsPersistedStatus() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        when(pipelineService.getPipeline(pipelineId))
            .thenReturn(new PipelineStatusResponse(
                pipelineId,
                "devflow-Add-auth-" + pipelineId,
                "RUNNING",
                "SYSTEM_DESIGN",
                new RepositoryContext(
                    "D:/projects/demo-app",
                    List.of("src"),
                    List.of("node_modules", ".git"),
                    List.of("src/App.tsx"),
                    4,
                    50,
                    65_536L,
                    25,
                    "standard"
                ),
                List.of(new StageStatusResponse(
                    "SYSTEM_DESIGN",
                    "COMPLETED",
                    true,
                    Map.of("design_doc", Map.of("summary", "系统设计草案"))
                ))
            ));

        mockMvc.perform(get("/api/v1/pipelines/{id}", pipelineId))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.pipelineId").value(pipelineId.toString()))
            .andExpect(jsonPath("$.status").value("RUNNING"))
            .andExpect(jsonPath("$.currentStage").value("SYSTEM_DESIGN"))
            .andExpect(jsonPath("$.repository.rootPath").value("D:/projects/demo-app"))
            .andExpect(jsonPath("$.repository.targetFiles[0]").value("src/App.tsx"))
            .andExpect(jsonPath("$.stages[0].name").value("SYSTEM_DESIGN"))
            .andExpect(jsonPath("$.stages[0].output.design_doc.summary").value("系统设计草案"));
    }

    @Test
    void getPipelineSummaryReturnsStageMetadataWithoutArtifacts() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000201");
        when(pipelineService.getPipelineSummary(pipelineId))
            .thenReturn(new PipelineSummaryResponse(
                pipelineId,
                "devflow-summary-" + pipelineId,
                "RUNNING",
                "CODE_GENERATION",
                null,
                OffsetDateTime.parse("2026-05-07T10:00:00Z"),
                List.of(new StageSummaryResponse(
                    "CODE_GENERATION",
                    "COMPLETED",
                    true,
                    true,
                    "rev-code"
                ))
            ));

        mockMvc.perform(get("/api/v1/pipelines/{id}/summary", pipelineId))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.pipelineId").value(pipelineId.toString()))
            .andExpect(jsonPath("$.currentStage").value("CODE_GENERATION"))
            .andExpect(jsonPath("$.stages[0].name").value("CODE_GENERATION"))
            .andExpect(jsonPath("$.stages[0].outputAvailable").value(true))
            .andExpect(jsonPath("$.stages[0].artifactRevision").value("rev-code"));
    }

    @Test
    void getStageArtifactReturnsOnlyRequestedStageOutput() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000202");
        when(pipelineService.getStageArtifact(pipelineId, "CODE_GENERATION"))
            .thenReturn(new StageArtifactResponse(
                pipelineId,
                "CODE_GENERATION",
                "COMPLETED",
                true,
                "rev-code",
                Map.of("diff_patch", "diff --git a/a b/a")
            ));

        mockMvc.perform(get("/api/v1/pipelines/{id}/stages/{stageName}/artifact", pipelineId, "CODE_GENERATION"))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.pipelineId").value(pipelineId.toString()))
            .andExpect(jsonPath("$.stageName").value("CODE_GENERATION"))
            .andExpect(jsonPath("$.artifactRevision").value("rev-code"))
            .andExpect(jsonPath("$.output.diff_patch").value("diff --git a/a b/a"));
    }

    @Test
    void checkpointRouteSendsSignalAndReturnsAcceptedDecision() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        when(pipelineService.submitCheckpointDecision(any(UUID.class), any(String.class), any(CheckpointDecisionRequest.class)))
            .thenReturn(new CheckpointDecisionResponse(
                "RUNNING",
                "Signal received. Pipeline resuming or re-routing.",
                "SYSTEM_DESIGN",
                Map.of("design_doc", Map.of("summary", "系统设计草案"))
            ));

        mockMvc.perform(post("/api/v1/pipelines/{id}/checkpoints/{stageName}", pipelineId, "SYSTEM_DESIGN")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "decision": "REJECT",
                      "feedback": "Add database schema details."
                    }
                    """))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.status").value("RUNNING"))
            .andExpect(jsonPath("$.message").value("Signal received. Pipeline resuming or re-routing."))
            .andExpect(jsonPath("$.stageName").value("SYSTEM_DESIGN"))
            .andExpect(jsonPath("$.stageOutput.design_doc.summary").value("系统设计草案"));

        verify(pipelineService).submitCheckpointDecision(any(UUID.class), any(String.class), any(CheckpointDecisionRequest.class));
    }
}
