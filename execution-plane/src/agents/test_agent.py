from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from langgraph.graph import END, StateGraph

from src.llm import LlmClient, LlmMessage, LlmRequest
from src.pipeline_context import append_stage_code_context, normalize_pipeline_context

if TYPE_CHECKING:
    from src.graph.state import DevFlowState
else:
    DevFlowState = dict[str, Any]


TEST_GENERATION = "TEST_GENERATION"
logger = logging.getLogger(__name__)


class TestAgentState(TypedDict, total=False):
    devflow_state: DevFlowState
    structured_prd: dict[str, Any]
    design_doc: dict[str, Any]
    code_context: dict[str, Any]
    pipeline_context: dict[str, Any]
    diff_patch: str
    code_generation_report: dict[str, Any]
    feedback_text: str
    response_language: str
    test_plan: dict[str, Any]
    draft_results: dict[str, Any]
    normalized_results: dict[str, Any]
    validation_report: dict[str, Any]
    attempts: int
    errors: list[str]
    result: DevFlowState


class TestAgent:
    """负责 TEST_GENERATION 阶段的 LangGraph 子图。

    TestAgent 位于代码生成之后，它消费 CoderAgent 产出的 `diff_patch`，生成面向人工审查
    和后续沙箱执行的测试补丁 `test_diff_patch`。这里刻意不直接写入测试文件，也不直接
    执行 shell 命令：在当前架构中，补丁应用、命令执行和真实环境隔离应由后续沙箱/守护
    进程或专门的执行 Activity 完成。本 Agent 仍会要求 LLM 给出 `execution_results`，
    但如果测试补丁尚未应用，状态应显式标记为 NOT_RUN，而不是假装已经通过。
    """

    def __init__(self, llm_client: LlmClient | None = None, max_repair_attempts: int = 1):
        self.llm_client = llm_client or LlmClient.from_sources()
        self.max_repair_attempts = max_repair_attempts
        self.graph = build_test_agent_graph(self)

    def run(self, state: DevFlowState) -> DevFlowState:
        result = self.graph.invoke(
            {
                "devflow_state": state,
                "attempts": 0,
                "errors": list(state.get("error_logs", [])),
            }
        )
        return result["result"]

    def prepare_input(self, state: TestAgentState) -> TestAgentState:
        devflow_state = state["devflow_state"]
        structured_prd = dict(devflow_state.get("structured_prd") or {})
        design_doc = dict(devflow_state.get("design_doc") or {})
        code_context = dict(devflow_state.get("code_context") or {})
        code_generation_report = dict(devflow_state.get("code_generation_report") or {})
        pipeline_context = normalize_pipeline_context(devflow_state.get("pipeline_context"))
        diff_patch = str(devflow_state.get("diff_patch") or "")
        feedback_text = normalize_text(devflow_state.get("human_feedback", ""))
        response_language = infer_response_language(
            " ".join(
                [
                    str(devflow_state.get("original_requirement", "")),
                    json.dumps(structured_prd, ensure_ascii=False),
                    json.dumps(design_doc, ensure_ascii=False),
                    feedback_text,
                ]
            )
        )
        errors = list(state.get("errors", []))

        if not diff_patch.strip():
            logger.warning(
                "TestAgent input is missing diff_patch; stage will return a blocked diagnostic result."
            )
            errors.append("diff_patch is required for test generation")
        if diff_patch and not design_doc:
            logger.warning(
                "TestAgent received diff_patch without design_doc; test generation will rely on code diff only."
            )
        if feedback_text:
            logger.warning(
                "TestAgent received human feedback and will include it in test generation. feedback_length=%s",
                len(feedback_text),
            )

        return {
            "structured_prd": structured_prd,
            "design_doc": design_doc,
            "code_context": code_context,
            "pipeline_context": pipeline_context,
            "diff_patch": diff_patch,
            "code_generation_report": code_generation_report,
            "feedback_text": feedback_text,
            "response_language": response_language,
            "errors": errors,
        }

    def plan_tests(self, state: TestAgentState) -> TestAgentState:
        changed_files = normalize_named_items((state.get("code_generation_report") or {}).get("changed_files"))
        test_plan = {
            "strategy": "diff_driven_test_patch_generation",
            "response_language": state.get("response_language", "same_as_requirement"),
            "changed_files": changed_files,
            "design_test_strategy": normalize_named_items((state.get("design_doc") or {}).get("test_strategy")),
            "acceptance_criteria": normalize_named_items((state.get("structured_prd") or {}).get("acceptance_criteria")),
            "suggested_commands": infer_test_commands(changed_files, state.get("design_doc") or {}),
            "constraints": [
                "只生成测试补丁，不直接写入文件",
                "不能执行 shell 命令",
                "如果补丁尚未应用，execution_results 必须标记为 NOT_RUN",
                "测试应覆盖 diff_patch 触及的行为和 PRD 验收条件",
            ],
        }
        test_plan["constraints"].append(
            "TEST_GENERATION 只生成 test_diff_patch、test_files、test_commands；真实 execution_results 由 APPLY_AND_RUN_TESTS 产出。"
        )
        self.llm_client.trace_recorder.record("test_agent.test_plan", {"test_plan": test_plan})
        return {"test_plan": test_plan}

    def draft_test_results(self, state: TestAgentState) -> TestAgentState:
        logger.warning(
            "TestAgent is calling LLM for test generation. diff_length=%s suggested_commands=%s feedback_present=%s",
            len(state.get("diff_patch", "")),
            len((state.get("test_plan") or {}).get("suggested_commands", [])),
            bool(state.get("feedback_text")),
        )
        request = LlmRequest(
            task="test_generation",
            messages=build_test_messages(state),
            json_schema=TEST_RESULTS_SCHEMA,
            metadata={"stage": TEST_GENERATION},
        )
        draft = self.llm_client.complete_json(request)
        self.llm_client.trace_recorder.record("test_agent.draft_results", {"draft_results": draft})
        return {"draft_results": draft}

    def validate_test_results(self, state: TestAgentState) -> TestAgentState:
        normalized = normalize_test_results_payload(state.get("draft_results") or {})
        issues = validate_test_results_payload(normalized)
        if issues:
            logger.warning("TestAgent validation found issues: %s", issues)
        validation_report = {
            "valid": not issues,
            "issues": issues,
            "repairable": bool(issues) and int(state.get("attempts", 0)) < self.max_repair_attempts,
        }
        self.llm_client.trace_recorder.record(
            "test_agent.validation_report",
            {"validation_report": validation_report},
        )
        return {
            "normalized_results": normalized,
            "validation_report": validation_report,
        }

    def repair_test_results(self, state: TestAgentState) -> TestAgentState:
        # 修复只处理 LLM 常见的格式外壳问题，例如把 unified diff 包在 Markdown
        # code fence 中。这里不会根据 diff_patch 自行编造测试代码，否则会破坏
        # “测试内容来自模型输出并可被人工审查”的产物边界。
        logger.warning(
            "TestAgent is repairing test result format. attempts=%s issues=%s",
            int(state.get("attempts", 0)) + 1,
            (state.get("validation_report") or {}).get("issues", []),
        )
        repaired = normalize_test_results_payload(state.get("normalized_results") or {})
        repaired["test_diff_patch"] = strip_markdown_fence(str(repaired.get("test_diff_patch") or ""))
        return {
            "draft_results": repaired,
            "attempts": int(state.get("attempts", 0)) + 1,
        }

    def finalize(self, state: TestAgentState) -> TestAgentState:
        test_results = {
            **(state.get("normalized_results") or {}),
            "status": str((state.get("normalized_results") or {}).get("status") or "GENERATED"),
            "test_plan": state.get("test_plan") or {},
            "feedback": state.get("feedback_text", ""),
            "source": "test_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=TEST_GENERATION,
            agent="test_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"test_results": test_results},
        )
        return {
            "result": {
                "test_results": test_results,
                "pipeline_context": pipeline_context,
                "current_step": TEST_GENERATION,
                "error_logs": list(state.get("errors", [])),
            }
        }

    def fail_soft(self, state: TestAgentState) -> TestAgentState:
        errors = list(state.get("errors", [])) or ["test generation failed"]
        logger.warning("TestAgent entered fail_soft. errors=%s", errors)
        test_results = {
            "status": "BLOCKED",
            "summary": "无法生成测试代码，缺少代码生成阶段的 diff_patch 或测试产物格式不可用。",
            "test_diff_patch": "",
            "test_files": [],
            "test_commands": [],
            "coverage_focus": [],
            "risks": ["TEST_GENERATION 阶段没有可审查测试补丁，后续评审不应假设测试已覆盖本次变更。"],
            "open_questions": ["请先确认 CODE_GENERATION 阶段已经产出有效 diff_patch。"],
            "feedback": state.get("feedback_text", ""),
            "quality": {"confidence": "LOW"},
            "source": "test_agent",
        }
        pipeline_context = append_stage_code_context(
            normalize_pipeline_context(state.get("pipeline_context")),
            stage=TEST_GENERATION,
            agent="test_agent",
            code_context=dict(state.get("code_context") or {}),
            artifacts={"test_results": test_results},
        )
        return {
            "result": {
                "test_results": test_results,
                "pipeline_context": pipeline_context,
                "current_step": TEST_GENERATION,
                "error_logs": errors,
            }
        }


def build_test_agent_graph(agent: TestAgent):
    builder = StateGraph(TestAgentState)
    builder.add_node("prepare_input", agent.prepare_input)
    builder.add_node("plan_tests", agent.plan_tests)
    builder.add_node("draft_test_results", agent.draft_test_results)
    builder.add_node("validate_test_results", agent.validate_test_results)
    builder.add_node("repair_test_results", agent.repair_test_results)
    builder.add_node("finalize", agent.finalize)
    builder.add_node("fail_soft", agent.fail_soft)

    builder.set_entry_point("prepare_input")
    builder.add_conditional_edges(
        "prepare_input",
        route_after_prepare,
        {"valid": "plan_tests", "invalid": "fail_soft"},
    )
    builder.add_edge("plan_tests", "draft_test_results")
    builder.add_edge("draft_test_results", "validate_test_results")
    builder.add_conditional_edges(
        "validate_test_results",
        route_after_validation,
        {"valid": "finalize", "repairable": "repair_test_results", "invalid": "fail_soft"},
    )
    builder.add_edge("repair_test_results", "validate_test_results")
    builder.add_edge("finalize", END)
    builder.add_edge("fail_soft", END)
    return builder.compile()


def route_after_prepare(state: TestAgentState) -> Literal["valid", "invalid"]:
    return "invalid" if state.get("errors") else "valid"


def route_after_validation(state: TestAgentState) -> Literal["valid", "repairable", "invalid"]:
    report = state.get("validation_report", {})
    if report.get("valid"):
        return "valid"
    if report.get("repairable"):
        return "repairable"
    return "invalid"


def build_test_messages(state: TestAgentState) -> tuple[LlmMessage, ...]:
    payload = {
        "structured_prd": state.get("structured_prd", {}),
        "design_doc": state.get("design_doc", {}),
        "diff_patch": state.get("diff_patch", ""),
        "code_generation_report": state.get("code_generation_report", {}),
        "code_context_summary": summarize_code_context(state.get("code_context") or {}),
        "human_feedback": state.get("feedback_text", ""),
        "test_plan": state.get("test_plan", {}),
        "response_language": state.get("response_language", "same_as_requirement"),
    }
    return (
        LlmMessage(
            role="system",
            content=(
                "你是 DevFlow Engine 的 Test Agent。只输出符合 schema 的 JSON。"
                "输出语言必须遵守 user payload 中的 response_language；中文需求使用简体中文描述 summary、purpose、risks、open_questions。"
                "你必须根据 diff_patch、design_doc 和验收条件生成或更新测试。"
                "核心产物 test_diff_patch 必须是 unified diff，且只包含测试文件或测试辅助文件的修改。"
                "只生成测试补丁；不能写入文件；不能执行 shell 命令；不能声称已经运行了未实际运行的测试。"
                "如果测试补丁尚未应用，execution_results 中对应命令必须使用 NOT_RUN，并解释原因。"
                "test_files 需要列出 path、framework、purpose、assertions，方便前端展示测试代码意图。"
                "test_commands 需要列出 command、purpose 和 expected_result，方便后续沙箱执行。"
                "execution_results 需要列出 command、status、exit_code、stdout、stderr；没有真实执行时 stdout 为空、status 为 NOT_RUN。"
            ),
        ),
        LlmMessage(
            role="system",
            content=(
                "重要更新：TEST_GENERATION 阶段只生成测试补丁、测试文件计划和测试命令。"
                "不要输出 execution_results；真实测试执行结果只由后续 APPLY_AND_RUN_TESTS 阶段产出。"
            ),
        ),
        LlmMessage(
            role="system",
            content=(
                "test_commands 必须使用结构化命令格式：command 只能包含单个可执行命令及参数，"
                "不得包含 cd、&&、||、;、|、>、< 或 shell 脚本片段。"
                "如果需要进入子目录执行，例如 demo 下的 npm test，请输出 "
                "{\"command\":\"npm test\",\"working_directory\":\"demo\"}。"
                "依赖安装和测试运行要拆成两个 test_commands 条目。"
            ),
        ),
        LlmMessage(role="user", content=json.dumps(payload, ensure_ascii=False)),
    )


def normalize_test_results_payload(value: dict[str, Any]) -> dict[str, Any]:
    test_diff_patch = strip_markdown_fence(
        first_raw_text(value, "test_diff_patch", "testDiffPatch", "diff_patch", "patch")
    )
    return {
        "status": first_text(value, "status") or "GENERATED",
        "summary": first_text(value, "summary"),
        "test_diff_patch": test_diff_patch,
        "test_files": normalize_named_items(value.get("test_files") or value.get("testFiles")),
        "test_commands": normalize_named_items(value.get("test_commands") or value.get("testCommands")),
        "coverage_focus": ensure_text_list(value.get("coverage_focus") or value.get("coverageFocus")),
        "risks": ensure_text_list(value.get("risks")),
        "open_questions": ensure_text_list(value.get("open_questions") or value.get("openQuestions")),
        "quality": dict(value.get("quality") or {}) if isinstance(value.get("quality"), dict) else {},
    }


def validate_test_results_payload(value: dict[str, Any]) -> list[str]:
    issues: list[str] = []
    test_diff_patch = str(value.get("test_diff_patch") or "")
    if not test_diff_patch.strip():
        issues.append("test_diff_patch is required")
    if test_diff_patch.strip() and not looks_like_unified_diff(test_diff_patch):
        issues.append("test_diff_patch must be a unified diff")
    if contains_forbidden_execution_text(test_diff_patch):
        issues.append("test_diff_patch must not contain command execution instructions")
    if not value.get("test_files"):
        issues.append("at least one test_files item is required")
    if not value.get("test_commands"):
        issues.append("at least one test_commands item is required")
    try:
        json.dumps(value, ensure_ascii=False)
    except TypeError as exc:
        issues.append(f"test_results must be JSON serializable: {exc}")
    return issues


def infer_test_commands(changed_files: list[dict[str, Any]], design_doc: dict[str, Any]) -> list[dict[str, str]]:
    paths = [str(item.get("path") or "") for item in changed_files]
    validation_text = json.dumps(design_doc.get("file_plan", []), ensure_ascii=False).casefold()
    commands: list[dict[str, str]] = []
    if any(path.endswith(".py") for path in paths) or "unittest" in validation_text or "pytest" in validation_text:
        commands.append({"command": "python -m unittest discover -s tests", "purpose": "运行 Python 单元测试"})
    if any(path.endswith((".ts", ".tsx", ".js", ".jsx")) for path in paths) or "npm" in validation_text:
        commands.append({"command": "npm test", "purpose": "运行前端或 Node.js 测试"})
    if any(path.endswith(".java") for path in paths) or "maven" in validation_text:
        commands.append({"command": "mvn test", "purpose": "运行 Java/Maven 单元测试"})
    return commands or [{"command": "manual review", "purpose": "无法从变更文件推断自动化测试命令"}]


def summarize_code_context(code_context: dict[str, Any]) -> dict[str, Any]:
    return {
        "status": code_context.get("status", "SKIPPED"),
        "root_path": code_context.get("root_path", ""),
        "inspected_files": list(code_context.get("inspected_files", [])),
        "evidence": list(code_context.get("evidence", []))[:8],
        "confidence": code_context.get("confidence", 0.0),
        "open_questions": list(code_context.get("open_questions", [])),
    }


def looks_like_unified_diff(text: str) -> bool:
    return bool(
        re.search(r"^diff --git\s+", text, re.MULTILINE)
        or (
            re.search(r"^---\s+", text, re.MULTILINE)
            and re.search(r"^\+\+\+\s+", text, re.MULTILINE)
            and re.search(r"^@@\s+", text, re.MULTILINE)
        )
    )


def contains_forbidden_execution_text(text: str) -> bool:
    lowered = text.casefold()
    forbidden = ("git commit", "git push", "rm -rf", "del /", "powershell ", "cmd /c")
    return any(token in lowered for token in forbidden)


def strip_markdown_fence(text: str) -> str:
    fenced = re.search(r"```(?:diff|patch)?\s*(.*?)```", text.strip(), re.IGNORECASE | re.DOTALL)
    return fenced.group(1).strip() if fenced else text


def normalize_named_items(value: Any) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for item in value if isinstance(value, list) else []:
        if isinstance(item, dict):
            normalized = {
                str(key): normalize_json_value(val)
                for key, val in item.items()
                if val is not None
            }
            if normalized:
                items.append(normalized)
        elif str(item or "").strip():
            items.append({"summary": str(item).strip()})
    return items


def ensure_text_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item or "").strip()]
    if value is None:
        return []
    text = str(value).strip()
    return [text] if text else []


def first_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def first_raw_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None and str(value).strip():
            return str(value)
    return ""


def normalize_json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_json_value(val) for key, val in value.items()}
    if isinstance(value, list):
        return [normalize_json_value(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def normalize_text(value: Any) -> str:
    return " ".join(str(value or "").split())


def infer_response_language(text: str) -> str:
    return "zh-Hans" if re.search(r"[\u4e00-\u9fff]", text or "") else "en"


TEST_RESULTS_SCHEMA = {
    "type": "object",
    "required": [
        "summary",
        "test_diff_patch",
        "test_files",
        "test_commands",
        "risks",
        "open_questions",
    ],
    "properties": {
        "status": {"type": "string"},
        "summary": {"type": "string"},
        "test_diff_patch": {
            "type": "string",
            "description": "Unified diff text for test files only.",
        },
        "test_files": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["path", "framework", "purpose"],
                "properties": {
                    "path": {"type": "string"},
                    "framework": {"type": "string"},
                    "purpose": {"type": "string"},
                    "assertions": {"type": "array", "items": {"type": "string"}},
                },
            },
        },
        "test_commands": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["command", "purpose"],
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "Single executable command and args only; no cd, &&, pipes, redirects, or shell scripts.",
                    },
                    "working_directory": {
                        "type": "string",
                        "description": "Optional safe relative directory under repository root, for example demo.",
                    },
                    "purpose": {"type": "string"},
                    "expected_result": {"type": "string"},
                },
            },
        },
        "coverage_focus": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "open_questions": {"type": "array", "items": {"type": "string"}},
        "quality": {"type": "object"},
    },
}
