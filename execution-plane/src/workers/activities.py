from __future__ import annotations

from typing import Any

from temporalio import activity

from src.graph.flow import (
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
        review_code,
        integrate_delivery,
    ]


def _execute_stage(stage_name: str, request: StageExecutionRequest) -> StageExecutionResult:
    state = _state_from_request(request)
    result_state = run_stage(stage_name, state)
    output_payload = _output_payload_for_stage(stage_name, result_state)
    return {
        "stageName": stage_name,
        "status": "COMPLETED",
        "outputPayload": output_payload,
    }


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
        "test_results",
        "review_report",
        "delivery_status",
        "human_feedback",
        "repository_context",
        "code_context",
        "current_step",
        "error_logs",
    ):
        if key in payload:
            state[key] = payload[key]  # type: ignore[literal-required]


def _output_payload_for_stage(stage_name: str, state: DevFlowState) -> dict[str, Any]:
    output_keys = {
        REQUIREMENT_ANALYSIS: ("structured_prd", "code_context", "codeContext"),
        SYSTEM_DESIGN: ("structured_prd", "design_doc", "human_feedback"),
        CODE_GENERATION: ("design_doc", "diff_patch"),
        TEST_GENERATION: ("diff_patch", "test_results"),
        CODE_REVIEW: ("test_results", "review_report"),
        DELIVERY_INTEGRATION: ("review_report", "delivery_status"),
    }[stage_name]

    output: dict[str, Any] = {"current_step": state.get("current_step", stage_name)}
    for key in output_keys:
        if key in state:
            output[key] = state[key]  # type: ignore[literal-required]
    return output
