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


SYSTEM_DESIGN = "SYSTEM_DESIGN"
logger = logging.getLogger(__name__)


class DesignAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    structured_prd: dict[str, Any]
    code_context: dict[str, Any]
    pipeline_context: dict[str, Any]
    feedback_text: str
    response_language: str
    design_plan: dict[str, Any]
    draft_design: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
    result: DevFlowState


class DesignAgent:
    """负责 SYSTEM_DESIGN 阶段的 LangGraph 子图。

    这个 Agent 的职责是把 RequirementAgent 产出的 `structured_prd` 和代码证据转成
    可供后续 CoderAgent 消费的设计文档。这里仍然保持“阶段内子图”的边界：Temporal 只
    调度 SYSTEM_DESIGN Activity，设计过程中的准备、生成、校验和修复由 LangGraph 管理。
    """

    def __init__(self, llm_client: LlmClient | None = None, max_repair_attempts: int = 1):
        self.llm_client = llm_client or LlmClient.from_sources()
        self.max_repair_attempts = max_repair_attempts
        self.graph = build_design_agent_graph(self)

    def run(self, state: DevFlowState) -> DevFlowState:
        result = self.graph.invoke(
            {
                "devflow_state": state,
                "attempts": 0,
                "errors": list(state.get("error_logs", [])),
            }
        )
        return result["result"]

    def prepare_input(self, state: DesignAgentState) -> DesignAgentState:
        devflow_state = state["devflow_state"]
        structured_prd = dict(devflow_state.get("structured_prd") or {})
        code_context = dict(devflow_state.get("code_context") or {})
        pipeline_context = normalize_pipeline_context(devflow_state.get("pipeline_context"))
        feedback_text = normalize_text(devflow_state.get("human_feedback", ""))
        response_language = infer_response_language(
            " ".join(
                [
                    str(devflow_state.get("original_requirement", "")),
                    json.dumps(structured_prd, ensure_ascii=False),
                    feedback_text,
                ]
            )
        )
        errors = list(state.get("errors", []))

        if not structured_prd:
            logger.warning(
                "DesignAgent input is missing structured_prd; stage will return a diagnostic design_doc."
            )
            errors.append("structured_prd is required for system design")
        if structured_prd and not code_context:
            logger.warning(
                "DesignAgent received structured_prd without code_context; design will rely on PRD only."
            )
        if feedback_text:
            logger.warning(
                "DesignAgent received human feedback and will prioritize design revision. feedback_length=%s",
                len(feedback_text),
            )

        return {
            "structured_prd": structured_prd,
            "code_context": code_context,
            "pipeline_context": pipeline_context,
            "feedback_text": feedback_text,
            "response_language": response_language,
            "errors": errors,
        }

    def plan_design(self, state: DesignAgentState) -> DesignAgentState:
        code_context = state.get("code_context") or {}
        structured_prd = state.get("structured_prd") or {}
        design_plan = {
            "sections": [
                "summary",
                "modules",
                "api_contracts",
                "data_changes",
                "file_plan",
                "test_strategy",
                "risks",
                "open_questions",
            ],
            "strategy": "prd_and_evidence_driven_design",
            "has_code_context": bool(code_context.get("inspected_files")),
            "acceptance_criteria_count": len(structured_prd.get("acceptance_criteria", [])),
            "response_language": state.get("response_language", "same_as_requirement"),
            "minimum_detail": {
                "modules": 3,
                "file_plan": 4,
                "risks": 3,
                "module_fields": [
                    "name",
                    "responsibility",
                    "dependencies",
                    "key_decisions",
                    "implementation_notes",
                    "test_focus",
                ],
                "file_plan_fields": [
                    "path",
                    "operation",
                    "reason",
                    "change_summary",
                    "validation",
                ],
            },
        }
        self.llm_client.trace_recorder.record(
            "design_agent.design_plan",
            {"design_plan": design_plan},
        )
        return {"design_plan": design_plan}

    def draft_design(self, state: DesignAgentState) -> DesignAgentState:
        logger.warning(
            "DesignAgent is calling LLM for design_doc generation. inspected_files=%s feedback_present=%s",
            len((state.get("code_context") or {}).get("inspected_files", [])),
            bool(state.get("feedback_text")),
        )
        request = LlmRequest(
            task="system_design",
            messages=build_design_messages(state),
            json_schema=DESIGN_DOC_SCHEMA,
            metadata={"stage": SYSTEM_DESIGN},
        )
        draft = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record(
            "design_agent.draft_design",
            {"draft_design": draft},
        )
        return {"draft_design": draft}

    def validate_design(self, state: DesignAgentState) -> DesignAgentState:
        design = normalize_design_doc(
            state.get("draft_design") or {},
            state.get("structured_prd") or {},
            state.get("code_context") or {},
            state.get("feedback_text", ""),
        )
        issues = validate_design_doc_shape(design)
        if issues:
            logger.warning("DesignAgent validation found issues: %s", issues)
        validation_report = {
            "valid": not issues,
            "issues": issues,
            "repairable": bool(issues)
            and int(state.get("attempts", 0)) < self.max_repair_attempts,
        }
        self.llm_client.trace_recorder.record(
            "design_agent.validation_report",
            {"validation_report": validation_report},
        )
        return {
            "draft_design": design,
            "validation_report": validation_report,
        }

    def repair_design(self, state: DesignAgentState) -> DesignAgentState:
        # 修复只补齐结构壳，不生成新的业务事实。真实设计内容仍然来自 LLM 的 draft、
        # PRD 和代码证据；这样既能让 JSONB 产物稳定落库，也避免把这里变成规则设计器。
        logger.warning(
            "DesignAgent is repairing structural gaps. attempts=%s issues=%s",
            int(state.get("attempts", 0)) + 1,
            (state.get("validation_report") or {}).get("issues", []),
        )
        repaired = repair_structural_design_gaps(
            state.get("draft_design") or {},
            state.get("structured_prd") or {},
            state.get("code_context") or {},
            state.get("feedback_text", ""),
        )
        return {
            "draft_design": repaired,
            "attempts": int(state.get("attempts", 0)) + 1,
        }

    def finalize(self, state: DesignAgentState) -> DesignAgentState:
        design_doc = state.get("draft_design") or {}
        code_context = dict(state.get("code_context") or {})
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=SYSTEM_DESIGN,
            agent="design_agent",
            code_context=code_context,
            artifacts={"design_doc": design_doc},
        )
        return {
            "result": {
                "design_doc": design_doc,
                "pipeline_context": pipeline_context,
                "current_step": SYSTEM_DESIGN,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: DesignAgentState) -> DesignAgentState:
        errors = list(state.get("errors", [])) or ["system design failed"]
        logger.warning("DesignAgent entered fail_soft. errors=%s", errors)
        design_doc = diagnostic_design_doc(
            state.get("structured_prd") or {},
            state.get("code_context") or {},
            state.get("feedback_text", ""),
        )
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=SYSTEM_DESIGN,
            agent="design_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"design_doc": design_doc},
        )
        return {
            "result": {
                "design_doc": design_doc,
                "pipeline_context": pipeline_context,
                "current_step": SYSTEM_DESIGN,
                "error_logs": errors,
            }
        }


def build_design_agent_graph(agent: DesignAgent):
    builder = StateGraph(DesignAgentState)
    builder.add_node("prepare_input", agent.prepare_input)
    builder.add_node("plan_design", agent.plan_design)
    builder.add_node("draft_design", agent.draft_design)
    builder.add_node("validate_design", agent.validate_design)
    builder.add_node("repair_design", agent.repair_design)
    builder.add_node("finalize", agent.finalize)
    builder.add_node("fail_soft", agent.fail_soft)

    builder.set_entry_point("prepare_input")
    builder.add_conditional_edges(
        "prepare_input",
        route_after_prepare,
        {"valid": "plan_design", "invalid": "fail_soft"},
    )
    builder.add_edge("plan_design", "draft_design")
    builder.add_edge("draft_design", "validate_design")
    builder.add_conditional_edges(
        "validate_design",
        route_after_validation,
        {"valid": "finalize", "repairable": "repair_design", "invalid": "fail_soft"},
    )
    builder.add_edge("repair_design", "validate_design")
    builder.add_edge("finalize", END)
    builder.add_edge("fail_soft", END)
    return builder.compile()


def route_after_prepare(state: DesignAgentState) -> Literal["valid", "invalid"]:
    return "invalid" if state.get("errors") else "valid"


def route_after_validation(state: DesignAgentState) -> Literal["valid", "repairable", "invalid"]:
    report = state.get("validation_report", {})
    if report.get("valid"):
        return "valid"
    if report.get("repairable"):
        return "repairable"
    return "invalid"


def build_design_messages(state: DesignAgentState) -> tuple[LlmMessage, ...]:
    payload = {
        "structured_prd": state.get("structured_prd", {}),
        "code_context_summary": summarize_code_context(state.get("code_context") or {}),
        "human_feedback": state.get("feedback_text", ""),
        "design_plan": state.get("design_plan", {}),
        "response_language": state.get("response_language", "same_as_requirement"),
        "output_limits": {
            "summary_max_chars": 240,
            "modules_max_items": 4,
            "module_field_max_chars": 180,
            "file_plan_max_items": 6,
            "file_plan_field_max_chars": 180,
            "risks_max_items": 4,
            "open_questions_max_items": 3,
            "policy": "Return compact JSON only. Do not copy source excerpts, PRD evidence, repository maps, or long markdown into design_doc.",
        },
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是 DevFlow Engine 的 Design Agent。只输出符合 schema 的 JSON。"
                "输出语言必须严格遵守 user payload 中的 response_language："
                "当 response_language 为 zh-Hans 时，summary、modules、file_plan、risks、open_questions 等所有自然语言字段都必须使用简体中文；"
                "当 response_language 为 en 时，所有自然语言字段使用英文。"
                "设计必须基于 structured_prd 和已提供的 code_context_summary；"
                "不能编造未由需求、证据或人工反馈支持的实现事实。"
                "输出必须足够详细，不能只给一两条概括性结论。"
                "modules 至少 3 项，每项都要说明 responsibility、dependencies、key_decisions、implementation_notes、test_focus。"
                "file_plan 至少 4 项，必须覆盖源码、测试、文档和集成点；每项包含 path、operation、reason、change_summary、validation。"
                "risks 至少 3 项，必须说明可观察的失败模式或调试关注点。"
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def normalize_design_doc(
    draft: dict[str, Any],
    structured_prd: dict[str, Any],
    code_context: dict[str, Any],
    feedback_text: str,
) -> dict[str, Any]:
    open_questions = ensure_text_list(draft.get("open_questions"))
    if structured_prd.get("open_questions"):
        open_questions.extend(
            question
            for question in ensure_text_list(structured_prd.get("open_questions"))
            if question not in open_questions
        )
    return {
        "summary": str(draft.get("summary") or "").strip(),
        "modules": normalize_modules(draft.get("modules"))[:4],
        "api_contracts": normalize_named_items(draft.get("api_contracts"))[:4],
        "data_changes": normalize_named_items(draft.get("data_changes"))[:4],
        "file_plan": normalize_file_plan(draft.get("file_plan"))[:6],
        "test_strategy": normalize_named_items(draft.get("test_strategy"))[:5],
        "risks": ensure_text_list(draft.get("risks"))[:4],
        "open_questions": open_questions[:3],
        "feedback": feedback_text,
        "code_context_summary": summarize_code_context(code_context),
        "prd_summary": str(structured_prd.get("summary") or "").strip(),
        "quality": {
            "has_code_context": bool(code_context.get("inspected_files")),
            "has_human_feedback": bool(feedback_text),
            "confidence": str((draft.get("quality") or {}).get("confidence") or "MEDIUM"),
        },
        "source": "design_agent",
    }


def validate_design_doc_shape(design_doc: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not design_doc.get("summary"):
        issues.append("summary is required")
    if len(design_doc.get("modules", [])) < 3:
        issues.append("at least three modules are required")
    if len(design_doc.get("file_plan", [])) < 4:
        issues.append("at least four file_plan items are required")
    for index, module in enumerate(design_doc.get("modules", []), start=1):
        if not module.get("name") or not module.get("responsibility"):
            issues.append(f"modules[{index}] requires name and responsibility")
    for index, item in enumerate(design_doc.get("file_plan", []), start=1):
        if not item.get("path") or not item.get("operation") or not item.get("reason"):
            issues.append(f"file_plan[{index}] requires path, operation and reason")
        if not item.get("change_summary") or not item.get("validation"):
            issues.append(f"file_plan[{index}] requires change_summary and validation")
    try:
        json.dumps(design_doc, ensure_ascii=False)
    except TypeError as exc:
        issues.append(f"design_doc must be JSON serializable: {exc}")
    return issues


def repair_structural_design_gaps(
    draft_design: dict[str, Any],
    structured_prd: dict[str, Any],
    code_context: dict[str, Any],
    feedback_text: str,
) -> dict[str, Any]:
    repaired = normalize_design_doc(draft_design, structured_prd, code_context, feedback_text)
    if not repaired.get("summary"):
        repaired["summary"] = str(structured_prd.get("summary") or "系统设计待细化").strip()
    if not repaired.get("modules"):
        repaired["modules"] = [
            {
                "name": "待确认模块",
                "responsibility": "根据 PRD 和人工反馈补齐模块边界",
                "dependencies": [],
                "key_decisions": [],
                "implementation_notes": ["LLM 未返回模块细节，需人工复核。"],
                "test_focus": ["补充模块级测试关注点。"],
            }
        ]
    while len(repaired.get("modules", [])) < 3:
        repaired["modules"].append(
            {
                "name": f"待确认模块 {len(repaired['modules']) + 1}",
                "responsibility": "LLM 输出模块数量不足，需结合代码证据继续细化职责。",
                "dependencies": [],
                "key_decisions": [],
                "implementation_notes": ["该项为结构性补位，不代表已确认设计事实。"],
                "test_focus": ["人工补充该模块的测试边界。"],
            }
        )
    if not repaired.get("file_plan"):
        inspected_files = list(code_context.get("inspected_files", []))
        path = inspected_files[0] if inspected_files else "待确认文件"
        repaired["file_plan"] = [
            {
                "path": path,
                "operation": "update",
                "reason": "LLM 未返回完整 file_plan，已保留为人工复核项",
                "change_summary": "需要继续拆分具体改动。",
                "validation": "人工检查该文件是否确实需要修改。",
            }
        ]
    while len(repaired.get("file_plan", [])) < 4:
        repaired["file_plan"].append(
            {
                "path": "待确认文件",
                "operation": "update",
                "reason": "LLM 输出文件计划数量不足，需继续根据设计和代码证据补齐。",
                "change_summary": "该项为结构性补位，不代表已确认文件改动。",
                "validation": "人工复核后再交给 CoderAgent。",
            }
        )
    repaired["open_questions"] = [
        *ensure_text_list(repaired.get("open_questions")),
        "LLM 未返回完整设计结构，已进行结构性补全；请人工复核模块边界和文件计划。",
    ]
    repaired["quality"] = {
        **dict(repaired.get("quality") or {}),
        "confidence": "LOW",
    }
    return repaired


def diagnostic_design_doc(
    structured_prd: dict[str, Any],
    code_context: dict[str, Any],
    feedback_text: str,
) -> dict[str, Any]:
    return {
        "summary": "无法生成完整系统设计，缺少必要的需求分析产物。",
        "modules": [],
        "api_contracts": [],
        "data_changes": [],
        "file_plan": [],
        "test_strategy": [],
        "risks": ["SYSTEM_DESIGN 阶段输入不完整，后续代码生成不应继续依赖该设计。"],
        "open_questions": ["请先完成 REQUIREMENT_ANALYSIS，并确认 structured_prd 已写入阶段产物。"],
        "feedback": feedback_text,
        "code_context_summary": summarize_code_context(code_context),
        "prd_summary": str(structured_prd.get("summary") or "").strip(),
        "quality": {
            "has_code_context": bool(code_context.get("inspected_files")),
            "has_human_feedback": bool(feedback_text),
            "confidence": "LOW",
        },
        "source": "design_agent",
    }


def summarize_code_context(code_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": code_context.get("status", "SKIPPED"),
        "root_path": code_context.get("root_path", ""),
        "inspected_files": list(code_context.get("inspected_files", []))[:12],
        "search_queries": list(code_context.get("search_queries", [])),
        "evidence_refs": [
            {
                "file_path": item.get("file_path") or item.get("filePath") or "",
                "line_start": item.get("line_start") or item.get("lineStart"),
                "line_end": item.get("line_end") or item.get("lineEnd"),
                "supports": list(item.get("supports") or [])[:4],
            }
            for item in list(code_context.get("evidence", []))[:8]
            if isinstance(item, dict)
        ],
        "confidence": code_context.get("confidence", 0.0),
        "open_questions": list(code_context.get("open_questions", [])),
        "notes": list(code_context.get("notes", [])),
    }


def normalize_modules(value: Any) -> list[dict[str, Any]]:
    modules: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            modules.append(
                {
                    "name": str(item.get("name") or "").strip(),
                    "responsibility": str(item.get("responsibility") or "").strip(),
                    "dependencies": ensure_text_list(item.get("dependencies")),
                    "key_decisions": ensure_text_list(item.get("key_decisions") or item.get("keyDecisions")),
                    "implementation_notes": ensure_text_list(
                        item.get("implementation_notes") or item.get("implementationNotes")
                    ),
                    "test_focus": ensure_text_list(item.get("test_focus") or item.get("testFocus")),
                }
            )
        elif str(item or "").strip():
            modules.append(
                {
                    "name": str(item).strip(),
                    "responsibility": "",
                    "dependencies": [],
                    "key_decisions": [],
                    "implementation_notes": [],
                    "test_focus": [],
                }
            )
    return modules


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
            items.append({"description": str(item).strip()})
    return items


def normalize_file_plan(value: Any) -> list[dict[str, str | list[str]]]:
    plan: list[dict[str, str | list[str]]] = []
    for item in value if isinstance(value, list) else []:
        if not isinstance(item, dict):
            continue
        operation = str(item.get("operation") or "update").strip()
        if operation not in {"create", "update", "delete"}:
            operation = "update"
        plan.append(
            {
                "path": str(item.get("path") or "").strip(),
                "operation": operation,
                "reason": str(item.get("reason") or "").strip(),
                "change_summary": str(item.get("change_summary") or item.get("changeSummary") or "").strip(),
                "validation": str(item.get("validation") or "").strip(),
                "related_modules": ensure_text_list(item.get("related_modules") or item.get("relatedModules")),
            }
        )
    return plan


def ensure_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


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
    """根据需求和上游 PRD 的主要文字判断 LLM 应使用的自然语言。

    这里不做复杂语言识别，只区分当前项目最关键的两类：中文需求返回简体中文，
    其他情况保持英文。判断结果会进入 prompt，让模型知道语言一致性是硬约束。
    """

    return "zh-Hans" if re.search(r"[\u4e00-\u9fff]", text or "") else "en"


DESIGN_DOC_SCHEMA = {
    "type": "object",
    "required": ["summary", "modules", "file_plan", "risks", "open_questions"],
    "properties": {
        "summary": {"type": "string", "maxLength": 240},
        "modules": {
            "type": "array",
            "minItems": 3,
            "maxItems": 4,
            "items": {
                "type": "object",
                "required": ["name", "responsibility"],
                "properties": {
                    "name": {"type": "string", "maxLength": 80},
                    "responsibility": {"type": "string", "maxLength": 180},
                    "dependencies": {"type": "array", "maxItems": 5, "items": {"type": "string", "maxLength": 80}},
                    "key_decisions": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 180}},
                    "implementation_notes": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 180}},
                    "test_focus": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 180}},
                },
            },
        },
        "api_contracts": {"type": "array", "maxItems": 4},
        "data_changes": {"type": "array", "maxItems": 4},
        "file_plan": {
            "type": "array",
            "minItems": 4,
            "maxItems": 6,
            "items": {
                "type": "object",
                "required": ["path", "operation", "reason"],
                "properties": {
                    "path": {"type": "string", "maxLength": 160},
                    "operation": {"type": "string", "enum": ["create", "update", "delete"]},
                    "reason": {"type": "string", "maxLength": 180},
                    "change_summary": {"type": "string", "maxLength": 180},
                    "validation": {"type": "string", "maxLength": 180},
                    "related_modules": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 80}},
                },
            },
        },
        "test_strategy": {"type": "array", "maxItems": 5},
        "risks": {"type": "array", "maxItems": 4, "items": {"type": "string", "maxLength": 180}},
        "open_questions": {"type": "array", "maxItems": 3, "items": {"type": "string", "maxLength": 180}},
        "quality": {"type": "object"},
    },
}
