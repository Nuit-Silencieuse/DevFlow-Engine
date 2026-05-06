from __future__ import annotations

import logging
import os
import shlex
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.pipeline_context import append_stage_code_context, normalize_pipeline_context

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


APPLY_AND_RUN_TESTS = "APPLY_AND_RUN_TESTS"
COMMAND_TIMEOUT_SECONDS = 120
OUTPUT_LIMIT = 16_000
logger = logging.getLogger(__name__)


class ApplyAndRunTestsAgent:
    """应用代码与测试补丁，并在目标仓库内执行真实测试命令。

    这个阶段刻意不再要求用户手工复制 diff。前置的 CODE_GENERATION 和
    TEST_GENERATION 已经通过人工检查点完成审查，所以这里的职责是把通过审查
    的代码 diff 与测试 diff 直接落到目标仓库工作区，然后运行 TestAgent 给出的
    测试命令。执行结果统一沉淀到 test_run_results，供控制台展示。
    """

    def run(self, state: DevFlowState) -> DevFlowState:
        error_logs = list(state.get("error_logs", []))
        pipeline_context = normalize_pipeline_context(state.get("pipeline_context"))
        repository_root = resolve_repository_root(state)
        test_results = dict(state.get("test_results") or {})

        if not repository_root:
            test_run_results = blocked_result(
                "无法定位目标仓库根目录，无法自动应用 diff 或运行测试。",
                ["repository_context.rootPath 或 code_context.root_path 为空"],
                test_results,
            )
            error_logs.append("APPLY_AND_RUN_TESTS requires repository root path")
            return self._finalize(state, pipeline_context, test_run_results, error_logs)

        repository_path = Path(repository_root).expanduser()
        if not repository_path.exists() or not repository_path.is_dir():
            test_run_results = blocked_result(
                f"目标仓库根目录不存在或不是目录：{repository_path}",
                [str(repository_path)],
                test_results,
            )
            error_logs.append(f"repository root is not a directory: {repository_path}")
            return self._finalize(state, pipeline_context, test_run_results, error_logs)

        code_patch = str(state.get("diff_patch") or "")
        test_patch = str(test_results.get("test_diff_patch") or test_results.get("testDiffPatch") or "")
        commands = normalize_test_commands(test_results.get("test_commands") or test_results.get("testCommands"))
        patch_results: list[dict[str, Any]] = []

        logger.warning(
            "ApplyAndRunTestsAgent will modify repository files and run tests. root=%s code_patch=%s test_patch=%s commands=%s",
            repository_path,
            bool(code_patch.strip()),
            bool(test_patch.strip()),
            len(commands),
        )

        for patch_name, patch_text in (("code_diff", code_patch), ("test_diff", test_patch)):
            patch_result = apply_patch_text(repository_path, patch_name, patch_text)
            patch_results.append(patch_result)
            if patch_result["status"] == "FAILED":
                logger.warning(
                    "Patch application failed. patch=%s stderr=%s",
                    patch_name,
                    patch_result.get("stderr", "")[:1000],
                )
                test_run_results = failed_apply_result(
                    repository_path,
                    patch_results,
                    commands,
                    f"{patch_name} 应用失败，测试命令未执行。",
                )
                error_logs.append(str(patch_result.get("stderr") or patch_result.get("summary") or "patch failed"))
                return self._finalize(state, pipeline_context, test_run_results, error_logs)

        if not commands:
            test_run_results = blocked_result(
                "测试 diff 已应用，但 TestAgent 没有提供可执行的测试命令。",
                ["test_results.test_commands 为空"],
                test_results,
                repository_path=repository_path,
                applied_patches=patch_results,
            )
            error_logs.append("test_commands is empty")
            return self._finalize(state, pipeline_context, test_run_results, error_logs)

        execution_results = [run_test_command(repository_path, command) for command in commands]
        failed_results = [item for item in execution_results if item.get("status") != "PASSED"]
        status = "FAILED" if failed_results else "PASSED"
        summary = (
            f"已应用 {len([item for item in patch_results if item['status'] == 'APPLIED'])} 个补丁，"
            f"执行 {len(execution_results)} 条测试命令，"
            f"{'存在失败命令' if failed_results else '全部通过'}。"
        )
        errors = [
            build_command_error_message(item)
            for item in failed_results
            if build_command_error_message(item)
        ]

        test_run_results = {
            "status": status,
            "summary": summary,
            "repository_root": str(repository_path),
            "apply_strategy": "AUTO_APPLY_APPROVED_DIFFS",
            "applied_patches": patch_results,
            "test_commands": commands,
            "execution_results": execution_results,
            "errors": errors,
            "source": "apply_and_run_tests_agent",
        }
        error_logs.extend(errors)
        return self._finalize(state, pipeline_context, test_run_results, error_logs)

    def _finalize(
        self,
        state: DevFlowState,
        pipeline_context: dict[str, Any],
        test_run_results: dict[str, Any],
        error_logs: list[str],
    ) -> DevFlowState:
        pipeline_context = append_stage_code_context(
            pipeline_context,
            stage=APPLY_AND_RUN_TESTS,
            agent="apply_and_run_tests_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"test_run_results": test_run_results},
        )
        return {
            "test_run_results": test_run_results,
            "pipeline_context": pipeline_context,
            "current_step": APPLY_AND_RUN_TESTS,
            "error_logs": error_logs,
        }


def resolve_repository_root(state: DevFlowState) -> str:
    repository_context = dict(state.get("repository_context") or {})
    code_context = dict(state.get("code_context") or {})
    pipeline_context = dict(state.get("pipeline_context") or {})
    latest_code_context = dict(pipeline_context.get("latest_code_context") or {})
    return first_text(
        repository_context.get("rootPath"),
        repository_context.get("root_path"),
        code_context.get("root_path"),
        code_context.get("rootPath"),
        latest_code_context.get("root_path"),
        latest_code_context.get("rootPath"),
    )


def apply_patch_text(repository_path: Path, patch_name: str, patch_text: str) -> dict[str, Any]:
    if not patch_text.strip():
        return {
            "name": patch_name,
            "status": "SKIPPED",
            "summary": "没有可应用的 diff。",
            "stdout": "",
            "stderr": "",
            "exit_code": None,
        }

    # 使用 git apply 而不是自定义解析 unified diff，可以复用 Git 对新增、删除、
    # 重命名、上下文校验的成熟处理能力。这里不提交，也不修改索引，只修改工作区文件。
    try:
        completed = subprocess.run(
            ["git", "apply", "--whitespace=nowarn", "-"],
            cwd=repository_path,
            input=patch_text,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        return {
            "name": patch_name,
            "status": "FAILED",
            "summary": "git apply 无法执行。",
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "name": patch_name,
            "status": "FAILED",
            "summary": f"git apply 超过 {COMMAND_TIMEOUT_SECONDS} 秒超时。",
            "stdout": limit_output(exc.stdout or ""),
            "stderr": limit_output(exc.stderr or ""),
            "exit_code": None,
        }
    return {
        "name": patch_name,
        "status": "APPLIED" if completed.returncode == 0 else "FAILED",
        "summary": "diff 已应用。" if completed.returncode == 0 else "diff 应用失败。",
        "stdout": limit_output(completed.stdout),
        "stderr": limit_output(completed.stderr),
        "exit_code": completed.returncode,
    }


def run_test_command(repository_path: Path, command: dict[str, Any]) -> dict[str, Any]:
    command_text = str(command.get("command") or "").strip()
    if not command_text:
        return {
            **command,
            "status": "BLOCKED",
            "exit_code": None,
            "stdout": "",
            "stderr": "测试命令为空。",
            "duration_ms": 0,
        }

    try:
        args = split_command(command_text)
    except ValueError as exc:
        return {
            **command,
            "status": "BLOCKED",
            "exit_code": None,
            "stdout": "",
            "stderr": f"测试命令无法解析：{exc}",
            "duration_ms": 0,
        }

    start = time.monotonic()
    try:
        completed = subprocess.run(
            normalize_windows_executable(args),
            cwd=repository_path,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            **command,
            "command": command_text,
            "status": "PASSED" if completed.returncode == 0 else "FAILED",
            "exit_code": completed.returncode,
            "stdout": limit_output(completed.stdout),
            "stderr": limit_output(completed.stderr),
            "duration_ms": duration_ms,
        }
    except FileNotFoundError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            **command,
            "command": command_text,
            "status": "BLOCKED",
            "exit_code": None,
            "stdout": "",
            "stderr": f"测试命令不存在或不可执行：{exc}",
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            **command,
            "command": command_text,
            "status": "FAILED",
            "exit_code": None,
            "stdout": limit_output(exc.stdout or ""),
            "stderr": limit_output((exc.stderr or "") + f"\n测试命令超过 {COMMAND_TIMEOUT_SECONDS} 秒超时。"),
            "duration_ms": duration_ms,
        }


def normalize_test_commands(value: Any) -> list[dict[str, Any]]:
    commands: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            command_text = str(item.get("command") or "").strip()
            if command_text:
                commands.append({str(key): val for key, val in item.items() if val is not None})
        elif str(item or "").strip():
            commands.append({"command": str(item).strip()})
    return commands


def split_command(command_text: str) -> list[str]:
    # TestAgent 只应该输出单条测试命令，不能输出 shell 管道或多命令串。
    # 这里显式拒绝常见 shell 控制符，避免 APPLY 阶段把“运行测试”扩大为任意脚本执行。
    if any(token in command_text for token in ("&&", "||", ";", "|", ">", "<")):
        raise ValueError("不支持包含 shell 控制符的复合命令")
    return shlex.split(command_text, posix=os.name != "nt")


def normalize_windows_executable(args: list[str]) -> list[str]:
    if os.name != "nt" or not args:
        return args
    executable_map = {
        "npm": "npm.cmd",
        "npx": "npx.cmd",
        "mvn": "mvn.cmd",
        "gradle": "gradle.bat",
    }
    first = args[0].casefold()
    if first in executable_map:
        return [executable_map[first], *args[1:]]
    return args


def blocked_result(
    summary: str,
    errors: list[str],
    test_results: dict[str, Any],
    repository_path: Path | None = None,
    applied_patches: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    return {
        "status": "BLOCKED",
        "summary": summary,
        "repository_root": str(repository_path) if repository_path else "",
        "apply_strategy": "AUTO_APPLY_APPROVED_DIFFS",
        "applied_patches": applied_patches or [],
        "test_commands": normalize_test_commands(test_results.get("test_commands") or test_results.get("testCommands")),
        "execution_results": [],
        "errors": errors,
        "source": "apply_and_run_tests_agent",
    }


def failed_apply_result(
    repository_path: Path,
    patch_results: list[dict[str, Any]],
    commands: list[dict[str, Any]],
    summary: str,
) -> dict[str, Any]:
    errors = [
        str(item.get("stderr") or item.get("summary") or "")
        for item in patch_results
        if item.get("status") == "FAILED"
    ]
    return {
        "status": "FAILED",
        "summary": summary,
        "repository_root": str(repository_path),
        "apply_strategy": "AUTO_APPLY_APPROVED_DIFFS",
        "applied_patches": patch_results,
        "test_commands": commands,
        "execution_results": [],
        "errors": [error for error in errors if error],
        "source": "apply_and_run_tests_agent",
    }


def build_command_error_message(result: dict[str, Any]) -> str:
    command = str(result.get("command") or "")
    stderr = str(result.get("stderr") or "").strip()
    stdout = str(result.get("stdout") or "").strip()
    detail = stderr or stdout
    if detail:
        return f"{command} 执行失败：{detail[:1000]}"
    return f"{command} 执行失败，退出码：{result.get('exit_code')}"


def limit_output(text: str) -> str:
    if len(text) <= OUTPUT_LIMIT:
        return text
    return text[:OUTPUT_LIMIT] + "\n... output truncated ..."


def first_text(*values: Any) -> str:
    for value in values:
        text = str(value or "").strip()
        if text:
            return text
    return ""
