from __future__ import annotations

import json
import unittest
from unittest.mock import patch

from src.llm import LlmClient, LlmClientConfig, LlmResponse


def coder_llm_client(response: dict) -> LlmClient:
    return LlmClient(
        config=LlmClientConfig(default_provider="fake", max_retries=0),
        providers={"fake": RecordingCoderProvider(response)},
    )


class RecordingCoderProvider:
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
        "original_requirement": "实现负责代码生成的 Agent 节点逻辑。",
        "structured_prd": {
            "summary": "代码生成阶段需要输出可审查的 diff_patch。",
            "acceptance_criteria": [
                {
                    "id": "AC-001",
                    "description": "输出统一 diff",
                    "verification": "检查 CODE_GENERATION 阶段产物",
                }
            ],
        },
        "design_doc": {
            "summary": "新增 CoderAgent 并接入 flow.py。",
            "modules": [
                {
                    "name": "CoderAgent",
                    "responsibility": "根据 design_doc 生成 unified diff",
                }
            ],
            "file_plan": [
                {
                    "path": "execution-plane/src/agents/coder_agent.py",
                    "operation": "create",
                    "reason": "实现代码生成 Agent",
                    "change_summary": "新增 LangGraph 子图",
                    "validation": "运行 CoderAgent 单元测试",
                },
                {
                    "path": "execution-plane/src/graph/flow.py",
                    "operation": "update",
                    "reason": "接入 CoderAgent",
                    "change_summary": "替换占位 generate_code_node",
                    "validation": "运行 flow 测试",
                },
            ],
            "risks": ["diff 格式不合法会阻塞后续测试生成"],
        },
        "code_context": {
            "status": "COMPLETE",
            "root_path": "D:/repo",
            "inspected_files": [
                "execution-plane/src/graph/flow.py",
                "execution-plane/src/agents/design_agent.py",
            ],
            "evidence": [
                {
                    "filePath": "execution-plane/src/graph/flow.py",
                    "lineStart": 30,
                    "lineEnd": 40,
                    "excerpt": "def generate_code_node(state): ...",
                    "relevanceReason": "当前代码生成节点仍是占位逻辑",
                }
            ],
            "confidence": 0.86,
        },
        "pipeline_context": {
            "version": 1,
            "code_contexts": [],
            "artifact_index": {},
        },
    }


class CoderAgentTest(unittest.TestCase):
    def test_generates_unified_diff_with_fake_llm(self):
        from src.agents.coder_agent import CoderAgent

        diff_patch = """diff --git a/execution-plane/src/graph/flow.py b/execution-plane/src/graph/flow.py
--- a/execution-plane/src/graph/flow.py
+++ b/execution-plane/src/graph/flow.py
@@ -1,3 +1,4 @@
+from src.agents import CoderAgent
 def generate_code_node(state):
-    return {"diff_patch": "reserved"}
+    return CoderAgent().run(state)
"""
        result = CoderAgent(
            llm_client=coder_llm_client(
                {
                    "summary": "接入 CoderAgent 生成 diff_patch。",
                    "diff_patch": diff_patch,
                    "changed_files": [
                        {
                            "path": "execution-plane/src/graph/flow.py",
                            "operation": "update",
                            "summary": "替换占位节点",
                        }
                    ],
                    "risks": ["需要确认导入路径"],
                    "open_questions": [],
                    "quality": {"confidence": "HIGH"},
                }
            )
        ).run(sample_state())

        self.assertEqual(result["current_step"], "CODE_GENERATION")
        self.assertEqual(result["error_logs"], [])
        self.assertIn("diff --git", result["diff_patch"])
        self.assertEqual(result["code_generation_report"]["source"], "coder_agent")
        self.assertEqual(
            result["pipeline_context"]["artifact_index"]["CODE_GENERATION"]["diff_patch"],
            diff_patch,
        )

    def test_prompt_preserves_language_and_forbids_file_writes(self):
        from src.agents.coder_agent import CoderAgent

        provider = RecordingCoderProvider(
            {
                "summary": "生成补丁",
                "diff_patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
                "changed_files": [{"path": "a.py", "operation": "update", "summary": "更新"}],
                "risks": [],
                "open_questions": [],
            }
        )
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={"fake": provider},
        )

        CoderAgent(llm_client=client).run(sample_state())

        serialized_messages = json.dumps(provider.last_messages, ensure_ascii=False)
        user_payload = json.loads(provider.last_messages[1]["content"])
        self.assertEqual(user_payload["response_language"], "zh-Hans")
        self.assertIn("只生成 unified diff", serialized_messages)
        self.assertIn("不能写入文件", serialized_messages)
        self.assertIn("不能执行 git", serialized_messages)

    def test_missing_design_doc_returns_diagnostic_patch(self):
        from src.agents.coder_agent import CoderAgent

        result = CoderAgent(llm_client=coder_llm_client({})).run(
            {"original_requirement": "缺少设计文档"}
        )

        self.assertEqual(result["current_step"], "CODE_GENERATION")
        self.assertIn("design_doc is required", result["error_logs"][0])
        self.assertEqual(result["diff_patch"], "")
        self.assertEqual(result["code_generation_report"]["status"], "BLOCKED")

    def test_flow_node_invokes_coder_agent(self):
        from src.graph.flow import generate_code_node

        class StubCoderAgent:
            def run(self, state):
                return {
                    "diff_patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n",
                    "code_generation_report": {"source": "coder_agent"},
                    "current_step": "CODE_GENERATION",
                    "error_logs": [],
                }

        with patch("src.graph.flow.CoderAgent", StubCoderAgent):
            result = generate_code_node({"design_doc": {"summary": "方案"}})

        self.assertEqual(result["current_step"], "CODE_GENERATION")
        self.assertEqual(result["code_generation_report"]["source"], "coder_agent")


if __name__ == "__main__":
    unittest.main()
