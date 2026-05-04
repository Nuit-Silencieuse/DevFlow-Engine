from __future__ import annotations

import asyncio
import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from src.llm import FakeProvider, LlmClient, LlmClientConfig, LlmTraceRecorder
from src.llm.config import (
    EXECUTION_PLANE_ROOT,
    load_env_file,
    merge_env_sources,
    resolve_env_file,
)
from src.workers.activities import analyze_requirement


def fake_llm_client(response: dict) -> LlmClient:
    return LlmClient(
        config=LlmClientConfig(default_provider="fake", max_retries=0),
        providers={"fake": FakeProvider(response_text=json.dumps(response, ensure_ascii=False))},
    )


class RequirementAgentTest(unittest.TestCase):
    def test_generates_structured_prd_with_fake_llm(self):
        from src.agents.requirement_agent import RequirementAgent

        agent = RequirementAgent(
            llm_client=fake_llm_client(
                {
                    "summary": "增加用户登录能力",
                    "problem_statement": "用户需要安全进入系统。",
                    "scope": {"in_scope": ["登录"], "out_of_scope": []},
                    "user_stories": [
                        {"role": "用户", "goal": "登录系统", "benefit": "访问个人功能"}
                    ],
                    "acceptance_criteria": [
                        {
                            "id": "AC-001",
                            "description": "输入正确账号密码时登录成功",
                            "verification": "提交有效凭据后返回成功状态",
                        },
                        {
                            "id": "AC-002",
                            "description": "输入错误密码时拒绝登录",
                            "verification": "提交无效凭据后返回错误提示",
                        },
                    ],
                    "edge_cases": ["空密码"],
                    "non_functional_requirements": {
                        "performance": [],
                        "security": ["密码不得明文保存"],
                        "reliability": [],
                        "compatibility": [],
                    },
                    "domain_terms": ["登录"],
                    "assumptions": [],
                    "open_questions": [],
                    "quality": {
                        "testable_acceptance_criteria": True,
                        "has_open_questions": False,
                        "confidence": "HIGH",
                    },
                }
            )
        )

        result = agent.run({"original_requirement": "实现用户登录"})

        self.assertEqual(result["current_step"], "REQUIREMENT_ANALYSIS")
        self.assertEqual(result["error_logs"], [])
        self.assertEqual(result["structured_prd"]["source"], "requirement_agent")
        self.assertEqual(result["structured_prd"]["summary"], "增加用户登录能力")
        self.assertGreaterEqual(len(result["structured_prd"]["acceptance_criteria"]), 2)

    def test_empty_requirement_returns_diagnostic_error(self):
        from src.agents.requirement_agent import RequirementAgent

        result = RequirementAgent(llm_client=fake_llm_client({})).run(
            {"original_requirement": "   "}
        )

        self.assertEqual(result["current_step"], "REQUIREMENT_ANALYSIS")
        self.assertIn("original_requirement is required", result["error_logs"][0])

    def test_uses_actual_project_repository_context_as_material(self):
        from src.agents.requirement_agent import RequirementAgent

        repo_root = Path(__file__).resolve().parents[2]
        agent = RequirementAgent(
            llm_client=fake_llm_client(
                {
                    "summary": "为执行平面补充需求分析 Agent",
                    "problem_statement": "流水线需要把自然语言需求变成可消费的 PRD。",
                    "user_stories": [
                        {
                            "role": "开发者",
                            "goal": "获得结构化 PRD",
                            "benefit": "后续设计 Agent 可以直接消费",
                        }
                    ],
                    "acceptance_criteria": [
                        {
                            "id": "AC-001",
                            "description": "Requirement Agent 输出 structured_prd",
                            "verification": "运行需求分析阶段后检查 source 字段",
                        },
                        {
                            "id": "AC-002",
                            "description": "Agent 记录被检查的项目文件",
                            "verification": "检查 evidence.inspected_files 非空",
                        },
                    ],
                }
            )
        )

        result = agent.run(
            {
                "original_requirement": "完成 T022 Requirement Agent",
                "repository_context": {
                    "rootPath": str(repo_root),
                    "includePaths": [
                        "specs/001-devflow-engine",
                        "execution-plane/src/llm",
                    ],
                    "excludePaths": [".git", "target", "node_modules", "venv", "__pycache__"],
                    "targetFiles": [
                        "specs/001-devflow-engine/tasks.md",
                        "specs/001-devflow-engine/t022-requirement-agent-design.md",
                    ],
                    "maxFiles": 8,
                    "maxBytes": 120000,
                },
            }
        )

        inspected_files = result["code_context"]["inspected_files"]
        self.assertIn("specs/001-devflow-engine/tasks.md", inspected_files)
        self.assertIn(
            "specs/001-devflow-engine/t022-requirement-agent-design.md",
            inspected_files,
        )
        self.assertEqual(
            result["structured_prd"]["evidence"]["inspected_files"],
            inspected_files,
        )

    def test_trace_file_contains_requirement_agent_intermediate_artifacts(self):
        from src.agents.requirement_agent import RequirementAgent

        trace_path = Path(".test_tmp") / "requirement-agent-trace.jsonl"
        if trace_path.exists():
            trace_path.unlink()
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={
                "fake": FakeProvider(
                    response_text=json.dumps(
                        {
                            "summary": "补充需求分析 Agent",
                            "user_stories": [
                                {"role": "开发者", "goal": "生成 PRD", "benefit": "推进后续设计"}
                            ],
                            "acceptance_criteria": [
                                {
                                    "id": "AC-001",
                                    "description": "输出 PRD",
                                    "verification": "检查 structured_prd",
                                },
                                {
                                    "id": "AC-002",
                                    "description": "输出上下文证据",
                                    "verification": "检查 code_context",
                                },
                            ],
                        },
                        ensure_ascii=False,
                    )
                )
            },
            trace_recorder=LlmTraceRecorder(enabled=True, file_path=trace_path),
        )

        RequirementAgent(llm_client=client).run({"original_requirement": "实现需求分析"})

        trace_content = trace_path.read_text(encoding="utf-8")
        self.assertIn("requirement_agent.analysis_plan", trace_content)
        self.assertIn("llm.request", trace_content)
        self.assertIn("llm.response", trace_content)
        self.assertIn("requirement_agent.validation_report", trace_content)

    def test_flow_node_invokes_requirement_agent(self):
        from src.graph.flow import analyze_requirement_node

        class StubRequirementAgent:
            def run(self, state):
                return {
                    "structured_prd": {"summary": state["original_requirement"], "source": "requirement_agent"},
                    "current_step": "REQUIREMENT_ANALYSIS",
                    "error_logs": [],
                }

        with patch("src.graph.flow.RequirementAgent", StubRequirementAgent):
            result = analyze_requirement_node({"original_requirement": "实现登录"})

        self.assertEqual(result["structured_prd"]["source"], "requirement_agent")

    def test_activity_output_contains_prd_and_code_context(self):
        class StubRequirementAgent:
            def run(self, state):
                return {
                    "structured_prd": {
                        "summary": state["original_requirement"],
                        "source": "requirement_agent",
                    },
                    "code_context": {"inspected_files": ["README.md"]},
                    "current_step": "REQUIREMENT_ANALYSIS",
                    "error_logs": [],
                }

        with patch("src.graph.flow.RequirementAgent", StubRequirementAgent):
            result = asyncio.run(
                analyze_requirement(
                    {
                        "pipelineId": "00000000-0000-0000-0000-000000000001",
                        "stageName": "REQUIREMENT_ANALYSIS",
                        "requirement": "实现登录",
                        "globalContext": {},
                        "previousOutput": {},
                    }
                )
            )

        self.assertEqual(result["outputPayload"]["structured_prd"]["source"], "requirement_agent")
        self.assertEqual(result["outputPayload"]["code_context"]["inspected_files"], ["README.md"])


class RequirementAgentEffectTest(unittest.TestCase):
    def test_real_llm_effect_with_project_repository_context(self):
        from src.agents.requirement_agent import RequirementAgent

        source = merge_env_sources(
            load_env_file(resolve_env_file(os.environ)),
            os.environ,
        )
        if source.get("DEVFLOW_LLM_INTEGRATION_TEST") != "1":
            self.skipTest(
                "set DEVFLOW_LLM_INTEGRATION_TEST=1 in .env.local or environment "
                "to run the real RequirementAgent effect test"
            )

        trace_path = EXECUTION_PLANE_ROOT / "logs" / "requirement-agent-effect.jsonl"
        result_path = EXECUTION_PLANE_ROOT / "logs" / "requirement-agent-effect-result.json"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        for path in (trace_path, result_path):
            if path.exists():
                path.unlink()

        config = LlmClientConfig.from_sources()
        full_stdout = source.get("DEVFLOW_REQUIREMENT_AGENT_EFFECT_FULL_STDOUT") == "1"
        client = LlmClient(
            config=config,
            trace_recorder=LlmTraceRecorder.from_sources(
                env={
                    **source,
                    "DEVFLOW_LLM_TRACE": "1",
                    "DEVFLOW_LLM_TRACE_STDOUT": "1" if full_stdout else "0",
                    "DEVFLOW_LLM_TRACE_FILE": str(trace_path),
                    "DEVFLOW_LLM_TRACE_MAX_CHARS": "120000",
                }
            ),
        )
        repo_root = EXECUTION_PLANE_ROOT.parent
        result = RequirementAgent(llm_client=client).run(
            {
                "original_requirement": (
                    "完成 T022 Requirement Agent：使用 LLM 将用户自然语言需求转为"
                    "结构化 PRD，并结合本项目代码库上下文生成证据。"
                ),
                "repository_context": {
                    "rootPath": str(repo_root),
                    "includePaths": [
                        "specs/001-devflow-engine",
                        "execution-plane/src/llm",
                        "execution-plane/src/context",
                        "execution-plane/src/graph",
                    ],
                    "excludePaths": [
                        ".git",
                        "target",
                        "node_modules",
                        "venv",
                        "__pycache__",
                        ".test_tmp",
                    ],
                    "targetFiles": [
                        "specs/001-devflow-engine/tasks.md",
                        "specs/001-devflow-engine/t022-requirement-agent-design.md",
                        "execution-plane/src/llm/client.py",
                        "execution-plane/src/context/repository_context.py",
                    ],
                    "maxFiles": 8,
                    "maxBytes": 120000,
                },
            }
        )

        prd = result["structured_prd"]
        result_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        print_effect_summary(result, trace_path, result_path)
        print_trace_preview(trace_path)

        self.assertEqual(prd["source"], "requirement_agent")
        self.assertGreaterEqual(len(prd.get("user_stories", [])), 1)
        self.assertGreaterEqual(len(prd.get("acceptance_criteria", [])), 2)
        self.assertTrue(result["code_context"]["inspected_files"])
        self.assertTrue(trace_path.exists())
        trace_content = trace_path.read_text(encoding="utf-8")
        self.assertIn("llm.request", trace_content)
        self.assertIn("llm.response", trace_content)
        self.assertIn("requirement_agent.context_pack", trace_content)


def print_effect_summary(result: dict, trace_path: Path, result_path: Path) -> None:
    prd = result["structured_prd"]
    print("=== RequirementAgent Effect Summary ===")
    print("summary:", prd.get("summary", ""))
    print("source:", prd.get("source", ""))
    print("confidence:", prd.get("quality", {}).get("confidence", ""))
    print("acceptance_criteria_count:", len(prd.get("acceptance_criteria", [])))
    for criterion in prd.get("acceptance_criteria", [])[:5]:
        print(
            "AC:",
            criterion.get("id", ""),
            criterion.get("description", ""),
            "| verification:",
            criterion.get("verification", ""),
        )
    print("open_questions_count:", len(prd.get("open_questions", [])))
    for question in prd.get("open_questions", [])[:5]:
        print("OPEN_QUESTION:", question)
    print("inspected_files:", ",".join(result.get("code_context", {}).get("inspected_files", [])))
    print("trace_file:", trace_path)
    print("result_file:", result_path)


def print_trace_preview(trace_path: Path) -> None:
    print("=== RequirementAgent Trace Preview ===")
    for line in trace_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        event_type = event.get("type")
        payload = event.get("payload", {})
        if event_type == "llm.request":
            print("TRACE_EVENT:", event_type)
            print("provider:", payload.get("provider"), "model:", payload.get("model"))
            for message in payload.get("messages", []):
                content = str(message.get("content", ""))
                print(
                    "message:",
                    message.get("role", ""),
                    content[:800] + ("...[TRUNCATED]" if len(content) > 800 else ""),
                )
        elif event_type == "llm.response":
            print("TRACE_EVENT:", event_type)
            print("latency_ms:", payload.get("latency_ms"), "usage:", payload.get("usage"))
            text = str(payload.get("text", ""))
            print("response_text:", text[:800] + ("...[TRUNCATED]" if len(text) > 800 else ""))
        elif event_type in (
            "requirement_agent.context_pack",
            "requirement_agent.analysis_plan",
            "requirement_agent.validation_report",
        ):
            print("TRACE_EVENT:", event_type)
            if event_type == "requirement_agent.context_pack":
                print("inspected_files:", ",".join(payload.get("inspected_files", [])))
                print("total_bytes:", payload.get("total_bytes"))
            else:
                print(json.dumps(payload, ensure_ascii=False)[:800])


if __name__ == "__main__":
    unittest.main()
