import asyncio
import unittest
from unittest.mock import patch

from src.workers.activities import (
    analyze_requirement,
    design_system,
    generate_code,
    generate_tests,
    apply_and_run_tests,
    registered_activities,
    _state_from_request,
    concise_activity_error,
)
from src.workers.worker import TASK_QUEUE


class StubRequirementAgent:
    def run(self, state):
        return {
            "structured_prd": {
                "summary": state.get("original_requirement", ""),
                "source": "requirement_agent",
            },
            "code_context": {"inspected_files": [], "search_queries": [], "source": "requirement_agent"},
            "current_step": "REQUIREMENT_ANALYSIS",
            "error_logs": [],
        }


class StubDesignAgent:
    def run(self, state):
        return {
            "design_doc": {
                "summary": state["structured_prd"]["summary"],
                "source": "design_agent",
            },
            "pipeline_context": state.get("pipeline_context", {}),
            "current_step": "SYSTEM_DESIGN",
            "error_logs": [],
        }


class StubCoderAgent:
    def run(self, state):
        return {
            "diff_patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-old\n+new\n",
            "code_generation_report": {
                "status": "GENERATED",
                "source": "coder_agent",
                "summary": state["design_doc"]["summary"],
            },
            "pipeline_context": state.get("pipeline_context", {}),
            "current_step": "CODE_GENERATION",
            "error_logs": [],
        }


class StubTestAgent:
    def run(self, state):
        return {
            "test_results": {
                "status": "GENERATED",
                "source": "test_agent",
                "summary": "生成测试代码并记录执行结果",
                "test_diff_patch": "diff --git a/tests/test_a.py b/tests/test_a.py\n--- a/tests/test_a.py\n+++ b/tests/test_a.py\n@@ -0,0 +1 @@\n+def test_a(): pass\n",
                "execution_results": [
                    {
                        "command": "python -m unittest tests.test_test_agent",
                        "status": "NOT_RUN",
                    }
                ],
            },
            "pipeline_context": state.get("pipeline_context", {}),
            "current_step": "TEST_GENERATION",
            "error_logs": [],
        }


class StubApplyAndRunTestsAgent:
    def run(self, state):
        return {
            "test_run_results": {
                "status": "PASSED",
                "source": "apply_and_run_tests_agent",
                "summary": "等待人工应用补丁并回填真实测试结果。",
            },
            "pipeline_context": state.get("pipeline_context", {}),
            "current_step": "APPLY_AND_RUN_TESTS",
            "error_logs": [],
        }


class TemporalWorkerActivitiesTest(unittest.TestCase):
    def test_registered_activity_names_match_java_contract(self):
        names = [
            getattr(activity, "__temporal_activity_definition").name
            for activity in registered_activities()
        ]

        self.assertEqual(
            names,
            [
                "analyzeRequirement",
                "designSystem",
                "generateCode",
                "generateTests",
                "applyAndRunTests",
                "reviewCode",
                "integrateDelivery",
            ],
        )
        self.assertEqual(TASK_QUEUE, "DEVFLOW_TASK_QUEUE")

    def test_activity_returns_java_stage_execution_result_shape(self):
        with patch("src.graph.flow.RequirementAgent", StubRequirementAgent):
            result = asyncio.run(
                analyze_requirement(
                    {
                        "pipelineId": "00000000-0000-0000-0000-000000000001",
                        "stageName": "REQUIREMENT_ANALYSIS",
                        "requirement": "实现用户登录、注册和鉴权",
                        "globalContext": {},
                        "previousOutput": {},
                    }
                )
            )

        self.assertEqual(result["stageName"], "REQUIREMENT_ANALYSIS")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertIn("outputPayload", result)
        self.assertIn("structured_prd", result["outputPayload"])
        self.assertIn("code_context", result["outputPayload"])

    def test_design_activity_returns_design_doc_and_pipeline_context(self):
        with patch("src.graph.flow.DesignAgent", StubDesignAgent):
            result = asyncio.run(
                design_system(
                    {
                        "pipelineId": "00000000-0000-0000-0000-000000000001",
                        "stageName": "SYSTEM_DESIGN",
                        "requirement": "设计控制台",
                        "globalContext": {},
                        "previousOutput": {
                            "structured_prd": {"summary": "控制台需求"},
                            "pipeline_context": {
                                "version": 1,
                                "code_contexts": [],
                                "artifact_index": {},
                            },
                        },
                    }
                )
            )

        self.assertEqual(result["stageName"], "SYSTEM_DESIGN")
        self.assertEqual(result["status"], "COMPLETED")
        self.assertEqual(result["outputPayload"]["design_doc"]["source"], "design_agent")
        self.assertIn("pipeline_context", result["outputPayload"])

    def test_code_generation_activity_returns_diff_patch_for_console_display(self):
        with patch("src.graph.flow.CoderAgent", StubCoderAgent):
            result = asyncio.run(
                generate_code(
                    {
                        "pipelineId": "00000000-0000-0000-0000-000000000001",
                        "stageName": "CODE_GENERATION",
                        "requirement": "生成代码",
                        "globalContext": {},
                        "previousOutput": {
                            "design_doc": {"summary": "代码生成方案"},
                            "pipeline_context": {
                                "version": 1,
                                "code_contexts": [],
                                "artifact_index": {},
                            },
                        },
                    }
                )
            )

        self.assertEqual(result["stageName"], "CODE_GENERATION")
        self.assertIn("diff --git", result["outputPayload"]["diff_patch"])
        self.assertEqual(result["outputPayload"]["code_generation_report"]["source"], "coder_agent")
        self.assertIn("pipeline_context", result["outputPayload"])

    def test_test_generation_activity_returns_test_results_for_console_display(self):
        with patch("src.graph.flow.TestAgent", StubTestAgent):
            result = asyncio.run(
                generate_tests(
                    {
                        "pipelineId": "00000000-0000-0000-0000-000000000001",
                        "stageName": "TEST_GENERATION",
                        "requirement": "生成测试",
                        "globalContext": {},
                        "previousOutput": {
                            "diff_patch": "diff --git a/a.py b/a.py\n--- a/a.py\n+++ b/a.py\n@@ -1 +1 @@\n-a\n+b\n",
                            "pipeline_context": {
                                "version": 1,
                                "code_contexts": [],
                                "artifact_index": {},
                            },
                        },
                    }
                )
            )

        self.assertEqual(result["stageName"], "TEST_GENERATION")
        self.assertIn("test_results", result["outputPayload"])
        self.assertIn("diff --git", result["outputPayload"]["test_results"]["test_diff_patch"])
        self.assertEqual(result["outputPayload"]["test_results"]["source"], "test_agent")
        self.assertIn("pipeline_context", result["outputPayload"])

    def test_apply_and_run_tests_activity_returns_test_run_result(self):
        with patch("src.graph.flow.ApplyAndRunTestsAgent", StubApplyAndRunTestsAgent):
            result = asyncio.run(
                apply_and_run_tests(
                    {
                        "pipelineId": "00000000-0000-0000-0000-000000000001",
                        "stageName": "APPLY_AND_RUN_TESTS",
                        "requirement": "运行测试",
                        "globalContext": {},
                        "previousOutput": {
                            "test_results": {
                                "test_diff_patch": "diff --git a/tests/test_a.py b/tests/test_a.py\n",
                                "test_commands": [{"command": "python -m unittest discover -s tests"}],
                            },
                            "pipeline_context": {
                                "version": 1,
                                "code_contexts": [],
                                "artifact_index": {},
                            },
                        },
                    }
                )
            )

        self.assertEqual(result["stageName"], "APPLY_AND_RUN_TESTS")
        self.assertEqual(result["outputPayload"]["test_run_results"]["status"], "PASSED")
        self.assertEqual(result["outputPayload"]["test_run_results"]["source"], "apply_and_run_tests_agent")

    def test_state_from_request_exposes_repository_context_to_agents(self):
        state = _state_from_request(
            {
                "requirement": "修改登录页面",
                "globalContext": {
                    "repository": {
                        "rootPath": "D:/projects/demo",
                        "includePaths": ["src"],
                        "excludePaths": ["node_modules"],
                    }
                },
                "previousOutput": {
                    "code_context": {
                        "inspected_files": ["src/App.tsx"],
                    }
                },
            }
        )

        self.assertEqual(state["repository_context"]["rootPath"], "D:/projects/demo")
        self.assertEqual(state["code_context"]["inspected_files"], ["src/App.tsx"])

    def test_state_from_request_preserves_shared_pipeline_context(self):
        state = _state_from_request(
            {
                "requirement": "继续设计",
                "globalContext": {},
                "previousOutput": {
                    "pipeline_context": {
                        "version": 1,
                        "code_contexts": [
                            {
                                "stage": "REQUIREMENT_ANALYSIS",
                                "confidence": 0.86,
                                "inspected_files": ["src/App.tsx"],
                            }
                        ],
                    }
                },
            }
        )

        self.assertEqual(
            state["pipeline_context"]["code_contexts"][0]["inspected_files"],
            ["src/App.tsx"],
        )

    def test_concise_activity_error_explains_llm_timeout_without_large_payload(self):
        error = concise_activity_error("SYSTEM_DESIGN", TimeoutError("LLM provider request timed out"))

        self.assertIn("SYSTEM_DESIGN Activity failed", str(error))
        self.assertIn("DEVFLOW_LLM_TIMEOUT_SECONDS", str(error))
        self.assertLess(len(str(error)), 1400)


if __name__ == "__main__":
    unittest.main()
