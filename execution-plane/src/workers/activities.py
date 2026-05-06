from __future__ import annotations

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
    try:
        state = _state_from_request(request)
        result_state = run_stage(stage_name, state)
        output_payload = _output_payload_for_stage(stage_name, result_state)
        return {
            "stageName": stage_name,
            "status": "COMPLETED",
            "outputPayload": output_payload,
        }
    except Exception as exc:
        raise concise_activity_error(stage_name, exc) from None


def concise_activity_error(stage_name: str, exc: Exception) -> ApplicationError:
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
    return ApplicationError(
        f"{stage_name} Activity failed: {error_type}: {message}{hint}",
        type=f"DevFlow{error_type}",
    )


def _state_from_request(request: StageExecutionRequest) -> DevFlowState:
    global_context = dict(request.get("globalContext") or {})
    previous_output = dict(request.get("previousOutput") or {})
    state: DevFlowState = {
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
        "repository_context",
        "pipeline_context",
        "code_context",
        "current_step",
        "error_logs",
    ):
        if key in payload:
            state[key] = payload[key]  # type: ignore[literal-required]


def _output_payload_for_stage(stage_name: str, state: DevFlowState) -> dict[str, Any]:
    output_keys = {
        REQUIREMENT_ANALYSIS: (
            "structured_prd",
            "code_context",
            "codeContext",
            "pipeline_context",
            "exploration_trace",
            "explorationTrace",
        ),
        SYSTEM_DESIGN: ("structured_prd", "design_doc", "human_feedback", "pipeline_context"),
        CODE_GENERATION: ("design_doc", "diff_patch", "code_generation_report", "pipeline_context"),
        TEST_GENERATION: ("diff_patch", "test_results", "pipeline_context"),
        APPLY_AND_RUN_TESTS: ("test_results", "test_run_results", "pipeline_context"),
        CODE_REVIEW: ("test_results", "review_report", "pipeline_context"),
        DELIVERY_INTEGRATION: ("review_report", "delivery_status", "pipeline_context"),
    }[stage_name]

    output: dict[str, Any] = {"current_step": state.get("current_step", stage_name)}
    for key in output_keys:
        if key in state:
            output[key] = state[key]  # type: ignore[literal-required]
    return output
