from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from src.agents import CoderAgent, DesignAgent, RequirementAgent
from src.pipeline_context import normalize_pipeline_context

from .state import DevFlowState


GraphNode = Callable[[DevFlowState], DevFlowState]

REQUIREMENT_ANALYSIS = "REQUIREMENT_ANALYSIS"
SYSTEM_DESIGN = "SYSTEM_DESIGN"
CODE_GENERATION = "CODE_GENERATION"
TEST_GENERATION = "TEST_GENERATION"
CODE_REVIEW = "CODE_REVIEW"
DELIVERY_INTEGRATION = "DELIVERY_INTEGRATION"

STAGE_ORDER = [
    REQUIREMENT_ANALYSIS,
    SYSTEM_DESIGN,
    CODE_GENERATION,
    TEST_GENERATION,
    CODE_REVIEW,
    DELIVERY_INTEGRATION,
]


def analyze_requirement_node(state: DevFlowState) -> DevFlowState:
    return RequirementAgent().run(state)


def design_system_node(state: DevFlowState) -> DevFlowState:
    return DesignAgent().run(state)


def generate_code_node(state: DevFlowState) -> DevFlowState:
    return CoderAgent().run(state)


def generate_tests_node(state: DevFlowState) -> DevFlowState:
    return {
        "test_results": {
            "status": "PENDING",
            "summary": "Test generation is reserved for the test agent implementation.",
        },
        "pipeline_context": normalize_pipeline_context(state.get("pipeline_context")),
        "current_step": TEST_GENERATION,
        "error_logs": state.get("error_logs", []),
    }


def review_code_node(state: DevFlowState) -> DevFlowState:
    return {
        "review_report": {
            "status": "PENDING",
            "summary": "Code review is reserved for the review agent implementation.",
        },
        "pipeline_context": normalize_pipeline_context(state.get("pipeline_context")),
        "current_step": CODE_REVIEW,
        "error_logs": state.get("error_logs", []),
    }


def integrate_delivery_node(state: DevFlowState) -> DevFlowState:
    return {
        "delivery_status": {
            "status": "PENDING",
            "summary": "Delivery integration is reserved for the delivery agent implementation.",
        },
        "pipeline_context": normalize_pipeline_context(state.get("pipeline_context")),
        "current_step": DELIVERY_INTEGRATION,
        "error_logs": state.get("error_logs", []),
    }


STAGE_NODES: dict[str, GraphNode] = {
    REQUIREMENT_ANALYSIS: analyze_requirement_node,
    SYSTEM_DESIGN: design_system_node,
    CODE_GENERATION: generate_code_node,
    TEST_GENERATION: generate_tests_node,
    CODE_REVIEW: review_code_node,
    DELIVERY_INTEGRATION: integrate_delivery_node,
}


def build_devflow_graph():
    builder = StateGraph(DevFlowState)
    for stage_name, node in STAGE_NODES.items():
        builder.add_node(stage_name, node)

    builder.set_entry_point(REQUIREMENT_ANALYSIS)
    builder.add_edge(REQUIREMENT_ANALYSIS, SYSTEM_DESIGN)
    builder.add_edge(SYSTEM_DESIGN, CODE_GENERATION)
    builder.add_edge(CODE_GENERATION, TEST_GENERATION)
    builder.add_edge(TEST_GENERATION, CODE_REVIEW)
    builder.add_edge(CODE_REVIEW, DELIVERY_INTEGRATION)
    builder.add_edge(DELIVERY_INTEGRATION, END)
    return builder.compile()


def run_stage(stage_name: str, state: DevFlowState) -> DevFlowState:
    try:
        node = STAGE_NODES[stage_name]
    except KeyError as exc:
        raise ValueError(f"Unsupported DevFlow stage: {stage_name}") from exc

    updates = node(state)
    merged: dict[str, Any] = dict(state)
    merged.update(updates)
    return merged
