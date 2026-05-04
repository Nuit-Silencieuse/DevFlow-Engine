# LLM 配置模板

本目录只提交无密钥模板。真实配置文件不要提交到版本库，建议使用:

- `../.env.local`: 本地密钥和真实网络测试开关，已被 `.gitignore` 忽略。
- `llm.local.json`: 本地联调配置，已被 `.gitignore` 忽略。
- `llm.production.json`: 生产挂载配置，已被 `.gitignore` 忽略。
- `*.secret.json`: 临时密钥配置，已被 `.gitignore` 忽略。

## 配置入口

执行平面支持三种配置入口:

1. `.env.local` 本地密钥文件:

```powershell
Copy-Item ..\.env.local.example ..\.env.local
```

然后编辑 `execution-plane/.env.local`，填入 `DEVFLOW_LLM_OPENAI_API_KEY` 或 `DEVFLOW_LLM_ANTHROPIC_API_KEY`。`LlmClient.from_sources()` 会默认读取该文件。

2. 纯环境变量:

```powershell
$env:DEVFLOW_LLM_PROVIDER="openai_compatible"
$env:DEVFLOW_LLM_OPENAI_API_KEY="<your-key>"
$env:DEVFLOW_LLM_OPENAI_DEFAULT_MODEL="gpt-4o-mini"
```

3. 配置文件 + `.env.local` 或环境变量密钥:

```powershell
$env:DEVFLOW_LLM_CONFIG_FILE="D:\ZPY\Agent学习\DevFlow-Engine\execution-plane\config\llm.local.json"
$env:DEVFLOW_LLM_OPENAI_API_KEY="<your-key>"
```

配置文件中推荐使用 `apiKeyEnv` 指向环境变量，不直接写真实 API Key。生产环境应由 Docker Secret、Kubernetes Secret 或 CI/CD 密钥注入环境变量。

配置优先级:

1. `execution-plane/.env.local`
2. `DEVFLOW_LLM_CONFIG_FILE` 指定的 JSON 配置文件
3. 进程环境变量覆盖 `.env.local` 和 JSON 文件
4. 单次 `LlmRequest.provider` / `LlmRequest.model` 覆盖默认 Provider 和模型

## 真实网络冒烟测试

默认单元测试不会访问真实网络。要显式执行真实 Provider 测试:

```powershell
Copy-Item ..\.env.local.example ..\.env.local
# 编辑 ..\.env.local，填入 API Key，并确认 DEVFLOW_LLM_INTEGRATION_TEST=1
.\venv\python.exe -m unittest tests.test_llm_client.LlmClientRealNetworkTest
```

Anthropic-compatible Provider:

```powershell
# 在 ..\.env.local 中设置:
# DEVFLOW_LLM_PROVIDER=anthropic_compatible
# DEVFLOW_LLM_ANTHROPIC_API_KEY=<your-key>
.\venv\python.exe -m unittest tests.test_llm_client.LlmClientRealNetworkTest
```

测试只校验 Provider 能返回合法 JSON，不会打印 API Key。

## 中间产物与 LLM 消息追踪

需要观察真实 LLM 到底发生了什么时，可以在 `.env.local` 中打开追踪:

```env
DEVFLOW_LLM_TRACE=1
DEVFLOW_LLM_TRACE_STDOUT=1
DEVFLOW_LLM_TRACE_FILE=./logs/llm-trace.jsonl
DEVFLOW_LLM_TRACE_MAX_CHARS=50000
```

追踪内容包括:

- `llm.request`: Provider、模型、任务名、JSON schema、完整 LLM messages。
- `llm.response`: 模型原始文本、解析后的 JSON、usage、latency。
- `requirement_agent.context_pack`: 代码库上下文包和实际检查文件。
- `requirement_agent.analysis_plan`: 需求分析计划。
- `requirement_agent.draft_prd`: LLM 初稿 PRD。
- `requirement_agent.validation_report`: PRD 结构校验结果。

日志文件是 JSONL 格式，每行一个事件。相对路径会按 `execution-plane/` 解析，例如 `./logs/llm-trace.jsonl` 会写入 `execution-plane/logs/llm-trace.jsonl`。API Key、Token、Authorization 等敏感字段会在写出前脱敏。

## Requirement Agent 效果测试

`tests/test_requirement_agent.py` 中提供真实效果测试，使用当前项目代码库作为上下文材料，并强制写出中间产物:

```powershell
.\venv\python.exe -m unittest tests.test_requirement_agent.RequirementAgentEffectTest
```

输出文件:

- `logs/requirement-agent-effect.jsonl`: 完整 trace，包含 LLM messages、模型响应、context_pack、analysis_plan、draft_prd、validation_report。
- `logs/requirement-agent-effect-result.json`: 最终 `DevFlowState` 增量，便于直接查看 `structured_prd` 和 `code_context`。

终端默认打印摘要和 LLM 消息预览。如果确实需要把完整 JSONL 事件也打印到终端:

```env
DEVFLOW_REQUIREMENT_AGENT_EFFECT_FULL_STDOUT=1
```
