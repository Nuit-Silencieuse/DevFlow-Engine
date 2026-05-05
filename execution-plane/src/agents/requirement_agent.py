from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from src.context import (
    BudgetUsage,
    EvidenceItem,
    ExplorationStep,
    RepositoryExplorationRequest,
    list_repository,
    read_file_range,
    search_text,
)
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
                    "status": "SKIPPED",
                    "root_path": "",
                    "files": [],
                    "inspected_files": existing_code_context.get("inspected_files", []),
                    "search_queries": existing_code_context.get("search_queries", []),
                    "candidate_files": [],
                    "evidence": [],
                    "skipped_paths": [],
                    "budget_usage": {
                        "rounds_used": 0,
                        "files_read": 0,
                        "bytes_read": 0,
                        "searches_used": 0,
                    },
                    "confidence": 0.0,
                    "open_questions": ["未提供 repository_context，无法从代码库确认实现约束。"],
                    "total_bytes": 0,
                    "notes": ["未提供 repository_context，需求分析仅基于用户输入。"],
                }
            }

        request = RepositoryExplorationRequest.from_mapping(repository_payload)
        if request is None:
            return {"context_pack": {"status": "SKIPPED", "files": [], "inspected_files": [], "search_queries": []}}

        context_pack = progressively_collect_context(
            request,
            state.get("requirement_text", ""),
        )
        self.llm_client.trace_recorder.record(
            "requirement_agent.context_pack",
            {
                "root_path": context_pack.get("root_path"),
                "status": context_pack.get("status"),
                "inspected_files": context_pack.get("inspected_files"),
                "search_queries": context_pack.get("search_queries"),
                "total_bytes": context_pack.get("total_bytes"),
                "candidate_files": context_pack.get("candidate_files"),
                "evidence": context_pack.get("evidence"),
                "budget_usage": context_pack.get("budget_usage"),
                "exploration_trace": context_pack.get("exploration_trace"),
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
            "status": context_pack.get("status", "COMPLETE" if context_pack.get("files") else "SKIPPED"),
            "root_path": context_pack.get("root_path", ""),
            "inspected_files": list(context_pack.get("inspected_files", [])),
            "search_queries": list(context_pack.get("search_queries", [])),
            "candidate_files": list(context_pack.get("candidate_files", [])),
            "evidence": list(context_pack.get("evidence", [])),
            "skipped_paths": list(context_pack.get("skipped_paths", [])),
            "budget_usage": dict(context_pack.get("budget_usage") or {}),
            "confidence": context_pack.get("confidence", 0.0),
            "open_questions": list(context_pack.get("open_questions", [])),
            "notes": list(context_pack.get("notes", [])),
            "total_bytes": context_pack.get("total_bytes", 0),
            "exploration_trace": list(context_pack.get("exploration_trace", [])),
            "source": "requirement_agent",
        }
        code_context_camel = code_context_to_camel(code_context)
        exploration_trace = list(context_pack.get("exploration_trace", []))
        return {
            "result": {
                "structured_prd": prd,
                "code_context": code_context,
                "codeContext": code_context_camel,
                "exploration_trace": exploration_trace,
                "explorationTrace": exploration_trace,
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
                "code_context": {
                    "status": "SKIPPED",
                    "inspected_files": [],
                    "search_queries": [],
                    "source": "requirement_agent",
                },
                "exploration_trace": [],
                "explorationTrace": [],
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
    open_questions.extend(
        question
        for question in ensure_list(context_pack.get("open_questions"))
        if question not in open_questions
    )
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


def progressively_collect_context(
    request: RepositoryExplorationRequest,
    requirement_text: str,
) -> dict[str, Any]:
    """用短循环自动披露代码上下文。

    US1 只实现一个保守的有限循环：先列出候选文件，再基于需求关键词搜索，
    最后读取少量高相关文件片段。这里的状态转移由 RequirementAgent 决定，
    Temporal 只负责外层工作流编排，不承载这些细粒度探索判断。
    """

    steps: list[ExplorationStep] = []
    steps.append(
        ExplorationStep(
            step_index=1,
            round_index=1,
            action_type="PLAN",
            reason="根据需求文本生成初始搜索词，并准备扫描仓库轮廓。",
            result_summary="plan progressive repository exploration",
        )
    )

    listing = list_repository(request)
    candidate_files = [file.path for file in listing.files]
    skipped_paths = [
        {"path": item.path, "reason": item.reason, "detail": item.detail}
        for item in listing.skipped
    ]
    steps.append(
        ExplorationStep(
            step_index=2,
            round_index=1,
            action_type="LIST_FILES",
            reason="获取仓库中可被 Agent 进一步搜索和读取的候选文件。",
            result_summary=f"found {len(candidate_files)} candidate files",
            selected_files=tuple(candidate_files[:10]),
        )
    )

    queries = extract_search_queries(requirement_text)
    matched_paths: list[str] = []
    searches_used = 0
    for query in queries[: request.budget.max_searches]:
        matches = search_text(request, query, max_results=request.budget.max_search_results)
        searches_used += 1
        matched_paths.extend(match.path for match in matches)
        steps.append(
            ExplorationStep(
                step_index=len(steps) + 1,
                round_index=1,
                action_type="SEARCH_TEXT",
                reason=f"用需求关键词 {query!r} 定位相关模块。",
                input={"query": query},
                result_summary=f"matched {len(matches)} lines",
                selected_files=tuple(dict.fromkeys(match.path for match in matches)),
            )
        )

    selected_paths = _ordered_unique(
        [
            *request.target_files,
            *matched_paths,
            *candidate_files,
        ]
    )[: request.budget.max_files]

    files: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    inspected_files: list[str] = []
    total_bytes = 0

    for path in selected_paths:
        remaining = request.budget.max_bytes - total_bytes
        if remaining <= 0:
            break
        try:
            read_result = read_file_range(
                request,
                path,
                line_start=1,
                line_end=80,
                max_bytes=min(remaining, 16_000),
            )
        except (OSError, UnicodeError, ValueError) as exc:
            skipped_paths.append({"path": path, "reason": "READ_ERROR", "detail": str(exc)})
            continue

        files.append(
            {
                "path": read_result.path,
                "content": read_result.content,
                "truncated": read_result.truncated,
            }
        )
        inspected_files.append(read_result.path)
        total_bytes += read_result.bytes_read
        excerpt = (
            f"{read_result.path} lines {read_result.line_start}-{read_result.line_end}; "
            f"{read_result.bytes_read} bytes read; content redacted by strict privacy mode."
            if request.privacy_mode == "strict"
            else read_result.content[:500]
        )
        supports = tuple(queries[:3]) if read_result.path in matched_paths or read_result.path in request.target_files else ()
        evidence_item = EvidenceItem(
            file_path=read_result.path,
            line_start=read_result.line_start,
            line_end=read_result.line_end,
            excerpt=excerpt,
            relevance_reason="该文件由需求关键词搜索或候选文件优先级选中。",
            supports=supports,
        )
        evidence.append(evidence_item_to_mapping(evidence_item))
        steps.append(
            ExplorationStep(
                step_index=len(steps) + 1,
                round_index=1,
                action_type="READ_FILE",
                reason="读取高相关候选文件片段，形成需求分析证据。",
                input={"path": read_result.path, "lineStart": read_result.line_start, "lineEnd": read_result.line_end},
                result_summary=f"read {read_result.bytes_read} bytes",
                selected_files=(read_result.path,),
            )
        )

    strong_evidence_count = sum(1 for item in evidence if item.get("supports"))
    confidence = min(0.88, 0.35 + strong_evidence_count * 0.22)
    open_questions = []
    if confidence < 0.5:
        open_questions.append("未能从代码库中找到足够证据，请补充目标文件或更明确的业务关键词。")
    if budget_exhausted := any(item.get("reason") == "BUDGET_EXHAUSTED" for item in skipped_paths):
        open_questions.append("探索预算已耗尽，部分候选路径尚未读取。")
    budget_usage = BudgetUsage(
        rounds_used=1,
        files_read=len(inspected_files),
        bytes_read=total_bytes,
        searches_used=searches_used,
    )
    steps.append(
        ExplorationStep(
            step_index=len(steps) + 1,
            round_index=1,
            action_type="EVALUATE",
            reason="评估当前证据是否足够支撑需求分析。",
            result_summary=f"confidence={confidence}",
        )
    )

    return {
        "status": "DEGRADED" if budget_exhausted or confidence < 0.5 else "COMPLETE",
        "root_path": str(request.resolved_root),
        "files": files,
        "inspected_files": inspected_files,
        "search_queries": list(queries),
        "candidate_files": candidate_files,
        "evidence": evidence,
        "skipped_paths": skipped_paths,
        "budget_usage": budget_usage_to_mapping(budget_usage),
        "confidence": confidence,
        "open_questions": open_questions,
        "exploration_trace": [exploration_step_to_mapping(step) for step in steps],
        "total_bytes": total_bytes,
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
        "evidence": context_pack.get("evidence", []),
        "open_questions": context_pack.get("open_questions", []),
        "files": files,
    }


def extract_search_queries(requirement_text: str) -> tuple[str, ...]:
    ascii_words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", requirement_text)
    stop_words = {
        "with",
        "from",
        "that",
        "this",
        "and",
        "for",
        "the",
        "page",
        "build",
        "implement",
        "implementation",
    }
    queries = [
        word
        for word in ascii_words
        if len(word) >= 4 and word.casefold() not in stop_words
    ][:8]
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


def evidence_item_to_mapping(item: EvidenceItem) -> dict[str, Any]:
    return {
        "filePath": item.file_path,
        "file_path": item.file_path,
        "lineStart": item.line_start,
        "line_start": item.line_start,
        "lineEnd": item.line_end,
        "line_end": item.line_end,
        "symbolName": item.symbol_name,
        "symbol_name": item.symbol_name,
        "excerpt": item.excerpt,
        "relevanceReason": item.relevance_reason,
        "relevance_reason": item.relevance_reason,
        "supports": list(item.supports),
    }


def budget_usage_to_mapping(usage: BudgetUsage) -> dict[str, int]:
    return {
        "rounds_used": usage.rounds_used,
        "roundsUsed": usage.rounds_used,
        "files_read": usage.files_read,
        "filesRead": usage.files_read,
        "bytes_read": usage.bytes_read,
        "bytesRead": usage.bytes_read,
        "searches_used": usage.searches_used,
        "searchesUsed": usage.searches_used,
    }


def exploration_step_to_mapping(step: ExplorationStep) -> dict[str, Any]:
    return {
        "stepIndex": step.step_index,
        "step_index": step.step_index,
        "roundIndex": step.round_index,
        "round_index": step.round_index,
        "actionType": step.action_type,
        "action_type": step.action_type,
        "reason": step.reason,
        "input": dict(step.input),
        "resultSummary": step.result_summary,
        "result_summary": step.result_summary,
        "selectedFiles": list(step.selected_files),
        "selected_files": list(step.selected_files),
        "error": step.error,
    }


def code_context_to_camel(code_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": code_context.get("status"),
        "rootPath": code_context.get("root_path", ""),
        "inspectedFiles": code_context.get("inspected_files", []),
        "searchQueries": code_context.get("search_queries", []),
        "candidateFiles": code_context.get("candidate_files", []),
        "evidence": code_context.get("evidence", []),
        "skippedPaths": code_context.get("skipped_paths", []),
        "budgetUsage": {
            "roundsUsed": code_context.get("budget_usage", {}).get("rounds_used", 0),
            "filesRead": code_context.get("budget_usage", {}).get("files_read", 0),
            "bytesRead": code_context.get("budget_usage", {}).get("bytes_read", 0),
            "searchesUsed": code_context.get("budget_usage", {}).get("searches_used", 0),
        },
        "confidence": code_context.get("confidence", 0.0),
        "openQuestions": code_context.get("open_questions", []),
        "notes": code_context.get("notes", []),
        "explorationTrace": code_context.get("exploration_trace", []),
    }


def _ordered_unique(paths: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = str(path).replace("\\", "/").strip().strip("/")
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


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
