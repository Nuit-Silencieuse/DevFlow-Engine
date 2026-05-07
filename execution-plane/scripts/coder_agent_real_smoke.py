from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
REPOSITORY_ROOT = PROJECT_ROOT.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.agents.coder_agent import CoderAgent
from src.llm import LlmClient, LlmClientConfig, LlmTraceRecorder


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real CoderAgent LLM smoke test.")
    parser.add_argument("--config", help="Path to llm JSON config file.", default=None)
    parser.add_argument("--provider", help="Provider override for this run.", default=None)
    parser.add_argument("--model", help="Model override for this run.", default=None)
    parser.add_argument(
        "--output",
        help="Result JSON path. Defaults to execution-plane/logs/coder-agent-real-<timestamp>.json",
        default=None,
    )
    args = parser.parse_args()

    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    output_path = Path(args.output) if args.output else PROJECT_ROOT / "logs" / f"coder-agent-real-{timestamp}.json"
    if not output_path.is_absolute():
        output_path = PROJECT_ROOT / output_path
    trace_path = output_path.with_suffix(".jsonl")

    config = LlmClientConfig.from_sources(config_path=args.config)
    if args.provider or args.model:
        config = config.for_request(args.provider or config.default_provider, args.model)

    recorder = LlmTraceRecorder(
        enabled=True,
        stdout=False,
        file_path=trace_path,
        secrets=collect_config_secrets(config),
        max_string_chars=200_000,
    )
    client = LlmClient(config=config, trace_recorder=recorder)
    state = build_smoke_state()

    payload: dict[str, Any] = {
        "started_at": timestamp,
        "provider": config.default_provider,
        "model": config.default_model
        or (config.settings_for(config.default_provider).get("default_model")),
        "trace_file": str(trace_path),
        "input_state": state,
    }

    try:
        result = CoderAgent(llm_client=client).run(state)
        payload["status"] = "COMPLETED"
        payload["result"] = result
    except Exception as exc:  # noqa: BLE001 - smoke 脚本需要把真实异常写入诊断文件
        payload["status"] = "FAILED"
        payload["error_type"] = exc.__class__.__name__
        payload["error_message"] = str(exc)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(str(output_path))
    return 0 if payload["status"] == "COMPLETED" else 1


def build_smoke_state() -> dict[str, Any]:
    tasks_excerpt = read_excerpt(
        REPOSITORY_ROOT / "specs" / "001-devflow-engine" / "tasks.md",
        max_chars=5000,
    )
    return {
        "original_requirement": "以当前项目的 tasks.md 为材料，生成一个最小的 CoderAgent 冒烟测试补丁，只创建 test/coder-agent-smoke/README.md。",
        "structured_prd": {
            "summary": "验证 CoderAgent 能基于方案设计生成可审查的 unified diff。",
            "acceptance_criteria": [
                {
                    "id": "AC-01",
                    "description": "代码生成阶段必须返回合法 JSON 对象，且 diff_patch 为 unified diff。",
                    "verification": "检查 CoderAgent 单独真实 LLM 冒烟测试输出文件。",
                },
                {
                    "id": "AC-02",
                    "description": "补丁只创建 test/coder-agent-smoke/README.md，不修改其他文件。",
                    "verification": "检查 changed_files 和 diff_patch 文件路径。",
                },
            ],
            "source": "coder_agent_real_smoke",
        },
        "design_doc": {
            "summary": "创建一个最小文档文件，用于验证 CoderAgent 的 JSON 输出和 diff 结构。",
            "modules": [
                {
                    "name": "CoderAgentSmokeDoc",
                    "responsibility": "记录本次真实 LLM 冒烟测试的目的和检查方式。",
                    "dependencies": ["specs/001-devflow-engine/tasks.md"],
                    "key_decisions": ["只生成一个 Markdown 文件，降低 diff 规模和 token 成本。"],
                    "implementation_notes": [
                        "文件路径固定为 test/coder-agent-smoke/README.md。",
                        "内容使用简体中文，说明这是 CoderAgent 真实 LLM 冒烟测试产物。",
                    ],
                    "test_focus": ["JSON 输出是否可解析", "diff_patch 是否为合法 unified diff"],
                }
            ],
            "file_plan": [
                {
                    "path": "test/coder-agent-smoke/README.md",
                    "operation": "create",
                    "reason": "提供一个小规模、低风险的代码生成冒烟测试目标。",
                    "change_summary": "新增 Markdown 文档，说明 CoderAgent 冒烟测试目的、输入和预期输出。",
                    "validation": "检查 diff_patch 中只包含该文件，并通过 CoderAgent 内置 diff 结构校验。",
                    "related_modules": ["CoderAgentSmokeDoc"],
                }
            ],
            "risks": ["模型可能返回非 JSON 或损坏的 unified diff，需要在报告中保留原始输出。"],
            "source": "coder_agent_real_smoke",
        },
        "code_context": {
            "status": "COMPLETE",
            "root_path": str(REPOSITORY_ROOT),
            "inspected_files": ["specs/001-devflow-engine/tasks.md"],
            "search_queries": ["T024", "CODE_GENERATION", "CoderAgent"],
            "evidence": [
                {
                    "filePath": "specs/001-devflow-engine/tasks.md",
                    "lineStart": 1,
                    "lineEnd": 120,
                    "excerpt": tasks_excerpt,
                    "relevanceReason": "任务列表描述了 DevFlow Engine 的阶段和 Agent 实现任务，可作为冒烟测试材料。",
                    "supports": ["tasks", "coder_agent_smoke"],
                }
            ],
            "confidence": 0.72,
            "open_questions": [],
        },
        "pipeline_context": {
            "version": 1,
            "code_contexts": [],
            "artifact_index": {},
        },
    }


def read_excerpt(path: Path, max_chars: int) -> str:
    try:
        return path.read_text(encoding="utf-8")[:max_chars]
    except OSError as exc:
        return f"无法读取 {path}: {exc}"


def collect_config_secrets(config: LlmClientConfig) -> list[str]:
    secrets: list[str] = []
    for provider_name in ("openai_compatible", "anthropic_compatible"):
        api_key = config.settings_for(provider_name).get("api_key")
        if api_key:
            secrets.append(str(api_key))
    return secrets


if __name__ == "__main__":
    raise SystemExit(main())
