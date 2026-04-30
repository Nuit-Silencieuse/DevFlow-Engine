package com.devflow.engine.api;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.blankOrNullString;
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
import org.junit.jupiter.api.Test;
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
            .thenReturn(new CreatePipelineResponse(pipelineId, "RUNNING"));

        mockMvc.perform(post("/api/v1/pipelines")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "name": "Add user authentication",
                      "requirement": "实现用户登录、注册和鉴权",
                      "stages": ["REQUIREMENT_ANALYSIS", "SYSTEM_DESIGN", "CODE_GENERATION"]
                    }
                    """))
            .andExpect(status().isCreated())
            .andExpect(jsonPath("$.pipelineId", not(blankOrNullString())))
            .andExpect(jsonPath("$.status").value("RUNNING"));
    }

    @Test
    void getPipelineReturnsPersistedStatus() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        when(pipelineService.getPipeline(pipelineId))
            .thenReturn(new PipelineStatusResponse(
                pipelineId,
                "RUNNING",
                "SYSTEM_DESIGN",
                List.of(new StageStatusResponse("SYSTEM_DESIGN", "PENDING", true, Map.of()))
            ));

        mockMvc.perform(get("/api/v1/pipelines/{id}", pipelineId))
            .andExpect(status().isOk())
            .andExpect(jsonPath("$.pipelineId").value(pipelineId.toString()))
            .andExpect(jsonPath("$.status").value("RUNNING"))
            .andExpect(jsonPath("$.currentStage").value("SYSTEM_DESIGN"))
            .andExpect(jsonPath("$.stages[0].name").value("SYSTEM_DESIGN"));
    }

    @Test
    void checkpointRouteSendsSignalAndReturnsAcceptedDecision() throws Exception {
        UUID pipelineId = UUID.fromString("00000000-0000-0000-0000-000000000001");
        when(pipelineService.submitCheckpointDecision(any(UUID.class), any(String.class), any(CheckpointDecisionRequest.class)))
            .thenReturn(new CheckpointDecisionResponse("RUNNING", "Signal received. Pipeline resuming or re-routing."));

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
            .andExpect(jsonPath("$.message").value("Signal received. Pipeline resuming or re-routing."));

        verify(pipelineService).submitCheckpointDecision(any(UUID.class), any(String.class), any(CheckpointDecisionRequest.class));
    }
}
