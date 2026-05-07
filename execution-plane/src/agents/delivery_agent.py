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
)

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


DELIVERY_INTEGRATION = "DELIVERY_INTEGRATION"
logger = logging.getLogger(__name__)


class DeliveryAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    structured_prd: dict[str, Any]
    design_doc: dict[str, Any]
    pipeline_context: dict[str, Any]
    diff_patch: str
    code_generation_report: dict[str, Any]
    test_results: dict[str, Any]
    test_run_results: dict[str, Any]
    review_report: dict[str, Any]
    feedback_text: str
    response_language: str
    delivery_plan: dict[str, Any]
    draft_status: dict[str, Any]
    normalized_status: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
    result: DevFlowState


class DeliveryAgent:
    """负责 DELIVERY_INTEGRATION 阶段的 LangGraph Agent。

    交付集成阶段的目标不是发布系统，也不直接提交代码；它把前面各阶段的产物
    整理成一个面向用户的交付状态：本次改动包含哪些产物、测试与评审是否通过、
    还有哪些人工检查项和开放风险。这样控制平面可以把最后阶段展示成清晰的
    handoff，而不是让用户在多个 JSON 产物之间来回寻找结论。
    """

    def __init__(self, llm_client: LlmClient | None = None, max_repair_attempts: int = 1):
        self.llm_client = llm_client or LlmClient.from_sources()
        self.max_repair_attempts = max_repair_attempts
        self.graph = build_delivery_agent_graph(self)

    def run(self, state: DevFlowState) -> DevFlowState:
        result = self.graph.invoke(
            {
                "devflow_state": state,
                "attempts": 0,
                "errors": list(state.get("error_logs", [])),
            }
        )
        return result["result"]

    def prepare_input(self, state: DeliveryAgentState) -> DeliveryAgentState:
        devflow_state = state["devflow_state"]
        structured_prd = dict(devflow_state.get("structured_prd") or {})
        design_doc = dict(devflow_state.get("design_doc") or {})
        pipeline_context = normalize_pipeline_context(devflow_state.get("pipeline_context"))
        diff_patch = str(devflow_state.get("diff_patch") or "")
        code_generation_report = dict(devflow_state.get("code_generation_report") or {})
        test_results = dict(devflow_state.get("test_results") or {})
        test_run_results = dict(devflow_state.get("test_run_results") or {})
        review_report = dict(devflow_state.get("review_report") or {})
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

        if not review_report:
            logger.warning(
                "DeliveryAgent input is missing review_report; delivery integration cannot be marked ready."
            )
            errors.append("review_report is required for delivery integration")
        if not test_run_results:
            logger.warning(
                "DeliveryAgent input is missing test_run_results; delivery status should remain blocked."
            )
        if feedback_text:
            logger.warning(
                "DeliveryAgent received human feedback and will include it in delivery handoff. feedback_length=%s",
                len(feedback_text),
            )

        return {
            "structured_prd": structured_prd,
            "design_doc": design_doc,
            "pipeline_context": pipeline_context,
            "diff_patch": diff_patch,
            "code_generation_report": code_generation_report,
            "test_results": test_results,
            "test_run_results": test_run_results,
            "review_report": review_report,
            "feedback_text": feedback_text,
            "response_language": response_language,
            "errors": errors,
        }

    def plan_delivery(self, state: DeliveryAgentState) -> DeliveryAgentState:
        # delivery_plan 是最终交付状态的事实底稿。它把“能否交付”的硬门禁明确列出，
        # 避免 LLM 只因为自然语言总结积极就给出 READY。只要测试或评审存在阻塞，
        # prompt 会要求模型把 delivery_status.status 保持为 BLOCKED。
        test_status = str((state.get("test_run_results") or {}).get("status") or "UNKNOWN")
        review_status = str((state.get("review_report") or {}).get("status") or "UNKNOWN")
        delivery_plan = {
            "strategy": "review_and_test_gate_handoff",
            "response_language": state.get("response_language", "same_as_requirement"),
            "diff_files": parse_diff_file_paths(state.get("diff_patch", "")),
            "changed_files": normalize_named_items((state.get("code_generation_report") or {}).get("changed_files")),
            "test_status": test_status,
            "review_status": review_status,
            "ready_gate": test_status.upper() == "PASSED" and review_status.upper() == "APPROVED",
            "required_handoff_sections": [
                "release_notes",
                "artifacts",
                "verification",
                "handoff_checklist",
                "risks",
                "open_questions",
            ],
        }
        self.llm_client.trace_recorder.record("delivery_agent.delivery_plan", {"delivery_plan": delivery_plan})
        return {"delivery_plan": delivery_plan}

    def draft_delivery_status(self, state: DeliveryAgentState) -> DeliveryAgentState:
        logger.warning(
            "DeliveryAgent is calling LLM for delivery integration. test_status=%s review_status=%s feedback_present=%s",
            (state.get("delivery_plan") or {}).get("test_status", "UNKNOWN"),
            (state.get("delivery_plan") or {}).get("review_status", "UNKNOWN"),
            bool(state.get("feedback_text")),
        )
        request = LlmRequest(
            task="delivery_integration",
            messages=build_delivery_messages(state),
            json_schema=DELIVERY_STATUS_SCHEMA,
            metadata={"stage": DELIVERY_INTEGRATION},
        )
        draft = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record("delivery_agent.draft_status", {"draft_status": draft})
        return {"draft_status": draft}

    def validate_delivery_status(self, state: DeliveryAgentState) -> DeliveryAgentState:
        normalized = normalize_delivery_status_payload(state.get("draft_status") or {})
        issues = validate_delivery_status_payload(normalized, state.get("delivery_plan") or {})
        if issues:
            logger.warning("DeliveryAgent validation found issues: %s", issues)
        validation_report = {
            "valid": not issues,
            "issues": issues,
            "repairable": bool(issues) and int(state.get("attempts", 0)) < self.max_repair_attempts,
        }
        self.llm_client.trace_recorder.record(
            "delivery_agent.validation_report",
            {"validation_report": validation_report},
        )
        return {"normalized_status": normalized, "validation_report": validation_report}

    def repair_delivery_status(self, state: DeliveryAgentState) -> DeliveryAgentState:
        # 修复节点只做结构层面的归一化。业务门禁仍然由 validate_delivery_status 控制：
        # 如果测试或评审没有通过，模型不能通过格式修复把状态提升为 READY。
        logger.warning(
            "DeliveryAgent is repairing delivery status format. attempts=%s issues=%s",
            int(state.get("attempts", 0)) + 1,
            (state.get("validation_report") or {}).get("issues", []),
        )
        return {
            "draft_status": normalize_delivery_status_payload(state.get("normalized_status") or {}),
            "attempts": int(state.get("attempts", 0)) + 1,
        }

    def finalize(self, state: DeliveryAgentState) -> DeliveryAgentState:
        delivery_status = {
            **(state.get("normalized_status") or {}),
            "delivery_plan": state.get("delivery_plan") or {},
            "feedback": state.get("feedback_text", ""),
            "source": "delivery_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=DELIVERY_INTEGRATION,
            agent="delivery_agent",
            code_context={},
            artifacts={"delivery_status": delivery_status},
        )
        return {
            "result": {
                "delivery_status": delivery_status,
                "pipeline_context": pipeline_context,
                "current_step": DELIVERY_INTEGRATION,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: DeliveryAgentState) -> DeliveryAgentState:
        errors = list(state.get("errors", []))
        validation_issues = list((state.get("validation_report") or {}).get("issues", []))
        errors = errors + validation_issues if validation_issues else errors
        errors = errors or ["delivery integration failed"]
        logger.warning("DeliveryAgent entered fail_soft. errors=%s", errors)
        delivery_status = {
            "status": "BLOCKED",
            "summary": "无法形成可交付状态，缺少评审报告、测试执行结果或交付产物结构不可用。",
            "release_notes": [],
            "artifacts": [],
            "verification": [],
            "handoff_checklist": [
                {"item": "补齐 CODE_REVIEW 评审报告", "status": "BLOCKED"},
                {"item": "确认 APPLY_AND_RUN_TESTS 真实执行结果", "status": "BLOCKED"},
            ],
            "risks": ["DELIVERY_INTEGRATION 阶段没有形成 READY 结论，当前流水线不应被视为完成交付。"],
            "open_questions": ["请先检查代码生成、测试执行和代码评审阶段的错误信息。"],
            "quality": {"confidence": "LOW"},
            "delivery_plan": state.get("delivery_plan") or {},
            "feedback": state.get("feedback_text", ""),
            "source": "delivery_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=DELIVERY_INTEGRATION,
            agent="delivery_agent",
            code_context={},
            artifacts={"delivery_status": delivery_status},
        )
        return {
            "result": {
                "delivery_status": delivery_status,
                "pipeline_context": pipeline_context,
                "current_step": DELIVERY_INTEGRATION,
                "error_logs": errors,
            }
        }


def build_delivery_agent_graph(agent: DeliveryAgent):
    builder = StateGraph(DeliveryAgentState)
    builder.add_node("prepare_input", agent.prepare_input)
    builder.add_node("plan_delivery", agent.plan_delivery)
    builder.add_node("draft_delivery_status", agent.draft_delivery_status)
    builder.add_node("validate_delivery_status", agent.validate_delivery_status)
    builder.add_node("repair_delivery_status", agent.repair_delivery_status)
    builder.add_node("finalize", agent.finalize)
    builder.add_node("fail_soft", agent.fail_soft)

    builder.set_entry_point("prepare_input")
    builder.add_conditional_edges(
        "prepare_input",
        route_after_prepare,
        {"valid": "plan_delivery", "invalid": "fail_soft"},
    )
    builder.add_edge("plan_delivery", "draft_delivery_status")
    builder.add_edge("draft_delivery_status", "validate_delivery_status")
    builder.add_conditional_edges(
        "validate_delivery_status",
        route_after_validation,
        {"valid": "finalize", "repairable": "repair_delivery_status", "invalid": "fail_soft"},
    )
    builder.add_edge("repair_delivery_status", "validate_delivery_status")
    builder.add_edge("finalize", END)
    builder.add_edge("fail_soft", END)
    return builder.compile()


def route_after_prepare(state: DeliveryAgentState) -> Literal["valid", "invalid"]:
    return "invalid" if state.get("errors") else "valid"


def route_after_validation(state: DeliveryAgentState) -> Literal["valid", "repairable", "invalid"]:
    report = state.get("validation_report", {})
    if report.get("valid"):
        return "valid"
    if report.get("repairable"):
        return "repairable"
    return "invalid"


def build_delivery_messages(state: DeliveryAgentState) -> tuple[LlmMessage, ...]:
    payload = {
        "structured_prd": state.get("structured_prd", {}),
        "design_doc": state.get("design_doc", {}),
        "diff_patch": state.get("diff_patch", ""),
        "code_generation_report": state.get("code_generation_report", {}),
        "test_results": state.get("test_results", {}),
        "test_run_results": state.get("test_run_results", {}),
        "review_report": state.get("review_report", {}),
        "human_feedback": state.get("feedback_text", ""),
        "delivery_plan": state.get("delivery_plan", {}),
        "response_language": state.get("response_language", "same_as_requirement"),
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是 DevFlow Engine 的 Delivery Agent。只输出符合 schema 的 JSON。"
                "输出语言必须遵守 user payload 中的 response_language；中文需求使用简体中文。"
                "你必须整合代码补丁、测试生成结果、真实测试执行结果和 review_report。"
                "不要提交代码、不要执行命令、不要声明已经发布。"
                "如果测试未通过或 review_report.status 不是 APPROVED，delivery status 必须是 BLOCKED。"
                "release_notes 面向用户说明改动，artifacts 列出可查看产物，verification 列出测试和评审证据。"
                "handoff_checklist 必须给出交付前仍需人工确认或已经完成的事项。"
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def normalize_delivery_status_payload(value: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": first_text(value, "status") or "BLOCKED",
        "summary": first_text(value, "summary"),
        "release_notes": ensure_text_list(value.get("release_notes") or value.get("releaseNotes")),
        "artifacts": normalize_named_items(value.get("artifacts")),
        "verification": normalize_named_items(value.get("verification")),
        "handoff_checklist": normalize_named_items(value.get("handoff_checklist") or value.get("handoffChecklist")),
        "risks": ensure_text_list(value.get("risks")),
        "open_questions": ensure_text_list(value.get("open_questions") or value.get("openQuestions")),
        "quality": dict(value.get("quality") or {}) if isinstance(value.get("quality"), dict) else {},
    }


def validate_delivery_status_payload(value: dict[str, Any], delivery_plan: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not str(value.get("summary") or "").strip():
        issues.append("delivery summary is required")
    if not value.get("handoff_checklist"):
        issues.append("handoff_checklist is required")
    if not value.get("verification"):
        issues.append("verification is required")
    if not delivery_plan.get("ready_gate") and str(value.get("status") or "").upper() == "READY":
        issues.append("delivery status cannot be READY unless tests passed and review is approved")
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        issues.append(f"delivery_status must be JSON serializable: {exc}")
    return issues


DELIVERY_STATUS_SCHEMA = {
    "type": "object",
    "required": [
        "status",
        "summary",
        "release_notes",
        "artifacts",
        "verification",
        "handoff_checklist",
        "risks",
        "open_questions",
    ],
    "properties": {
        "status": {"type": "string", "enum": ["READY", "BLOCKED", "FAILED"]},
        "summary": {"type": "string"},
        "release_notes": {"type": "array", "items": {"type": "string"}},
        "artifacts": {"type": "array", "items": {"type": "object"}},
        "verification": {"type": "array", "items": {"type": "object"}},
        "handoff_checklist": {"type": "array", "items": {"type": "object"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "quality": {"type": "object"},
    },
}
