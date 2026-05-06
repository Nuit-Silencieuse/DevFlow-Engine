from __future__ import annotations

import unittest
import uuid
from pathlib import Path


TMP_ROOT = Path(__file__).resolve().parents[1] / ".test_tmp"


class ApplyAndRunTestsAgentTest(unittest.TestCase):
    def test_applies_code_and_test_diff_then_runs_command(self):
        from src.agents.apply_and_run_tests_agent import ApplyAndRunTestsAgent

        root = make_workspace()
        try:
            (root / "src").mkdir()
            (root / "tests").mkdir()
            (root / "src" / "app.py").write_text('VALUE = "old"\n', encoding="utf-8")

            result = ApplyAndRunTestsAgent().run(
                {
                    "repository_context": {"rootPath": str(root)},
                    "diff_patch": (
                        "diff --git a/src/app.py b/src/app.py\n"
                        "--- a/src/app.py\n"
                        "+++ b/src/app.py\n"
                        "@@ -1 +1 @@\n"
                        '-VALUE = "old"\n'
                        '+VALUE = "new"\n'
                    ),
                    "test_results": {
                        "test_diff_patch": (
                            "diff --git a/tests/test_app.py b/tests/test_app.py\n"
                            "new file mode 100644\n"
                            "--- /dev/null\n"
                            "+++ b/tests/test_app.py\n"
                            "@@ -0,0 +1,7 @@\n"
                            "+import pathlib\n"
                            "+import unittest\n"
                            "+\n"
                            "+class AppTest(unittest.TestCase):\n"
                            "+    def test_value(self):\n"
                            '+        value = pathlib.Path("src/app.py").read_text(encoding="utf-8").strip()\n'
                            '+        self.assertEqual(value, \'VALUE = "new"\')\n'
                        ),
                        "test_commands": [
                            {
                                "command": "python -m unittest discover -s tests",
                                "purpose": "run generated unittest",
                            }
                        ],
                    },
                    "pipeline_context": {"version": 1, "code_contexts": [], "artifact_index": {}},
                }
            )

            self.assertEqual(result["current_step"], "APPLY_AND_RUN_TESTS")
            self.assertEqual(result["test_run_results"]["status"], "PASSED")
            self.assertEqual((root / "src" / "app.py").read_text(encoding="utf-8"), 'VALUE = "new"\n')
            self.assertEqual(result["test_run_results"]["execution_results"][0]["status"], "PASSED")
            self.assertEqual(result["test_run_results"]["source"], "apply_and_run_tests_agent")
        finally:
            cleanup_workspace(root)

    def test_reports_test_command_failure_with_error_output(self):
        from src.agents.apply_and_run_tests_agent import ApplyAndRunTestsAgent

        root = make_workspace()
        try:
            (root / "tests").mkdir()

            result = ApplyAndRunTestsAgent().run(
                {
                    "repository_context": {"rootPath": str(root)},
                    "diff_patch": "",
                    "test_results": {
                        "test_diff_patch": (
                            "diff --git a/tests/test_failure.py b/tests/test_failure.py\n"
                            "new file mode 100644\n"
                            "--- /dev/null\n"
                            "+++ b/tests/test_failure.py\n"
                            "@@ -0,0 +1,5 @@\n"
                            "+import unittest\n"
                            "+\n"
                            "+class FailureTest(unittest.TestCase):\n"
                            "+    def test_failure(self):\n"
                            "+        self.assertEqual(1, 2)\n"
                        ),
                        "test_commands": [{"command": "python -m unittest discover -s tests"}],
                    },
                    "pipeline_context": {"version": 1, "code_contexts": [], "artifact_index": {}},
                }
            )

            self.assertEqual(result["test_run_results"]["status"], "FAILED")
            self.assertEqual(result["test_run_results"]["execution_results"][0]["status"], "FAILED")
            self.assertTrue(result["test_run_results"]["errors"])
            self.assertIn("执行失败", result["test_run_results"]["errors"][0])
        finally:
            cleanup_workspace(root)


def make_workspace() -> Path:
    TMP_ROOT.mkdir(exist_ok=True)
    root = TMP_ROOT / f"apply-agent-{uuid.uuid4().hex}"
    root.mkdir()
    return root


def cleanup_workspace(root: Path) -> None:
    for path in sorted(root.rglob("*"), key=lambda item: len(item.parts), reverse=True):
        if path.is_file():
            path.unlink(missing_ok=True)
        elif path.is_dir():
            path.rmdir()
    root.rmdir()


if __name__ == "__main__":
    unittest.main()
