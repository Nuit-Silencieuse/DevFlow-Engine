from __future__ import annotations

import json
import logging
import os
import re
import shlex
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.pipeline_context import append_stage_code_context, normalize_pipeline_context

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


APPLY_AND_RUN_TESTS = "APPLY_AND_RUN_TESTS"
COMMAND_TIMEOUT_SECONDS = 240
OUTPUT_LIMIT = 16_000
GIT_APPLY_INPUT_ENCODING = "utf-8"
EXECUTION_PLANE_ROOT = Path(__file__).resolve().parents[2]
APPLY_DEBUG_DIR_ENV = "DEVFLOW_APPLY_DEBUG_DIR"
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
            "patch_line_count": 0,
        }

    raw_patch_text = patch_text
    patch_text = normalize_patch_for_apply(patch_text)
    normalization_applied = patch_text != raw_patch_text
    check_args = ["git", "apply", "--check", "--whitespace=nowarn", "-"]
    apply_args = ["git", "apply", "--whitespace=nowarn", "-"]

    # 应用阶段是最后一道保护：即使上游 Agent 已做过 diff 校验，这里仍会用本地逻辑修正
    # hunk header 行数，并先执行 git apply --check。这样可以处理旧流水线产物、测试补丁
    # 或人工反馈后产生的补丁，不需要再消耗 LLM token 重新生成。
    try:
        check_completed = subprocess.run(
            check_args,
            cwd=repository_path,
            input=encode_patch_input(patch_text),
            text=False,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        if check_completed.returncode != 0:
            result = {
                "name": patch_name,
                "status": "FAILED",
                "summary": "diff 预检失败，尚未修改工作区。",
                "stdout": limit_output(decode_process_output(check_completed.stdout)),
                "stderr": limit_output(decode_process_output(check_completed.stderr)),
                "exit_code": check_completed.returncode,
                "patch_line_count": count_patch_lines(raw_patch_text),
                "normalized_patch_line_count": count_patch_lines(patch_text),
                "normalization_applied": normalization_applied,
            }
            return attach_apply_diagnostics(
                repository_path,
                patch_name,
                raw_patch_text,
                check_args,
                result,
                normalized_patch_text=patch_text,
            )

        completed = subprocess.run(
            apply_args,
            cwd=repository_path,
            input=encode_patch_input(patch_text),
            text=False,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
    except FileNotFoundError as exc:
        result = {
            "name": patch_name,
            "status": "FAILED",
            "summary": "无法执行 git apply。",
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
            "patch_line_count": count_patch_lines(raw_patch_text),
            "normalized_patch_line_count": count_patch_lines(patch_text),
            "normalization_applied": normalization_applied,
        }
        return attach_apply_diagnostics(
            repository_path,
            patch_name,
            raw_patch_text,
            apply_args,
            result,
            normalized_patch_text=patch_text,
        )
    except subprocess.TimeoutExpired as exc:
        result = {
            "name": patch_name,
            "status": "FAILED",
            "summary": f"git apply 超过 {COMMAND_TIMEOUT_SECONDS} 秒后超时。",
            "stdout": limit_output(decode_process_output(exc.stdout or b"")),
            "stderr": limit_output(decode_process_output(exc.stderr or b"")),
            "exit_code": None,
            "patch_line_count": count_patch_lines(raw_patch_text),
            "normalized_patch_line_count": count_patch_lines(patch_text),
            "normalization_applied": normalization_applied,
        }
        return attach_apply_diagnostics(
            repository_path,
            patch_name,
            raw_patch_text,
            apply_args,
            result,
            normalized_patch_text=patch_text,
        )

    result = {
        "name": patch_name,
        "status": "APPLIED" if completed.returncode == 0 else "FAILED",
        "summary": "diff 已应用。" if completed.returncode == 0 else "diff 应用失败。",
        "stdout": limit_output(decode_process_output(completed.stdout)),
        "stderr": limit_output(decode_process_output(completed.stderr)),
        "exit_code": completed.returncode,
        "patch_line_count": count_patch_lines(raw_patch_text),
        "normalized_patch_line_count": count_patch_lines(patch_text),
        "normalization_applied": normalization_applied,
    }
    if completed.returncode != 0:
        return attach_apply_diagnostics(
            repository_path,
            patch_name,
            raw_patch_text,
            apply_args,
            result,
            normalized_patch_text=patch_text,
        )
    return result


def attach_apply_diagnostics(
    repository_path: Path,
    patch_name: str,
    patch_text: str,
    command_args: list[str],
    result: dict[str, Any],
    *,
    normalized_patch_text: str | None = None,
) -> dict[str, Any]:
    diagnostic_file = write_apply_diagnostic_file(
        repository_path=repository_path,
        patch_name=patch_name,
        patch_text=patch_text,
        command_args=command_args,
        result=result,
        normalized_patch_text=normalized_patch_text,
    )
    enriched = dict(result)
    enriched["diagnostic_file"] = str(diagnostic_file)
    enriched["diagnostic_summary"] = "完整补丁、git apply 输出和运行上下文已写入诊断文件。"
    logger.warning(
        "Patch application diagnostic written. patch=%s file=%s exit_code=%s",
        patch_name,
        diagnostic_file,
        result.get("exit_code"),
    )
    return enriched


def write_apply_diagnostic_file(
    repository_path: Path,
    patch_name: str,
    patch_text: str,
    command_args: list[str],
    result: dict[str, Any],
    *,
    normalized_patch_text: str | None = None,
) -> Path:
    debug_dir = Path(os.getenv(APPLY_DEBUG_DIR_ENV) or (EXECUTION_PLANE_ROOT / "logs"))
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    safe_patch_name = "".join(char if char.isalnum() or char in ("-", "_") else "_" for char in patch_name)
    diagnostic_file = debug_dir / f"apply-patch-failure-{safe_patch_name}-{timestamp}.json"

    # 诊断文件刻意不截断 patch/stdout/stderr。它不进入 Temporal 历史，只保存在本地，
    # 目的是让用户能直接根据 git 的 line N 定位到补丁原文中的同一行。
    payload = {
        "stage": APPLY_AND_RUN_TESTS,
        "patch_name": patch_name,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repository_root": str(repository_path),
        "command": command_args,
        "exit_code": result.get("exit_code"),
        "status": result.get("status"),
        "summary": result.get("summary"),
        "stdout": str(result.get("stdout") or ""),
        "stderr": str(result.get("stderr") or ""),
        "patch_line_count": count_patch_lines(patch_text),
        "normalized_patch_line_count": count_patch_lines(normalized_patch_text or ""),
        "normalization_applied": bool(result.get("normalization_applied")),
        "patch_ends_with_newline": bool(patch_text.endswith("\n")),
        "normalized_patch_ends_with_newline": bool((normalized_patch_text or "").endswith("\n")),
        "patch_input_encoding": GIT_APPLY_INPUT_ENCODING,
        "patch_text": patch_text,
        "patch_lines": numbered_patch_lines(patch_text),
        "normalized_patch_text": normalized_patch_text or "",
        "normalized_patch_lines": numbered_patch_lines(normalized_patch_text or ""),
    }
    diagnostic_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return diagnostic_file


def normalize_patch_for_apply(patch_text: str) -> str:
    normalized = normalize_unified_diff_hunk_headers(strip_markdown_fence(patch_text))
    return ensure_trailing_newline(normalized)


def strip_markdown_fence(text: str) -> str:
    fenced = re.fullmatch(r"\s*```(?:diff|patch)?\s*(.*?)```\s*", text, re.IGNORECASE | re.DOTALL)
    return fenced.group(1).strip() + "\n" if fenced else text


def ensure_trailing_newline(text: str) -> str:
    if not text:
        return text
    return text if text.endswith("\n") else text + "\n"


def encode_patch_input(patch_text: str) -> bytes:
    # Windows subprocess text mode encodes stdin with the process locale, often CP936.
    # Git patches are byte streams, so feed UTF-8 bytes explicitly to preserve Chinese.
    return patch_text.encode(GIT_APPLY_INPUT_ENCODING)


def decode_process_output(output: bytes | str | None) -> str:
    if output is None:
        return ""
    if isinstance(output, str):
        return output
    return output.decode(GIT_APPLY_INPUT_ENCODING, errors="replace")


def normalize_unified_diff_hunk_headers(diff_patch: str) -> str:
    """修正 unified diff 的 hunk 行数声明。

    LLM 经常能生成视觉上正确的 diff，但 `@@ -a,b +c,d @@` 中的 b/d 数字不准。
    Git 会在 hunk 结束附近报 `corrupt patch at line N`，而不是直接指出 header 错误。
    这里只重算 header 中的 old/new 行数，不改动任何实际代码内容。
    """

    lines = diff_patch.splitlines()
    if not lines:
        return diff_patch

    hunk_pattern = re.compile(
        r"^@@ -(?P<old_start>\d+)(?:,(?P<old_count>\d+))? "
        r"\+(?P<new_start>\d+)(?:,(?P<new_count>\d+))? @@(?P<suffix>.*)$"
    )
    normalized = list(lines)
    index = 0
    while index < len(lines):
        match = hunk_pattern.match(lines[index])
        if not match:
            index += 1
            continue

        old_seen = 0
        new_seen = 0
        hunk_index = index
        index += 1
        while index < len(lines):
            line = lines[index]
            if line.startswith("diff --git ") or hunk_pattern.match(line):
                break
            if line.startswith("\\"):
                index += 1
                continue
            if line.startswith(" "):
                old_seen += 1
                new_seen += 1
            elif line.startswith("-"):
                old_seen += 1
            elif line.startswith("+"):
                new_seen += 1
            elif line == "":
                old_seen += 1
                new_seen += 1
            index += 1

        old_start = match.group("old_start")
        new_start = match.group("new_start")
        suffix = match.group("suffix") or ""
        normalized[hunk_index] = f"@@ -{old_start},{old_seen} +{new_start},{new_seen} @@{suffix}"

    trailing_newline = "\n" if diff_patch.endswith("\n") else ""
    return "\n".join(normalized) + trailing_newline


def count_patch_lines(patch_text: str) -> int:
    return len(patch_text.splitlines())


def numbered_patch_lines(patch_text: str) -> list[dict[str, Any]]:
    return [
        {"line": index, "text": line}
        for index, line in enumerate(patch_text.splitlines(), start=1)
    ]


def run_test_command(repository_path: Path, command: dict[str, Any]) -> dict[str, Any]:
    normalized_command = normalize_test_command_for_execution(command)
    command_text = str(normalized_command.get("command") or "").strip()
    if not command_text:
        return {
            **normalized_command,
            "status": "BLOCKED",
            "exit_code": None,
            "stdout": "",
            "stderr": "测试命令为空。",
            "duration_ms": 0,
        }

    try:
        args = split_command(command_text)
        command_cwd = resolve_command_working_directory(repository_path, normalized_command)
    except ValueError as exc:
        return {
            **normalized_command,
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
            cwd=command_cwd,
            text=True,
            capture_output=True,
            timeout=COMMAND_TIMEOUT_SECONDS,
            check=False,
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            **normalized_command,
            "command": command_text,
            "working_directory": relative_working_directory(repository_path, command_cwd),
            "status": "PASSED" if completed.returncode == 0 else "FAILED",
            "exit_code": completed.returncode,
            "stdout": limit_output(completed.stdout),
            "stderr": limit_output(completed.stderr),
            "duration_ms": duration_ms,
        }
    except FileNotFoundError as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            **normalized_command,
            "command": command_text,
            "working_directory": relative_working_directory(repository_path, command_cwd),
            "status": "BLOCKED",
            "exit_code": None,
            "stdout": "",
            "stderr": f"测试命令不存在或不可执行：{exc}",
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired as exc:
        duration_ms = int((time.monotonic() - start) * 1000)
        return {
            **normalized_command,
            "command": command_text,
            "working_directory": relative_working_directory(repository_path, command_cwd),
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
                commands.append(
                    normalize_test_command_for_execution(
                        {str(key): val for key, val in item.items() if val is not None}
                    )
                )
        elif str(item or "").strip():
            commands.append(normalize_test_command_for_execution({"command": str(item).strip()}))
    return commands


def normalize_test_command_for_execution(command: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(command)
    command_text = str(normalized.get("command") or "").strip()
    working_directory = normalized.get("working_directory") or normalized.get("workingDirectory")

    # 兼容 LLM 已经生成的 `cd demo && npm test`，但不启用 shell。
    # 这里只转换最简单的相对路径 cd 模式，复杂管道、重定向和多段命令仍由 split_command 拦截。
    cd_match = re.fullmatch(r"\s*cd\s+(.+?)\s*&&\s*(.+?)\s*", command_text)
    if cd_match and not working_directory:
        directory_text = strip_simple_shell_quotes(cd_match.group(1).strip())
        remaining_command = cd_match.group(2).strip()
        if is_simple_relative_working_directory(directory_text) and remaining_command:
            normalized["command"] = remaining_command
            normalized["working_directory"] = directory_text
            normalized["normalized_from_command"] = command_text
            normalized.pop("workingDirectory", None)
    elif working_directory:
        normalized["working_directory"] = str(working_directory).strip()
        normalized.pop("workingDirectory", None)

    return normalized


def strip_simple_shell_quotes(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def is_simple_relative_working_directory(value: str) -> bool:
    if not value or any(token in value for token in ("&&", "||", ";", "|", ">", "<")):
        return False
    path = Path(value)
    return not path.is_absolute() and all(part not in ("", "..") for part in path.parts)


def resolve_command_working_directory(repository_path: Path, command: dict[str, Any]) -> Path:
    working_directory = str(
        command.get("working_directory") or command.get("workingDirectory") or "."
    ).strip() or "."
    if working_directory == ".":
        return repository_path
    if not is_simple_relative_working_directory(working_directory):
        raise ValueError("working_directory must be a safe relative path")

    resolved_root = repository_path.resolve()
    resolved_cwd = (resolved_root / working_directory).resolve()
    if resolved_cwd != resolved_root and resolved_root not in resolved_cwd.parents:
        raise ValueError("working_directory must stay inside repository root")
    if not resolved_cwd.exists() or not resolved_cwd.is_dir():
        raise ValueError(f"working_directory does not exist: {working_directory}")
    return resolved_cwd


def relative_working_directory(repository_path: Path, command_cwd: Path) -> str:
    try:
        relative = command_cwd.resolve().relative_to(repository_path.resolve())
    except ValueError:
        return str(command_cwd)
    return "." if str(relative) == "." else str(relative).replace("\\", "/")


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
        build_patch_apply_error_message(item)
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


def build_patch_apply_error_message(result: dict[str, Any]) -> str:
    detail = str(result.get("stderr") or result.get("summary") or "").strip()
    diagnostic_file = str(result.get("diagnostic_file") or "").strip()
    if diagnostic_file:
        return f"{detail}\n诊断文件: {diagnostic_file}"
    return detail


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
