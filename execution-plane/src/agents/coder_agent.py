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
            timeout_seconds=120,
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
        repaired = normalize_patch_payload(state.get("normalized_patch") or {})
        repaired["diff_patch"] = strip_markdown_fence(str(repaired.get("diff_patch") or ""))
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
        errors = list(state.get("errors", [])) or ["code generation failed"]
        logger.warning("CoderAgent entered fail_soft. errors=%s", errors)
        report = {
            "status": "BLOCKED",
            "summary": "无法生成代码 diff，缺少必要设计产物或 LLM 返回的补丁格式不可用。",
            "changed_files": [],
            "risks": ["CODE_GENERATION 阶段未产出可审查 diff，后续测试生成不应继续依赖该结果。"],
            "open_questions": ["请确认 SYSTEM_DESIGN 阶段已生成完整 design_doc，并重新执行代码生成。"],
            "feedback": state.get("feedback_text", ""),
            "quality": {"confidence": "LOW"},
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
                "输出语言必须遵守 user payload 中的 response_language；中文需求使用简体中文描述 summary、risks、open_questions。"
                "核心产物 diff_patch 必须是 unified diff，不要使用 JSON Patch。"
                "只生成 unified diff；不能写入文件；不能执行 git；不能运行 shell 命令。"
                "diff 必须尽量小，只覆盖 design_doc.file_plan 指定或代码证据明确支持的文件。"
                "如果缺少必要文件内容，不要猜测完整实现，应在 open_questions 中说明阻塞点。"
                "changed_files 需要列出每个文件的 path、operation、summary，方便前端和人工审批展示。"
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def normalize_patch_payload(value: dict[str, Any]) -> dict[str, Any]:
    raw_diff = first_raw_text(value, "diff_patch", "diffPatch", "patch", "unified_diff")
    diff_patch = strip_markdown_fence(raw_diff)
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


def validate_patch_payload(value: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    diff_patch = str(value.get("diff_patch") or "")
    if not diff_patch.strip():
        issues.append("diff_patch is required")
    if diff_patch.strip() and not looks_like_unified_diff(diff_patch):
        issues.append("diff_patch must be a unified diff")
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


def contains_forbidden_execution_text(text: str) -> bool:
    lowered = text.casefold()
    forbidden = ("git commit", "git push", "rm -rf", "del /", "powershell ", "cmd /c")
    return any(token in lowered for token in forbidden)


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
    fenced = re.search(r"```(?:diff|patch)?\s*(.*?)```", text.strip(), re.IGNORECASE | re.DOTALL)
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
