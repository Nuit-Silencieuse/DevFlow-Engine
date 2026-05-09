from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from dataclasses import replace
from typing import Any, Callable, Mapping, Protocol

from .config import LlmClientConfig
from .errors import (
    LlmAuthenticationError,
    LlmConfigurationError,
    LlmProviderError,
    LlmRateLimitError,
    LlmTimeoutError,
    sanitize_message,
)
from .messages import LlmMessage, LlmRequest, LlmResponse

Transport = Callable[[str, dict[str, str], dict[str, Any], float], dict[str, Any]]


class LlmProvider(Protocol):
    name: str

    def complete(self, request: LlmRequest, config: LlmClientConfig) -> LlmResponse:
        ...


class FakeProvider:
    """测试专用 Provider。

    Fake Provider 只模拟 LLM I/O 契约，不承载任何业务规则。后续 Agent 测试
    可以把固定响应注入这里，从而验证 Prompt、状态写入和错误处理；生产
    路径仍然必须选择真实 Provider。
    """

    def __init__(
        self,
        response_text: str = "{}",
        name: str = "fake",
        failures: list[Exception] | None = None,
    ):
        self.name = name
        self.response_text = response_text
        self.failures = list(failures or [])
        self.calls = 0

    def complete(self, request: LlmRequest, config: LlmClientConfig) -> LlmResponse:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return LlmResponse(
            provider=self.name,
            model=request.model or config.default_model or "fake-model",
            text=self.response_text,
            parsed_json=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=0,
            request_id=None,
        )


class OpenAICompatibleProvider:
    name = "openai_compatible"

    def __init__(self, transport: Transport | None = None):
        self.transport = transport or default_json_transport

    def complete(self, request: LlmRequest, config: LlmClientConfig) -> LlmResponse:
        settings = config.settings_for(self.name)
        api_key, base_url, model = resolve_provider_settings(
            provider_name=self.name,
            request=request,
            config=config,
            settings=settings,
            default_base_url="https://api.openai.com/v1",
        )
        timeout_seconds = request.timeout_seconds or config.timeout_seconds
        payload = {
            "model": model,
            "messages": [message_to_mapping(message) for message in request.messages],
            "temperature": request.temperature
            if request.temperature is not None
            else config.temperature,
        }
        if request.max_tokens:
            payload["max_tokens"] = request.max_tokens
        if request.response_format == "json":
            payload["response_format"] = {"type": "json_object"}
            payload["messages"] = append_json_instruction(
                payload["messages"],
                request.json_schema,
            )

        started_at = time.monotonic()
        data = self.transport(
            url=f"{base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        data = ensure_response_mapping(data)
        latency_ms = int((time.monotonic() - started_at) * 1000)
        text = extract_openai_compatible_text(data)
        return LlmResponse(
            provider=self.name,
            model=model,
            text=text,
            parsed_json=None,
            usage=data.get("usage") or {},
            latency_ms=latency_ms,
            request_id=data.get("id"),
        )


class AnthropicCompatibleProvider:
    name = "anthropic_compatible"

    def __init__(self, transport: Transport | None = None):
        self.transport = transport or default_json_transport

    def complete(self, request: LlmRequest, config: LlmClientConfig) -> LlmResponse:
        settings = config.settings_for(self.name)
        api_key, base_url, model = resolve_provider_settings(
            provider_name=self.name,
            request=request,
            config=config,
            settings=settings,
            default_base_url="https://api.anthropic.com/v1",
        )
        timeout_seconds = request.timeout_seconds or config.timeout_seconds
        system_messages = [message.content for message in request.messages if message.role == "system"]
        chat_messages = [
            message_to_mapping(message)
            for message in request.messages
            if message.role != "system"
        ]
        payload = {
            "model": model,
            "messages": chat_messages,
            "temperature": request.temperature
            if request.temperature is not None
            else config.temperature,
            "max_tokens": request.max_tokens or 4096,
        }
        if system_messages:
            payload["system"] = "\n\n".join(system_messages)
        if request.response_format == "json":
            payload["messages"] = append_json_instruction(
                payload["messages"],
                request.json_schema,
            )

        started_at = time.monotonic()
        data = self.transport(
            url=f"{base_url.rstrip('/')}/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "Content-Type": "application/json",
            },
            payload=payload,
            timeout_seconds=timeout_seconds,
        )
        data = ensure_response_mapping(data)
        latency_ms = int((time.monotonic() - started_at) * 1000)
        text_parts = extract_anthropic_compatible_text_parts(data)
        return LlmResponse(
            provider=self.name,
            model=model,
            text="".join(text_parts),
            parsed_json=None,
            usage=data.get("usage") or {},
            latency_ms=latency_ms,
            request_id=data.get("id"),
        )


def resolve_provider_settings(
    provider_name: str,
    request: LlmRequest,
    config: LlmClientConfig,
    settings: dict[str, Any],
    default_base_url: str,
) -> tuple[str, str, str]:
    api_key = settings.get("api_key")
    base_url = settings.get("base_url") or default_base_url
    model = request.model or config.default_model or settings.get("default_model")
    secrets = [str(api_key)] if api_key else []
    if not api_key:
        raise LlmConfigurationError(f"{provider_name} api_key is required")
    if not model:
        message = sanitize_message(f"{provider_name} default model is required", secrets)
        raise LlmConfigurationError(message)
    return str(api_key), str(base_url), str(model)


def ensure_response_mapping(data: Any) -> Mapping[str, Any]:
    if isinstance(data, Mapping):
        return data
    raise LlmProviderError(
        f"LLM provider response root must be an object, got {type(data).__name__}"
    )


def message_to_mapping(message: LlmMessage) -> dict[str, str]:
    return {"role": message.role, "content": message.content}


def extract_openai_compatible_text(data: Mapping[str, Any]) -> str:
    """Extract assistant text from OpenAI-compatible responses defensively.

    Some providers claim OpenAI compatibility but return a relaxed shape such as
    `choices: ["..."]` or `message: "..."`. The old parser assumed every layer was
    a dict and raised AttributeError after the model had already spent a long time
    generating. Here we accept the common relaxed forms and raise a provider error
    with a clear message only when no textual payload can be found.
    """

    choices = data.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0]
        if isinstance(choice, str):
            return choice
        if isinstance(choice, Mapping):
            message = choice.get("message")
            if isinstance(message, str):
                return message
            if isinstance(message, Mapping):
                content = message.get("content")
                extracted = extract_text_content(content)
                if extracted:
                    return extracted
            extracted = extract_text_content(choice.get("text"))
            if extracted:
                return extracted
    extracted = extract_text_content(data.get("output")) or extract_text_content(data.get("text"))
    if extracted:
        return extracted
    raise LlmProviderError("LLM provider response did not contain assistant text")


def extract_anthropic_compatible_text_parts(data: Mapping[str, Any]) -> list[str]:
    content = data.get("content")
    if isinstance(content, str):
        return [content]
    if not isinstance(content, list):
        return []
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, Mapping) and item.get("type") == "text":
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return parts


def extract_text_content(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, Mapping):
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    if isinstance(value, Mapping):
        return extract_text_content(value.get("text") or value.get("content"))
    return ""


def append_json_instruction(
    messages: list[dict[str, str]],
    json_schema: dict[str, Any] | None,
) -> list[dict[str, str]]:
    """把结构化输出要求追加到最后一条用户消息。

    OpenAI-compatible 和 Anthropic-compatible 对 JSON mode 的原生支持不完全
    相同。客户端统一追加一段轻量指令，让 Agent 不需要关心 Provider 差异。
    业务字段是否完整仍由 Agent validator 负责，客户端只负责格式层面的 JSON。
    """

    instruction = "只返回合法 JSON，不要包含 Markdown 或额外解释。"
    if json_schema:
        instruction += compact_json_schema_instruction(json_schema)
    if not messages:
        return [{"role": "user", "content": instruction}]
    copied = [dict(message) for message in messages]
    copied[-1]["content"] = f"{copied[-1]['content']}\n\n{instruction}"
    return copied


def compact_json_schema_instruction(json_schema: dict[str, Any]) -> str:
    """Build a compact schema hint instead of appending the full JSON Schema.

    Full schemas for PRD/design/diff artifacts can be thousands of characters and
    are repeated on every request. The Agent-side validator still checks the exact
    structure after the model returns; the prompt only needs top-level shape hints
    to keep the request small and reduce generation latency.
    """

    required = json_schema.get("required") or []
    properties = json_schema.get("properties") or {}
    property_names = list(properties.keys()) if isinstance(properties, dict) else []
    parts = [" Top-level JSON object required."]
    if required:
        parts.append(" Required keys: " + ", ".join(str(key) for key in required) + ".")
    if property_names:
        parts.append(" Allowed top-level keys: " + ", ".join(str(key) for key in property_names) + ".")
    return "".join(parts)


def default_json_transport(
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    timeout_seconds: float,
) -> dict[str, Any]:
    """标准库 HTTP transport。

    项目当前没有引入 requests/httpx。为了保持 T021 的依赖面很小，首版使用
    `urllib`，测试则通过注入 transport 验证请求形状，避免访问真实网络。
    """

    request = urllib.request.Request(
        url=url,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            body = response.read().decode("utf-8")
            return json.loads(body)
    except TimeoutError as exc:
        raise LlmTimeoutError("LLM provider request timed out") from exc
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise LlmAuthenticationError("LLM provider authentication failed") from exc
        if exc.code == 429:
            raise LlmRateLimitError("LLM provider rate limited the request") from exc
        if exc.code >= 500:
            raise LlmProviderError(f"LLM provider returned HTTP {exc.code}") from exc
        raise LlmProviderError(f"LLM provider returned HTTP {exc.code}") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, TimeoutError):
            raise LlmTimeoutError("LLM provider request timed out") from exc
        raise LlmProviderError("LLM provider request failed") from exc
