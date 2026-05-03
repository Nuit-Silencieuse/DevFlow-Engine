from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class LlmMessage:
    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(frozen=True)
class LlmRequest:
    task: str
    messages: tuple[LlmMessage, ...]
    response_format: Literal["text", "json"] = "json"
    json_schema: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    timeout_seconds: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class LlmResponse:
    provider: str
    model: str
    text: str
    parsed_json: dict[str, Any] | None
    usage: dict[str, Any]
    latency_ms: int
    request_id: str | None = None
