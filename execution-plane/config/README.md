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
