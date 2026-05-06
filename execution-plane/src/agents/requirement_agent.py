from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from src.context import (
    BudgetUsage,
    CompactRepositoryMap,
    EvidenceItem,
    ExplorationStep,
    RepositoryFile,
    RepositoryExplorationRequest,
    SearchMatch,
    inspect_compact_repository_map,
    list_repository,
    read_file_range,
    search_text,
)
from src.llm import LlmClient, LlmMessage, LlmRequest
from src.pipeline_context import (
    append_stage_code_context,
    context_pack_from_reused_code_context,
    normalize_pipeline_context,
    select_reusable_code_context,
)

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
        pipeline_context = normalize_pipeline_context(devflow_state.get("pipeline_context"))
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

        # 渐进式披露不是 RequirementAgent 的私有缓存，而是整个流水线共享的上下文能力。
        # 因此在真正调用 compact map / search / read 之前，先检查前序阶段是否已经留下
        # 同一仓库、足够高置信度的代码证据。命中后直接复用，避免后续 Agent 重复消耗
        # token、网络时间和文件 IO；未命中时才进入下面的增量探索路径。
        reusable_code_context = select_reusable_code_context(pipeline_context, repository_payload)
        if reusable_code_context:
            context_pack = context_pack_from_reused_code_context(reusable_code_context)
            self.llm_client.trace_recorder.record(
                "requirement_agent.context_reuse",
                {
                    "source_stage": reusable_code_context.get("stage"),
                    "inspected_files": reusable_code_context.get("inspected_files", []),
                    "confidence": reusable_code_context.get("confidence", 0.0),
                },
            )
            return {"context_pack": context_pack}

        context_pack = progressively_collect_context(
            request,
            state.get("requirement_text", ""),
            llm_client=self.llm_client,
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
        repaired = repair_structural_prd_gaps(repaired, state.get("requirement_text", ""))
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
            "repository_map": dict(context_pack.get("repository_map") or {}),
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
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context((state.get("devflow_state") or {}).get("pipeline_context")),
            stage=REQUIREMENT_ANALYSIS,
            agent="requirement_agent",
            code_context=code_context,
            artifacts={"structured_prd": prd},
        )
        return {
            "result": {
                "structured_prd": prd,
                "code_context": code_context,
                "codeContext": code_context_camel,
                "pipeline_context": pipeline_context,
                "exploration_trace": exploration_trace,
                "explorationTrace": exploration_trace,
                "current_step": REQUIREMENT_ANALYSIS,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: RequirementAgentState) -> RequirementAgentState:
        requirement_text = state.get("requirement_text", "")
        errors = list(state.get("errors", [])) or ["requirement analysis failed"]
        code_context = {
            "status": "SKIPPED",
            "inspected_files": [],
            "search_queries": [],
            "source": "requirement_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context((state.get("devflow_state") or {}).get("pipeline_context")),
            stage=REQUIREMENT_ANALYSIS,
            agent="requirement_agent",
            code_context=code_context,
            artifacts={},
        )
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
                "code_context": code_context,
                "codeContext": code_context_to_camel(code_context),
                "pipeline_context": pipeline_context,
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
                "user_stories 必须是对象数组，每项必须包含非空 role、goal、benefit。"
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
    open_questions = deduplicate_texts(
        [*open_questions, *ensure_list(context_pack.get("open_questions"))]
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
        issues.append("at least one complete user_story is required")
    if len(prd.get("acceptance_criteria", [])) < 2:
        issues.append("at least two acceptance_criteria are required")
    for index, story in enumerate(prd.get("user_stories", []), start=1):
        if not story.get("role") or not story.get("goal") or not story.get("benefit"):
            issues.append(f"user_stories[{index}] requires role, goal and benefit")
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
            role = first_text(story, "role", "actor", "user", "persona", "as_a")
            goal = first_text(story, "goal", "want", "need", "i_want", "task")
            benefit = first_text(story, "benefit", "value", "reason", "so_that", "outcome")
            if role and goal and benefit:
                result.append({"role": role, "goal": goal, "benefit": benefit})
    return result


def repair_structural_prd_gaps(prd: dict[str, Any], requirement_text: str) -> dict[str, Any]:
    repaired = dict(prd)
    if not repaired.get("user_stories"):
        summary = str(repaired.get("summary") or requirement_text).strip()
        if summary:
            repaired["user_stories"] = [
                {
                    "role": "目标用户",
                    "goal": summary,
                    "benefit": "获得可验证的需求分析结果",
                }
            ]
            repaired["open_questions"] = deduplicate_texts(
                [
                    *ensure_list(repaired.get("open_questions")),
                    "LLM 未返回完整 user_stories，已进行结构性补全；请人工复核角色、目标和收益是否准确。",
                ]
            )
            quality = dict(repaired.get("quality") or {})
            quality["has_open_questions"] = True
            quality["confidence"] = "LOW"
            repaired["quality"] = quality
    return repaired


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
    *,
    llm_client: LlmClient | None = None,
) -> dict[str, Any]:
    """用工具调用式渐进披露循环收集代码上下文。

    这个方法只维护一次阶段内的短循环：compact map -> LLM 规划 -> search_text
    观察 -> 本地评分 -> read_file_range 少量读取 -> 证据评估。Temporal 仍然负责
    外层阶段调度；这里的状态转移属于 Agent 内部的工具使用策略。
    """

    steps: list[ExplorationStep] = []
    notes: list[str] = []
    repo_map = inspect_compact_repository_map(request)
    plan = build_exploration_plan(request, requirement_text, repo_map, llm_client)
    if not plan.get("used_llm"):
        notes.append("LLM exploration plan unavailable; used compact repository map fallback.")

    steps.append(
        ExplorationStep(
            step_index=1,
            round_index=1,
            action_type="PLAN",
            reason="先基于 compact repository map 生成工具调用计划，而不是直接读取候选文件内容。",
            input={
                "usedLlm": plan.get("used_llm", False),
                "queries": plan.get("queries", []),
                "pathHints": plan.get("path_hints", []),
            },
            result_summary=str(plan.get("strategy") or "progressive tool exploration"),
        )
    )

    listing = list_repository(request)
    skipped_paths = [
        {"path": item.path, "reason": item.reason, "detail": item.detail}
        for item in (*repo_map.skipped, *listing.skipped)
    ]
    repository_files = {file.path: file for file in (*repo_map.files, *listing.files)}
    map_candidate_paths = _ordered_unique(
        [
            *request.target_files,
            *plan.get("path_hints", []),
            *repo_map.high_signal_files,
            *repo_map.entrypoint_files,
            *(file.path for file in listing.files),
        ]
    )
    steps.append(
        ExplorationStep(
            step_index=2,
            round_index=1,
            action_type="LIST_FILES",
            reason="生成规划用仓库地图和有限候选路径；这里仍然不读取源码正文。",
            result_summary=(
                f"map_files={len(repo_map.files)}, "
                f"candidate_paths={len(map_candidate_paths)}"
            ),
            selected_files=tuple(map_candidate_paths[:10]),
        )
    )

    searches_used = 0
    matches_by_path: dict[str, list[SearchMatch]] = {}
    query_matches_by_path: dict[str, set[str]] = {}
    matched_queries: set[str] = set()
    queries = tuple(plan.get("queries", []))[: request.budget.max_searches]
    for query in queries:
        matches = search_text(request, query, max_results=request.budget.max_search_results)
        searches_used += 1
        if matches:
            matched_queries.add(query)
        for match in matches:
            matches_by_path.setdefault(match.path, []).append(match)
            query_matches_by_path.setdefault(match.path, set()).add(query)
        steps.append(
            ExplorationStep(
                step_index=len(steps) + 1,
                round_index=1,
                action_type="SEARCH_TEXT",
                reason="执行 LLM 规划出的文本搜索，先观察命中路径和行号，再决定是否读取片段。",
                input={"query": query},
                result_summary=f"matched {len(matches)} lines",
                selected_files=tuple(_ordered_unique([match.path for match in matches])),
            )
        )

    scored_candidates = score_context_candidates(
        request,
        plan,
        repository_files,
        map_candidate_paths,
        matches_by_path,
    )
    steps.append(
        ExplorationStep(
            step_index=len(steps) + 1,
            round_index=1,
            action_type="OBSERVE_TOOLS",
            reason="综合 targetFiles、LLM pathHints、搜索命中、文件名高信号和语言类型进行本地评分。",
            input={"candidateCount": len(scored_candidates)},
            result_summary="ranked candidates before range reads",
            selected_files=tuple(path for path, _score, _reasons in scored_candidates[:10]),
        )
    )

    selected_paths = [
        path
        for path, score, _reasons in scored_candidates
        if score >= 0.35 or path in request.target_files
    ][:read_limit_for_request(request)]

    files: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []
    inspected_files: list[str] = []
    total_bytes = 0
    read_ranges = normalize_plan_read_ranges(plan.get("read_ranges", []))

    for path in selected_paths:
        remaining = request.budget.max_bytes - total_bytes
        if remaining <= 0:
            break
        line_start, line_end = choose_read_range(path, matches_by_path, read_ranges)
        try:
            read_result = read_file_range(
                request,
                path,
                line_start=line_start,
                line_end=line_end,
                max_bytes=min(remaining, 16_000),
            )
        except (OSError, UnicodeError, ValueError) as exc:
            skipped_paths.append({"path": path, "reason": "READ_ERROR", "detail": str(exc)})
            continue

        candidate_score, score_reasons = candidate_score_details(path, scored_candidates)
        files.append(
            {
                "path": read_result.path,
                "content": read_result.content,
                "truncated": read_result.truncated,
                "score": candidate_score,
                "score_reasons": score_reasons,
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
        supports = tuple(
            _ordered_unique(
                [
                    *sorted(query_matches_by_path.get(path, set())),
                    *(reason for reason in score_reasons if reason == "target_file"),
                    *(
                        reason
                        for reason in score_reasons
                        if reason == "llm_path_hint" and plan.get("used_llm")
                    ),
                ]
            )[:4]
        )
        evidence_item = EvidenceItem(
            file_path=read_result.path,
            line_start=read_result.line_start,
            line_end=read_result.line_end,
            excerpt=excerpt,
            relevance_reason=(
                "该片段由渐进式探索评分选中；评分依据包括 "
                + ", ".join(score_reasons or ["local_candidate"])
                + f"，score={candidate_score:.2f}。"
            ),
            supports=supports,
        )
        evidence.append(evidence_item_to_mapping(evidence_item))
        steps.append(
            ExplorationStep(
                step_index=len(steps) + 1,
                round_index=1,
                action_type="READ_FILE",
                reason="只读取评分最高路径的短范围片段，避免把全部候选文件放入 prompt。",
                input={
                    "path": read_result.path,
                    "lineStart": read_result.line_start,
                    "lineEnd": read_result.line_end,
                    "score": candidate_score,
                },
                result_summary=f"read {read_result.bytes_read} bytes",
                selected_files=(read_result.path,),
            )
        )

    strong_evidence_count = sum(1 for item in evidence if item.get("supports"))
    matched_query_count = len(matched_queries)
    read_limit = read_limit_for_request(request)
    read_budget_exhausted = total_bytes >= request.budget.max_bytes or any(
        bool(file.get("truncated")) for file in files
    )
    listing_truncated = any(item.get("reason") == "BUDGET_EXHAUSTED" for item in skipped_paths)
    if listing_truncated:
        notes.append(
            "候选文件列表达到 maxFiles 上限，compact map/list_repository 未覆盖全部仓库；"
            "这只表示候选发现被截断，不等同于已读取证据预算耗尽。"
        )
    confidence = calculate_context_confidence(
        evidence_count=len(evidence),
        strong_evidence_count=strong_evidence_count,
        used_llm=bool(plan.get("used_llm")),
        searches_used=searches_used,
        query_count=len(queries),
        matched_query_count=matched_query_count,
        read_limit=read_limit,
        target_file_count=len(request.target_files),
        inspected_target_count=sum(1 for path in request.target_files if path in inspected_files),
    )
    open_questions = []
    if confidence < 0.5:
        open_questions.append("未能从代码库中找到足够证据，请补充目标路径或更明确的业务线索。")
    if read_budget_exhausted:
        open_questions.append("读取预算已耗尽，部分已选证据片段被截断；请提高 maxBytes 或缩小目标范围。")
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
            reason="评估当前证据是否足以支撑需求分析，并记录降级原因。",
            result_summary=f"confidence={confidence}",
        )
    )

    return {
        "status": "DEGRADED" if read_budget_exhausted or confidence < 0.5 else "COMPLETE",
        "root_path": str(request.resolved_root),
        "files": files,
        "inspected_files": inspected_files,
        "search_queries": list(queries),
        "candidate_files": [path for path, _score, _reasons in scored_candidates[:50]],
        "repository_map": compact_repository_map_to_mapping(repo_map),
        "evidence": evidence,
        "skipped_paths": deduplicate_skipped_paths(skipped_paths),
        "budget_usage": budget_usage_to_mapping(budget_usage),
        "confidence": confidence,
        "open_questions": open_questions,
        "exploration_trace": [exploration_step_to_mapping(step) for step in steps],
        "total_bytes": total_bytes,
        "notes": notes,
    }


def build_exploration_plan(
    request: RepositoryExplorationRequest,
    requirement_text: str,
    repo_map: CompactRepositoryMap,
    llm_client: LlmClient | None,
) -> dict[str, Any]:
    if llm_client is None:
        return fallback_exploration_plan(request, repo_map)
    try:
        raw_plan = llm_client.complete_json(
            LlmRequest(
                task="progressive_context_exploration_plan",
                messages=build_exploration_plan_messages(requirement_text, repo_map, request),
                json_schema=EXPLORATION_PLAN_SCHEMA,
                temperature=0.0,
                timeout_seconds=60,
                metadata={"stage": REQUIREMENT_ANALYSIS, "rootPath": str(request.resolved_root)},
            )
        )
    except Exception:
        return fallback_exploration_plan(request, repo_map)
    plan = normalize_exploration_plan(raw_plan)
    if not plan.get("queries") and not plan.get("path_hints") and not plan.get("read_ranges"):
        return fallback_exploration_plan(request, repo_map)
    plan["used_llm"] = True
    return plan


def build_exploration_plan_messages(
    requirement_text: str,
    repo_map: CompactRepositoryMap,
    request: RepositoryExplorationRequest,
) -> tuple[LlmMessage, ...]:
    """让 LLM 只规划工具调用，不直接生成需求分析结论。

    传入 prompt 的仓库信息是 compact map 的摘要：路径、语言、目录和高信号文件名。
    这里不放源码正文，避免规划阶段提前消耗大量 token。
    """

    map_payload = compact_repository_map_to_mapping(repo_map)
    map_payload["files"] = map_payload["files"][:180]
    map_payload["directorySummaries"] = map_payload["directorySummaries"][:80]
    user_payload = {
        "requirement": requirement_text,
        "repositoryMap": map_payload,
        "constraints": {
            "targetFiles": list(request.target_files),
            "includePaths": list(request.include_paths),
            "excludePaths": list(request.effective_exclude_paths),
            "maxSearches": request.budget.max_searches,
            "maxSearchResults": request.budget.max_search_results,
            "maxFilesToRead": read_limit_for_request(request),
        },
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是代码库渐进式探索规划器。只输出 JSON。"
                "你不能根据文件名编造源码事实，只能决定下一步工具调用。"
                "queries 必须是单个关键词或标识符，不能是短语或句子；"
                "例如输出 Temporal、worker、RequirementAgent，而不是 Temporal worker。"
                "优先给出少量 search_text queries、pathHints 和可选 readRanges。"
                "include/exclude 是约束，不是普通用户必须填写的输入。"
            ),
        ),
        LlmMessage(role="user", content=json.dumps(user_payload, ensure_ascii=False)),
    )


def normalize_exploration_plan(raw_plan: dict[str, Any]) -> dict[str, Any]:
    raw_queries = [
        *ensure_list(raw_plan.get("queries")),
        *ensure_list(raw_plan.get("search_queries")),
        *ensure_list(raw_plan.get("searchQueries")),
    ]
    path_hints = _ordered_unique(
        [
            *ensure_list(raw_plan.get("path_hints")),
            *ensure_list(raw_plan.get("pathHints")),
            *ensure_list(raw_plan.get("candidate_files")),
            *ensure_list(raw_plan.get("candidateFiles")),
        ]
    )[:40]
    symbols = ensure_list(raw_plan.get("symbols"))[:20]
    queries = normalize_search_keywords([*raw_queries, *symbols])[:8]
    if not queries:
        queries = normalize_search_keywords(symbols)[:8]
    return {
        "queries": queries,
        "path_hints": path_hints,
        "symbols": symbols,
        "read_ranges": raw_plan.get("read_ranges") or raw_plan.get("readRanges") or [],
        "strategy": str(raw_plan.get("strategy") or "llm_planned_progressive_exploration"),
        "used_llm": False,
    }


def normalize_search_keywords(values: list[str]) -> list[str]:
    """把 LLM 规划中的搜索短语规范化为 search_text 可执行的关键词。

    search_text 当前是面向单个关键词/标识符的精确子串匹配工具；如果把
    "pipeline status update frontend" 这类短语直接传进去，很多相关行只包含其中
    一个词，结果会完全 miss。这里属于工具调用参数规范化，不承担需求分析规则。
    """

    keywords: list[str] = []
    stop_words = {
        "and",
        "or",
        "the",
        "a",
        "an",
        "to",
        "of",
        "for",
        "with",
        "in",
        "on",
        "by",
        "how",
        "should",
        "current",
    }
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        # 中文/日文等无空格关键词可以直接作为一次 query；英文短语则拆成词或代码标识符。
        tokens = re.findall(r"[\u4e00-\u9fff]+|[A-Za-z_][A-Za-z0-9_]*|\d+", text)
        if not tokens and text:
            tokens = [text]
        for token in tokens:
            normalized = token.strip()
            if len(normalized) < 2:
                continue
            if normalized.casefold() in stop_words:
                continue
            keywords.append(normalized)
    return _ordered_unique(keywords)


def fallback_exploration_plan(
    request: RepositoryExplorationRequest,
    repo_map: CompactRepositoryMap,
) -> dict[str, Any]:
    """LLM 规划不可用时的保守兜底。

    兜底策略不从需求文本里用正则抽关键词；它只尊重显式 targetFiles，并参考 compact
    map 暴露的高信号路径。这样效果有限，但不会退回到旧的“关键词规则分析器”。
    """

    path_hints = _ordered_unique(
        [
            *request.target_files,
            *repo_map.high_signal_files[:20],
            *repo_map.entrypoint_files[:10],
        ]
    )
    return {
        "queries": [],
        "path_hints": path_hints,
        "symbols": [],
        "read_ranges": [{"path": path, "lineStart": 1, "lineEnd": 80} for path in path_hints[:6]],
        "strategy": "compact_map_fallback_without_requirement_keyword_rules",
        "used_llm": False,
    }


def score_context_candidates(
    request: RepositoryExplorationRequest,
    plan: dict[str, Any],
    repository_files: dict[str, RepositoryFile],
    candidate_paths: list[str],
    matches_by_path: dict[str, list[SearchMatch]],
) -> list[tuple[str, float, list[str]]]:
    """对工具观察结果做本地评分，控制 read_file_range 的数量。

    score 不是语义真值，只是读取优先级。高分来源包括用户显式 targetFiles、LLM pathHints、
    search_text 命中、文件名结构信号和常见源码语言。准确性来自“先搜索再读片段”的反馈，
    性能来自只对排名靠前的候选执行范围读取。
    """

    path_hints = set(_ordered_unique(plan.get("path_hints", [])))
    all_paths = _ordered_unique(
        [
            *request.target_files,
            *plan.get("path_hints", []),
            *matches_by_path.keys(),
            *candidate_paths,
        ]
    )
    scored: list[tuple[str, float, list[str]]] = []
    for path in all_paths:
        file = repository_files.get(path)
        score = 0.0
        reasons: list[str] = []
        if path in request.target_files:
            score += 0.45
            reasons.append("target_file")
        if path in path_hints or any(path.startswith(f"{hint.rstrip('/')}/") for hint in path_hints):
            score += 0.30
            reasons.append("llm_path_hint")
        if path in matches_by_path:
            score += min(0.35, 0.16 + 0.05 * len(matches_by_path[path]))
            reasons.append("search_match")
        if file and file.priority_hint in ("runtime", "target"):
            score += 0.12
            reasons.append(f"priority_{file.priority_hint}")
        if file and file.language in ("python", "java", "typescript", "javascript", "markdown"):
            score += 0.04
            reasons.append(f"language_{file.language}")
        if is_high_signal_name(path):
            score += 0.10
            reasons.append("high_signal_name")
        scored.append((path, min(score, 1.0), _ordered_unique(reasons)))
    return sorted(scored, key=lambda item: (-item[1], item[0].casefold()))


def is_high_signal_name(path: str) -> bool:
    lowered = path.casefold()
    return any(
        token in lowered
        for token in (
            "agent",
            "workflow",
            "worker",
            "activity",
            "service",
            "controller",
            "repository",
            "client",
            "provider",
            "config",
            "test",
            "spec",
        )
    )


def read_limit_for_request(request: RepositoryExplorationRequest) -> int:
    preferred = 6 if not request.target_files else min(10, 4 + len(request.target_files))
    return max(1, min(request.budget.max_files, preferred))


def normalize_plan_read_ranges(value: Any) -> dict[str, tuple[int, int]]:
    ranges: dict[str, tuple[int, int]] = {}
    if not isinstance(value, list):
        return ranges
    for item in value:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "").replace("\\", "/").strip().strip("/")
        if not path:
            continue
        line_start = _positive_int_or_default(item.get("lineStart") or item.get("line_start"), 1)
        line_end = _positive_int_or_default(item.get("lineEnd") or item.get("line_end"), line_start + 79)
        ranges[path] = (line_start, max(line_start, min(line_end, line_start + 120)))
    return ranges


def choose_read_range(
    path: str,
    matches_by_path: dict[str, list[SearchMatch]],
    read_ranges: dict[str, tuple[int, int]],
) -> tuple[int, int]:
    if path in read_ranges:
        return read_ranges[path]
    matches = matches_by_path.get(path) or []
    if matches:
        first_line = matches[0].line_number
        return max(1, first_line - 20), first_line + 60
    return 1, 80


def candidate_score_details(
    path: str,
    scored_candidates: list[tuple[str, float, list[str]]],
) -> tuple[float, list[str]]:
    for candidate_path, score, reasons in scored_candidates:
        if candidate_path == path:
            return score, reasons
    return 0.0, []


def calculate_context_confidence(
    *,
    evidence_count: int,
    strong_evidence_count: int,
    used_llm: bool,
    searches_used: int,
    query_count: int,
    matched_query_count: int,
    read_limit: int,
    target_file_count: int,
    inspected_target_count: int,
) -> float:
    if evidence_count == 0:
        return 0.0
    query_hit_rate = matched_query_count / query_count if query_count else 0.0
    read_coverage = min(evidence_count / max(read_limit, 1), 1.0)
    target_coverage = (
        inspected_target_count / target_file_count if target_file_count else 0.0
    )

    confidence = 0.25
    confidence += min(strong_evidence_count, 4) * 0.08
    confidence += read_coverage * 0.15
    confidence += query_hit_rate * 0.20
    confidence += min(target_coverage, 1.0) * 0.10
    if used_llm:
        confidence += 0.03
    if searches_used and matched_query_count == 0:
        confidence -= 0.12
    return max(0.0, min(0.86, round(confidence, 2)))


def compact_repository_map_to_mapping(repo_map: CompactRepositoryMap) -> dict[str, Any]:
    return {
        "rootPath": repo_map.root_path,
        "files": [
            {
                "path": file.path,
                "sizeBytes": file.size_bytes,
                "language": file.language,
                "priorityHint": file.priority_hint,
            }
            for file in repo_map.files
        ],
        "directorySummaries": [
            {
                "path": summary.path,
                "fileCount": summary.file_count,
                "childDirectoryCount": summary.child_directory_count,
                "languages": dict(summary.languages),
            }
            for summary in repo_map.directory_summaries
        ],
        "languageStats": dict(repo_map.language_stats),
        "highSignalFiles": list(repo_map.high_signal_files),
        "entrypointFiles": list(repo_map.entrypoint_files),
        "skipped": [
            {"path": item.path, "reason": item.reason, "detail": item.detail}
            for item in repo_map.skipped
        ],
    }


def deduplicate_skipped_paths(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[tuple[str, str]] = set()
    result: list[dict[str, Any]] = []
    for item in items:
        key = (str(item.get("path", "")), str(item.get("reason", "")))
        if key in seen:
            continue
        seen.add(key)
        result.append(item)
    return result


def _positive_int_or_default(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default


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


def first_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def normalize_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def empty_non_functional_requirements() -> dict[str, list[str]]:
    return {"performance": [], "security": [], "reliability": [], "compatibility": []}


def empty_evidence() -> dict[str, list[str]]:
    return {"inspected_files": [], "search_queries": [], "notes": []}


def deduplicate_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = str(value or "").strip()
        if not text:
            continue
        key = re.sub(r"[\s，。,.；;：:、]+", "", text.casefold())
        if "预算" in key and ("候选路径" in key or "候选文件" in key):
            key = "candidate_budget_exhausted"
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


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
        "repositoryMap": code_context.get("repository_map", {}),
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


EXPLORATION_PLAN_SCHEMA = {
    "type": "object",
    "required": ["queries", "pathHints"],
    "properties": {
        "queries": {
            "type": "array",
            "items": {
                "type": "string",
                "description": "Single search keyword or code identifier, not a phrase.",
            },
        },
        "pathHints": {"type": "array", "items": {"type": "string"}},
        "symbols": {"type": "array", "items": {"type": "string"}},
        "readRanges": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path"],
                "properties": {
                    "path": {"type": "string"},
                    "lineStart": {"type": "integer"},
                    "lineEnd": {"type": "integer"},
                },
            },
        },
        "strategy": {"type": "string"},
    },
}


REQUIREMENT_PRD_SCHEMA = {
    "type": "object",
    "required": ["summary", "user_stories", "acceptance_criteria", "open_questions"],
    "properties": {
        "summary": {"type": "string"},
        "problem_statement": {"type": "string"},
        "scope": {"type": "object"},
        "user_stories": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["role", "goal", "benefit"],
                "properties": {
                    "role": {"type": "string"},
                    "goal": {"type": "string"},
                    "benefit": {"type": "string"},
                },
            },
        },
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
