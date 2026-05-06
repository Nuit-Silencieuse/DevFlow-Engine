from __future__ import annotations

from typing import Any, TypedDict


class DevFlowState(TypedDict, total=False):
    original_requirement: str
    repository_context: dict[str, Any]
    pipeline_context: dict[str, Any]
    code_context: dict[str, Any]
    structured_prd: dict[str, Any]
    design_doc: dict[str, Any]
    diff_patch: str
    code_generation_report: dict[str, Any]
    test_results: dict[str, Any]
    review_report: dict[str, Any]
    delivery_status: dict[str, Any]
    human_feedback: str
    current_step: str
    error_logs: list[str]
