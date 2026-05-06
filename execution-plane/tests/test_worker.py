import asyncio
import unittest
from unittest.mock import patch

from src.workers.activities import analyze_requirement, design_system, registered_activities, _state_from_request
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


if __name__ == "__main__":
    unittest.main()
