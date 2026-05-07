from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.llm import LlmClient, LlmClientConfig, LlmResponse


def review_llm_client(response: dict) -> LlmClient:
    return LlmClient(
        config=LlmClientConfig(default_provider="fake", max_retries=0),
        providers={"fake": RecordingReviewProvider(response)},
    )


class RecordingReviewProvider:
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
        "original_requirement": "为流水线增加代码评审阶段。",
        "structured_prd": {"summary": "评审阶段需要指出风险和阻塞项。"},
        "design_doc": {
            "summary": "CODE_REVIEW 位于 APPLY_AND_RUN_TESTS 之后。",
            "file_plan": [{"path": "execution-plane/src/graph/flow.py"}],
        },
        "diff_patch": (
            "diff --git a/execution-plane/src/graph/flow.py b/execution-plane/src/graph/flow.py\n"
            "--- a/execution-plane/src/graph/flow.py\n"
            "+++ b/execution-plane/src/graph/flow.py\n"
            "@@ -1 +1 @@\n"
            "-old\n"
            "+new\n"
        ),
        "code_generation_report": {
            "status": "GENERATED",
            "changed_files": [{"path": "execution-plane/src/graph/flow.py", "operation": "update"}],
        },
        "test_results": {
            "status": "GENERATED",
            "test_files": [{"path": "execution-plane/tests/test_flow.py", "framework": "unittest"}],
            "test_commands": [{"command": "python -m unittest tests.test_flow"}],
        },
        "test_run_results": {
            "status": "PASSED",
            "execution_results": [
                {"command": "python -m unittest tests.test_flow", "status": "PASSED", "exit_code": 0}
            ],
        },
        "pipeline_context": {"version": 1, "code_contexts": [], "artifact_index": {}},
    }


class ReviewAgentTest(unittest.TestCase):
    def test_generates_review_report_with_fake_llm(self):
        from src.agents.review_agent import ReviewAgent

        result = ReviewAgent(
            llm_client=review_llm_client(
                {
                    "status": "APPROVED",
                    "summary": "补丁范围清晰，测试已通过。",
                    "findings": [
                        {
                            "severity": "LOW",
                            "file_path": "execution-plane/src/graph/flow.py",
                            "line": 1,
                            "description": "仅需确认真实流水线阶段顺序。",
                            "recommendation": "在集成测试中覆盖完整顺序。",
                        }
                    ],
                    "quality_gates": [
                        {"name": "tests", "status": "PASSED", "evidence": "python -m unittest tests.test_flow"}
                    ],
                    "risks": ["后续交付阶段需要引用评审结论。"],
                    "open_questions": [],
                    "quality": {"confidence": "HIGH"},
                }
            )
        ).run(sample_state())

        self.assertEqual(result["current_step"], "CODE_REVIEW")
        self.assertEqual(result["error_logs"], [])
        self.assertEqual(result["review_report"]["source"], "review_agent")
        self.assertEqual(result["review_report"]["status"], "APPROVED")
        self.assertEqual(
            result["pipeline_context"]["artifact_index"]["CODE_REVIEW"]["review_report"]["source"],
            "review_agent",
        )

    def test_prompt_includes_patch_test_results_and_language(self):
        from src.agents.review_agent import ReviewAgent

        provider = RecordingReviewProvider(
            {
                "status": "NEEDS_CHANGES",
                "summary": "需要处理一个风险。",
                "findings": [{"severity": "HIGH", "description": "风险", "recommendation": "修复"}],
                "quality_gates": [{"name": "tests", "status": "PASSED"}],
                "risks": [],
                "open_questions": [],
            }
        )
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={"fake": provider},
        )

        ReviewAgent(llm_client=client).run(sample_state())

        payload = json.loads(provider.last_messages[-1]["content"])
        serialized = json.dumps(provider.last_messages, ensure_ascii=False)
        self.assertEqual(provider.calls, ["code_review"])
        self.assertEqual(payload["response_language"], "zh-Hans")
        self.assertIn("diff_patch", payload)
        self.assertIn("test_run_results", payload)
        self.assertIn("只输出符合 schema 的 JSON", serialized)

    def test_missing_diff_patch_returns_blocked_report(self):
        from src.agents.review_agent import ReviewAgent

        result = ReviewAgent(llm_client=review_llm_client({})).run(
            {"original_requirement": "评审缺少补丁。", "test_run_results": {"status": "PASSED"}}
        )

        self.assertEqual(result["current_step"], "CODE_REVIEW")
        self.assertEqual(result["review_report"]["status"], "BLOCKED")
        self.assertIn("diff_patch is required", result["error_logs"][0])

    def test_flow_node_invokes_review_agent(self):
        from src.graph.flow import review_code_node

        class StubReviewAgent:
            def run(self, state):
                return {
                    "review_report": {"status": "APPROVED", "source": "review_agent"},
                    "current_step": "CODE_REVIEW",
                    "error_logs": [],
                }

        with patch("src.graph.flow.ReviewAgent", StubReviewAgent):
            result = review_code_node(sample_state())

        self.assertEqual(result["current_step"], "CODE_REVIEW")
        self.assertEqual(result["review_report"]["source"], "review_agent")


if __name__ == "__main__":
    unittest.main()
