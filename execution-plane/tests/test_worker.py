import asyncio
import unittest

from src.workers.activities import analyze_requirement, registered_activities
from src.workers.worker import TASK_QUEUE


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


if __name__ == "__main__":
    unittest.main()
