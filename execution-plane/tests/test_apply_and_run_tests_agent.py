from __future__ import annotations

import json
import os
import sys
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

    def test_runs_test_command_with_structured_working_directory(self):
        from src.agents.apply_and_run_tests_agent import run_test_command

        root = make_workspace()
        try:
            demo = root / "demo"
            demo.mkdir()
            (demo / "check_cwd.py").write_text(
                "from pathlib import Path\n"
                "Path('cwd-marker.txt').write_text('ok', encoding='utf-8')\n",
                encoding="utf-8",
            )

            result = run_test_command(
                root,
                {
                    "command": f"{sys.executable} check_cwd.py",
                    "working_directory": "demo",
                    "purpose": "verify structured cwd",
                },
            )

            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["working_directory"], "demo")
            self.assertTrue((demo / "cwd-marker.txt").exists())
            self.assertFalse((root / "cwd-marker.txt").exists())
        finally:
            cleanup_workspace(root)

    def test_normalizes_simple_cd_and_command_without_shell_execution(self):
        from src.agents.apply_and_run_tests_agent import normalize_test_commands, run_test_command

        root = make_workspace()
        try:
            demo = root / "demo"
            demo.mkdir()
            (demo / "check_cwd.py").write_text(
                "from pathlib import Path\n"
                "Path('legacy-marker.txt').write_text('ok', encoding='utf-8')\n",
                encoding="utf-8",
            )

            commands = normalize_test_commands(
                [{"command": f"cd demo && {sys.executable} check_cwd.py"}]
            )
            result = run_test_command(root, commands[0])

            self.assertEqual(commands[0]["working_directory"], "demo")
            self.assertEqual(commands[0]["command"], f"{sys.executable} check_cwd.py")
            self.assertEqual(result["status"], "PASSED")
            self.assertEqual(result["working_directory"], "demo")
            self.assertTrue((demo / "legacy-marker.txt").exists())
        finally:
            cleanup_workspace(root)

    def test_writes_patch_apply_failure_diagnostic_file(self):
        from src.agents.apply_and_run_tests_agent import ApplyAndRunTestsAgent

        root = make_workspace()
        try:
            corrupt_patch = (
                "@@ -0,0 +1,2 @@\n"
                "+one\n"
                "+two\n"
            )

            old_debug_dir = os.environ.get("DEVFLOW_APPLY_DEBUG_DIR")
            os.environ["DEVFLOW_APPLY_DEBUG_DIR"] = str(root / "logs")
            try:
                result = ApplyAndRunTestsAgent().run(
                    {
                        "repository_context": {"rootPath": str(root)},
                        "diff_patch": corrupt_patch,
                        "test_results": {"test_commands": [{"command": "python -m unittest discover -s tests"}]},
                        "pipeline_context": {"version": 1, "code_contexts": [], "artifact_index": {}},
                    }
                )
            finally:
                if old_debug_dir is None:
                    os.environ.pop("DEVFLOW_APPLY_DEBUG_DIR", None)
                else:
                    os.environ["DEVFLOW_APPLY_DEBUG_DIR"] = old_debug_dir

            patch_result = result["test_run_results"]["applied_patches"][0]
            self.assertEqual(result["test_run_results"]["status"], "FAILED")
            self.assertEqual(patch_result["status"], "FAILED")
            self.assertIn("diagnostic_file", patch_result)
            self.assertIn("诊断文件:", result["test_run_results"]["errors"][0])

            diagnostic_path = Path(patch_result["diagnostic_file"])
            self.assertTrue(diagnostic_path.exists())
            diagnostic = json.loads(diagnostic_path.read_text(encoding="utf-8"))
            self.assertEqual(diagnostic["patch_name"], "code_diff")
            self.assertEqual(diagnostic["patch_text"], corrupt_patch)
            self.assertTrue(diagnostic["stderr"])
            self.assertEqual(diagnostic["patch_lines"][1]["line"], 2)
            self.assertEqual(diagnostic["patch_lines"][1]["text"], "+one")
        finally:
            cleanup_workspace(root)

    def test_normalizes_realistic_generated_patch_before_apply(self):
        from src.agents.apply_and_run_tests_agent import apply_patch_text

        root = make_workspace()
        try:
            patch_text = (
                "diff --git a/test/index.html b/test/index.html\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/test/index.html\n"
                "@@ -0,0 +1,13 @@\n"
                "+<!DOCTYPE html>\n"
                "+<html lang=\"zh-CN\">\n"
                "+<head>\n"
                "+    <meta charset=\"UTF-8\">\n"
                "+    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1.0\">\n"
                "+    <title>DevFlow Test Page</title>\n"
                "+</head>\n"
                "+<body>\n"
                "+    <h1>DevFlow Test Environment</h1>\n"
                "+    <p>Open the browser console to verify plugin initialization.</p>\n"
                "+    <script type=\"module\" src=\"./plugin.js\"></script>\n"
                "+</body>\n"
                "+</html>\n"
                "diff --git a/test/plugin.js b/test/plugin.js\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/test/plugin.js\n"
                "@@ -0,0 +1,14 @@\n"
                "+/**\n"
                "+ * DevFlow Test Plugin\n"
                "+ */\n"
                "+const DevFlowTestPlugin = {\n"
                "+    init() {\n"
                "+        console.log('DevFlow Test Plugin Initialized');\n"
                "+    }\n"
                "+};\n"
                "+\n"
                "+export default DevFlowTestPlugin;\n"
                "+\n"
                "+// Auto init for smoke verification.\n"
                "+DevFlowTestPlugin.init();\n"
                "diff --git a/test/README.md b/test/README.md\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/test/README.md\n"
                "@@ -0,0 +1,14 @@\n"
                "+# DevFlow Test Infrastructure\n"
                "+\n"
                "+This folder provides a lightweight browser smoke test for DevFlow-Engine.\n"
                "+\n"
                "+## Usage\n"
                "+\n"
                "+1. Open `index.html` in a browser.\n"
                "+2. Open Developer Tools.\n"
                "+3. Confirm the console output: \"DevFlow Test Plugin Initialized\".\n"
                "+4. Confirm `plugin.js` is loaded successfully.\n"
                "+\n"
                "+## 文件说明\n"
                "+\n"
                "+- `index.html`: 测试入口页面。\n"
                "+- `plugin.js`: 插件逻辑代码。\n"
            )

            result = apply_patch_text(root, "code_diff", patch_text)

            self.assertEqual(result["status"], "APPLIED")
            self.assertTrue(result["normalization_applied"])
            self.assertTrue((root / "test" / "index.html").exists())
            self.assertTrue((root / "test" / "plugin.js").exists())
            self.assertTrue((root / "test" / "README.md").exists())
        finally:
            cleanup_workspace(root)

    def test_applies_patch_without_trailing_newline(self):
        from src.agents.apply_and_run_tests_agent import apply_patch_text

        root = make_workspace()
        try:
            patch_text = (
                "diff --git a/demo.txt b/demo.txt\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/demo.txt\n"
                "@@ -0,0 +1,2 @@\n"
                "+one\n"
                "+two"
            )

            result = apply_patch_text(root, "code_diff", patch_text)

            self.assertEqual(result["status"], "APPLIED")
            self.assertTrue(result["normalization_applied"])
            self.assertTrue((root / "demo.txt").exists())
            self.assertEqual((root / "demo.txt").read_text(encoding="utf-8"), "one\ntwo\n")
        finally:
            cleanup_workspace(root)

    def test_applies_chinese_patch_as_utf8_bytes(self):
        from src.agents.apply_and_run_tests_agent import apply_patch_text

        root = make_workspace()
        try:
            chinese_line = "\u8bf7\u6253\u5f00\u6d4f\u89c8\u5668\u63a7\u5236\u53f0\u67e5\u770b\u63d2\u4ef6\u521d\u59cb\u5316\u65e5\u5fd7\u3002"
            patch_text = (
                "diff --git a/readme.md b/readme.md\n"
                "new file mode 100644\n"
                "--- /dev/null\n"
                "+++ b/readme.md\n"
                "@@ -0,0 +1,1 @@\n"
                f"+{chinese_line}\n"
            )

            result = apply_patch_text(root, "code_diff", patch_text)
            output_path = root / "readme.md"

            self.assertEqual(result["status"], "APPLIED")
            self.assertEqual(output_path.read_text(encoding="utf-8"), chinese_line + "\n")
            self.assertIn(chinese_line.encode("utf-8"), output_path.read_bytes())
            self.assertNotIn(chinese_line.encode("gbk"), output_path.read_bytes())
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
