from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.llm import LlmClient, LlmClientConfig, LlmResponse


def test_llm_client(response: dict) -> LlmClient:
    return LlmClient(
        config=LlmClientConfig(default_provider="fake", max_retries=0),
        providers={"fake": RecordingTestProvider(response)},
    )


class RecordingTestProvider:
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
        "original_requirement": "为代码生成阶段补充测试生成 Agent。",
        "structured_prd": {
            "summary": "测试生成阶段需要输出测试代码和执行结果。",
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "description": "输出 test_results",
                    "verification": "检查 TEST_GENERATION 阶段产物",
                }
            ],
        },
        "design_doc": {
            "summary": "新增 TestAgent 并接入 flow.py。",
            "test_strategy": [
                {
                    "name": "Agent 单元测试",
                    "description": "使用 Fake LLM 验证 test_results 结构。",
                }
            ],
            "file_plan": [
                {
                    "path": "execution-plane/src/agents/test_agent.py",
                    "operation": "create",
                    "reason": "实现测试生成 Agent",
                    "validation": "运行 tests.test_test_agent",
                }
            ],
        },
        "diff_patch": (
            "diff --git a/execution-plane/src/graph/flow.py b/execution-plane/src/graph/flow.py\n"
            "--- a/execution-plane/src/graph/flow.py\n"
            "+++ b/execution-plane/src/graph/flow.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-def generate_tests_node(state):\n"
            "+def generate_tests_node(state):\n"
        ),
        "code_generation_report": {
            "status": "GENERATED",
            "changed_files": [{"path": "execution-plane/src/graph/flow.py", "operation": "update"}],
        },
        "code_context": {
            "status": "COMPLETE",
            "root_path": "D:/repo",
            "inspected_files": ["execution-plane/src/graph/flow.py"],
            "evidence": [
                {
                    "filePath": "execution-plane/src/graph/flow.py",
                    "lineStart": 40,
                    "lineEnd": 48,
                    "excerpt": "def generate_tests_node(state): ...",
                    "relevanceReason": "测试生成节点当前仍是占位逻辑",
                }
            ],
            "confidence": 0.84,
        },
        "pipeline_context": {
            "version": 1,
            "code_contexts": [],
            "artifact_index": {
                "CODE_GENERATION": {
                    "diff_patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n"
                }
            },
        },
    }


class TestAgentTest(unittest.TestCase):
    def test_generates_test_results_with_fake_llm(self):
        from src.agents.test_agent import TestAgent

        test_diff = (
            "diff --git a/execution-plane/tests/test_flow.py b/execution-plane/tests/test_flow.py\n"
            "--- a/execution-plane/tests/test_flow.py\n"
            "+++ b/execution-plane/tests/test_flow.py\n"
            "@@ -1,2 +1,3 @@\n"
            "+def test_generate_tests_node_invokes_agent():\n"
            "+    assert True\n"
        )
        result = TestAgent(
            llm_client=test_llm_client(
                {
                    "summary": "为 TestAgent 增加单元测试。",
                    "test_diff_patch": test_diff,
                    "test_files": [
                        {
                            "path": "execution-plane/tests/test_flow.py",
                            "framework": "unittest",
                            "purpose": "验证 flow 节点调用 TestAgent",
                            "assertions": ["current_step 为 TEST_GENERATION"],
                        }
                    ],
                    "test_commands": [
                        {
                            "command": "python -m unittest tests.test_test_agent",
                            "purpose": "运行 TestAgent 单元测试",
                        }
                    ],
                    "execution_results": [
                        {
                            "command": "python -m unittest tests.test_test_agent",
                            "status": "NOT_RUN",
                            "exit_code": None,
                            "stdout": "",
                            "stderr": "补丁尚未应用，不能运行生成的测试。",
                        }
                    ],
                    "coverage_focus": ["TestAgent 输入校验", "测试 diff 展示"],
                    "risks": [],
                    "open_questions": [],
                    "quality": {"confidence": "HIGH"},
                }
            )
        ).run(sample_state())

        self.assertEqual(result["current_step"], "TEST_GENERATION")
        self.assertEqual(result["error_logs"], [])
        self.assertIn("test_diff_patch", result["test_results"])
        self.assertIn("diff --git", result["test_results"]["test_diff_patch"])
        self.assertEqual(result["test_results"]["source"], "test_agent")
        self.assertNotIn("execution_results", result["test_results"])
        self.assertEqual(
            result["pipeline_context"]["artifact_index"]["TEST_GENERATION"]["test_results"]["source"],
            "test_agent",
        )

    def test_prompt_uses_diff_and_forbids_workspace_writes(self):
        from src.agents.test_agent import TestAgent

        provider = RecordingTestProvider(
            {
                "summary": "生成测试",
                "test_diff_patch": "diff --git a/tests/test_app.py b/tests/test_app.py\n--- a/tests/test_app.py\n+++ b/tests/test_app.py\n@@ -0,0 +1 @@\n+def test_app(): pass\n",
                "test_files": [{"path": "tests/test_app.py", "framework": "pytest", "purpose": "验证 app"}],
                "test_commands": [{"command": "pytest tests/test_app.py", "purpose": "运行测试"}],
                "execution_results": [{"command": "pytest tests/test_app.py", "status": "NOT_RUN"}],
                "coverage_focus": [],
                "risks": [],
                "open_questions": [],
            }
        )
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={"fake": provider},
        )

        TestAgent(llm_client=client).run(sample_state())

        serialized_messages = json.dumps(provider.last_messages, ensure_ascii=False)
        user_payload = json.loads(provider.last_messages[-1]["content"])
        self.assertEqual(user_payload["response_language"], "zh-Hans")
        self.assertIn("diff_patch", user_payload)
        self.assertIn("只生成测试补丁", serialized_messages)
        self.assertIn("不能写入文件", serialized_messages)
        self.assertIn("不能执行 shell 命令", serialized_messages)

    def test_missing_diff_patch_returns_blocked_result(self):
        from src.agents.test_agent import TestAgent

        result = TestAgent(llm_client=test_llm_client({})).run(
            {"original_requirement": "缺少代码 diff", "design_doc": {"summary": "方案"}}
        )

        self.assertEqual(result["current_step"], "TEST_GENERATION")
        self.assertIn("diff_patch is required", result["error_logs"][0])
        self.assertEqual(result["test_results"]["status"], "BLOCKED")
        self.assertEqual(result["test_results"]["test_diff_patch"], "")

    def test_flow_node_invokes_test_agent(self):
        from src.graph.flow import generate_tests_node

        class StubTestAgent:
            def run(self, state):
                return {
                    "test_results": {"status": "GENERATED", "source": "test_agent"},
                    "current_step": "TEST_GENERATION",
                    "error_logs": [],
                }

        with patch("src.graph.flow.TestAgent", StubTestAgent):
            result = generate_tests_node({"diff_patch": "diff --git a/a.py b/a.py\n"})

        self.assertEqual(result["current_step"], "TEST_GENERATION")
        self.assertEqual(result["test_results"]["source"], "test_agent")


if __name__ == "__main__":
    unittest.main()
