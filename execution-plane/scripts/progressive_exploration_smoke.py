from __future__ import annotations

import json
import sys
from pathlib import Path

EXECUTION_PLANE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = EXECUTION_PLANE_ROOT.parent
sys.path.insert(0, str(EXECUTION_PLANE_ROOT))

from src.agents.requirement_agent import RequirementAgent  # noqa: E402
from src.llm import LlmClient, LlmClientConfig, LlmResponse, LlmTraceRecorder  # noqa: E402


class ProgressiveSmokeProvider:
    name = "fake"

    def __init__(self, prd_response: dict):
        self.prd_response = prd_response

    def complete(self, request, config):
        if request.task == "progressive_context_exploration_plan":
            payload = {
                "queries": [
                    "RequirementAgent",
                    "repository context",
                    "PipelineController",
                    "frontend health",
                ],
                "pathHints": [
                    "execution-plane/src/agents/requirement_agent.py",
                    "execution-plane/src/context/repository_context.py",
                    "control-plane/devflow-engine/src/main/java/com/devflow/engine/api/PipelineController.java",
                    "sandbox/frontend/src/main.ts",
                ],
                "readRanges": [
                    {"path": "execution-plane/src/agents/requirement_agent.py", "lineStart": 1, "lineEnd": 120},
                    {"path": "execution-plane/src/context/repository_context.py", "lineStart": 1, "lineEnd": 140},
                ],
                "strategy": "smoke_test_progressive_tool_plan",
            }
        else:
            payload = self.prd_response
        return LlmResponse(
            provider=self.name,
            model=request.model or config.default_model or "fake-smoke-model",
            text=json.dumps(payload, ensure_ascii=False),
            parsed_json=None,
            usage={"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            latency_ms=0,
            request_id=None,
        )


def main() -> int:
    logs_dir = EXECUTION_PLANE_ROOT / "logs"
    trace_path = logs_dir / "progressive-exploration-smoke.jsonl"
    result_path = logs_dir / "progressive-exploration-smoke-result.json"
    if trace_path.exists():
        trace_path.unlink()
    if result_path.exists():
        result_path.unlink()

    fake_response = {
        "summary": "为测试环境增加一页健康检查视图，聚合控制平面、执行平面和 Temporal worker 的状态。",
        "problem_statement": "当前测试部署出现 worker 未轮询任务队列时，用户缺少一处可审查的状态入口。",
        "scope": {
            "in_scope": [
                "读取现有控制平面 API 与前端状态展示代码",
                "确认执行平面需求分析阶段如何输出代码上下文证据",
            ],
            "out_of_scope": ["修改真实生产部署拓扑"],
        },
        "user_stories": [
            {
                "role": "测试环境使用者",
                "goal": "查看控制平面、执行平面和 worker 状态",
                "benefit": "快速定位是否存在未启动 worker 或阶段产物缺失的问题",
            }
        ],
        "acceptance_criteria": [
            {
                "id": "AC-1",
                "description": "需求分析结果必须包含已检索文件、证据和探索轨迹。",
                "verification": "运行 progressive_exploration_smoke.py 后检查 result JSON 与 trace JSONL。",
            },
            {
                "id": "AC-2",
                "description": "阶段产物必须保留 codeContext 与 explorationTrace，便于前端展示中间产物。",
                "verification": "检查 result JSON 中 codeContext.evidence 和 explorationTrace 的动作序列。",
            }
        ],
        "edge_cases": ["仓库预算耗尽时返回 DEGRADED 并给出开放问题。"],
        "non_functional_requirements": {
            "performance": ["上下文探索必须受 maxFiles、maxBytes 和 maxSearchResults 限制。"],
            "security": ["默认跳过 .env、日志、构建产物和依赖目录。"],
            "reliability": ["即使 LLM 使用 FakeProvider，也要真实调用代码库上下文工具。"],
        },
        "domain_terms": ["Temporal", "Task Queue", "CodeContextSummary", "ExplorationStep"],
        "assumptions": ["本脚本只验证上下文工具链，不发起真实网络 LLM 请求。"],
        "open_questions": [],
        "evidence": {
            "files": [],
            "queries": [],
            "notes": ["证据由 RequirementAgent 的 code_context 字段补齐。"],
        },
        "quality": {
            "testable_acceptance_criteria": True,
            "has_open_questions": False,
            "evidence_backed": True,
        },
    }
    llm_client = LlmClient(
        config=LlmClientConfig(default_provider="fake", default_model="fake-smoke-model"),
        providers={"fake": ProgressiveSmokeProvider(fake_response)},
        trace_recorder=LlmTraceRecorder(
            enabled=True,
            stdout=False,
            file_path=trace_path,
            max_string_chars=120_000,
        ),
    )

    state = {
        "original_requirement": (
            "Build a health check page for control plane, execution plane, "
            "Temporal worker, pipeline status, and requirement agent trace."
        ),
        "repository_context": {
            "rootPath": str(REPO_ROOT),
            "targetFiles": [
                "execution-plane/src/agents/requirement_agent.py",
                "execution-plane/src/context/repository_context.py",
                "sandbox/frontend/src/main.ts",
                "control-plane/devflow-engine/src/main/java/com/devflow/engine/api/PipelineController.java",
            ],
            "excludePaths": [
                ".git",
                "target",
                "node_modules",
                "venv",
                "__pycache__",
                ".test_tmp",
                "dist",
                "logs",
            ],
            "maxRounds": 4,
            "maxFiles": 8,
            "maxBytes": 120_000,
            "maxSearchResults": 20,
            "privacyMode": "standard",
        },
    }

    result = RequirementAgent(llm_client=llm_client).run(state)
    result_path.parent.mkdir(parents=True, exist_ok=True)
    result_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    code_context = result.get("code_context") or {}
    trace = result.get("exploration_trace") or []
    action_types = {str(step.get("actionType") or step.get("action_type")) for step in trace}
    trace_lines = trace_path.read_text(encoding="utf-8").splitlines() if trace_path.exists() else []

    failures = []
    if not code_context.get("search_queries"):
        failures.append("code_context.search_queries is empty")
    if not code_context.get("inspected_files"):
        failures.append("code_context.inspected_files is empty")
    if not code_context.get("evidence"):
        failures.append("code_context.evidence is empty")
    required_actions = {"PLAN", "LIST_FILES", "SEARCH_TEXT", "READ_FILE", "EVALUATE"}
    missing_actions = sorted(required_actions - action_types)
    if missing_actions:
        failures.append(f"exploration_trace missing actions: {missing_actions}")
    required_events = {
        "requirement_agent.context_pack",
        "llm.request",
        "llm.response",
    }
    trace_text = "\n".join(trace_lines)
    missing_events = sorted(event for event in required_events if event not in trace_text)
    if missing_events:
        failures.append(f"trace file missing events: {missing_events}")

    print("=== Progressive Exploration Smoke ===")
    print(f"status: {code_context.get('status')}")
    print(f"search_queries: {', '.join(code_context.get('search_queries', []))}")
    print(f"inspected_files: {', '.join(code_context.get('inspected_files', []))}")
    print(f"evidence_count: {len(code_context.get('evidence', []))}")
    print(f"trace_actions: {', '.join(sorted(action_types))}")
    print(f"trace_file: {trace_path}")
    print(f"result_file: {result_path}")

    if failures:
        print("failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
