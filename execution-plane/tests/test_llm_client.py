import json
import os
import unittest
from pathlib import Path

from src.llm import (
    AnthropicCompatibleProvider,
    FakeProvider,
    LlmClient,
    LlmClientConfig,
    LlmConfigurationError,
    LlmJsonParseError,
    LlmMessage,
    LlmProviderError,
    LlmRequest,
    OpenAICompatibleProvider,
)
from src.llm.config import (
    EXECUTION_PLANE_ROOT,
    load_env_file,
    merge_env_sources,
    resolve_config_file,
    resolve_env_file,
)


class LlmClientTest(unittest.TestCase):
    def test_fake_provider_returns_structured_json(self):
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={
                "fake": FakeProvider(
                    response_text='{"summary": "登录能力", "acceptance_criteria": ["可以登录"]}'
                )
            },
        )

        result = client.complete_json(
            LlmRequest(
                task="requirement_analysis",
                messages=(LlmMessage(role="user", content="实现登录"),),
            )
        )

        self.assertEqual(result["summary"], "登录能力")
        self.assertEqual(result["acceptance_criteria"], ["可以登录"])

    def test_request_provider_override_switches_at_runtime(self):
        client = LlmClient(
            config=LlmClientConfig(default_provider="first", max_retries=0),
            providers={
                "first": FakeProvider(name="first", response_text='{"provider": "first"}'),
                "second": FakeProvider(name="second", response_text='{"provider": "second"}'),
            },
        )

        first = client.complete_json(
            LlmRequest(task="first_call", messages=(LlmMessage("user", "a"),))
        )
        second = client.complete_json(
            LlmRequest(
                task="second_call",
                provider="second",
                messages=(LlmMessage("user", "b"),),
            )
        )

        self.assertEqual(first["provider"], "first")
        self.assertEqual(second["provider"], "second")

    def test_markdown_json_block_is_extracted(self):
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=0),
            providers={
                "fake": FakeProvider(
                    response_text='```json\n{"status": "READY", "next_actions": []}\n```'
                )
            },
        )

        result = client.complete_json(
            LlmRequest(task="delivery", messages=(LlmMessage("user", "生成交付状态"),))
        )

        self.assertEqual(result["status"], "READY")

    def test_invalid_json_raises_parse_error(self):
        client = LlmClient(
            config=LlmClientConfig(
                default_provider="fake",
                max_retries=0,
                json_repair_attempts=0,
            ),
            providers={"fake": FakeProvider(response_text="not-json")},
        )

        with self.assertRaises(LlmJsonParseError):
            client.complete_json(
                LlmRequest(task="bad_json", messages=(LlmMessage("user", "x"),))
            )

    def test_missing_provider_configuration_raises_error(self):
        client = LlmClient(
            config=LlmClientConfig(default_provider="missing", max_retries=0),
            providers={"fake": FakeProvider(response_text="{}")},
        )

        with self.assertRaises(LlmConfigurationError):
            client.complete_json(
                LlmRequest(task="missing", messages=(LlmMessage("user", "x"),))
            )

    def test_retryable_provider_error_is_retried(self):
        provider = FakeProvider(
            response_text='{"ok": true}',
            failures=[LlmProviderError("temporary failure")],
        )
        client = LlmClient(
            config=LlmClientConfig(default_provider="fake", max_retries=1),
            providers={"fake": provider},
            sleep=lambda _: None,
        )

        result = client.complete_json(
            LlmRequest(task="retry", messages=(LlmMessage("user", "x"),))
        )

        self.assertEqual(result, {"ok": True})
        self.assertEqual(provider.calls, 2)

    def test_sensitive_api_key_is_not_exposed_in_configuration_error(self):
        config = LlmClientConfig(
            default_provider="openai_compatible",
            provider_settings={
                "openai_compatible": {
                    "api_key": "sk-secret-value",
                    "default_model": "",
                }
            },
        )
        client = LlmClient(
            config=config,
            providers={"openai_compatible": OpenAICompatibleProvider()},
        )

        with self.assertRaises(LlmConfigurationError) as raised:
            client.complete_json(
                LlmRequest(task="missing_model", messages=(LlmMessage("user", "x"),))
            )

        self.assertNotIn("sk-secret-value", str(raised.exception))

    def test_default_registry_contains_two_real_provider_adapters(self):
        client = LlmClient(config=LlmClientConfig(default_provider="fake"))

        self.assertIn("openai_compatible", client.providers)
        self.assertIn("anthropic_compatible", client.providers)

    def test_config_file_loads_provider_settings_from_key_env(self):
        config_path = write_test_config(
            "llm-file-load.json",
            {
                "defaultProvider": "openai_compatible",
                "defaultModel": "global-model",
                "temperature": 0.1,
                "timeoutSeconds": 15,
                "maxRetries": 0,
                "jsonRepairAttempts": 0,
                "providers": {
                    "openai_compatible": {
                        "apiKeyEnv": "TEST_OPENAI_KEY",
                        "baseUrl": "https://llm.example/v1",
                        "defaultModel": "file-model",
                    }
                },
            },
        )
        config = LlmClientConfig.from_file(
            config_path,
            env={"TEST_OPENAI_KEY": "sk-from-env"},
        )

        self.assertEqual(config.default_provider, "openai_compatible")
        self.assertEqual(config.timeout_seconds, 15)
        self.assertEqual(
            config.provider_settings["openai_compatible"]["api_key"],
            "sk-from-env",
        )

    def test_sources_allow_environment_to_override_file_defaults(self):
        config_path = write_test_config(
            "llm-source-override.json",
            {
                "defaultProvider": "openai_compatible",
                "defaultModel": "file-global",
                "providers": {
                    "openai_compatible": {
                        "apiKeyEnv": "FILE_OPENAI_KEY",
                        "defaultModel": "file-provider-model",
                    }
                },
            },
        )
        config = LlmClientConfig.from_sources(
            config_path,
            env={
                "FILE_OPENAI_KEY": "sk-file",
                "DEVFLOW_LLM_PROVIDER": "anthropic_compatible",
                "DEVFLOW_LLM_MODEL": "env-global",
                "DEVFLOW_LLM_ANTHROPIC_API_KEY": "sk-anthropic",
                "DEVFLOW_LLM_ANTHROPIC_DEFAULT_MODEL": "env-anthropic",
            },
        )

        self.assertEqual(config.default_provider, "anthropic_compatible")
        self.assertEqual(config.default_model, "env-global")
        self.assertEqual(
            config.provider_settings["openai_compatible"]["api_key"],
            "sk-file",
        )
        self.assertEqual(
            config.provider_settings["anthropic_compatible"]["default_model"],
            "env-anthropic",
        )

    def test_relative_config_file_is_resolved_from_execution_plane_root(self):
        config_path = EXECUTION_PLANE_ROOT / ".test_tmp" / "relative-config.json"
        config_path.parent.mkdir(exist_ok=True)
        config_path.write_text(
            json.dumps(
                {
                    "defaultProvider": "openai_compatible",
                    "providers": {
                        "openai_compatible": {
                            "apiKeyEnv": "RELATIVE_OPENAI_KEY",
                            "defaultModel": "relative-model",
                        }
                    },
                }
            ),
            encoding="utf-8",
        )

        resolved = resolve_config_file(".test_tmp/relative-config.json")
        config = LlmClientConfig.from_sources(
            config_path=".test_tmp/relative-config.json",
            env={"RELATIVE_OPENAI_KEY": "sk-relative"},
        )

        self.assertEqual(resolved, config_path)
        self.assertEqual(
            config.provider_settings["openai_compatible"]["api_key"],
            "sk-relative",
        )
        self.assertEqual(
            config.provider_settings["openai_compatible"]["default_model"],
            "relative-model",
        )

    def test_sources_load_api_key_from_env_local_file(self):
        env_file = write_env_file(
            "llm.env.local",
            """
            # 本地真实密钥文件格式
            DEVFLOW_LLM_PROVIDER=openai_compatible
            DEVFLOW_LLM_OPENAI_API_KEY="sk-from-dotenv"
            DEVFLOW_LLM_OPENAI_DEFAULT_MODEL=gpt-4o-mini
            """,
        )

        config = LlmClientConfig.from_sources(
            env={"DEVFLOW_LLM_ENV_FILE": str(env_file)},
        )

        self.assertEqual(config.default_provider, "openai_compatible")
        self.assertEqual(
            config.provider_settings["openai_compatible"]["api_key"],
            "sk-from-dotenv",
        )
        self.assertEqual(
            config.provider_settings["openai_compatible"]["default_model"],
            "gpt-4o-mini",
        )

    def test_process_environment_overrides_env_local_file(self):
        env_file = write_env_file(
            "llm-env-override.env",
            """
            DEVFLOW_LLM_PROVIDER=openai_compatible
            DEVFLOW_LLM_OPENAI_API_KEY=sk-from-file
            DEVFLOW_LLM_OPENAI_DEFAULT_MODEL=file-model
            """,
        )

        config = LlmClientConfig.from_sources(
            env={
                "DEVFLOW_LLM_ENV_FILE": str(env_file),
                "DEVFLOW_LLM_OPENAI_DEFAULT_MODEL": "env-model",
            },
        )

        self.assertEqual(
            config.provider_settings["openai_compatible"]["api_key"],
            "sk-from-file",
        )
        self.assertEqual(
            config.provider_settings["openai_compatible"]["default_model"],
            "env-model",
        )

    def test_explicit_test_environment_does_not_load_default_env_local(self):
        config = LlmClientConfig.from_sources(
            env={
                "DEVFLOW_LLM_OPENAI_API_KEY": "sk-test-only",
                "DEVFLOW_LLM_OPENAI_DEFAULT_MODEL": "test-model",
            },
        )

        self.assertEqual(
            config.provider_settings["openai_compatible"]["api_key"],
            "sk-test-only",
        )
        self.assertEqual(
            config.provider_settings["openai_compatible"]["default_model"],
            "test-model",
        )

    def test_env_file_parser_ignores_comments_and_export_prefix(self):
        env_file = write_env_file(
            "parser.env",
            """
            export DEVFLOW_LLM_PROVIDER='anthropic_compatible'
            DEVFLOW_LLM_MODEL="quoted-model" # inline comment
            INVALID_LINE
            """,
        )

        loaded = load_env_file(env_file)

        self.assertEqual(loaded["DEVFLOW_LLM_PROVIDER"], "anthropic_compatible")
        self.assertEqual(loaded["DEVFLOW_LLM_MODEL"], "quoted-model")
        self.assertNotIn("INVALID_LINE", loaded)

    def test_openai_provider_converts_request_and_response_shape(self):
        captured = {}

        def transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            return {
                "id": "req-openai",
                "choices": [{"message": {"content": '{"ok": true}'}}],
                "usage": {"total_tokens": 12},
            }

        provider = OpenAICompatibleProvider(transport=transport)
        config = LlmClientConfig(
            provider_settings={
                "openai_compatible": {
                    "api_key": "sk-test",
                    "base_url": "https://llm.example/v1",
                    "default_model": "demo-openai",
                }
            }
        )

        response = provider.complete(
            LlmRequest(
                task="shape",
                response_format="json",
                messages=(LlmMessage("user", "返回 JSON"),),
            ),
            config,
        )

        self.assertEqual(captured["url"], "https://llm.example/v1/chat/completions")
        self.assertEqual(captured["payload"]["model"], "demo-openai")
        self.assertEqual(captured["payload"]["response_format"], {"type": "json_object"})
        self.assertNotIn("sk-test", json.dumps(response.usage))
        self.assertEqual(response.text, '{"ok": true}')

    def test_anthropic_provider_converts_request_and_response_shape(self):
        captured = {}

        def transport(url, headers, payload, timeout_seconds):
            captured["url"] = url
            captured["headers"] = headers
            captured["payload"] = payload
            captured["timeout_seconds"] = timeout_seconds
            return {
                "id": "req-anthropic",
                "content": [{"type": "text", "text": '{"ok": true}'}],
                "usage": {"input_tokens": 5, "output_tokens": 7},
            }

        provider = AnthropicCompatibleProvider(transport=transport)
        config = LlmClientConfig(
            provider_settings={
                "anthropic_compatible": {
                    "api_key": "sk-ant",
                    "base_url": "https://anthropic.example/v1",
                    "default_model": "demo-anthropic",
                }
            }
        )

        response = provider.complete(
            LlmRequest(
                task="shape",
                response_format="json",
                messages=(
                    LlmMessage("system", "只返回 JSON"),
                    LlmMessage("user", "返回 JSON"),
                ),
            ),
            config,
        )

        self.assertEqual(captured["url"], "https://anthropic.example/v1/messages")
        self.assertEqual(captured["payload"]["model"], "demo-anthropic")
        self.assertEqual(captured["payload"]["system"], "只返回 JSON")
        self.assertIn("只返回合法 JSON", captured["payload"]["messages"][-1]["content"])
        self.assertEqual(response.text, '{"ok": true}')


class LlmClientRealNetworkTest(unittest.TestCase):
    def test_real_provider_returns_json(self):
        source = merge_env_sources(
            load_env_file(resolve_env_file(os.environ)),
            os.environ,
        )
        if source.get("DEVFLOW_LLM_INTEGRATION_TEST") != "1":
            self.skipTest(
                "set DEVFLOW_LLM_INTEGRATION_TEST=1 in environment or .env.local "
                "to run real LLM network smoke test"
            )

        config = LlmClientConfig.from_sources()
        provider = config.default_provider
        required_by_provider = {
            "openai_compatible": [
                "api_key",
                "default_model",
            ],
            "anthropic_compatible": [
                "api_key",
                "default_model",
            ],
        }
        provider_settings = config.settings_for(provider)
        missing = [
            name
            for name in required_by_provider.get(provider, [])
            if not provider_settings.get(name)
        ]
        if missing:
            self.fail(
                "real LLM smoke test enabled but missing provider settings: "
                + ", ".join(missing)
            )

        client = LlmClient(config=config)
        result = client.complete_json(
            LlmRequest(
                task="real_network_smoke_test",
                provider=provider,
                temperature=0,
                timeout_seconds=30,
                messages=(
                    LlmMessage(
                        "system",
                        "You are a test endpoint. Return only valid JSON.",
                    ),
                    LlmMessage(
                        "user",
                        'Return exactly this JSON object shape: {"ok": true, "provider": "name"}.',
                    ),
                ),
                json_schema={
                    "type": "object",
                    "required": ["ok", "provider"],
                    "properties": {
                        "ok": {"type": "boolean"},
                        "provider": {"type": "string"},
                    },
                },
            )
        )

        self.assertIs(result["ok"], True)
        self.assertIn("provider", result)


def write_test_config(name: str, data: dict) -> Path:
    """把配置测试文件写到仓库内临时目录。

    当前执行环境可能禁止写系统 Temp 目录，因此测试统一使用已被 `.gitignore`
    忽略的 `execution-plane/.test_tmp/`。
    """

    temp_dir = Path(".test_tmp")
    temp_dir.mkdir(exist_ok=True)
    config_path = temp_dir / name
    config_path.write_text(json.dumps(data), encoding="utf-8")
    return config_path


def write_env_file(name: str, content: str) -> Path:
    temp_dir = Path(".test_tmp")
    temp_dir.mkdir(exist_ok=True)
    env_path = temp_dir / name
    env_path.write_text(content.strip(), encoding="utf-8")
    return env_path


if __name__ == "__main__":
    unittest.main()
