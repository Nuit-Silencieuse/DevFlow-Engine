from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from .config import (
    EXECUTION_PLANE_ROOT,
    load_env_file,
    merge_env_sources,
    resolve_env_file,
)


class LlmTraceRecorder:
    """记录 LLM 调用和 Agent 中间产物的轻量追踪器。

    追踪能力只在测试、联调或问题排查时打开。默认关闭，避免把 Prompt、
    代码上下文或模型响应写入日志。打开后同时支持终端打印和 JSONL 文件，
    并在写出前递归脱敏 API Key、Token、Authorization 等敏感值。
    """

    def __init__(
        self,
        enabled: bool = False,
        stdout: bool = False,
        file_path: Path | None = None,
        secrets: list[str] | None = None,
        max_string_chars: int = 50_000,
    ):
        self.enabled = enabled
        self.stdout = stdout
        self.file_path = file_path
        self.secrets = [secret for secret in secrets or [] if secret]
        self.max_string_chars = max_string_chars

    @classmethod
    def from_sources(
        cls,
        env: Mapping[str, str] | None = None,
        env_file: str | Path | None = None,
    ) -> "LlmTraceRecorder":
        process_env = os.environ if env is None else env
        source = merge_env_sources(
            load_env_file(resolve_env_file(process_env, env_file, use_default=env is None)),
            process_env,
        )
        enabled = parse_bool(source.get("DEVFLOW_LLM_TRACE"))
        stdout = parse_bool(source.get("DEVFLOW_LLM_TRACE_STDOUT"))
        file_path = resolve_trace_file(source.get("DEVFLOW_LLM_TRACE_FILE"))
        return cls(
            enabled=enabled,
            stdout=stdout,
            file_path=file_path,
            secrets=collect_secrets(source),
            max_string_chars=parse_int(source.get("DEVFLOW_LLM_TRACE_MAX_CHARS"), 50_000),
        )

    @classmethod
    def disabled(cls) -> "LlmTraceRecorder":
        return cls(enabled=False)

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

    def redact(self, value: Any) -> Any:
        if isinstance(value, dict):
            redacted = {}
            for key, item in value.items():
                if is_sensitive_key(str(key)):
                    redacted[key] = "[REDACTED]"
                else:
                    redacted[key] = self.redact(item)
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


def parse_bool(value: str | None) -> bool:
    return str(value or "").strip().casefold() in {"1", "true", "yes", "on"}


def parse_int(value: str | None, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except ValueError:
        return default


def is_sensitive_key(key: str) -> bool:
    lowered = key.casefold()
    return any(
        marker in lowered
        for marker in (
            "api_key",
            "apikey",
            "access_token",
            "refresh_token",
            "id_token",
            "secret",
            "password",
            "authorization",
        )
    )


def collect_secrets(env: Mapping[str, str]) -> list[str]:
    secrets: list[str] = []
    for key, value in env.items():
        if value and is_sensitive_key(key):
            secrets.append(value)
    return secrets


def resolve_trace_file(value: str | None) -> Path | None:
    if not value or not value.strip():
        return None
    path = Path(value.strip())
    if path.is_absolute():
        return path
    return EXECUTION_PLANE_ROOT / path
