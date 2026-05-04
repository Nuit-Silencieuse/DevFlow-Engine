from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from src.context import RepositoryContext, build_context_pack
from src.llm import LlmClient, LlmMessage, LlmRequest

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


REQUIREMENT_ANALYSIS = "REQUIREMENT_ANALYSIS"


class RequirementAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    requirement_text: str
    feedback_text: str
    context_pack: dict[str, Any]
    analysis_plan: dict[str, Any]
    draft_prd: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
    result: DevFlowState


class RequirementAgent:
    """把自然语言需求转换为结构化 PRD 的执行平面 Agent。

    这里没有实现任何规则模板型 Requirement Analyzer。固定逻辑只负责准备
    输入、收集有限代码上下文、调用 T021 LLM Client、校验输出结构；真正的
    PRD 内容来自 LLM 或测试注入的 Fake Provider。
    """

    def __init__(self, llm_client: LlmClient | None = None, max_repair_attempts: int = 1):
        self.llm_client = llm_client or LlmClient.from_sources()
        self.max_repair_attempts = max_repair_attempts
        self.graph = build_requirement_agent_graph(self)

    def run(self, state: DevFlowState) -> DevFlowState:
        result = self.graph.invoke(
            {
                "devflow_state": state,
                "attempts": 0,
                "errors": list(state.get("error_logs", [])),
            }
        )
        return result["result"]

    def prepare_input(self, state: RequirementAgentState) -> RequirementAgentState:
        devflow_state = state["devflow_state"]
        requirement_text = normalize_text(devflow_state.get("original_requirement", ""))
        feedback_text = normalize_text(devflow_state.get("human_feedback", ""))
        errors = list(state.get("errors", []))
        if not requirement_text:
            errors.append("original_requirement is required for requirement analysis")
        return {
            "requirement_text": requirement_text,
            "feedback_text": feedback_text,
            "errors": errors,
        }

    def collect_context(self, state: RequirementAgentState) -> RequirementAgentState:
        devflow_state = state["devflow_state"]
        repository_payload = devflow_state.get("repository_context")
        existing_code_context = dict(devflow_state.get("code_context") or {})
        if not repository_payload:
            return {
                "context_pack": {
                    "root_path": "",
                    "files": [],
                    "inspected_files": existing_code_context.get("inspected_files", []),
                    "search_queries": existing_code_context.get("search_queries", []),
                    "total_bytes": 0,
                    "notes": ["未提供 repository_context，需求分析仅基于用户输入。"],
                }
            }

        # Requirement Agent 只需要了解项目背景，不应该像代码生成 Agent 那样深读
        # 整个仓库。这里复用 T019 的预算限制，同时优先 targetFiles，避免把无关
        # 构建产物塞进 LLM 上下文。
        context = RepositoryContext.from_mapping(repository_payload)
        if context is None:
            return {"context_pack": {"files": [], "inspected_files": [], "search_queries": []}}

        queries = extract_search_queries(state.get("requirement_text", ""))
        pack = build_context_pack(
            context,
            paths=context.target_files,
            search_queries=queries,
        )
        context_pack = context_pack_to_mapping(pack)
        self.llm_client.trace_recorder.record(
            "requirement_agent.context_pack",
            {
                "root_path": context_pack.get("root_path"),
                "inspected_files": context_pack.get("inspected_files"),
                "search_queries": context_pack.get("search_queries"),
                "total_bytes": context_pack.get("total_bytes"),
                "files": [
                    {
                        "path": file.get("path"),
                        "truncated": file.get("truncated"),
                        "content": file.get("content"),
                    }
                    for file in context_pack.get("files", [])
                ],
            },
        )
        return {"context_pack": context_pack}

    def plan_analysis(self, state: RequirementAgentState) -> RequirementAgentState:
        requirement_type = infer_requirement_type(state.get("requirement_text", ""))
        analysis_plan = {
            "requirement_type": requirement_type,
            "sections": [
                "summary",
                "problem_statement",
                "scope",
                "user_stories",
                "acceptance_criteria",
                "edge_cases",
                "non_functional_requirements",
                "open_questions",
                "evidence",
            ],
            "strategy": "task_first_decomposition",
        }
        self.llm_client.trace_recorder.record(
            "requirement_agent.analysis_plan",
            {"analysis_plan": analysis_plan},
        )
        return {"analysis_plan": analysis_plan}

    def draft_prd(self, state: RequirementAgentState) -> RequirementAgentState:
        request = LlmRequest(
            task="requirement_analysis",
            messages=build_messages(state),
            json_schema=REQUIREMENT_PRD_SCHEMA,
            timeout_seconds=90,
            metadata={"stage": REQUIREMENT_ANALYSIS},
        )
        draft = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record(
            "requirement_agent.draft_prd",
            {"draft_prd": draft},
        )
        return {"draft_prd": draft}

    def validate_prd(self, state: RequirementAgentState) -> RequirementAgentState:
        draft = normalize_prd(
            state.get("draft_prd") or {},
            state.get("requirement_text", ""),
            state.get("context_pack") or {},
            state.get("feedback_text", ""),
        )
        issues = validate_prd_shape(draft)
        validation_report = {
            "valid": not issues,
            "issues": issues,
            "repairable": bool(issues)
            and int(state.get("attempts", 0)) < self.max_repair_attempts,
        }
        self.llm_client.trace_recorder.record(
            "requirement_agent.validation_report",
            {"validation_report": validation_report},
        )
        return {
            "draft_prd": draft,
            "validation_report": validation_report,
        }

    def repair_prd(self, state: RequirementAgentState) -> RequirementAgentState:
        # 修复只处理结构缺口，不根据规则生成完整需求内容。这样可以保证测试稳定，
        # 但不会把 Agent 变回 RuleBasedRequirementAnalyzer。
        repaired = normalize_prd(
            state.get("draft_prd") or {},
            state.get("requirement_text", ""),
            state.get("context_pack") or {},
            state.get("feedback_text", ""),
        )
        return {
            "draft_prd": repaired,
            "attempts": int(state.get("attempts", 0)) + 1,
        }

    def finalize(self, state: RequirementAgentState) -> RequirementAgentState:
        prd = state.get("draft_prd") or {}
        context_pack = state.get("context_pack") or {}
        code_context = {
            "root_path": context_pack.get("root_path", ""),
            "inspected_files": list(context_pack.get("inspected_files", [])),
            "search_queries": list(context_pack.get("search_queries", [])),
            "total_bytes": context_pack.get("total_bytes", 0),
            "source": "requirement_agent",
        }
        return {
            "result": {
                "structured_prd": prd,
                "code_context": code_context,
                "current_step": REQUIREMENT_ANALYSIS,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: RequirementAgentState) -> RequirementAgentState:
        requirement_text = state.get("requirement_text", "")
        errors = list(state.get("errors", [])) or ["requirement analysis failed"]
        return {
            "result": {
                "structured_prd": {
                    "summary": requirement_text,
                    "problem_statement": "",
                    "scope": {"in_scope": [], "out_of_scope": []},
                    "user_stories": [],
                    "acceptance_criteria": [],
                    "edge_cases": [],
                    "non_functional_requirements": empty_non_functional_requirements(),
                    "domain_terms": [],
                    "assumptions": [],
                    "open_questions": ["请补充明确的需求描述。"],
                    "evidence": empty_evidence(),
                    "quality": {
                        "testable_acceptance_criteria": False,
                        "has_open_questions": True,
                        "confidence": "LOW",
                    },
                    "source": "requirement_agent",
                },
                "code_context": {"inspected_files": [], "search_queries": [], "source": "requirement_agent"},
                "current_step": REQUIREMENT_ANALYSIS,
                "error_logs": errors,
            }
        }


def build_requirement_agent_graph(agent: RequirementAgent):
    builder = StateGraph(RequirementAgentState)
    builder.add_node("prepare_input", agent.prepare_input)
    builder.add_node("collect_context", agent.collect_context)
    builder.add_node("plan_analysis", agent.plan_analysis)
    builder.add_node("draft_prd", agent.draft_prd)
    builder.add_node("validate_prd", agent.validate_prd)
    builder.add_node("repair_prd", agent.repair_prd)
    builder.add_node("finalize", agent.finalize)
    builder.add_node("fail_soft", agent.fail_soft)

    builder.set_entry_point("prepare_input")
    builder.add_conditional_edges(
        "prepare_input",
        route_after_prepare,
        {"valid": "collect_context", "invalid": "fail_soft"},
    )
    builder.add_edge("collect_context", "plan_analysis")
    builder.add_edge("plan_analysis", "draft_prd")
    builder.add_edge("draft_prd", "validate_prd")
    builder.add_conditional_edges(
        "validate_prd",
        route_after_validation,
        {"valid": "finalize", "repairable": "repair_prd", "invalid": "fail_soft"},
    )
    builder.add_edge("repair_prd", "validate_prd")
    builder.add_edge("finalize", END)
    builder.add_edge("fail_soft", END)
    return builder.compile()


def route_after_prepare(state: RequirementAgentState) -> Literal["valid", "invalid"]:
    return "invalid" if state.get("errors") else "valid"


def route_after_validation(
    state: RequirementAgentState,
) -> Literal["valid", "repairable", "invalid"]:
    report = state.get("validation_report", {})
    if report.get("valid"):
        return "valid"
    if report.get("repairable"):
        return "repairable"
    return "invalid"


def build_messages(state: RequirementAgentState) -> tuple[LlmMessage, ...]:
    context_pack = state.get("context_pack") or {}
    context_summary = summarize_context_for_prompt(context_pack)
    user_payload = {
        "requirement": state.get("requirement_text", ""),
        "human_feedback": state.get("feedback_text", ""),
        "analysis_plan": state.get("analysis_plan", {}),
        "repository_context": context_summary,
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是 DevFlow Engine 的 Requirement Agent。"
                "只输出符合 schema 的 JSON，不要编造未知事实；缺失信息写入 open_questions。"
                "acceptance_criteria 必须是对象数组，每项必须包含 id、description、verification。"
            ),
        ),
        LlmMessage(
            role="user",
            content=json.dumps(user_payload, ensure_ascii=False),
        ),
    )


def normalize_prd(
    draft: dict[str, Any],
    requirement_text: str,
    context_pack: dict[str, Any],
    feedback_text: str,
) -> dict[str, Any]:
    evidence = normalize_evidence(draft.get("evidence"), context_pack)
    quality = dict(draft.get("quality") or {})
    acceptance_criteria = normalize_acceptance_criteria(draft.get("acceptance_criteria"))
    open_questions = ensure_list(draft.get("open_questions"))
    assumptions = ensure_list(draft.get("assumptions"))
    if feedback_text:
        assumptions.append(f"人工反馈已纳入需求分析参考: {feedback_text}")

    normalized = {
        "summary": str(draft.get("summary") or requirement_text).strip(),
        "problem_statement": str(draft.get("problem_statement") or "").strip(),
        "scope": normalize_scope(draft.get("scope")),
        "user_stories": normalize_user_stories(draft.get("user_stories")),
        "acceptance_criteria": acceptance_criteria,
        "edge_cases": ensure_list(draft.get("edge_cases")),
        "non_functional_requirements": normalize_non_functional_requirements(
            draft.get("non_functional_requirements")
        ),
        "domain_terms": ensure_list(draft.get("domain_terms")),
        "assumptions": assumptions,
        "open_questions": open_questions,
        "evidence": evidence,
        "quality": {
            "testable_acceptance_criteria": all(
                bool(item.get("verification")) for item in acceptance_criteria
            ),
            "has_open_questions": bool(open_questions),
            "confidence": quality.get("confidence") or infer_confidence(evidence, open_questions),
        },
        "source": "requirement_agent",
    }
    return normalized


def validate_prd_shape(prd: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    if not prd.get("summary"):
        issues.append("summary is required")
    if not prd.get("user_stories"):
        issues.append("at least one user_story is required")
    if len(prd.get("acceptance_criteria", [])) < 2:
        issues.append("at least two acceptance_criteria are required")
    for index, criterion in enumerate(prd.get("acceptance_criteria", []), start=1):
        if not criterion.get("description") or not criterion.get("verification"):
            issues.append(f"acceptance_criteria[{index}] requires description and verification")
    try:
        json.dumps(prd, ensure_ascii=False)
    except TypeError as exc:
        issues.append(f"structured_prd must be JSON serializable: {exc}")
    return issues


def normalize_scope(scope: Any) -> dict[str, list[str]]:
    if not isinstance(scope, dict):
        return {"in_scope": [], "out_of_scope": []}
    return {
        "in_scope": ensure_list(scope.get("in_scope")),
        "out_of_scope": ensure_list(scope.get("out_of_scope")),
    }


def normalize_user_stories(stories: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for story in stories if isinstance(stories, list) else []:
        if isinstance(story, dict):
            result.append(
                {
                    "role": str(story.get("role") or "目标用户"),
                    "goal": str(story.get("goal") or ""),
                    "benefit": str(story.get("benefit") or ""),
                }
            )
    return result


def normalize_acceptance_criteria(criteria: Any) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []
    for index, item in enumerate(criteria if isinstance(criteria, list) else [], start=1):
        if isinstance(item, dict):
            result.append(
                {
                    "id": str(item.get("id") or f"AC-{index:03d}"),
                    "description": str(item.get("description") or ""),
                    "verification": str(item.get("verification") or ""),
                }
            )
        elif isinstance(item, str):
            result.append(
                {
                    "id": f"AC-{index:03d}",
                    "description": item,
                    "verification": "人工检查该验收标准是否满足。",
                }
            )
    return result


def normalize_non_functional_requirements(value: Any) -> dict[str, list[str]]:
    defaults = empty_non_functional_requirements()
    if not isinstance(value, dict):
        return defaults
    for key in defaults:
        defaults[key] = ensure_list(value.get(key))
    return defaults


def normalize_evidence(value: Any, context_pack: dict[str, Any]) -> dict[str, list[str]]:
    evidence = dict(value or {}) if isinstance(value, dict) else {}
    inspected_files = list(context_pack.get("inspected_files", []))
    search_queries = list(context_pack.get("search_queries", []))
    notes = ensure_list(evidence.get("notes"))
    if not inspected_files:
        notes.append("未读取代码库文件。")
    return {
        "inspected_files": inspected_files,
        "search_queries": search_queries,
        "notes": notes,
    }


def context_pack_to_mapping(pack: Any) -> dict[str, Any]:
    return {
        "root_path": pack.root_path,
        "files": [
            {"path": file.path, "content": file.content, "truncated": file.truncated}
            for file in pack.files
        ],
        "inspected_files": list(pack.inspected_files),
        "search_queries": list(pack.search_queries),
        "total_bytes": pack.total_bytes,
        "notes": [],
    }


def summarize_context_for_prompt(context_pack: dict[str, Any]) -> dict[str, Any]:
    # Prompt 中只放文件片段摘要，完整文件内容不进入最终 PRD。这样既给 LLM 足够
    # 证据，又避免阶段产物膨胀或泄露大量源码。
    files = []
    for file in context_pack.get("files", [])[:6]:
        content = str(file.get("content", ""))
        files.append(
            {
                "path": file.get("path", ""),
                "excerpt": content[:1500],
                "truncated": bool(file.get("truncated")),
            }
        )
    return {
        "root_path": context_pack.get("root_path", ""),
        "inspected_files": context_pack.get("inspected_files", []),
        "search_queries": context_pack.get("search_queries", []),
        "files": files,
    }


def extract_search_queries(requirement_text: str) -> tuple[str, ...]:
    ascii_words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", requirement_text)
    queries = [word for word in ascii_words if len(word) >= 4][:3]
    return tuple(dict.fromkeys(queries))


def infer_requirement_type(requirement_text: str) -> str:
    lowered = requirement_text.casefold()
    if any(keyword in lowered for keyword in ("修复", "bug", "fix")):
        return "bugfix"
    if any(keyword in lowered for keyword in ("重构", "refactor")):
        return "refactor"
    if any(keyword in lowered for keyword in ("测试", "test")):
        return "test"
    return "feature"


def infer_confidence(evidence: dict[str, Any], open_questions: list[str]) -> str:
    if open_questions:
        return "LOW"
    if evidence.get("inspected_files"):
        return "HIGH"
    return "MEDIUM"


def ensure_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item) for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def empty_non_functional_requirements() -> dict[str, list[str]]:
    return {"performance": [], "security": [], "reliability": [], "compatibility": []}


def empty_evidence() -> dict[str, list[str]]:
    return {"inspected_files": [], "search_queries": [], "notes": []}


REQUIREMENT_PRD_SCHEMA = {
    "type": "object",
    "required": ["summary", "user_stories", "acceptance_criteria", "open_questions"],
    "properties": {
        "summary": {"type": "string"},
        "problem_statement": {"type": "string"},
        "scope": {"type": "object"},
        "user_stories": {"type": "array"},
        "acceptance_criteria": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["id", "description", "verification"],
                "properties": {
                    "id": {"type": "string"},
                    "description": {"type": "string"},
                    "verification": {"type": "string"},
                },
            },
        },
        "edge_cases": {"type": "array"},
        "non_functional_requirements": {"type": "object"},
        "domain_terms": {"type": "array"},
        "assumptions": {"type": "array"},
        "open_questions": {"type": "array"},
        "evidence": {"type": "object"},
        "quality": {"type": "object"},
    },
}
