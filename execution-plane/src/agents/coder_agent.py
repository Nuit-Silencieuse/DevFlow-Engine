from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from src.llm import LlmClient, LlmMessage, LlmRequest
from src.pipeline_context import append_stage_code_context, normalize_pipeline_context

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


CODE_GENERATION = "CODE_GENERATION"
logger = logging.getLogger(__name__)


class CoderAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    structured_prd: dict[str, Any]
    design_doc: dict[str, Any]
    code_context: dict[str, Any]
    pipeline_context: dict[str, Any]
    feedback_text: str
    response_language: str
    code_plan: dict[str, Any]
    draft_patch: dict[str, Any]
    normalized_patch: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
    result: DevFlowState


class CoderAgent:
    """负责 CODE_GENERATION 阶段的 LangGraph 子图。

    CoderAgent 的输出边界非常明确：它只生成可审查的 unified diff 文本和结构化
    code_generation_report，不直接写文件、不运行命令、不提交 Git。真正把 diff
    应用到工作区应由后续沙箱/守护进程或用户明确确认后的文件管理模块完成。
    """

    def __init__(self, llm_client: LlmClient | None = None, max_repair_attempts: int = 1):
        self.llm_client = llm_client or LlmClient.from_sources()
        self.max_repair_attempts = max_repair_attempts
        self.graph = build_coder_agent_graph(self)

    def run(self, state: DevFlowState) -> DevFlowState:
        result = self.graph.invoke(
            {
                "devflow_state": state,
                "attempts": 0,
                "errors": list(state.get("error_logs", [])),
            }
        )
        return result["result"]

    def prepare_input(self, state: CoderAgentState) -> CoderAgentState:
        devflow_state = state["devflow_state"]
        structured_prd = dict(devflow_state.get("structured_prd") or {})
        design_doc = dict(devflow_state.get("design_doc") or {})
        code_context = dict(devflow_state.get("code_context") or {})
        pipeline_context = normalize_pipeline_context(devflow_state.get("pipeline_context"))
        feedback_text = normalize_text(devflow_state.get("human_feedback", ""))
        response_language = infer_response_language(
            " ".join(
                [
                    str(devflow_state.get("original_requirement", "")),
                    json.dumps(structured_prd, ensure_ascii=False),
                    json.dumps(design_doc, ensure_ascii=False),
                    feedback_text,
                ]
            )
        )
        errors = list(state.get("errors", []))

        if not design_doc:
            logger.warning(
                "CoderAgent input is missing design_doc; stage will return a blocked diagnostic result."
            )
            errors.append("design_doc is required for code generation")
        if design_doc and not code_context:
            logger.warning(
                "CoderAgent received design_doc without code_context; diff generation will rely on design_doc only."
            )
        if feedback_text:
            logger.warning(
                "CoderAgent received human feedback and will include it in patch generation. feedback_length=%s",
                len(feedback_text),
            )

        return {
            "structured_prd": structured_prd,
            "design_doc": design_doc,
            "code_context": code_context,
            "pipeline_context": pipeline_context,
            "feedback_text": feedback_text,
            "response_language": response_language,
            "errors": errors,
        }

    def plan_code(self, state: CoderAgentState) -> CoderAgentState:
        design_doc = state.get("design_doc") or {}
        code_context = state.get("code_context") or {}
        file_plan = normalize_named_items(design_doc.get("file_plan"))
        code_plan = {
            "strategy": "design_doc_driven_unified_diff_generation",
            "response_language": state.get("response_language", "same_as_requirement"),
            "file_plan": file_plan,
            "target_files": [
                str(item.get("path"))
                for item in file_plan
                if item.get("path")
            ],
            "inspected_files": list(code_context.get("inspected_files", [])),
            "constraints": [
                "只生成 unified diff 文本",
                "不能写入文件",
                "不能执行 git 或其他命令",
                "不能修改 file_plan 之外的无关文件",
            ],
        }
        self.llm_client.trace_recorder.record(
            "coder_agent.code_plan",
            {"code_plan": code_plan},
        )
        return {"code_plan": code_plan}

    def draft_code_patch(self, state: CoderAgentState) -> CoderAgentState:
        logger.warning(
            "CoderAgent is calling LLM for diff generation. file_plan_items=%s inspected_files=%s feedback_present=%s",
            len((state.get("code_plan") or {}).get("file_plan", [])),
            len((state.get("code_context") or {}).get("inspected_files", [])),
            bool(state.get("feedback_text")),
        )
        request = LlmRequest(
            task="code_generation",
            messages=build_coder_messages(state),
            json_schema=CODER_PATCH_SCHEMA,
            metadata={"stage": CODE_GENERATION},
        )
        draft = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record(
            "coder_agent.draft_patch",
            {"draft_patch": draft},
        )
        return {"draft_patch": draft}

    def validate_patch(self, state: CoderAgentState) -> CoderAgentState:
        normalized = normalize_patch_payload(state.get("draft_patch") or {})
        issues = validate_patch_payload(normalized)
        if issues:
            logger.warning("CoderAgent validation found issues: %s", issues)
        validation_report = {
            "valid": not issues,
            "issues": issues,
            "repairable": bool(issues)
            and int(state.get("attempts", 0)) < self.max_repair_attempts,
        }
        self.llm_client.trace_recorder.record(
            "coder_agent.validation_report",
            {"validation_report": validation_report},
        )
        return {
            "normalized_patch": normalized,
            "validation_report": validation_report,
        }

    def repair_patch(self, state: CoderAgentState) -> CoderAgentState:
        # 修复范围只限格式外壳，例如去掉 Markdown fenced code block。这里不会根据
        # design_doc 自行编造 diff，因为那会绕过 LLM 生成和人工审查边界。
        logger.warning(
            "CoderAgent is repairing patch format. attempts=%s issues=%s",
            int(state.get("attempts", 0)) + 1,
            (state.get("validation_report") or {}).get("issues", []),
        )
        request = LlmRequest(
            task="code_generation_repair",
            messages=build_coder_repair_messages(state),
            json_schema=CODER_PATCH_SCHEMA,
            metadata={"stage": CODE_GENERATION, "repair": True},
        )
        repaired = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record(
            "coder_agent.repaired_patch",
            {"repaired_patch": repaired},
        )
        return {
            "draft_patch": repaired,
            "attempts": int(state.get("attempts", 0)) + 1,
        }

    def finalize(self, state: CoderAgentState) -> CoderAgentState:
        patch_payload = state.get("normalized_patch") or {}
        diff_patch = str(patch_payload.get("diff_patch") or "")
        report = build_code_generation_report(
            patch_payload,
            status="GENERATED",
            code_plan=state.get("code_plan") or {},
            feedback_text=state.get("feedback_text", ""),
        )
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=CODE_GENERATION,
            agent="coder_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={
                "diff_patch": diff_patch,
                "code_generation_report": report,
            },
        )
        return {
            "result": {
                "diff_patch": diff_patch,
                "code_generation_report": report,
                "pipeline_context": pipeline_context,
                "current_step": CODE_GENERATION,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: CoderAgentState) -> CoderAgentState:
        errors = list(state.get("errors", []))
        validation_issues = list((state.get("validation_report") or {}).get("issues", []))
        errors = errors + validation_issues if validation_issues else errors
        errors = errors or ["code generation failed"]
        logger.warning("CoderAgent entered fail_soft. errors=%s", errors)
        missing_design_doc = any("design_doc is required" in error for error in errors)
        report = {
            "status": "BLOCKED",
            "summary": (
                "无法生成代码 diff，缺少必要设计产物。"
                if missing_design_doc
                else "无法生成代码 diff，LLM 返回的补丁未通过结构校验。"
            ),
            "changed_files": [],
            "risks": ["CODE_GENERATION 阶段未产出可审查 diff，后续测试生成不应继续依赖该结果。"],
            "open_questions": build_blocked_open_questions(
                missing_design_doc=missing_design_doc,
                validation_issues=validation_issues,
            ),
            "feedback": state.get("feedback_text", ""),
            "quality": {"confidence": "LOW"},
            "llm_diagnostics": build_llm_diagnostics(state),
            "source": "coder_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=CODE_GENERATION,
            agent="coder_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"diff_patch": "", "code_generation_report": report},
        )
        return {
            "result": {
                "diff_patch": "",
                "code_generation_report": report,
                "pipeline_context": pipeline_context,
                "current_step": CODE_GENERATION,
                "error_logs": errors,
            }
        }


def build_coder_agent_graph(agent: CoderAgent):
    builder = StateGraph(CoderAgentState)
    builder.add_node("prepare_input", agent.prepare_input)
    builder.add_node("plan_code", agent.plan_code)
    builder.add_node("draft_code_patch", agent.draft_code_patch)
    builder.add_node("validate_patch", agent.validate_patch)
    builder.add_node("repair_patch", agent.repair_patch)
    builder.add_node("finalize", agent.finalize)
    builder.add_node("fail_soft", agent.fail_soft)

    builder.set_entry_point("prepare_input")
    builder.add_conditional_edges(
        "prepare_input",
        route_after_prepare,
        {"valid": "plan_code", "invalid": "fail_soft"},
    )
    builder.add_edge("plan_code", "draft_code_patch")
    builder.add_edge("draft_code_patch", "validate_patch")
    builder.add_conditional_edges(
        "validate_patch",
        route_after_validation,
        {"valid": "finalize", "repairable": "repair_patch", "invalid": "fail_soft"},
    )
    builder.add_edge("repair_patch", "validate_patch")
    builder.add_edge("finalize", END)
    builder.add_edge("fail_soft", END)
    return builder.compile()


def route_after_prepare(state: CoderAgentState) -> Literal["valid", "invalid"]:
    return "invalid" if state.get("errors") else "valid"


def route_after_validation(state: CoderAgentState) -> Literal["valid", "repairable", "invalid"]:
    report = state.get("validation_report", {})
    if report.get("valid"):
        return "valid"
    if report.get("repairable"):
        return "repairable"
    return "invalid"


def build_coder_messages(state: CoderAgentState) -> tuple[LlmMessage, ...]:
    payload = {
        "structured_prd": state.get("structured_prd", {}),
        "design_doc": state.get("design_doc", {}),
        "code_context_summary": summarize_code_context(state.get("code_context") or {}),
        "human_feedback": state.get("feedback_text", ""),
        "code_plan": state.get("code_plan", {}),
        "response_language": state.get("response_language", "same_as_requirement"),
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是 DevFlow Engine 的 Coder Agent。只输出符合 schema 的 JSON。"
                "必须返回 JSON 对象本身，首字符必须是 {，不能把整个 JSON 对象作为字符串返回。"
                "不要输出 Markdown、代码围栏、前后解释、注释或自然语言前缀。"
                "输出语言必须遵守 user payload 中的 response_language；中文需求使用简体中文描述 summary、risks、open_questions。"
                "核心产物 diff_patch 必须是 unified diff，不要使用 JSON Patch。"
                "diff_patch 是 JSON 字符串字段，内部换行必须由 JSON 编码正确转义，不能破坏外层 JSON。"
                "只生成 unified diff；不能写入文件；不能执行 git；不能运行 shell 命令。"
                "diff 必须尽量小，只覆盖 design_doc.file_plan 指定或代码证据明确支持的文件。"
                "如果缺少必要文件内容，不要猜测完整实现，应在 open_questions 中说明阻塞点。"
                "changed_files 需要列出每个文件的 path、operation、summary，方便前端和人工审批展示。"
            ),
        ),
        LlmMessage(
            role="system",
            content=(
                "Hard rule: diff_patch must be unified diff text only and must contain "
                "`diff --git a/path b/path`. diff_patch must not contain bash blocks, "
                "shell commands, validation commands, test commands, markdown fences, or prose. "
                "Commands such as `node test/plugin.test.mjs`, `npm test`, "
                "`python -m unittest`, and `mvn test` belong to test command artifacts, "
                "never to CODE_GENERATION diff_patch."
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def build_coder_repair_messages(state: CoderAgentState) -> tuple[LlmMessage, ...]:
    payload = {
        "structured_prd": state.get("structured_prd", {}),
        "design_doc": state.get("design_doc", {}),
        "code_context_summary": summarize_code_context(state.get("code_context") or {}),
        "code_plan": state.get("code_plan", {}),
        "response_language": state.get("response_language", "same_as_requirement"),
        "validation_report": state.get("validation_report", {}),
        "invalid_patch": state.get("normalized_patch", {}),
        "repair_instructions": [
            "Return JSON only.",
            "Replace diff_patch with a valid unified diff containing `diff --git a/... b/...`.",
            "Do not put shell commands, bash snippets, test commands, markdown fences, or prose in diff_patch.",
            "A command such as `node test/plugin.test.mjs` is not a patch; it belongs to a later test command artifact.",
            "Keep changed_files, risks, open_questions, and quality consistent with the repaired diff.",
        ],
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "You are repairing an invalid CODE_GENERATION JSON payload. "
                "The previous diff_patch failed validation. Return JSON only. "
                "diff_patch must be unified diff text only, not shell commands."
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def normalize_patch_payload(value: dict[str, Any]) -> dict[str, Any]:
    raw_diff = first_raw_text(value, "diff_patch", "diffPatch", "patch", "unified_diff")
    diff_patch = normalize_unified_diff_hunk_headers(strip_markdown_fence(raw_diff))
    return {
        "summary": first_text(value, "summary"),
        "diff_patch": diff_patch,
        "changed_files": normalize_named_items(
            value.get("changed_files") or value.get("changedFiles")
        ),
        "risks": ensure_text_list(value.get("risks")),
        "open_questions": ensure_text_list(value.get("open_questions") or value.get("openQuestions")),
        "quality": dict(value.get("quality") or {}) if isinstance(value.get("quality"), dict) else {},
    }


def normalize_unified_diff_hunk_headers(diff_patch: str) -> str:
    """按 hunk 实际内容重写 unified diff 的行数声明。

    LLM 生成 diff 时经常把 `@@ -0,0 +1,N @@` 中的 N 写错，但正文内容本身
    是可审查、可应用的。hunk header 的 old/new 行数是纯格式元数据，可以用
    本地确定性扫描修正；这里不改任何代码正文，也不补文件头或业务内容。
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
        normalized[hunk_index] = (
            f"@@ -{old_start},{old_seen} +{new_start},{new_seen} @@{suffix}"
        )

    trailing_newline = "\n" if diff_patch.endswith("\n") else ""
    return "\n".join(normalized) + trailing_newline


def validate_patch_payload(value: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    diff_patch = str(value.get("diff_patch") or "")
    if not diff_patch.strip():
        issues.append("diff_patch is required")
    if diff_patch.strip() and is_shell_command_instead_of_diff(diff_patch):
        issues.append("diff_patch contains a shell/test command instead of a unified diff")
    if diff_patch.strip() and not looks_like_unified_diff(diff_patch):
        issues.append("diff_patch must be a unified diff")
    if diff_patch.strip():
        issues.extend(validate_unified_diff_structure(diff_patch))
    if contains_forbidden_execution_text(diff_patch):
        issues.append("diff_patch must not contain command execution instructions")
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        issues.append(f"code generation payload must be JSON serializable: {exc}")
    return issues


def build_code_generation_report(
    patch_payload: dict[str, Any],
    *,
    status: str,
    code_plan: dict[str, Any],
    feedback_text: str,
) -> dict[str, Any]:
    return {
        "status": status,
        "summary": patch_payload.get("summary", ""),
        "changed_files": list(patch_payload.get("changed_files", [])),
        "risks": list(patch_payload.get("risks", [])),
        "open_questions": list(patch_payload.get("open_questions", [])),
        "feedback": feedback_text,
        "code_plan": code_plan,
        "quality": {
            "confidence": (patch_payload.get("quality") or {}).get("confidence", "MEDIUM"),
            "unified_diff": looks_like_unified_diff(str(patch_payload.get("diff_patch") or "")),
            "file_count": len(parse_diff_file_paths(str(patch_payload.get("diff_patch") or ""))),
        },
        "source": "coder_agent",
    }


def build_llm_diagnostics(state: CoderAgentState) -> dict[str, Any]:
    """构造给前端和日志排查使用的失败诊断快照。

    CoderAgent 的失败经常发生在 LLM 已经返回 JSON、但 `diff_patch` 不是合法
    unified diff 的场景。过去报告只写 `BLOCKED`，用户无法判断模型到底返回了
    空补丁、Markdown、损坏 hunk，还是根本没有按 schema 输出。这里把关键中间
    产物带出去，同时对字符串做长度限制，避免把超大 diff 塞进 Temporal payload。
    """

    return {
        "attempts": int(state.get("attempts", 0)),
        "raw_model_output": redact_large_strings(state.get("draft_patch") or {}),
        "normalized_patch": redact_large_strings(state.get("normalized_patch") or {}),
        "validation_report": redact_large_strings(state.get("validation_report") or {}),
        "code_plan": redact_large_strings(state.get("code_plan") or {}),
    }


def build_blocked_open_questions(
    *,
    missing_design_doc: bool,
    validation_issues: list[str],
) -> list[str]:
    if missing_design_doc:
        return ["请确认 SYSTEM_DESIGN 阶段已生成完整 design_doc，并重新执行代码生成。"]
    if validation_issues:
        return [
            "请查看 code_generation_report.llm_diagnostics.raw_model_output，确认模型返回的 diff_patch 是否为空、不是 unified diff，或 hunk 行数不一致。",
            "如 validation_report.issues 指向 corrupt hunk，可要求模型缩小变更范围后重试，或改用更强的代码生成模型。",
        ]
    return ["请查看 code_generation_report.llm_diagnostics，确认 CoderAgent 在哪个步骤没有获得有效补丁。"]


def redact_large_strings(value: Any, *, limit: int = 60000) -> Any:
    if isinstance(value, dict):
        return {str(key): redact_large_strings(item, limit=limit) for key, item in value.items()}
    if isinstance(value, list):
        return [redact_large_strings(item, limit=limit) for item in value]
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"\n...[truncated {len(value) - limit} chars]"
    return value


def summarize_code_context(code_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": code_context.get("status", "SKIPPED"),
        "root_path": code_context.get("root_path", ""),
        "inspected_files": list(code_context.get("inspected_files", [])),
        "evidence": list(code_context.get("evidence", []))[:8],
        "confidence": code_context.get("confidence", 0.0),
        "open_questions": list(code_context.get("open_questions", [])),
        "notes": list(code_context.get("notes", [])),
    }


def looks_like_unified_diff(text: str) -> bool:
    return bool(
        re.search(r"^diff --git\s+", text, re.MULTILINE)
        or (
            re.search(r"^---\s+", text, re.MULTILINE)
            and re.search(r"^\+\+\+\s+", text, re.MULTILINE)
            and re.search(r"^@@\s+", text, re.MULTILINE)
        )
    )


def validate_unified_diff_structure(diff_patch: str) -> list[str]:
    """对 unified diff 做轻量结构校验，提前拦截 git apply 会拒绝的补丁。

    `looks_like_unified_diff` 只能确认补丁外形存在 `diff --git`、`---/+++` 和
    hunk header；但真实失败常见于 hunk header 声明的旧/新行数与后续正文
    不一致。这里按 hunk 逐段统计上下文行、删除行和新增行，能够在代码生成
    阶段给出可读错误，避免把明显损坏的 patch 推迟到 APPLY_AND_RUN_TESTS。
    """

    issues: list[str] = []
    lines = diff_patch.splitlines()
    hunk_pattern = re.compile(r"^@@ -\d+(?:,(\d+))? \+\d+(?:,(\d+))? @@")
    index = 0
    while index < len(lines):
        match = hunk_pattern.match(lines[index])
        if not match:
            index += 1
            continue

        old_expected = int(match.group(1) or "1")
        new_expected = int(match.group(2) or "1")
        old_seen = 0
        new_seen = 0
        hunk_line = index + 1
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
            else:
                issues.append(
                    f"diff hunk at line {hunk_line} contains invalid line {index + 1}: "
                    "body lines must start with space, '+', '-', or '\\'"
                )
            index += 1

        if old_seen != old_expected or new_seen != new_expected:
            issues.append(
                f"diff hunk at line {hunk_line} declares -{old_expected}/+{new_expected} "
                f"lines but contains -{old_seen}/+{new_seen} lines"
            )

    return issues


def contains_forbidden_execution_text(text: str) -> bool:
    lowered = text.casefold()
    forbidden = ("git commit", "git push", "rm -rf", "del /", "powershell ", "cmd /c")
    return any(token in lowered for token in forbidden)


def is_shell_command_instead_of_diff(text: str) -> bool:
    if looks_like_unified_diff(text):
        return False
    lines = [
        line.lstrip("+ ").strip().casefold()
        for line in strip_markdown_fence(text).splitlines()
        if line.strip()
    ]
    if not lines:
        return False
    shell_markers = {"bash", "sh", "shell", "cmd", "powershell", "pwsh"}
    command_prefixes = (
        "node ",
        "npm ",
        "npx ",
        "python ",
        "pytest",
        "mvn ",
        "gradle ",
        "java ",
        "go test",
        "cargo test",
    )
    return lines[0] in shell_markers or any(
        line.startswith(command_prefixes) for line in lines[:4]
    )


def parse_diff_file_paths(diff_patch: str) -> list[str]:
    paths: list[str] = []
    for match in re.finditer(r"^diff --git\s+a/(.*?)\s+b/(.*?)$", diff_patch, re.MULTILINE):
        paths.append(match.group(2))
    if paths:
        return ordered_unique(paths)
    for match in re.finditer(r"^\+\+\+\s+b/(.*?)$", diff_patch, re.MULTILINE):
        paths.append(match.group(1))
    return ordered_unique(paths)


def strip_markdown_fence(text: str) -> str:
    fenced = re.fullmatch(r"\s*```[A-Za-z0-9_-]*\s*(.*?)```\s*", text, re.IGNORECASE | re.DOTALL)
    return fenced.group(1).strip() if fenced else text


def normalize_named_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            normalized = {
                str(key): normalize_json_value(val)
                for key, val in item.items()
                if val is not None
            }
            if normalized:
                items.append(normalized)
        elif str(item or "").strip():
            items.append({"summary": str(item).strip()})
    return items


def ensure_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def first_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def first_raw_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_json_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def infer_response_language(text: str) -> str:
    return "zh-Hans" if re.search(r"[\u4e00-\u9fff]", text or "") else "en"


def ordered_unique(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        normalized = str(value or "").strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


CODER_PATCH_SCHEMA = {
    "type": "object",
    "required": ["summary", "diff_patch", "changed_files", "risks", "open_questions"],
    "properties": {
        "summary": {"type": "string"},
        "diff_patch": {
            "type": "string",
            "description": "Unified diff text only. Do not return JSON Patch.",
        },
        "changed_files": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "operation", "summary"],
                "properties": {
                    "path": {"type": "string"},
                    "operation": {"type": "string", "enum": ["create", "update", "delete"]},
                    "summary": {"type": "string"},
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "quality": {"type": "object"},
    },
}
