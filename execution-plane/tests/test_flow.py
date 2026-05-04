import unittest
from unittest.mock import patch

from src.graph.flow import build_devflow_graph, run_stage


class StubRequirementAgent:
    def run(self, state):
        return {
            "structured_prd": {
                "summary": state.get("original_requirement", ""),
                "acceptance_criteria": [
                    {
                        "id": "AC-001",
                        "description": "需求已结构化",
                        "verification": "检查 structured_prd",
                    }
                ],
                "source": "requirement_agent",
            },
            "code_context": {"inspected_files": [], "search_queries": [], "source": "requirement_agent"},
            "current_step": "REQUIREMENT_ANALYSIS",
            "error_logs": state.get("error_logs", []),
        }


class DevFlowGraphTest(unittest.TestCase):
    def test_graph_runs_all_pipeline_nodes_in_order(self):
        with patch("src.graph.flow.RequirementAgent", StubRequirementAgent):
            graph = build_devflow_graph()
            result = graph.invoke({"original_requirement": "实现用户登录、注册和鉴权"})

        self.assertEqual(result["current_step"], "DELIVERY_INTEGRATION")
        self.assertIn("structured_prd", result)
        self.assertIn("design_doc", result)
        self.assertIn("diff_patch", result)
        self.assertIn("test_results", result)
        self.assertIn("review_report", result)
        self.assertIn("delivery_status", result)
        self.assertEqual(result["error_logs"], [])

    def test_design_stage_keeps_human_feedback_in_state(self):
        result = run_stage(
            "SYSTEM_DESIGN",
            {
                "original_requirement": "实现用户登录、注册和鉴权",
                "structured_prd": {"summary": "登录注册"},
                "human_feedback": "补充数据库表结构",
            },
        )

        self.assertEqual(result["current_step"], "SYSTEM_DESIGN")
        self.assertEqual(result["design_doc"]["feedback"], "补充数据库表结构")


if __name__ == "__main__":
    unittest.main()
