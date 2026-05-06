from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.llm import LlmClient, LlmClientConfig, LlmResponse


def design_llm_client(response: dict) -> LlmClient:
    return LlmClient(
        config=LlmClientConfig(default_provider="fake", max_retries=0),
        providers={"fake": RecordingDesignProvider(response)},
    )


class RecordingDesignProvider:
    name = "fake"

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[str] = []
        self.last_messages: list[dict[str, str]] = []

    def complete(self, request, config):
        self.calls.append(request.task)
        self.last_messages = [
            {"role": message.role, "content": message.content}
            for message in request.messages
        ]
        return LlmResponse(
            provider=self.name,
            model=request.model or config.default_model or "fake-model",
            text=json.dumps(self.response, ensure_ascii=False),
            parsed_json=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=0,
            request_id=None,
        )


def sample_state() -> dict:
    return {
        "original_requirement": "在控制台展示需求分析阶段的中间产物。",
        "structured_prd": {
            "summary": "前端控制台需要展示结构化 PRD 和代码探索证据。",
            "user_stories": [
                {
                    "role": "项目负责人",
                    "goal": "审查需求分析阶段产物",
                    "benefit": "在进入设计前确认需求质量",
                }
            ],
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "description": "展示 structured_prd",
                    "verification": "查看 REQUIREMENT_ANALYSIS 输出",
                },
                {
                    "id": "AC-002",
                    "description": "展示 codeContext",
                    "verification": "查看已读文件和 evidence",
                },
            ],
            "open_questions": [],
        },
        "code_context": {
            "status": "COMPLETE",
            "root_path": "D:/repo",
            "inspected_files": [
                "sandbox/frontend/src/main.ts",
                "sandbox/frontend/src/viewModel.ts",
            ],
            "search_queries": ["artifact", "stage"],
            "evidence": [
                {
                    "filePath": "sandbox/frontend/src/viewModel.ts",
                    "lineStart": 1,
                    "lineEnd": 80,
                    "excerpt": "export function toPipelineViewModel() {}",
                    "relevanceReason": "前端展示模型证据",
                    "supports": ["artifact"],
                }
            ],
            "confidence": 0.86,
        },
        "pipeline_context": {
            "version": 1,
            "latest_code_context": {
                "stage": "REQUIREMENT_ANALYSIS",
                "confidence": 0.86,
                "inspected_files": ["sandbox/frontend/src/viewModel.ts"],
            },
            "code_contexts": [],
            "artifact_index": {},
        },
    }


class DesignAgentTest(unittest.TestCase):
    def test_generates_design_doc_with_fake_llm(self):
        from src.agents.design_agent import DesignAgent

        provider_response = {
            "summary": "在控制台阶段详情中增加中间产物设计。",
            "modules": [
                {
                    "name": "PipelineConsole",
                    "responsibility": "展示阶段状态、PRD 和代码证据",
                    "dependencies": ["Pipeline API"],
                }
            ],
            "api_contracts": [
                {
                    "name": "GET /api/v1/pipelines/{id}",
                    "description": "返回阶段 outputPayload",
                }
            ],
            "data_changes": [],
            "file_plan": [
                {
                    "path": "sandbox/frontend/src/viewModel.ts",
                    "operation": "update",
                    "reason": "补充 design_doc 展示模型",
                }
            ],
            "risks": ["需要控制 outputPayload 展示长度"],
            "open_questions": [],
        }
        client = design_llm_client(provider_response)

        result = DesignAgent(llm_client=client).run(sample_state())

        self.assertEqual(result["current_step"], "SYSTEM_DESIGN")
        self.assertEqual(result["error_logs"], [])
        self.assertEqual(result["design_doc"]["source"], "design_agent")
        self.assertEqual(result["design_doc"]["summary"], provider_response["summary"])
        self.assertEqual(result["design_doc"]["feedback"], "")
        self.assertEqual(
            result["design_doc"]["code_context_summary"]["inspected_files"],
            ["sandbox/frontend/src/main.ts", "sandbox/frontend/src/viewModel.ts"],
        )
        self.assertIn("pipeline_context", result)
        self.assertEqual(
            result["pipeline_context"]["artifact_index"]["SYSTEM_DESIGN"]["design_doc"]["summary"],
            provider_response["summary"],
        )

    def test_human_feedback_is_included_in_prompt_and_output(self):
        from src.agents.design_agent import DesignAgent

        state = sample_state()
        state["human_feedback"] = "请把 API 字段兼容性风险写清楚。"
        provider = RecordingDesignProvider(
            {
                "summary": "修订后的设计",
                "modules": [
                    {"name": "StageArtifactPanel", "responsibility": "展示产物", "dependencies": []}
                ],
                "api_contracts": [],
                "data_changes": [],
                "file_plan": [
                    {"path": "sandbox/frontend/src/main.ts", "operation": "update", "reason": "展示反馈"}
                ],
                "risks": ["API 字段兼容性需要人工确认"],
                "open_questions": [],
            }
        )
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={"fake": provider},
        )

        result = DesignAgent(llm_client=client).run(state)

        serialized_messages = json.dumps(provider.last_messages, ensure_ascii=False)
        self.assertIn("请把 API 字段兼容性风险写清楚", serialized_messages)
        self.assertEqual(result["design_doc"]["feedback"], "请把 API 字段兼容性风险写清楚。")

    def test_missing_prd_returns_diagnostic_design_doc(self):
        from src.agents.design_agent import DesignAgent

        result = DesignAgent(llm_client=design_llm_client({})).run(
            {"original_requirement": "缺少需求分析产物"}
        )

        self.assertEqual(result["current_step"], "SYSTEM_DESIGN")
        self.assertIn("structured_prd is required", result["error_logs"][0])
        self.assertEqual(result["design_doc"]["source"], "design_agent")
        self.assertTrue(result["design_doc"]["open_questions"])

    def test_flow_node_invokes_design_agent(self):
        from src.graph.flow import design_system_node

        class StubDesignAgent:
            def run(self, state):
                return {
                    "design_doc": {"summary": state["structured_prd"]["summary"], "source": "design_agent"},
                    "current_step": "SYSTEM_DESIGN",
                    "error_logs": [],
                }

        with patch("src.graph.flow.DesignAgent", StubDesignAgent):
            result = design_system_node({"structured_prd": {"summary": "需求摘要"}})

        self.assertEqual(result["design_doc"]["source"], "design_agent")


if __name__ == "__main__":
    unittest.main()
