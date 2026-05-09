from __future__ import annotations

from copy import deepcopy
import json
from typing import Any

from temporalio import activity
from temporalio.exceptions import ApplicationError

from src.graph.flow import (
    APPLY_AND_RUN_TESTS,
    CODE_GENERATION,
    CODE_REVIEW,
    DELIVERY_INTEGRATION,
    REQUIREMENT_ANALYSIS,
    SYSTEM_DESIGN,
    TEST_GENERATION,
    run_stage,
)
from src.graph.state import DevFlowState
from src.observability import AgentTraceRecorder

StageExecutionRequest = dict[str, Any]
StageExecutionResult = dict[str, Any]


@activity.defn(name="analyzeRequirement")
async def analyze_requirement(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(REQUIREMENT_ANALYSIS, request)


@activity.defn(name="designSystem")
async def design_system(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(SYSTEM_DESIGN, request)


@activity.defn(name="generateCode")
async def generate_code(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(CODE_GENERATION, request)


@activity.defn(name="generateTests")
async def generate_tests(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(TEST_GENERATION, request)


@activity.defn(name="applyAndRunTests")
async def apply_and_run_tests(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(APPLY_AND_RUN_TESTS, request)


@activity.defn(name="reviewCode")
async def review_code(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(CODE_REVIEW, request)


@activity.defn(name="integrateDelivery")
async def integrate_delivery(request: StageExecutionRequest) -> StageExecutionResult:
    return _execute_stage(DELIVERY_INTEGRATION, request)


def registered_activities():
    return [
        analyze_requirement,
        design_system,
        generate_code,
        generate_tests,
        apply_and_run_tests,
        review_code,
        integrate_delivery,
    ]


def _execute_stage(stage_name: str, request: StageExecutionRequest) -> StageExecutionResult:
    trace_recorder = AgentTraceRecorder.from_sources(
        pipeline_id=str(request.get("pipelineId") or request.get("pipeline_id") or "unknown")
    )
    try:
        trace_recorder.record(
            "activity.stage.start",
            {
                "stage": stage_name,
                "pipelineId": request.get("pipelineId") or request.get("pipeline_id"),
                "previousOutputKeys": list((request.get("previousOutput") or {}).keys()),
                "hasLlmConfig": bool((request.get("globalContext") or {}).get("llm_config")),
            },
        )
        state = _state_from_request(request)
        result_state = run_stage(stage_name, state)
        output_payload = _output_payload_for_stage(stage_name, result_state)
        trace_recorder.record(
            "activity.stage.end",
            {
                "stage": stage_name,
                "pipelineId": request.get("pipelineId") or request.get("pipeline_id"),
                "outputKeys": list(output_payload.keys()),
            },
        )
        return {
            "stageName": stage_name,
            "status": "COMPLETED",
            "outputPayload": output_payload,
        }
    except Exception as exc:
        trace_recorder.record(
            "activity.stage.error",
            {
                "stage": stage_name,
                "pipelineId": request.get("pipelineId") or request.get("pipeline_id"),
                "errorType": exc.__class__.__name__,
                "message": str(exc),
            },
        )
        raise concise_activity_error(
            stage_name,
            exc,
            pipeline_id=str(request.get("pipelineId") or request.get("pipeline_id") or "unknown"),
        ) from None


def concise_activity_error(stage_name: str, exc: Exception, pipeline_id: str | None = None) -> ApplicationError:
    error_type = exc.__class__.__name__
    message = str(exc).strip() or error_type
    if len(message) > 1200:
        message = message[:1200] + "... [truncated]"
    hint = ""
    if error_type == "LlmTimeoutError" or "timed out" in message.casefold():
        hint = (
            "。这通常表示模型响应超过当前超时时间；可提高 DEVFLOW_LLM_TIMEOUT_SECONDS，"
            "或降低上下文/输出规模后重试。"
        )
    trace_hint = ""
    diagnostics = AgentTraceRecorder.from_sources(pipeline_id=pipeline_id).diagnostics(tail=5)
    if diagnostics.get("recent_events"):
        trace_hint = f" Recent agent trace: {json_compact(diagnostics, limit=600)}"
    return ApplicationError(
        f"{stage_name} Activity failed: {error_type}: {message}{hint}{trace_hint}",
        type=f"DevFlow{error_type}",
    )


def _state_from_request(request: StageExecutionRequest) -> DevFlowState:
    global_context = dict(request.get("globalContext") or {})
    previous_output = dict(request.get("previousOutput") or {})
    state: DevFlowState = {
        "pipeline_id": request.get("pipelineId") or request.get("pipeline_id") or "",
        "original_requirement": request.get("requirement")
        or global_context.get("original_requirement", ""),
        "error_logs": [],
    }

    _merge_known_state(state, global_context)
    _merge_known_state(state, previous_output)

    # 控制平面使用 globalContext.repository 存储仓库配置；执行平面统一映射为
    # repository_context，后续 Agent 节点即可直接调用 src.context 工具读取代码库。
    repository_context = global_context.get("repository")
    if repository_context:
        state["repository_context"] = repository_context

    llm_config = global_context.get("llm_config")
    if llm_config:
        state["llm_config"] = llm_config
        state["llm_runtime_config"] = select_stage_llm_config(llm_config, str(request.get("stageName") or request.get("stage_name") or ""))

    feedback = global_context.get("human_feedback")
    if feedback:
        state["human_feedback"] = str(feedback)
    return state


def _merge_known_state(state: DevFlowState, payload: dict[str, Any]) -> None:
    for key in (
        "structured_prd",
        "design_doc",
        "diff_patch",
        "code_generation_report",
        "test_results",
        "test_run_results",
        "review_report",
        "delivery_status",
        "human_feedback",
        "rejected_stage",
        "repository_context",
        "pipeline_context",
        "code_context",
        "current_step",
        "error_logs",
        "llm_config",
        "llm_runtime_config",
    ):
        if key in payload:
            state[key] = payload[key]  # type: ignore[literal-required]


def select_stage_llm_config(value: Any, stage_name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    selected: dict[str, Any] = {}
    default_config = value.get("default") or value.get("defaultConfig")
    if isinstance(default_config, dict):
        selected.update(default_config)
    stage_overrides = value.get("stageOverrides") or value.get("stage_overrides") or {}
    if isinstance(stage_overrides, dict):
        override = stage_overrides.get(stage_name) or stage_overrides.get(stage_name.upper())
        if isinstance(override, dict):
            selected.update({key: item for key, item in override.items() if item not in (None, "")})
    return selected


def _output_payload_for_stage(stage_name: str, state: DevFlowState) -> dict[str, Any]:
    output_keys = {
        REQUIREMENT_ANALYSIS: (
            "structured_prd",
            "code_context",
            "codeContext",
            "exploration_trace",
            "explorationTrace",
        ),
        SYSTEM_DESIGN: ("structured_prd", "design_doc", "human_feedback", "code_context"),
        CODE_GENERATION: ("design_doc", "diff_patch", "code_generation_report", "code_context"),
        TEST_GENERATION: ("diff_patch", "test_results", "code_context"),
        APPLY_AND_RUN_TESTS: (
            "diff_patch",
            "code_generation_report",
            "test_results",
            "test_run_results",
            "code_context",
        ),
        CODE_REVIEW: ("test_results", "test_run_results", "review_report", "code_context"),
        DELIVERY_INTEGRATION: ("review_report", "delivery_status", "code_context"),
    }[stage_name]

    output: dict[str, Any] = {"current_step": state.get("current_step", stage_name)}
    for key in output_keys:
        if key in state:
            output[key] = slim_output_value(key, state[key])  # type: ignore[literal-required]
    return output


def slim_output_value(key: str, value: Any) -> Any:
    if key in ("code_context", "codeContext"):
        return slim_code_context(value)
    if key in ("exploration_trace", "explorationTrace"):
        return slim_exploration_trace(value)
    return value


def slim_code_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    context = deepcopy(value)
    context.pop("files", None)
    context["candidate_files"] = list(context.get("candidate_files") or [])[:80]
    context["inspected_files"] = list(context.get("inspected_files") or [])
    context["search_queries"] = list(context.get("search_queries") or [])[:40]
    context["skipped_paths"] = list(context.get("skipped_paths") or [])[:40]
    context["evidence"] = [slim_evidence_item(item) for item in list(context.get("evidence") or [])[:24]]
    context["exploration_trace"] = slim_exploration_trace(context.get("exploration_trace"))
    context["repository_map_summary"] = repository_map_summary(context.get("repository_map"))
    context.pop("repository_map", None)
    return context


def slim_evidence_item(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    item = dict(value)
    if "excerpt" in item:
        item["excerpt"] = truncate_text(item.get("excerpt"), 800)
    return item


def slim_exploration_trace(value: Any) -> list[Any]:
    if not isinstance(value, list):
        return []
    slimmed = []
    for step in value[:40]:
        if not isinstance(step, dict):
            slimmed.append(step)
            continue
        slimmed.append(
            {
                "stepIndex": step.get("stepIndex") or step.get("step_index"),
                "roundIndex": step.get("roundIndex") or step.get("round_index"),
                "actionType": step.get("actionType") or step.get("action_type"),
                "resultSummary": truncate_text(step.get("resultSummary") or step.get("result_summary"), 500),
                "selectedFiles": list(step.get("selectedFiles") or step.get("selected_files") or [])[:20],
            }
        )
    return slimmed


def repository_map_summary(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    files = value.get("files")
    directories = value.get("directorySummaries") or value.get("directory_summaries")
    high_signal = value.get("highSignalFiles") or value.get("high_signal_files")
    entrypoints = value.get("entrypointFiles") or value.get("entrypoint_files")
    return {
        "file_count": len(files) if isinstance(files, list) else 0,
        "directory_count": len(directories) if isinstance(directories, list) else 0,
        "high_signal_files": list(high_signal or [])[:40],
        "entrypoint_files": list(entrypoints or [])[:40],
    }


def truncate_text(value: Any, limit: int) -> str:
    text = str(value or "")
    return text if len(text) <= limit else text[:limit] + "... [truncated]"


def json_compact(value: Any, limit: int = 1600) -> str:
    text = json.dumps(value, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "... [truncated]"
