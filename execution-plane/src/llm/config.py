from __future__ import annotations

import json
import os
from pathlib import Path
from dataclasses import dataclass, field, replace
from typing import Any, Mapping

EXECUTION_PLANE_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_LOCAL_ENV_FILE = EXECUTION_PLANE_ROOT / ".env.local"

DEFAULT_TASK_TIMEOUT_SECONDS = {
    "progressive_context_exploration_plan": 120.0,
    "requirement_analysis": 180.0,
    "system_design": 360.0,
    "code_generation": 240.0,
    "test_generation": 240.0,
}


@dataclass(frozen=True)
class LlmClientConfig:
    default_provider: str = "openai_compatible"
    default_model: str | None = None
    temperature: float = 0.2
    timeout_seconds: float = 240.0
    max_retries: int = 2
    json_repair_attempts: int = 1
    task_timeout_seconds: dict[str, float] = field(
        default_factory=lambda: dict(DEFAULT_TASK_TIMEOUT_SECONDS)
    )
    provider_settings: dict[str, dict[str, Any]] = field(default_factory=dict)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "LlmClientConfig":
        source = os.environ if env is None else env
        return cls(
            default_provider=source.get("DEVFLOW_LLM_PROVIDER", "openai_compatible"),
            default_model=empty_to_none(source.get("DEVFLOW_LLM_MODEL")),
            temperature=parse_float(source.get("DEVFLOW_LLM_TEMPERATURE"), 0.2),
            timeout_seconds=parse_float(
                source.get("DEVFLOW_LLM_TIMEOUT_SECONDS"),
                240.0,
            ),
            max_retries=parse_int(source.get("DEVFLOW_LLM_MAX_RETRIES"), 2),
            json_repair_attempts=parse_int(
                source.get("DEVFLOW_LLM_JSON_REPAIR_ATTEMPTS"),
                1,
            ),
            task_timeout_seconds=task_timeouts_from_env(source),
            provider_settings={
                "openai_compatible": {
                    "api_key": empty_to_none(source.get("DEVFLOW_LLM_OPENAI_API_KEY")),
                    "base_url": source.get(
                        "DEVFLOW_LLM_OPENAI_BASE_URL",
                        "https://api.openai.com/v1",
                    ),
                    "default_model": empty_to_none(
                        source.get("DEVFLOW_LLM_OPENAI_DEFAULT_MODEL")
                    ),
                },
                "anthropic_compatible": {
                    "api_key": empty_to_none(
                        source.get("DEVFLOW_LLM_ANTHROPIC_API_KEY")
                    ),
                    "base_url": source.get(
                        "DEVFLOW_LLM_ANTHROPIC_BASE_URL",
                        "https://api.anthropic.com/v1",
                    ),
                    "default_model": empty_to_none(
                        source.get("DEVFLOW_LLM_ANTHROPIC_DEFAULT_MODEL")
                    ),
                },
            },
        )

    @classmethod
    def from_file(
        cls,
        path: str | Path,
        env: Mapping[str, str] | None = None,
    ) -> "LlmClientConfig":
        """从 JSON 配置文件加载 LLM 客户端配置。

        配置文件用于测试、生产和本地联调共享同一套结构。文件中推荐只写
        `apiKeyEnv`，真实 API Key 仍放在环境变量或密钥系统里；如果本地临时
        使用 `apiKey`，也会被归一化到内部字段，但模板不会鼓励这种方式。
        """

        config_path = Path(path)
        data = json.loads(config_path.read_text(encoding="utf-8"))
        return cls.from_mapping(data, env=env)

    @classmethod
    def from_sources(
        cls,
        config_path: str | Path | None = None,
        env: Mapping[str, str] | None = None,
        env_file: str | Path | None = None,
    ) -> "LlmClientConfig":
        """加载统一入口配置，优先 `.env.local`，再加载配置文件，最后环境覆盖。

        `DEVFLOW_LLM_CONFIG_FILE` 允许容器镜像固定代码，只在部署时挂载不同
        配置文件。环境变量拥有最高优先级，便于生产系统通过 Secret 注入
        API Key、模型名或临时切换 Provider。

        本地开发时默认读取 `execution-plane/.env.local`。这个文件被
        `.gitignore` 忽略，适合保存个人 API Key；CI/生产可以通过
        `DEVFLOW_LLM_ENV_FILE` 指向其它密钥文件，或完全使用平台环境变量。
        """

        process_env = os.environ if env is None else env
        source = merge_env_sources(
            load_env_file(resolve_env_file(process_env, env_file, use_default=env is None)),
            process_env,
        )
        resolved_path = resolve_config_file(
            config_path or empty_to_none(source.get("DEVFLOW_LLM_CONFIG_FILE"))
        )
        if not resolved_path:
            return cls.from_env(source)
        return cls.from_file(resolved_path, env=source).with_env_overrides(source)

    @classmethod
    def from_mapping(
        cls,
        data: Mapping[str, Any],
        env: Mapping[str, str] | None = None,
    ) -> "LlmClientConfig":
        source = os.environ if env is None else env
        providers = data.get("providers") or data.get("provider_settings") or {}
        return cls(
            default_provider=str(
                get_any(data, "defaultProvider", "default_provider")
                or "openai_compatible"
            ),
            default_model=empty_to_none(
                get_any(data, "defaultModel", "default_model")
            ),
            temperature=parse_float(get_any(data, "temperature"), 0.2),
            timeout_seconds=parse_float(
                get_any(data, "timeoutSeconds", "timeout_seconds"),
                240.0,
            ),
            max_retries=parse_int(get_any(data, "maxRetries", "max_retries"), 2),
            json_repair_attempts=parse_int(
                get_any(data, "jsonRepairAttempts", "json_repair_attempts"),
                1,
            ),
            task_timeout_seconds=task_timeouts_from_mapping(
                get_any(data, "taskTimeoutSeconds", "task_timeout_seconds")
            ),
            provider_settings={
                str(provider_name): normalize_provider_settings(settings, source)
                for provider_name, settings in providers.items()
            },
        )

    def with_env_overrides(
        self,
        env: Mapping[str, str] | None = None,
    ) -> "LlmClientConfig":
        source = os.environ if env is None else env
        env_config = LlmClientConfig.from_env(source)
        provider_settings = merge_provider_settings(
            self.provider_settings,
            env_config.provider_settings,
        )
        task_timeout_seconds = merge_task_timeouts(
            self.task_timeout_seconds,
            task_timeouts_from_env(source, include_defaults=False),
        )
        return replace(
            self,
            default_provider=source.get("DEVFLOW_LLM_PROVIDER", self.default_provider),
            default_model=empty_to_none(source.get("DEVFLOW_LLM_MODEL"))
            or self.default_model,
            temperature=parse_float(
                source.get("DEVFLOW_LLM_TEMPERATURE"),
                self.temperature,
            ),
            timeout_seconds=parse_float(
                source.get("DEVFLOW_LLM_TIMEOUT_SECONDS"),
                self.timeout_seconds,
            ),
            max_retries=parse_int(
                source.get("DEVFLOW_LLM_MAX_RETRIES"),
                self.max_retries,
            ),
            json_repair_attempts=parse_int(
                source.get("DEVFLOW_LLM_JSON_REPAIR_ATTEMPTS"),
                self.json_repair_attempts,
            ),
            task_timeout_seconds=task_timeout_seconds,
            provider_settings=provider_settings,
        )

    def for_request(self, provider_name: str | None, model: str | None) -> "LlmClientConfig":
        """合并单次请求的 Provider/模型 override。

        Worker 进程通常只启动一次，但不同流水线或不同 Agent 可能需要不同
        Provider。这里不修改全局配置，而是为单次请求派生一个不可变配置，
        避免并发 Activity 之间互相污染。
        """

        return replace(
            self,
            default_provider=provider_name or self.default_provider,
            default_model=model or self.default_model,
        )

    def settings_for(self, provider_name: str) -> dict[str, Any]:
        return dict(self.provider_settings.get(provider_name, {}))

    def timeout_for_task(
        self,
        task: str | None,
        request_timeout_seconds: float | None = None,
    ) -> float:
        task_timeout = self.task_timeout_seconds.get(normalize_task_name(task))
        configured = task_timeout if task_timeout is not None else request_timeout_seconds
        if configured is None:
            configured = self.timeout_seconds
        return max(float(configured), float(self.timeout_seconds))


def empty_to_none(value: Any) -> str | None:
    if value is None:
        return None
    stripped = str(value).strip()
    return stripped or None


def parse_float(value: Any, default: float) -> float:
    try:
        return float(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def parse_int(value: Any, default: int) -> int:
    try:
        return int(value) if value not in (None, "") else default
    except (TypeError, ValueError):
        return default


def get_any(data: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in data:
            return data[key]
    return None


def normalize_provider_settings(
    settings: Mapping[str, Any],
    env: Mapping[str, str],
) -> dict[str, Any]:
    api_key_env = get_any(settings, "apiKeyEnv", "api_key_env")
    api_key = get_any(settings, "apiKey", "api_key")
    if api_key_env:
        api_key = empty_to_none(env.get(str(api_key_env))) or api_key
    return {
        "api_key": empty_to_none(api_key),
        "base_url": get_any(settings, "baseUrl", "base_url"),
        "default_model": empty_to_none(
            get_any(settings, "defaultModel", "default_model")
        ),
    }


def merge_provider_settings(
    base: Mapping[str, Mapping[str, Any]],
    overrides: Mapping[str, Mapping[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = {
        provider_name: dict(settings)
        for provider_name, settings in base.items()
    }
    for provider_name, settings in overrides.items():
        current = merged.setdefault(provider_name, {})
        for key, value in settings.items():
            if value not in (None, ""):
                current[key] = value
    return merged


def normalize_task_name(task: str | None) -> str:
    return str(task or "").strip().lower()


def parse_task_timeout_map(value: Any) -> dict[str, float]:
    if not isinstance(value, Mapping):
        return dict(DEFAULT_TASK_TIMEOUT_SECONDS)
    result = dict(DEFAULT_TASK_TIMEOUT_SECONDS)
    for key, raw_value in value.items():
        timeout = parse_float(raw_value, result.get(normalize_task_name(key), 0.0))
        if timeout > 0:
            result[normalize_task_name(key)] = timeout
    return result


def task_timeouts_from_env(
    env: Mapping[str, str],
    include_defaults: bool = True,
) -> dict[str, float]:
    result = dict(DEFAULT_TASK_TIMEOUT_SECONDS) if include_defaults else {}
    for task_name, default_timeout in DEFAULT_TASK_TIMEOUT_SECONDS.items():
        env_key = f"DEVFLOW_LLM_TIMEOUT_{task_name.upper()}"
        if env_key not in env and not include_defaults:
            continue
        timeout = parse_float(env.get(env_key), default_timeout)
        if timeout > 0:
            result[task_name] = timeout
    return result


def task_timeouts_from_mapping(value: Any) -> dict[str, float]:
    return parse_task_timeout_map(value)


def merge_task_timeouts(
    base: Mapping[str, float],
    overrides: Mapping[str, float],
) -> dict[str, float]:
    merged = dict(base)
    for task_name, timeout in overrides.items():
        if timeout > 0:
            merged[normalize_task_name(task_name)] = float(timeout)
    return merged


def resolve_env_file(
    env: Mapping[str, str],
    env_file: str | Path | None = None,
    use_default: bool = True,
) -> Path | None:
    explicit = env_file or empty_to_none(env.get("DEVFLOW_LLM_ENV_FILE"))
    if explicit:
        return Path(explicit)
    if not use_default:
        return None
    return DEFAULT_LOCAL_ENV_FILE


def resolve_config_file(path: str | Path | None) -> Path | None:
    """把配置文件相对路径固定解析到 `execution-plane/`。

    PyCharm、命令行、CI 的 working directory 经常不同。如果直接把
    `config/llm.local.json` 交给 `Path.read_text()`，它会相对于当前进程
    工作目录解析，导致 IDE 从仓库根目录启动时找不到文件。这里统一约定：
    相对配置路径都相对于执行平面根目录，绝对路径则原样使用。
    """

    if path is None:
        return None
    config_path = Path(path)
    if config_path.is_absolute():
        return config_path
    return EXECUTION_PLANE_ROOT / config_path


def load_env_file(path: Path | None) -> dict[str, str]:
    """读取 dotenv 风格文件，避免为本地密钥管理新增第三方依赖。

    支持常见的 `KEY=value`、`export KEY=value`、单双引号和 `#` 注释。
    解析结果只进入当前配置构造过程，不会写回 `os.environ`，因此不会污染
    其它测试或长生命周期进程。
    """

    if path is None or not path.exists():
        return {}
    loaded: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        parsed = parse_env_line(raw_line)
        if parsed is not None:
            key, value = parsed
            loaded[key] = value
    return loaded


def parse_env_line(line: str) -> tuple[str, str] | None:
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None
    if stripped.startswith("export "):
        stripped = stripped[len("export ") :].strip()
    if "=" not in stripped:
        return None
    key, value = stripped.split("=", 1)
    key = key.strip()
    if not key:
        return None
    value = strip_inline_comment(value.strip())
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    return key, value


def strip_inline_comment(value: str) -> str:
    in_single_quote = False
    in_double_quote = False
    for index, char in enumerate(value):
        if char == "'" and not in_double_quote:
            in_single_quote = not in_single_quote
        elif char == '"' and not in_single_quote:
            in_double_quote = not in_double_quote
        elif char == "#" and not in_single_quote and not in_double_quote:
            if index == 0 or value[index - 1].isspace():
                return value[:index].strip()
    return value


def merge_env_sources(
    file_env: Mapping[str, str],
    process_env: Mapping[str, str],
) -> dict[str, str]:
    merged = dict(file_env)
    merged.update({key: value for key, value in process_env.items() if value is not None})
    return merged
