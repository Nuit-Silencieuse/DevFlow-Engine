from __future__ import annotations

import json
import os
import re
import time
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .config import EXECUTION_PLANE_ROOT, LlmClientConfig
from .errors import (
    LlmConfigurationError,
    LlmJsonParseError,
    LlmProviderError,
    LlmRateLimitError,
    LlmTimeoutError,
)
from .messages import LlmRequest, LlmResponse
from .providers import (
    AnthropicCompatibleProvider,
    FakeProvider,
    LlmProvider,
    OpenAICompatibleProvider,
)
from .tracing import LlmTraceRecorder


class LlmClient:
    """Agent 调用 LLM 的唯一门面。

    这个类刻意把 Provider 选择、短周期重试、JSON 格式解析放在 Agent 外部。
    后续 Requirement/Design/Coder Agent 只需要描述任务和输出 schema，不能
    直接读取 API Key 或依赖某个厂商 SDK。这样才能在运行时为不同阶段切换
    Provider，也能用 Fake Provider 做确定性 TDD。
    """

    def __init__(
        self,
        config: LlmClientConfig | None = None,
        providers: dict[str, LlmProvider] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        trace_recorder: LlmTraceRecorder | None = None,
    ):
        self.config = config or LlmClientConfig.from_env()
        self.providers: dict[str, LlmProvider] = self.default_providers()
        if providers:
            self.providers.update(providers)
        self.sleep = sleep
        self.trace_recorder = trace_recorder or LlmTraceRecorder.from_sources()

    @classmethod
    def from_env(cls) -> "LlmClient":
        return cls(config=LlmClientConfig.from_env())

    @classmethod
    def from_sources(
        cls,
        config_path: str | None = None,
        env_file: str | None = None,
    ) -> "LlmClient":
        return cls(
            config=LlmClientConfig.from_sources(
                config_path=config_path,
                env_file=env_file,
            )
        )

    @staticmethod
    def default_providers() -> dict[str, LlmProvider]:
        return {
            "openai_compatible": OpenAICompatibleProvider(),
            "anthropic_compatible": AnthropicCompatibleProvider(),
            "fake": FakeProvider(),
        }

    def complete_json(self, request: LlmRequest) -> dict[str, Any]:
        json_request = replace(request, response_format="json")
        response = self.complete(json_request)
        if response.parsed_json is None:
            raise LlmJsonParseError("LLM response did not contain parsed JSON")
        return response.parsed_json

    def complete(self, request: LlmRequest) -> LlmResponse:
        provider_name = request.provider or self.config.default_provider
        provider = self.providers.get(provider_name)
        if provider is None:
            raise LlmConfigurationError(f"LLM provider '{provider_name}' is not registered")
        request_config = self.config.for_request(provider_name, request.model)
        provider_settings = request_config.settings_for(provider_name)
        effective_timeout_seconds = request_config.timeout_for_task(
            request.task,
            request.timeout_seconds,
        )
        self.trace_recorder.record(
            "llm.request",
            {
                "task": request.task,
                "provider": provider_name,
                "model": request.model
                or request_config.default_model
                or provider_settings.get("default_model"),
                "response_format": request.response_format,
                "temperature": request.temperature
                if request.temperature is not None
                else request_config.temperature,
                "timeout_seconds": effective_timeout_seconds,
                "metadata": request.metadata,
                "messages": [
                    {"role": message.role, "content": message.content}
                    for message in request.messages
                ],
                "json_schema": request.json_schema,
            },
        )
        response = self._complete_with_retries(
            provider,
            replace(request, timeout_seconds=effective_timeout_seconds),
            request_config,
        )
        if request.response_format != "json":
            self.trace_recorder.record(
                "llm.response",
                response_to_trace_payload(request, response),
            )
            return response
        try:
            parsed_json = parse_json_response(
                response.text,
                repair_attempts=request_config.json_repair_attempts,
            )
        except LlmJsonParseError as exc:
            # JSON 解析失败时也记录原始响应。否则 Temporal 只会看到
            # “line 1 column 1” 这类 JSONDecodeError，无法判断模型到底是
            # 返回了纯 diff、Markdown 说明、空文本，还是 provider 的异常页面。
            self.trace_recorder.record(
                "llm.response",
                response_to_trace_payload(request, response),
            )
            debug_path = write_invalid_json_debug_file(request, response, exc)
            root_error = exc.__cause__ or exc
            raise LlmJsonParseError(
                "LLM response is not valid JSON: "
                f"{root_error}; full_response_file={debug_path}"
            ) from root_error
        parsed_response = replace(response, parsed_json=parsed_json)
        self.trace_recorder.record(
            "llm.response",
            response_to_trace_payload(request, parsed_response),
        )
        return parsed_response

    def _complete_with_retries(
        self,
        provider: LlmProvider,
        request: LlmRequest,
        config: LlmClientConfig,
    ) -> LlmResponse:
        """执行有限重试，避免和 Temporal Activity 重试叠加成长期阻塞。

        Temporal 已经负责阶段级重试和持久化调度。客户端内部只处理 timeout、
        rate limit、Provider 5xx 这类短周期错误，而且次数受配置限制。
        """

        attempt = 0
        while True:
            try:
                return provider.complete(request, config)
            except (LlmTimeoutError, LlmRateLimitError, LlmProviderError):
                if attempt >= config.max_retries:
                    raise
                self.sleep(min(0.1 * (2**attempt), 2.0))
                attempt += 1


def parse_json_response(text: str, repair_attempts: int = 1) -> dict[str, Any]:
    extracted = extract_json_text(text)
    candidates = [extracted]
    if repair_attempts > 0:
        candidates.extend(
            [
                repair_json_text(extracted),
                escape_control_chars_in_json_strings(extracted),
                repair_json_text(escape_control_chars_in_json_strings(extracted)),
                repair_json_text(unescape_json_shell_text(extracted)),
                repair_json_text(
                    escape_control_chars_in_json_strings(unescape_json_shell_text(extracted))
                ),
            ]
        )

    last_error: Exception | None = None
    for candidate in ordered_unique_texts(candidates):
        try:
            parsed = parse_json_candidate(candidate)
            if not isinstance(parsed, dict):
                raise LlmJsonParseError("LLM JSON response root must be an object")
            return parsed
        except (json.JSONDecodeError, LlmJsonParseError) as exc:
            last_error = exc
    preview = response_preview(text)
    raise LlmJsonParseError(
        f"LLM response is not valid JSON: {last_error}; response_preview={preview!r}"
    ) from last_error


def parse_json_candidate(text: str) -> Any:
    parsed = json.loads(text)
    if isinstance(parsed, str):
        nested = parsed.strip()
        if nested.startswith("{") and nested.endswith("}"):
            return json.loads(escape_control_chars_in_json_strings(nested))
    return parsed


def response_preview(text: str, limit: int = 240) -> str:
    compact = text.strip().replace("\r", "\\r").replace("\n", "\\n")
    if len(compact) <= limit:
        return compact
    return compact[:limit]


def extract_json_text(text: str) -> str:
    """提取模型响应中的 JSON 文本。

    很多模型即使被要求返回 JSON，也可能包一层 Markdown fenced code block。
    这里只处理“包住整个响应”的格式外壳，不补业务字段，避免客户端偷偷变成
    规则生成器。注意不能在完整 JSON 内部搜索任意 fenced code block，因为
    代码生成阶段的 `diff_patch` 经常包含 README 里的 ```javascript 示例。
    """

    stripped = text.strip()
    fenced = re.fullmatch(
        r"```(?:json)?\s*(.*?)\s*```",
        stripped,
        re.IGNORECASE | re.DOTALL,
    )
    if fenced:
        return fenced.group(1).strip()
    return stripped


def repair_json_text(text: str) -> str:
    """有限 JSON 格式修复。

    修复范围故意很窄：去掉对象/数组结尾前的尾随逗号，并在响应前后包含
    说明文字时截取最外层对象。业务字段缺失、类型不对由 Agent validator
    处理，不能在这里用规则补齐。
    """

    repaired = re.sub(r",\s*([}\]])", r"\1", text.strip())
    start = repaired.find("{")
    end = repaired.rfind("}")
    if start != -1 and end != -1 and start < end:
        repaired = repaired[start : end + 1]
    return repaired


def escape_control_chars_in_json_strings(text: str) -> str:
    """只修复 JSON 字符串内部的裸控制字符。

    代码生成阶段的 `diff_patch` 很长，部分模型会把 diff 中的真实换行直接放进
    JSON 字符串，导致 `json.loads` 报 `Invalid control character`。这里通过
    字符级扫描只处理已经进入字符串后的换行、回车和制表符，不改变 JSON 对象
    外部的格式，也不补任何业务字段。
    """

    result: list[str] = []
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                result.append(char)
                escaped = False
                continue
            if char == "\\":
                result.append(char)
                escaped = True
                continue
            if char == '"':
                result.append(char)
                in_string = False
                continue
            if char == "\n":
                result.append("\\n")
                continue
            if char == "\r":
                result.append("\\r")
                continue
            if char == "\t":
                result.append("\\t")
                continue
            result.append(char)
            continue

        result.append(char)
        if char == '"':
            in_string = True
    return "".join(result)


def unescape_json_shell_text(text: str) -> str:
    """处理模型把 JSON 对象外壳二次转义的情况。

    某些 OpenAI-compatible 服务在 JSON mode 下会返回形如
    `{\\n  \"summary\": \"...\"}` 的文本。它看起来是 JSON，但对象外层的
    换行和引号都被多转义了一次。该修复只作为普通解析失败后的候选路径。
    """

    stripped = text.strip()
    if '\\"' not in stripped and "\\n" not in stripped:
        return stripped
    return (
        stripped.replace('\\"', '"')
        .replace("\\n", "\n")
        .replace("\\r", "\r")
        .replace("\\t", "\t")
    )


def ordered_unique_texts(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            result.append(value)
    return result


def response_to_trace_payload(request: LlmRequest, response: LlmResponse) -> dict[str, Any]:
    return {
        "task": request.task,
        "provider": response.provider,
        "model": response.model,
        "text": response.text,
        "parsed_json": response.parsed_json,
        "usage": response.usage,
        "latency_ms": response.latency_ms,
        "request_id": response.request_id,
    }


def write_invalid_json_debug_file(
    request: LlmRequest,
    response: LlmResponse,
    error: Exception,
) -> Path:
    """把无法解析的模型响应完整写入调试文件。

    Activity 异常消息不能无限增长，否则 Temporal 会再次报 failure payload 过大。
    因此这里不把完整模型响应塞进异常 message，而是落到本地日志文件。文件中
    保留原始响应全文，不做 `[truncated]` 截断，方便复盘模型到底返回了什么。
    """

    debug_dir = resolve_invalid_json_debug_dir()
    debug_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    task_name = re.sub(r"[^A-Za-z0-9_.-]+", "-", request.task or "unknown").strip("-")
    path = debug_dir / f"llm-invalid-json-{task_name or 'unknown'}-{timestamp}.json"
    payload = {
        "task": request.task,
        "metadata": request.metadata,
        "provider": response.provider,
        "model": response.model,
        "request_id": response.request_id,
        "usage": response.usage,
        "latency_ms": response.latency_ms,
        "error_type": error.__class__.__name__,
        "error_message": str(error),
        "response_text": response.text,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def resolve_invalid_json_debug_dir() -> Path:
    configured = os.getenv("DEVFLOW_LLM_INVALID_JSON_DIR")
    if configured and configured.strip():
        path = Path(configured.strip())
        return path if path.is_absolute() else EXECUTION_PLANE_ROOT / path
    return EXECUTION_PLANE_ROOT / "logs"
