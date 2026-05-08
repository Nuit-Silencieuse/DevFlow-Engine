from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.llm.config import EXECUTION_PLANE_ROOT, load_env_file, merge_env_sources, resolve_env_file
from src.llm.tracing import collect_secrets, is_sensitive_key, parse_int


DEFAULT_TRACE_DIR = EXECUTION_PLANE_ROOT / "logs"


class AgentTraceRecorder:
    """Lightweight JSONL recorder for Agent node-level observability.

    The recorder deliberately stores summaries instead of full prompts, diffs or
    repository excerpts. Temporal payloads and frontend artifacts can reference
    this file without copying large debugging data into workflow history.
    """

    def __init__(
        self,
        *,
        enabled: bool,
        stdout: bool,
        file_path: Path | None,
        secrets: list[str] | None = None,
        max_string_chars: int = 4000,
    ):
        self.enabled = enabled
        self.stdout = stdout
        self.file_path = file_path
        self.secrets = [secret for secret in secrets or [] if secret]
        self.max_string_chars = max_string_chars

    @classmethod
    def from_sources(
        cls,
        *,
        pipeline_id: str | None = None,
        env: Mapping[str, str] | None = None,
        env_file: str | Path | None = None,
    ) -> "AgentTraceRecorder":
        process_env = os.environ if env is None else env
        source = merge_env_sources(
            load_env_file(resolve_env_file(process_env, env_file, use_default=env is None)),
            process_env,
        )
        enabled = parse_bool_with_default(source.get("DEVFLOW_AGENT_TRACE"), default=True)
        stdout = parse_bool_with_default(source.get("DEVFLOW_AGENT_TRACE_STDOUT"), default=False)
        file_path = resolve_agent_trace_file(source, pipeline_id)
        return cls(
            enabled=enabled,
            stdout=stdout,
            file_path=file_path,
            secrets=collect_secrets(source),
            max_string_chars=parse_int(source.get("DEVFLOW_AGENT_TRACE_MAX_CHARS"), 4000),
        )

    @classmethod
    def disabled(cls) -> "AgentTraceRecorder":
        return cls(enabled=False, stdout=False, file_path=None)

    def record(self, event_type: str, payload: dict[str, Any]) -> None:
        if not self.enabled:
            return
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": event_type,
            "payload": self.redact(payload),
        }
        line = json.dumps(event, ensure_ascii=False, sort_keys=True)
        if self.stdout:
            print(line)
        if self.file_path is not None:
            self.file_path.parent.mkdir(parents=True, exist_ok=True)
            with self.file_path.open("a", encoding="utf-8") as file:
                file.write(line + "\n")

    def diagnostics(self, *, tail: int = 12) -> dict[str, Any]:
        events = read_recent_events(self.file_path, tail=tail) if self.file_path else []
        return {
            "enabled": self.enabled,
            "trace_file": str(self.file_path) if self.file_path else "",
            "recent_events": events,
        }

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                redacted[key] = "[REDACTED]" if is_sensitive_key(str(key)) else self.redact(item)
            return redacted
        if isinstance(value, list):
            return [self.redact(item) for item in value]
        if isinstance(value, tuple):
            return [self.redact(item) for item in value]
        if isinstance(value, str):
            text = value
            for secret in self.secrets:
                text = text.replace(secret, "[REDACTED]")
            if self.max_string_chars > 0 and len(text) > self.max_string_chars:
                return text[: self.max_string_chars] + "...[TRUNCATED]"
            return text
        return value


def resolve_agent_trace_file(source: Mapping[str, str], pipeline_id: str | None) -> Path | None:
    configured = str(source.get("DEVFLOW_AGENT_TRACE_FILE") or "").strip()
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else EXECUTION_PLANE_ROOT / path

    trace_dir_text = str(source.get("DEVFLOW_AGENT_TRACE_DIR") or "").strip()
    trace_dir = Path(trace_dir_text) if trace_dir_text else DEFAULT_TRACE_DIR
    if not trace_dir.is_absolute():
        trace_dir = EXECUTION_PLANE_ROOT / trace_dir

    safe_pipeline_id = safe_trace_name(pipeline_id or "unknown")
    return trace_dir / f"agent-trace-{safe_pipeline_id}.jsonl"


def safe_trace_name(value: str) -> str:
    result = []
    for char in str(value):
        result.append(char if char.isalnum() or char in ("-", "_") else "-")
    text = "".join(result).strip("-")
    return text[:80] or "unknown"


def parse_bool_with_default(value: str | None, *, default: bool) -> bool:
    if value is None or str(value).strip() == "":
        return default
    return str(value).strip().casefold() in {"1", "true", "yes", "on"}


def read_recent_events(file_path: Path | None, *, tail: int = 12) -> list[dict[str, Any]]:
    if file_path is None or not file_path.exists():
        return []
    lines = file_path.read_text(encoding="utf-8").splitlines()[-tail:]
    events: list[dict[str, Any]] = []
    for line in lines:
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return events
