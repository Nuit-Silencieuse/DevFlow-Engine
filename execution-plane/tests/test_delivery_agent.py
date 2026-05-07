from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.llm import LlmClient, LlmClientConfig, LlmResponse


def delivery_llm_client(response: dict) -> LlmClient:
    return LlmClient(
        config=LlmClientConfig(default_provider="fake", max_retries=0),
        providers={"fake": RecordingDeliveryProvider(response)},
    )


class RecordingDeliveryProvider:
    name = "fake"

    def __init__(self, response: dict):
        self.response = response
        self.calls: list[str] = []
        self.last_messages: list[dict[str, str]] = []
        self.timeout_by_task: dict[str, float | None] = {}

    def complete(self, request, config):
        self.calls.append(request.task)
        self.timeout_by_task[request.task] = request.timeout_seconds
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
        "original_requirement": "交付阶段需要整合补丁、测试和评审结论。",
        "structured_prd": {"summary": "最终交付状态需要可读。"},
        "design_doc": {"summary": "DELIVERY_INTEGRATION 是最后一个执行平面阶段。"},
        "diff_patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
        "code_generation_report": {
            "status": "GENERATED",
            "changed_files": [{"path": "a.py", "operation": "update", "summary": "update"}],
        },
        "test_results": {"status": "GENERATED", "test_commands": [{"command": "python -m unittest"}]},
        "test_run_results": {
            "status": "PASSED",
            "execution_results": [{"command": "python -m unittest", "status": "PASSED", "exit_code": 0}],
        },
        "review_report": {
            "status": "APPROVED",
            "summary": "可以交付。",
            "findings": [],
            "quality_gates": [{"name": "review", "status": "PASSED"}],
            "source": "review_agent",
        },
        "pipeline_context": {"version": 1, "code_contexts": [], "artifact_index": {}},
    }


class DeliveryAgentTest(unittest.TestCase):
    def test_generates_delivery_status_with_fake_llm(self):
        from src.agents.delivery_agent import DeliveryAgent

        result = DeliveryAgent(
            llm_client=delivery_llm_client(
                {
                    "status": "READY",
                    "summary": "补丁已生成，测试通过，评审批准。",
                    "release_notes": ["新增交付集成阶段。"],
                    "artifacts": [
                        {"name": "code_diff", "type": "diff", "status": "READY"},
                        {"name": "review_report", "type": "json", "status": "READY"},
                    ],
                    "verification": [
                        {"name": "unit_tests", "status": "PASSED", "evidence": "python -m unittest"}
                    ],
                    "handoff_checklist": [
                        {"item": "人工检查评审结论", "status": "DONE"},
                        {"item": "确认测试结果", "status": "DONE"},
                    ],
                    "risks": [],
                    "open_questions": [],
                    "quality": {"confidence": "HIGH"},
                }
            )
        ).run(sample_state())

        self.assertEqual(result["current_step"], "DELIVERY_INTEGRATION")
        self.assertEqual(result["error_logs"], [])
        self.assertEqual(result["delivery_status"]["source"], "delivery_agent")
        self.assertEqual(result["delivery_status"]["status"], "READY")
        self.assertEqual(
            result["pipeline_context"]["artifact_index"]["DELIVERY_INTEGRATION"]["delivery_status"]["source"],
            "delivery_agent",
        )

    def test_prompt_includes_review_and_test_status(self):
        from src.agents.delivery_agent import DeliveryAgent

        provider = RecordingDeliveryProvider(
            {
                "status": "BLOCKED",
                "summary": "评审未通过，暂不可交付。",
                "release_notes": [],
                "artifacts": [],
                "verification": [],
                "handoff_checklist": [],
                "risks": ["review blocked"],
                "open_questions": ["等待修复"],
            }
        )
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={"fake": provider},
        )

        DeliveryAgent(llm_client=client).run(sample_state())

        payload = json.loads(provider.last_messages[-1]["content"])
        serialized = json.dumps(provider.last_messages, ensure_ascii=False)
        self.assertEqual(provider.calls, ["delivery_integration"])
        self.assertEqual(payload["response_language"], "zh-Hans")
        self.assertIn("review_report", payload)
        self.assertIn("test_run_results", payload)
        self.assertIn("只输出符合 schema 的 JSON", serialized)

    def test_missing_review_report_returns_blocked_status(self):
        from src.agents.delivery_agent import DeliveryAgent

        state = sample_state()
        state.pop("review_report")
        result = DeliveryAgent(llm_client=delivery_llm_client({})).run(state)

        self.assertEqual(result["current_step"], "DELIVERY_INTEGRATION")
        self.assertEqual(result["delivery_status"]["status"], "BLOCKED")
        self.assertIn("review_report is required", result["error_logs"][0])

    def test_flow_node_invokes_delivery_agent(self):
        from src.graph.flow import integrate_delivery_node

        class StubDeliveryAgent:
            def run(self, state):
                return {
                    "delivery_status": {"status": "READY", "source": "delivery_agent"},
                    "current_step": "DELIVERY_INTEGRATION",
                    "error_logs": [],
                }

        with patch("src.graph.flow.DeliveryAgent", StubDeliveryAgent):
            result = integrate_delivery_node(sample_state())

        self.assertEqual(result["current_step"], "DELIVERY_INTEGRATION")
        self.assertEqual(result["delivery_status"]["source"], "delivery_agent")


if __name__ == "__main__":
    unittest.main()
