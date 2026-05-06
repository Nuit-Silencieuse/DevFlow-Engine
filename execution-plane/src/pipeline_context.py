from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any


PIPELINE_CONTEXT_VERSION = 1
DEFAULT_REUSE_CONFIDENCE = 0.75


def empty_pipeline_context() -> dict[str, Any]:
    return {
        "version": PIPELINE_CONTEXT_VERSION,
        "repository": {},
        "code_contexts": [],
        "artifact_index": {},
    }


def normalize_pipeline_context(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return empty_pipeline_context()
    normalized = deepcopy(value)
    normalized.setdefault("version", PIPELINE_CONTEXT_VERSION)
    normalized.setdefault("repository", {})
    normalized.setdefault("code_contexts", [])
    normalized.setdefault("artifact_index", {})
    if not isinstance(normalized["code_contexts"], list):
        normalized["code_contexts"] = []
    if not isinstance(normalized["repository"], dict):
        normalized["repository"] = {}
    if not isinstance(normalized["artifact_index"], dict):
        normalized["artifact_index"] = {}
    return normalized


def select_reusable_code_context(
    pipeline_context: dict[str, Any],
    repository_payload: dict[str, Any] | None,
    *,
    min_confidence: float = DEFAULT_REUSE_CONFIDENCE,
) -> dict[str, Any] | None:
    contexts = pipeline_context.get("code_contexts")
    if not isinstance(contexts, list):
        return None
    requested_root = normalize_root_path((repository_payload or {}).get("rootPath"))
    for code_context in reversed(contexts):
        if not isinstance(code_context, dict):
            continue
        if requested_root and normalize_root_path(code_context.get("root_path")) != requested_root:
            continue
        if float_or_zero(code_context.get("confidence")) < min_confidence:
            continue
        if not code_context.get("inspected_files") and not code_context.get("repository_map"):
            continue
        return deepcopy(code_context)
    return None


def context_pack_from_reused_code_context(code_context: dict[str, Any]) -> dict[str, Any]:
    notes = list(code_context.get("notes", []))
    notes.append(
        "复用 pipeline_context 中已有的高置信度代码上下文；本阶段未重复执行 compact map、SEARCH_TEXT 或 READ_FILE。"
    )
    return {
        "status": code_context.get("status", "COMPLETE"),
        "root_path": code_context.get("root_path", ""),
        "files": [],
        "inspected_files": list(code_context.get("inspected_files", [])),
        "search_queries": list(code_context.get("search_queries", [])),
        "candidate_files": list(code_context.get("candidate_files", [])),
        "repository_map": dict(code_context.get("repository_map") or {}),
        "evidence": list(code_context.get("evidence", [])),
        "skipped_paths": list(code_context.get("skipped_paths", [])),
        "budget_usage": {"rounds_used": 0, "files_read": 0, "bytes_read": 0, "searches_used": 0},
        "confidence": code_context.get("confidence", 0.0),
        "open_questions": list(code_context.get("open_questions", [])),
        "notes": notes,
        "total_bytes": 0,
        "exploration_trace": [
            {
                "stepIndex": 1,
                "roundIndex": 0,
                "actionType": "REUSE_CONTEXT",
                "reason": "已有阶段产物满足当前仓库的上下文复用条件。",
                "input": {
                    "sourceStage": code_context.get("stage"),
                    "confidence": code_context.get("confidence", 0.0),
                },
                "resultSummary": "reused shared pipeline code context",
                "selectedFiles": list(code_context.get("inspected_files", [])),
            }
        ],
        "reuse_policy": {
            "reused": True,
            "source_stage": code_context.get("stage"),
            "min_confidence": DEFAULT_REUSE_CONFIDENCE,
        },
    }


def append_stage_code_context(
    pipeline_context: dict[str, Any] | None,
    *,
    stage: str,
    agent: str,
    code_context: dict[str, Any],
    artifacts: dict[str, Any] | None = None,
) -> dict[str, Any]:
    shared = normalize_pipeline_context(pipeline_context)
    stored_context = deepcopy(code_context)
    stored_context["stage"] = stage
    stored_context["agent"] = agent

    contexts = [
        item
        for item in shared.get("code_contexts", [])
        if not (
            isinstance(item, dict)
            and item.get("stage") == stage
            and item.get("root_path") == stored_context.get("root_path")
        )
    ]
    contexts.append(stored_context)
    shared["code_contexts"] = contexts
    shared["latest_code_context"] = stored_context

    repository_map = stored_context.get("repository_map")
    if repository_map:
        shared["repository"] = {
            "root_path": stored_context.get("root_path", ""),
            "repository_map": repository_map,
        }

    if artifacts:
        artifact_index = dict(shared.get("artifact_index") or {})
        artifact_index[stage] = deepcopy(artifacts)
        shared["artifact_index"] = artifact_index
    return shared


def normalize_root_path(value: Any) -> str:
    if not value:
        return ""
    text = str(value).replace("\\", "/").strip()
    try:
        return str(Path(text).expanduser().resolve()).replace("\\", "/").casefold()
    except (OSError, RuntimeError):
        return text.rstrip("/").casefold()


def float_or_zero(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
