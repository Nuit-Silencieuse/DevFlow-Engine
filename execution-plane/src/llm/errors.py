from __future__ import annotations

from typing import Iterable


class LlmError(Exception):
    """LLM 客户端的统一异常基类，便于 Agent 捕获后写入 error_logs。"""


class LlmConfigurationError(LlmError):
    """Provider 名称、模型、API Key 或基础配置缺失。"""


class LlmAuthenticationError(LlmError):
    """Provider 鉴权失败，这类错误不应由客户端内部重试。"""


class LlmRateLimitError(LlmError):
    """Provider 限流，属于短周期可恢复错误。"""


class LlmTimeoutError(LlmError):
    """Provider 请求超时，属于短周期可恢复错误。"""


class LlmProviderError(LlmError):
    """Provider 返回 5xx、网络抖动或无法识别的临时错误。"""


class LlmJsonParseError(LlmError):
    """模型响应不是合法 JSON，或 JSON 根节点不符合客户端契约。"""


def sanitize_message(message: str, secrets: Iterable[str] = ()) -> str:
    """从错误文本中移除密钥片段。

    Provider 配置错误经常发生在启动或首次调用阶段，如果异常直接携带
    API Key、Authorization Header，后续写入 `error_logs` 或测试输出时就会
    泄露敏感信息。这里做集中脱敏，让配置层和 Provider 层都复用同一规则。
    """

    sanitized = message
    for secret in secrets:
        if secret:
            sanitized = sanitized.replace(secret, "[REDACTED]")
    return sanitized
