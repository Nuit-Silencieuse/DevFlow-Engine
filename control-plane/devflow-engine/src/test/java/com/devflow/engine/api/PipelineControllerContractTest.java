package com.devflow.engine.api;

import static org.hamcrest.Matchers.not;
import static org.hamcrest.Matchers.blankOrNullString;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.get;
import static org.springframework.test.web.servlet.request.MockMvcRequestBuilders.post;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.jsonPath;
import static org.springframework.test.web.servlet.result.MockMvcResultMatchers.status;

import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Autowired;
import org.springframework.boot.test.autoconfigure.web.servlet.WebMvcTest;
import org.springframework.http.MediaType;
import org.springframework.test.web.servlet.MockMvc;

@WebMvcTest(PipelineController.class)
class PipelineControllerContractTest {
    @Autowired
    private MockMvc mockMvc;

    @Test
    void createPipelineAcceptsContractRequest() throws Exception {
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
    void getPipelineRouteExistsBeforeBusinessLogicIsImplemented() throws Exception {
        mockMvc.perform(get("/api/v1/pipelines/00000000-0000-0000-0000-000000000001"))
            .andExpect(status().isNotImplemented())
            .andExpect(jsonPath("$.code").value("NOT_IMPLEMENTED"));
    }

    @Test
    void checkpointRouteExistsBeforeSignalHandlingIsImplemented() throws Exception {
        mockMvc.perform(post("/api/v1/pipelines/00000000-0000-0000-0000-000000000001/checkpoints/SYSTEM_DESIGN")
                .contentType(MediaType.APPLICATION_JSON)
                .content("""
                    {
                      "decision": "REJECT",
                      "feedback": "Add database schema details."
                    }
                    """))
            .andExpect(status().isNotImplemented())
            .andExpect(jsonPath("$.code").value("NOT_IMPLEMENTED"));
    }
}
