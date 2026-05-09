from __future__ import annotations

from collections.abc import Callable
from typing import Any

from langgraph.graph import END, StateGraph

from src.agents import (
    ApplyAndRunTestsAgent,
    CoderAgent,
    DeliveryAgent,
    DesignAgent,
    RequirementAgent,
    ReviewAgent,
    TestAgent,
)
from src.llm import LlmClient, LlmClientConfig
from .state import DevFlowState


GraphNode = Callable[[DevFlowState], DevFlowState]

REQUIREMENT_ANALYSIS = "REQUIREMENT_ANALYSIS"
SYSTEM_DESIGN = "SYSTEM_DESIGN"
CODE_GENERATION = "CODE_GENERATION"
TEST_GENERATION = "TEST_GENERATION"
APPLY_AND_RUN_TESTS = "APPLY_AND_RUN_TESTS"
CODE_REVIEW = "CODE_REVIEW"
DELIVERY_INTEGRATION = "DELIVERY_INTEGRATION"

STAGE_ORDER = [
    REQUIREMENT_ANALYSIS,
    SYSTEM_DESIGN,
    CODE_GENERATION,
    TEST_GENERATION,
    APPLY_AND_RUN_TESTS,
    CODE_REVIEW,
    DELIVERY_INTEGRATION,
]


def analyze_requirement_node(state: DevFlowState) -> DevFlowState:
    return instantiate_agent(RequirementAgent, state).run(state)


def design_system_node(state: DevFlowState) -> DevFlowState:
    return instantiate_agent(DesignAgent, state).run(state)


def generate_code_node(state: DevFlowState) -> DevFlowState:
    return instantiate_agent(CoderAgent, state).run(state)


def generate_tests_node(state: DevFlowState) -> DevFlowState:
    return instantiate_agent(TestAgent, state).run(state)


def apply_and_run_tests_node(state: DevFlowState) -> DevFlowState:
    return ApplyAndRunTestsAgent().run(state)


def review_code_node(state: DevFlowState) -> DevFlowState:
    return instantiate_agent(ReviewAgent, state).run(state)


def integrate_delivery_node(state: DevFlowState) -> DevFlowState:
    return instantiate_agent(DeliveryAgent, state).run(state)


def instantiate_agent(agent_class: Any, state: DevFlowState) -> Any:
    runtime_config = state.get("llm_runtime_config")
    if not isinstance(runtime_config, dict) or not runtime_config:
        return agent_class()
    client = LlmClient(config=LlmClientConfig.from_sources().with_runtime_overrides(runtime_config))
    try:
        return agent_class(llm_client=client)
    except TypeError:
        return agent_class()


STAGE_NODES: dict[str, GraphNode] = {
    REQUIREMENT_ANALYSIS: analyze_requirement_node,
    SYSTEM_DESIGN: design_system_node,
    CODE_GENERATION: generate_code_node,
    TEST_GENERATION: generate_tests_node,
    APPLY_AND_RUN_TESTS: apply_and_run_tests_node,
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
    builder.add_edge(TEST_GENERATION, APPLY_AND_RUN_TESTS)
    builder.add_edge(APPLY_AND_RUN_TESTS, CODE_REVIEW)
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
