# T021 可配置 LLM 调用客户端设计文档

本文档定义 T021 的实现方案。目标是在 `execution-plane/src/llm/` 中实现统一的 LLM 调用客户端，供 T022-T027 的各类 Agent 复用。

T021 是真实 Agent 前的基础能力。Requirement Agent、Design Agent、Coder Agent 等不应各自直接拼接 HTTP 请求或读取 Provider 环境变量，而应统一依赖本客户端。

## 设计目标

LLM 客户端需要满足以下目标:

- 支持至少两个不同模型提供商，首版建议支持 `openai_compatible` 与 `anthropic_compatible`。
- Provider、模型、超时、重试、温度等参数可配置。
- 支持运行时切换 Provider，而不是只能在进程启动时固定。
- 支持结构化 JSON 输出，方便 Agent 结果进入 Temporal payload 和 PostgreSQL JSONB。
- 提供 Fake Provider，保证单元测试不依赖真实网络和真实 API Key。
- 统一错误类型、重试策略和调用元数据，便于写入 `error_logs` 和阶段产物。

## 实现状态

T021 已完成首版实现:

- `execution-plane/src/llm/client.py` 实现 `LlmClient.complete` 与 `complete_json`。
- `execution-plane/src/llm/config.py` 实现环境变量配置和单次请求 override。
- `execution-plane/src/llm/messages.py` 定义跨 Provider 的请求/响应数据结构。
- `execution-plane/src/llm/providers.py` 实现 OpenAI-compatible、Anthropic-compatible 和 Fake Provider。
- `execution-plane/src/llm/errors.py` 实现统一异常类型和错误文本脱敏。
- `execution-plane/tests/test_llm_client.py` 覆盖 Provider 切换、结构化 JSON、错误处理、重试和真实 Provider 请求形状。
- `execution-plane/config/llm.test.example.json` 和 `execution-plane/config/llm.production.example.json` 提供测试/生产配置模板。
- `execution-plane/.env.local.example` 提供本地真实 API Key 配置模板，真实 `.env.local` 不进入版本库。
- `execution-plane/scripts/llm_smoke_test.py` 提供手动真实网络冒烟测试入口。

首版没有新增第三方 HTTP 依赖，真实 Provider 适配器使用 Python 标准库 `urllib`；测试通过注入 fake transport 验证请求形状，不访问真实网络。

## 不支持的方案

不再支持 `RuleBasedRequirementAnalyzer` 或其他规则模板生成器作为 Agent 生产路径。

原因:

- 项目目标是 AI 驱动的软件研发流水线，核心 Agent 产物应来自 LLM 推理。
- 规则模板会让测试结果稳定，但也会掩盖真实模型调用、结构化输出和错误处理问题。
- 稳定测试应由 Fake Provider 完成，而不是由业务 Agent 内部维护一套与真实 LLM 行为不同的规则生成路径。

允许存在的非真实模型能力只有 Fake Provider。Fake Provider 的职责是测试 LLM 客户端和 Agent 编排，不作为生产生成器。

## 模块结构

建议新增:

```text
execution-plane/src/llm/
  __init__.py
  client.py
  config.py
  messages.py
  providers.py
  errors.py

execution-plane/tests/
  test_llm_client.py
```

模块职责:

| 文件 | 职责 |
|------|------|
| `client.py` | 暴露 `LlmClient`，负责编排配置解析、Provider 选择、重试、JSON 解析 |
| `config.py` | 定义 `LlmClientConfig`，从环境变量和运行时 override 合并配置 |
| `messages.py` | 定义 `LlmMessage`、`LlmRequest`、`LlmResponse` 等跨 Provider 数据结构 |
| `providers.py` | 定义 `LlmProvider` 协议，以及 OpenAI-compatible、Anthropic-compatible、Fake Provider |
| `errors.py` | 定义统一异常，例如配置错误、认证错误、限流、超时、JSON 解析失败 |

## 核心接口

### `LlmMessage`

```python
@dataclass(frozen=True)
class LlmMessage:
    role: Literal["system", "user", "assistant"]
    content: str
```

### `LlmRequest`

```python
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
```

字段说明:

| 字段 | 含义 |
|------|------|
| `task` | 调用目的，例如 `requirement_analysis`、`system_design` |
| `messages` | 标准消息列表，由 Agent 负责构造 |
| `response_format` | `json` 时客户端必须解析并返回 `parsed_json` |
| `json_schema` | Agent 期望的结构化输出约束 |
| `provider` | 运行时 Provider override；为空时使用默认配置 |
| `model` | 运行时模型 override；为空时使用 Provider 默认模型 |
| `temperature` | 运行时采样参数 override |
| `timeout_seconds` | 单次请求超时 |
| `metadata` | 调用追踪信息，例如 pipelineId、stageName |

### `LlmResponse`

```python
@dataclass(frozen=True)
class LlmResponse:
    provider: str
    model: str
    text: str
    parsed_json: dict[str, Any] | None
    usage: dict[str, Any]
    latency_ms: int
    request_id: str | None = None
```

Agent 只消费 `parsed_json` 或 `text`，不直接依赖 Provider 原始响应。

### `LlmProvider`

```python
class LlmProvider(Protocol):
    name: str

    def complete(self, request: LlmRequest, config: LlmClientConfig) -> LlmResponse:
        ...
```

Provider 只负责适配不同厂商 API。重试、Provider 选择、JSON 校验由 `LlmClient` 统一处理。

## Provider 设计

### `OpenAICompatibleProvider`

用途:

- 适配 OpenAI-compatible 的 Chat Completions 或 Responses 风格接口。
- 支持通过 `base_url` 切换到兼容服务。

配置项:

```text
DEVFLOW_LLM_OPENAI_API_KEY
DEVFLOW_LLM_OPENAI_BASE_URL
DEVFLOW_LLM_OPENAI_DEFAULT_MODEL
```

### `AnthropicCompatibleProvider`

用途:

- 适配 Anthropic-compatible 的 Messages 风格接口。
- 与 OpenAI-compatible Provider 保持统一的 `LlmRequest/LlmResponse`。

配置项:

```text
DEVFLOW_LLM_ANTHROPIC_API_KEY
DEVFLOW_LLM_ANTHROPIC_BASE_URL
DEVFLOW_LLM_ANTHROPIC_DEFAULT_MODEL
```

### `FakeProvider`

用途:

- 单元测试。
- 本地离线验证 Agent 图状态运转。
- 在无 API Key 的环境中验证 JSON 解析、错误路由和 T020 落库展示链路。

约束:

- Fake Provider 只能通过显式配置启用，例如 `DEVFLOW_LLM_PROVIDER=fake` 或测试注入。
- Fake Provider 的固定响应应写在测试中，不应作为生产 fallback。

## 配置模型

### 默认环境变量

```text
DEVFLOW_LLM_PROVIDER=openai_compatible
DEVFLOW_LLM_MODEL=
DEVFLOW_LLM_TEMPERATURE=0.2
DEVFLOW_LLM_TIMEOUT_SECONDS=60
DEVFLOW_LLM_MAX_RETRIES=2
DEVFLOW_LLM_JSON_REPAIR_ATTEMPTS=1
```

### 配置文件入口

除纯环境变量外，执行平面还支持 `.env.local` 和 JSON 配置文件:

```text
execution-plane/.env.local
```

`.env.local` 用于本地密钥和测试开关，示例:

```text
DEVFLOW_LLM_PROVIDER=openai_compatible
DEVFLOW_LLM_CONFIG_FILE=./config/llm.local.json
DEVFLOW_LLM_INTEGRATION_TEST=1
DEVFLOW_LLM_OPENAI_API_KEY=<your-key>
DEVFLOW_LLM_OPENAI_DEFAULT_MODEL=gpt-4o-mini
```

```python
client = LlmClient.from_sources(config_path="execution-plane/config/llm.production.json")
```

或通过环境变量指定:

```text
DEVFLOW_LLM_CONFIG_FILE=/etc/devflow/llm.production.json
```

配置文件示例:

```json
{
  "defaultProvider": "openai_compatible",
  "temperature": 0.2,
  "timeoutSeconds": 60,
  "maxRetries": 2,
  "jsonRepairAttempts": 1,
  "providers": {
    "openai_compatible": {
      "apiKeyEnv": "DEVFLOW_LLM_OPENAI_API_KEY",
      "baseUrl": "https://api.openai.com/v1",
      "defaultModel": "gpt-4o"
    },
    "anthropic_compatible": {
      "apiKeyEnv": "DEVFLOW_LLM_ANTHROPIC_API_KEY",
      "baseUrl": "https://api.anthropic.com/v1",
      "defaultModel": "claude-3-5-sonnet-latest"
    }
  }
}
```

配置优先级:

1. 内置默认值。
2. `execution-plane/.env.local` 或 `DEVFLOW_LLM_ENV_FILE` 指定的 dotenv 文件。
3. `DEVFLOW_LLM_CONFIG_FILE` 或 `LlmClient.from_sources(config_path=...)` 指定的 JSON 配置文件。
4. 进程环境变量覆盖 `.env.local` 和 JSON 配置文件。
5. 单次 `LlmRequest.provider` / `LlmRequest.model` 覆盖运行时默认值。

生产环境推荐把配置文件挂载为只读文件，把 API Key 通过 Secret 注入到环境变量或 `DEVFLOW_LLM_ENV_FILE` 指向的密钥文件。模板文件只提交 `apiKeyEnv`，不提交真实密钥。

### 运行时切换

运行时切换应支持两层:

1. 全局默认:
   - 由环境变量决定。
   - Worker 启动后默认使用该 Provider。

2. 单次请求 override:
   - `LlmRequest.provider`
   - `LlmRequest.model`
   - 未来可从 `DevFlowState.llm_config` 或 `globalContext.llm` 透传。

示例:

```python
client.complete_json(LlmRequest(
    task="requirement_analysis",
    provider="anthropic_compatible",
    model="provider-specific-model",
    messages=(...),
    json_schema=REQUIREMENT_PRD_SCHEMA,
))
```

这意味着同一个 Python Worker 内，不同流水线或不同阶段可以选择不同 Provider。

## 调用流程

```text
Agent
  -> 构造 LlmRequest
  -> LlmClient.complete_json
  -> 合并默认配置和 request override
  -> ProviderRegistry 选择 Provider
  -> Provider.complete 发送请求
  -> LlmClient 解析 JSON
  -> 如 JSON 无效，按配置执行有限修复或抛出 LlmJsonParseError
  -> 返回 LlmResponse
  -> Agent 执行业务校验并写入 DevFlowState
```

## JSON 输出策略

所有 Agent 产物默认要求 JSON 输出。T021 客户端需要提供:

```python
def complete_json(self, request: LlmRequest) -> dict[str, Any]:
    ...
```

行为:

1. 强制 `response_format = "json"`。
2. 将 JSON Schema 或字段说明放入 Provider 请求。
3. 解析模型返回文本。
4. 如果模型返回 markdown fenced code block，提取其中 JSON。
5. 如果解析失败，根据 `DEVFLOW_LLM_JSON_REPAIR_ATTEMPTS` 做有限修复。
6. 仍失败则抛出 `LlmJsonParseError`。

注意: JSON 修复只能修复格式问题，不能补业务字段。业务字段完整性由具体 Agent 的 validator 负责。

## 错误模型

建议异常:

| 异常 | 含义 | 处理策略 |
|------|------|----------|
| `LlmConfigurationError` | Provider、模型或 API Key 缺失 | 不可恢复，写入 `error_logs` 或让 Activity 失败 |
| `LlmAuthenticationError` | 鉴权失败 | 不可恢复，需要用户修正配置 |
| `LlmRateLimitError` | 限流 | 可重试 |
| `LlmTimeoutError` | 超时 | 可重试 |
| `LlmProviderError` | Provider 返回 5xx 或未知错误 | 可重试或写入诊断 |
| `LlmJsonParseError` | 输出不是合法 JSON | 可触发有限修复，失败后交给 Agent 处理 |

## 重试策略

客户端内部只处理短周期、明确可恢复的错误:

- timeout
- rate limit
- provider 5xx
- 临时连接错误

不重试:

- API Key 缺失
- 鉴权失败
- Provider 名称不存在
- JSON Schema 设计错误

重试参数:

```text
max_retries = DEVFLOW_LLM_MAX_RETRIES
backoff = exponential + jitter
```

Temporal Activity 仍负责更外层的阶段级重试。客户端内部重试次数不能过多，避免单个 Activity 长时间阻塞。

## 安全与配置边界

- API Key 只从环境变量或安全配置读取，不写入 `DevFlowState`、`Stage.output_payload`、日志或测试快照。
- `LlmResponse` 中的 `usage` 可以保存 token 数量，但不能保存 Provider 原始请求头。
- 错误消息需要脱敏，不能包含 API Key、Authorization header。
- Provider base URL 可以配置，但应在文档中提醒生产环境只配置可信地址。

## 与 Agent 的关系

T022-T027 的 Agent 只依赖 `LlmClient`，不直接依赖具体 Provider:

```python
class RequirementAgent:
    def __init__(self, llm_client: LlmClient | None = None):
        self.llm_client = llm_client or LlmClient.from_env()
```

Agent 负责:

- 构造 prompt。
- 提供 JSON Schema。
- 对返回 JSON 做业务校验。
- 将结果写入 `DevFlowState`。

LLM Client 负责:

- Provider 选择。
- HTTP 调用。
- 重试。
- JSON 解析。
- 错误归一化。

## 测试设计

建议新增 `execution-plane/tests/test_llm_client.py`。

测试用例:

| 用例 | 验证点 |
|------|--------|
| 默认配置选择 Provider | `DEVFLOW_LLM_PROVIDER` 能选择 Provider |
| 请求级 Provider override | 单次 `LlmRequest.provider` 能覆盖默认 Provider |
| Fake Provider 返回 JSON | `complete_json` 返回 dict |
| Markdown JSON 提取 | 能从 fenced code block 中解析 JSON |
| JSON 解析失败 | 抛出 `LlmJsonParseError` |
| 缺少 Provider 配置 | 抛出 `LlmConfigurationError` |
| Provider 运行时切换 | 同一 client 可连续调用不同 Provider |
| 敏感信息脱敏 | 异常文本不包含 API Key |

测试原则:

- 不访问真实网络。
- 不要求真实 OpenAI/Anthropic API Key。
- OpenAI-compatible 和 Anthropic-compatible Provider 的 HTTP 适配可用 fake transport 或 monkeypatch 验证请求形状。

真实网络测试:

- `LlmClientRealNetworkTest` 默认跳过。
- 设置 `DEVFLOW_LLM_INTEGRATION_TEST=1` 后才会访问真实 Provider。
- OpenAI-compatible 需要 `DEVFLOW_LLM_OPENAI_API_KEY` 和 `DEVFLOW_LLM_OPENAI_DEFAULT_MODEL`。
- Anthropic-compatible 需要 `DEVFLOW_LLM_ANTHROPIC_API_KEY` 和 `DEVFLOW_LLM_ANTHROPIC_DEFAULT_MODEL`。
- 本地也可以运行 `python scripts/llm_smoke_test.py --config config/llm.local.json`。

## 文档与注释要求

实现 T021 时需要在关键代码处说明“为什么这样分层”:

- 为什么 Agent 不能直接调用 Provider SDK。
- 为什么 Provider 可运行时切换。
- 为什么 Fake Provider 是测试设施，不是生产 fallback。
- 为什么 JSON 格式修复和业务字段校验分属 LLM Client 与 Agent。
- 为什么客户端内部重试次数必须有限，避免和 Temporal Activity 重试叠加导致长时间阻塞。
