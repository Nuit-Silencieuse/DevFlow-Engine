from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from src.llm import LlmClient, LlmMessage, LlmRequest
from src.pipeline_context import append_stage_code_context, normalize_pipeline_context

from .coder_agent import (
    ensure_text_list,
    first_text,
    infer_response_language,
    normalize_named_items,
    normalize_text,
    parse_diff_file_paths,
    summarize_code_context,
)

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


CODE_REVIEW = "CODE_REVIEW"
logger = logging.getLogger(__name__)


class ReviewAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    structured_prd: dict[str, Any]
    design_doc: dict[str, Any]
    code_context: dict[str, Any]
    pipeline_context: dict[str, Any]
    diff_patch: str
    code_generation_report: dict[str, Any]
    test_results: dict[str, Any]
    test_run_results: dict[str, Any]
    feedback_text: str
    response_language: str
    review_plan: dict[str, Any]
    draft_report: dict[str, Any]
    normalized_report: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
    result: DevFlowState


class ReviewAgent:
    """负责 CODE_REVIEW 阶段的 LangGraph Agent。

    这个 Agent 不再生成代码，也不会直接修改工作区。它把代码补丁、测试补丁、
    真实测试执行结果和前置设计文档压缩成评审输入，然后要求 LLM 按结构化 schema
    产出 review_report。这样控制台可以稳定展示严重级别、质量门禁、剩余风险和
    阻塞问题，而不是只展示一段不可解析的自然语言评审意见。
    """

    def __init__(self, llm_client: LlmClient | None = None, max_repair_attempts: int = 1):
        self.llm_client = llm_client or LlmClient.from_sources()
        self.max_repair_attempts = max_repair_attempts
        self.graph = build_review_agent_graph(self)

    def run(self, state: DevFlowState) -> DevFlowState:
        result = self.graph.invoke(
            {
                "devflow_state": state,
                "attempts": 0,
                "errors": list(state.get("error_logs", [])),
            }
        )
        return result["result"]

    def prepare_input(self, state: ReviewAgentState) -> ReviewAgentState:
        devflow_state = state["devflow_state"]
        structured_prd = dict(devflow_state.get("structured_prd") or {})
        design_doc = dict(devflow_state.get("design_doc") or {})
        code_context = dict(devflow_state.get("code_context") or {})
        pipeline_context = normalize_pipeline_context(devflow_state.get("pipeline_context"))
        diff_patch = str(devflow_state.get("diff_patch") or "")
        code_generation_report = dict(devflow_state.get("code_generation_report") or {})
        test_results = dict(devflow_state.get("test_results") or {})
        test_run_results = dict(devflow_state.get("test_run_results") or {})
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

        if not diff_patch.strip():
            logger.warning(
                "ReviewAgent input is missing diff_patch; review cannot judge generated code changes."
            )
            errors.append("diff_patch is required for code review")
        if not test_run_results:
            logger.warning(
                "ReviewAgent input is missing test_run_results; report will treat verification as incomplete."
            )
        if feedback_text:
            logger.warning(
                "ReviewAgent received human feedback and will include it in the review. feedback_length=%s",
                len(feedback_text),
            )

        return {
            "structured_prd": structured_prd,
            "design_doc": design_doc,
            "code_context": code_context,
            "pipeline_context": pipeline_context,
            "diff_patch": diff_patch,
            "code_generation_report": code_generation_report,
            "test_results": test_results,
            "test_run_results": test_run_results,
            "feedback_text": feedback_text,
            "response_language": response_language,
            "errors": errors,
        }

    def plan_review(self, state: ReviewAgentState) -> ReviewAgentState:
        # 评审计划是给 LLM 的“检查清单”，不是模型输出的最终结论。
        # 它把 diff 中涉及的文件、测试执行状态和设计目标合并，避免模型只按单一文本
        # 自由发挥，从而提高 review_report 中 findings/quality_gates 的可追踪性。
        changed_files = normalize_named_items((state.get("code_generation_report") or {}).get("changed_files"))
        diff_files = parse_diff_file_paths(state.get("diff_patch", ""))
        review_plan = {
            "strategy": "diff_and_test_result_driven_review",
            "response_language": state.get("response_language", "same_as_requirement"),
            "changed_files": changed_files,
            "diff_files": diff_files,
            "test_status": (state.get("test_run_results") or {}).get("status", "UNKNOWN"),
            "quality_gates": [
                "diff 是否只覆盖设计文档计划范围内的文件",
                "测试补丁和真实执行结果是否能支撑验收条件",
                "是否存在安全、并发、数据丢失、异常处理或回滚风险",
                "是否存在需要人工确认的开放问题",
            ],
        }
        self.llm_client.trace_recorder.record("review_agent.review_plan", {"review_plan": review_plan})
        return {"review_plan": review_plan}

    def draft_review_report(self, state: ReviewAgentState) -> ReviewAgentState:
        logger.warning(
            "ReviewAgent is calling LLM for code review. diff_length=%s test_status=%s feedback_present=%s",
            len(state.get("diff_patch", "")),
            (state.get("test_run_results") or {}).get("status", "UNKNOWN"),
            bool(state.get("feedback_text")),
        )
        request = LlmRequest(
            task="code_review",
            messages=build_review_messages(state),
            json_schema=REVIEW_REPORT_SCHEMA,
            metadata={"stage": CODE_REVIEW},
        )
        draft = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record("review_agent.draft_report", {"draft_report": draft})
        return {"draft_report": draft}

    def validate_review_report(self, state: ReviewAgentState) -> ReviewAgentState:
        normalized = normalize_review_report_payload(state.get("draft_report") or {})
        issues = validate_review_report_payload(normalized)
        if issues:
            logger.warning("ReviewAgent validation found issues: %s", issues)
        validation_report = {
            "valid": not issues,
            "issues": issues,
            "repairable": bool(issues) and int(state.get("attempts", 0)) < self.max_repair_attempts,
        }
        self.llm_client.trace_recorder.record(
            "review_agent.validation_report",
            {"validation_report": validation_report},
        )
        return {"normalized_report": normalized, "validation_report": validation_report}

    def repair_review_report(self, state: ReviewAgentState) -> ReviewAgentState:
        # 修复只做结构归一化：例如把单条 finding 转成数组、补默认 status。
        # 如果 LLM 没有给出实质评审内容，仍然进入 fail_soft，避免生成虚假的通过结论。
        logger.warning(
            "ReviewAgent is repairing review report format. attempts=%s issues=%s",
            int(state.get("attempts", 0)) + 1,
            (state.get("validation_report") or {}).get("issues", []),
        )
        return {
            "draft_report": normalize_review_report_payload(state.get("normalized_report") or {}),
            "attempts": int(state.get("attempts", 0)) + 1,
        }

    def finalize(self, state: ReviewAgentState) -> ReviewAgentState:
        review_report = {
            **(state.get("normalized_report") or {}),
            "review_plan": state.get("review_plan") or {},
            "feedback": state.get("feedback_text", ""),
            "source": "review_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=CODE_REVIEW,
            agent="review_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"review_report": review_report},
        )
        return {
            "result": {
                "review_report": review_report,
                "pipeline_context": pipeline_context,
                "current_step": CODE_REVIEW,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: ReviewAgentState) -> ReviewAgentState:
        errors = list(state.get("errors", []))
        validation_issues = list((state.get("validation_report") or {}).get("issues", []))
        errors = errors + validation_issues if validation_issues else errors
        errors = errors or ["code review failed"]
        logger.warning("ReviewAgent entered fail_soft. errors=%s", errors)
        review_report = {
            "status": "BLOCKED",
            "summary": "无法完成代码评审，缺少可评审的 diff_patch 或评审结果结构不可用。",
            "findings": [],
            "quality_gates": [
                {"name": "review_input", "status": "BLOCKED", "evidence": "; ".join(errors)}
            ],
            "risks": ["CODE_REVIEW 阶段没有形成可信评审结论，交付集成阶段应保持阻塞。"],
            "open_questions": ["请确认 CODE_GENERATION 和 APPLY_AND_RUN_TESTS 阶段已经产出有效结果。"],
            "quality": {"confidence": "LOW"},
            "review_plan": state.get("review_plan") or {},
            "feedback": state.get("feedback_text", ""),
            "source": "review_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=CODE_REVIEW,
            agent="review_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"review_report": review_report},
        )
        return {
            "result": {
                "review_report": review_report,
                "pipeline_context": pipeline_context,
                "current_step": CODE_REVIEW,
                "error_logs": errors,
            }
        }


def build_review_agent_graph(agent: ReviewAgent):
    builder = StateGraph(ReviewAgentState)
    builder.add_node("prepare_input", agent.prepare_input)
    builder.add_node("plan_review", agent.plan_review)
    builder.add_node("draft_review_report", agent.draft_review_report)
    builder.add_node("validate_review_report", agent.validate_review_report)
    builder.add_node("repair_review_report", agent.repair_review_report)
    builder.add_node("finalize", agent.finalize)
    builder.add_node("fail_soft", agent.fail_soft)

    builder.set_entry_point("prepare_input")
    builder.add_conditional_edges(
        "prepare_input",
        route_after_prepare,
        {"valid": "plan_review", "invalid": "fail_soft"},
    )
    builder.add_edge("plan_review", "draft_review_report")
    builder.add_edge("draft_review_report", "validate_review_report")
    builder.add_conditional_edges(
        "validate_review_report",
        route_after_validation,
        {"valid": "finalize", "repairable": "repair_review_report", "invalid": "fail_soft"},
    )
    builder.add_edge("repair_review_report", "validate_review_report")
    builder.add_edge("finalize", END)
    builder.add_edge("fail_soft", END)
    return builder.compile()


def route_after_prepare(state: ReviewAgentState) -> Literal["valid", "invalid"]:
    return "invalid" if state.get("errors") else "valid"


def route_after_validation(state: ReviewAgentState) -> Literal["valid", "repairable", "invalid"]:
    report = state.get("validation_report", {})
    if report.get("valid"):
        return "valid"
    if report.get("repairable"):
        return "repairable"
    return "invalid"


def build_review_messages(state: ReviewAgentState) -> tuple[LlmMessage, ...]:
    payload = {
        "structured_prd": state.get("structured_prd", {}),
        "design_doc": state.get("design_doc", {}),
        "diff_patch": state.get("diff_patch", ""),
        "code_generation_report": state.get("code_generation_report", {}),
        "test_results": state.get("test_results", {}),
        "test_run_results": state.get("test_run_results", {}),
        "code_context_summary": summarize_code_context(state.get("code_context") or {}),
        "human_feedback": state.get("feedback_text", ""),
        "review_plan": state.get("review_plan", {}),
        "response_language": state.get("response_language", "same_as_requirement"),
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是 DevFlow Engine 的 Review Agent。只输出符合 schema 的 JSON。"
                "输出语言必须遵守 user payload 中的 response_language；中文需求使用简体中文。"
                "你必须基于 diff_patch、code_generation_report、test_results 和 test_run_results 做评审。"
                "不要生成或修改代码；不要声称执行了未执行的命令。"
                "findings 必须给出 severity、description、recommendation；能够定位文件时给出 file_path 和 line。"
                "quality_gates 必须说明测试、补丁范围、需求覆盖和剩余风险是否通过。"
                "如果测试未运行或失败，status 不能是 APPROVED。"
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def normalize_review_report_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": first_text(value, "status") or "NEEDS_CHANGES",
        "summary": first_text(value, "summary"),
        "findings": normalize_named_items(value.get("findings")),
        "quality_gates": normalize_named_items(value.get("quality_gates") or value.get("qualityGates")),
        "risks": ensure_text_list(value.get("risks")),
        "open_questions": ensure_text_list(value.get("open_questions") or value.get("openQuestions")),
        "quality": dict(value.get("quality") or {}) if isinstance(value.get("quality"), dict) else {},
    }


def validate_review_report_payload(value: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not str(value.get("summary") or "").strip():
        issues.append("review summary is required")
    if not value.get("findings") and str(value.get("status") or "").upper() != "APPROVED":
        issues.append("findings are required unless status is APPROVED")
    if not value.get("quality_gates"):
        issues.append("at least one quality_gates item is required")
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        issues.append(f"review_report must be JSON serializable: {exc}")
    return issues


REVIEW_REPORT_SCHEMA = {
    "type": "object",
    "required": ["status", "summary", "findings", "quality_gates", "risks", "open_questions"],
    "properties": {
        "status": {"type": "string", "enum": ["APPROVED", "NEEDS_CHANGES", "BLOCKED"]},
        "summary": {"type": "string"},
        "findings": {"type": "array", "items": {"type": "object"}},
        "quality_gates": {"type": "array", "items": {"type": "object"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "quality": {"type": "object"},
    },
}
