from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import Path
from typing import Any


DEFAULT_MAX_FILES = 500
DEFAULT_MAX_BYTES = 1_048_576
DEFAULT_MAX_ROUNDS = 4
DEFAULT_MAX_SEARCHES = 8
DEFAULT_MAX_SEARCH_RESULTS = 30

DEFAULT_EXCLUDE_PATHS = (
    ".git",
    "node_modules",
    "target",
    "build",
    "dist",
    ".venv",
    "venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".devflow-test-env",
    ".test_tmp",
    "logs",
    "*.pyc",
    "*.class",
    "*.jar",
    "*.log",
    ".env",
    ".env.*",
    "*secret*",
    "*key*",
)

VALID_PRIVACY_MODES = ("standard", "strict")


@dataclass(frozen=True)
class ExplorationBudget:
    """一次渐进探索的硬预算。

    这些字段不是给 LLM 的建议，而是工具层必须执行的上限。Agent 可以决定
    下一步要读什么，但不能绕过轮次、文件数、字节数和搜索结果数的限制。
    """

    max_rounds: int = DEFAULT_MAX_ROUNDS
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES
    max_searches: int = DEFAULT_MAX_SEARCHES
    max_search_results: int = DEFAULT_MAX_SEARCH_RESULTS


@dataclass(frozen=True)
class BudgetUsage:
    """记录当前探索已经消耗的预算，供 trace、前端展示和降级判断使用。"""

    rounds_used: int = 0
    files_read: int = 0
    bytes_read: int = 0
    searches_used: int = 0


@dataclass(frozen=True)
class RepositoryExplorationRequest:
    """渐进式代码感知入口请求。

    常规用户只需要 root_path。include/exclude/target/budget 都是高级约束，
    它们只能收窄或优先排序探索范围，不能扩大 root_path 之外的访问权限。
    """

    root_path: Path
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    target_files: tuple[str, ...] = ()
    budget: ExplorationBudget = field(default_factory=ExplorationBudget)
    privacy_mode: str = "standard"

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RepositoryExplorationRequest | None":
        if not data:
            return None

        root_path = data.get("rootPath") or data.get("root_path")
        if not root_path:
            raise ValueError("repository.rootPath is required")

        request = cls(
            root_path=Path(root_path),
            include_paths=_normalize_paths(data.get("includePaths") or data.get("include_paths")),
            exclude_paths=_normalize_paths(data.get("excludePaths") or data.get("exclude_paths")),
            target_files=_normalize_paths(data.get("targetFiles") or data.get("target_files")),
            budget=ExplorationBudget(
                max_rounds=_positive_int(
                    data.get("maxRounds") or data.get("max_rounds"),
                    DEFAULT_MAX_ROUNDS,
                    "maxRounds",
                ),
                max_files=_positive_int(
                    data.get("maxFiles") or data.get("max_files"),
                    DEFAULT_MAX_FILES,
                    "maxFiles",
                ),
                max_bytes=_positive_int(
                    data.get("maxBytes") or data.get("max_bytes"),
                    DEFAULT_MAX_BYTES,
                    "maxBytes",
                ),
                max_searches=_positive_int(
                    data.get("maxSearches") or data.get("max_searches"),
                    DEFAULT_MAX_SEARCHES,
                    "maxSearches",
                ),
                max_search_results=_positive_int(
                    data.get("maxSearchResults") or data.get("max_search_results"),
                    DEFAULT_MAX_SEARCH_RESULTS,
                    "maxSearchResults",
                ),
            ),
            privacy_mode=str(data.get("privacyMode") or data.get("privacy_mode") or "standard"),
        )
        request.validate()
        return request

    @property
    def resolved_root(self) -> Path:
        return self.root_path.expanduser().resolve()

    @property
    def effective_include_paths(self) -> tuple[str, ...]:
        return self.include_paths or (".",)

    @property
    def effective_exclude_paths(self) -> tuple[str, ...]:
        return _combined_exclude_paths(self.exclude_paths)

    def validate(self) -> None:
        root = self.resolved_root
        if not root.exists() or not root.is_dir():
            raise ValueError(f"Repository root does not exist or is not a directory: {self.root_path}")

        if self.privacy_mode not in VALID_PRIVACY_MODES:
            raise ValueError(f"Unsupported privacyMode: {self.privacy_mode}")

        # 这里不要求 include/exclude/target 指向的文件一定存在；它们可能是用户
        # 预先设置的范围。但必须先解析并确认没有逃逸 root_path。
        for relative_path in (
            *self.include_paths,
            *self.exclude_paths,
            *self.target_files,
        ):
            _resolve_inside_root(root, relative_path)


@dataclass(frozen=True)
class ExplorationSession:
    """一次需求分析阶段内的代码探索会话状态。"""

    session_id: str
    pipeline_id: str
    stage_name: str
    requirement: str
    status: str = "PLANNED"
    budget: ExplorationBudget = field(default_factory=ExplorationBudget)
    budget_usage: BudgetUsage = field(default_factory=BudgetUsage)
    confidence: float = 0.0
    created_at: str | None = None
    completed_at: str | None = None


@dataclass(frozen=True)
class ExplorationStep:
    """一次可审计的 Agent 决策或工具调用记录。"""

    step_index: int
    round_index: int
    action_type: str
    reason: str
    input: Mapping[str, Any] = field(default_factory=dict)
    result_summary: str = ""
    selected_files: tuple[str, ...] = ()
    skipped_files: tuple["SkippedFile", ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class EvidenceItem:
    """支持需求分析结论的代码证据。"""

    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    symbol_name: str | None = None
    excerpt: str = ""
    relevance_reason: str = ""
    supports: tuple[str, ...] = ()


@dataclass(frozen=True)
class SkippedFile:
    """记录被安全规则、预算或文件类型策略跳过的路径。"""

    path: str
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class CodeContextSummary:
    """最终注入需求分析和控制平面阶段产物的代码上下文摘要。"""

    root_path: str
    status: str = "COMPLETE"
    search_queries: tuple[str, ...] = ()
    inspected_files: tuple[str, ...] = ()
    candidate_files: tuple[str, ...] = ()
    evidence: tuple[EvidenceItem, ...] = ()
    skipped_paths: tuple[SkippedFile, ...] = ()
    budget_usage: BudgetUsage = field(default_factory=BudgetUsage)
    confidence: float = 0.0
    open_questions: tuple[str, ...] = ()


@dataclass(frozen=True)
class RepositoryContext:
    root_path: Path
    include_paths: tuple[str, ...] = ()
    exclude_paths: tuple[str, ...] = ()
    target_files: tuple[str, ...] = ()
    max_files: int = DEFAULT_MAX_FILES
    max_bytes: int = DEFAULT_MAX_BYTES

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any] | None) -> "RepositoryContext | None":
        if not data:
            return None

        root_path = data.get("rootPath") or data.get("root_path")
        if not root_path:
            raise ValueError("repository.rootPath is required")

        return cls(
            root_path=Path(root_path),
            include_paths=_normalize_paths(data.get("includePaths") or data.get("include_paths")),
            exclude_paths=_normalize_paths(data.get("excludePaths") or data.get("exclude_paths")),
            target_files=_normalize_paths(data.get("targetFiles") or data.get("target_files")),
            max_files=int(data.get("maxFiles") or data.get("max_files") or DEFAULT_MAX_FILES),
            max_bytes=int(data.get("maxBytes") or data.get("max_bytes") or DEFAULT_MAX_BYTES),
        )

    @property
    def resolved_root(self) -> Path:
        return self.root_path.expanduser().resolve()


@dataclass(frozen=True)
class FileMatch:
    path: str
    size: int


@dataclass(frozen=True)
class RepositoryFile:
    path: str
    size_bytes: int
    language: str
    priority_hint: str = ""


@dataclass(frozen=True)
class RepositoryListing:
    files: tuple[RepositoryFile, ...]
    skipped: tuple[SkippedFile, ...] = ()


@dataclass(frozen=True)
class RepositoryDirectorySummary:
    """Compact repo map 中的目录级摘要。

    这个结构只记录目录的元数据，不读取源码内容。Agent 先用它判断应该搜索哪些
    子树，再决定是否进入更细粒度的 search/read_file_range 工具调用。
    """

    path: str
    file_count: int = 0
    child_directory_count: int = 0
    languages: Mapping[str, int] = field(default_factory=dict)


@dataclass(frozen=True)
class CompactRepositoryMap:
    """面向 Agent 规划阶段的轻量仓库地图。

    它不是最终上下文，也不直接进入 PRD prompt 的源码片段区域；它的作用是让 LLM
    先看到有限的仓库形状、语言分布和高信号路径，然后输出下一步工具调用计划。
    这样可以避免一开始就把几百甚至几千个候选文件 excerpt 给模型。
    """

    root_path: str
    files: tuple[RepositoryFile, ...]
    directory_summaries: tuple[RepositoryDirectorySummary, ...]
    language_stats: Mapping[str, int] = field(default_factory=dict)
    high_signal_files: tuple[str, ...] = ()
    entrypoint_files: tuple[str, ...] = ()
    skipped: tuple[SkippedFile, ...] = ()


@dataclass(frozen=True)
class SearchMatch:
    path: str
    line_number: int
    line: str
    preview: str = ""
    truncated: bool = False
    score_hint: float = 0.0


@dataclass(frozen=True)
class FileRangeRead:
    path: str
    line_start: int
    line_end: int
    content: str
    bytes_read: int
    truncated: bool = False


@dataclass(frozen=True)
class ContextFile:
    path: str
    content: str
    truncated: bool = False


@dataclass(frozen=True)
class ContextPack:
    root_path: str
    files: tuple[ContextFile, ...]
    inspected_files: tuple[str, ...]
    search_queries: tuple[str, ...]
    total_bytes: int


def list_files(context: RepositoryContext) -> list[FileMatch]:
    """按 repository 配置遍历文件，并返回稳定排序的相对路径列表。"""
    root = _ensure_repository_root(context)
    discovered: dict[str, FileMatch] = {}
    include_paths = context.include_paths or (".",)

    for include_path in include_paths:
        resolved = _resolve_inside_root(root, include_path)
        _collect_files(root, resolved, context, discovered)
        if len(discovered) >= context.max_files:
            break

    for target_file in context.target_files:
        resolved = _resolve_inside_root(root, target_file)
        if resolved.is_file():
            relative_path = _relative_path(root, resolved)
            if not _is_excluded(relative_path, context.exclude_paths):
                discovered[relative_path] = FileMatch(relative_path, resolved.stat().st_size)

    return sorted(discovered.values(), key=lambda file: file.path)[: context.max_files]


def list_repository(request: RepositoryExplorationRequest) -> RepositoryListing:
    """渐进探索的文件发现工具。

    这个函数只负责确定性地列出可候选文件和跳过原因，不做需求判断。
    RequirementAgent 后续会根据需求文本和搜索结果再决定读取哪些文件。
    """

    root = request.resolved_root
    discovered: dict[str, RepositoryFile] = {}
    skipped: dict[str, SkippedFile] = {}

    for exclude_path in request.exclude_paths:
        skipped.setdefault(
            exclude_path,
            SkippedFile(
                path=exclude_path,
                reason="EXCLUDED",
                detail="user exclude rule is active for this exploration",
            ),
        )

    # targetFiles 是用户显式指出的高价值线索，即使 maxFiles 很小也应优先进入
    # 候选集合。后续 include 扫描只能填充剩余预算，不能把目标文件挤掉。
    for target_file in request.target_files:
        resolved = _resolve_inside_root(root, target_file)
        relative_path = _relative_path(root, resolved)
        if _is_excluded(relative_path, request.exclude_paths):
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="EXCLUDED",
                detail="target file is excluded by repository exploration rules",
            )
        elif resolved.is_file():
            discovered[relative_path] = _repository_file(root, resolved, "target")
        elif resolved.exists():
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="READ_ERROR",
                detail="target path exists but is not a regular file",
            )

    for include_path in request.effective_include_paths:
        resolved = _resolve_inside_root(root, include_path)
        _collect_repository_files(root, resolved, request, discovered, skipped)
        if len(discovered) >= request.budget.max_files:
            skipped.setdefault(
                _normalize_path(include_path),
                SkippedFile(
                    path=_normalize_path(include_path),
                    reason="BUDGET_EXHAUSTED",
                    detail="maxFiles reached before this include path was fully scanned",
                ),
            )

    return RepositoryListing(
        files=tuple(list(discovered.values())[: request.budget.max_files]),
        skipped=tuple(sorted(skipped.values(), key=lambda item: item.path)),
    )


def inspect_compact_repository_map(
    request: RepositoryExplorationRequest,
    *,
    max_map_files: int = 800,
    max_directory_depth: int = 4,
    max_high_signal_files: int = 120,
) -> CompactRepositoryMap:
    """构建一次规划用的 compact repository map。

    这里刻意只读取文件系统元数据：路径、大小、后缀推断出的语言、目录计数和少量
    高信号文件名。后续 Agent 若认为某个区域相关，必须继续通过 search_text 或
    read_file_range 读取小片段源码，不能把 map 当作源码上下文。
    """

    root = request.resolved_root
    discovered: dict[str, RepositoryFile] = {}
    skipped: dict[str, SkippedFile] = {}
    directory_stats: dict[str, dict[str, Any]] = {}

    for exclude_path in request.exclude_paths:
        skipped.setdefault(
            exclude_path,
            SkippedFile(
                path=exclude_path,
                reason="EXCLUDED",
                detail="user exclude rule is active for this exploration",
            ),
        )

    for target_file in request.target_files:
        resolved = _resolve_inside_root(root, target_file)
        relative_path = _relative_path(root, resolved)
        if _is_excluded(relative_path, request.exclude_paths):
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="EXCLUDED",
                detail="target file is excluded by repository exploration rules",
            )
        elif resolved.is_file() and not _has_binary_suffix(resolved):
            file = _repository_file(root, resolved, "target")
            discovered[relative_path] = file
            _record_directory_file(directory_stats, relative_path, file.language, max_directory_depth)

    for include_path in request.effective_include_paths:
        resolved = _resolve_inside_root(root, include_path)
        _collect_compact_repository_map(
            root,
            resolved,
            request,
            discovered,
            skipped,
            directory_stats,
            max_map_files=max_map_files,
            max_directory_depth=max_directory_depth,
        )
        if len(discovered) >= max_map_files:
            skipped.setdefault(
                _normalize_path(include_path),
                SkippedFile(
                    path=_normalize_path(include_path),
                    reason="BUDGET_EXHAUSTED",
                    detail="compact repository map reached max_map_files",
                ),
            )
            break

    files = tuple(
        sorted(
            discovered.values(),
            key=lambda file: (
                0 if file.priority_hint == "target" else 1,
                0 if _is_high_signal_path(file.path, file.priority_hint) else 1,
                file.path.casefold(),
            ),
        )
    )
    language_stats: dict[str, int] = {}
    for file in files:
        language_stats[file.language] = language_stats.get(file.language, 0) + 1

    directory_summaries = tuple(
        RepositoryDirectorySummary(
            path=path,
            file_count=int(stats.get("file_count", 0)),
            child_directory_count=len(stats.get("child_directories", set())),
            languages=dict(sorted(dict(stats.get("languages", {})).items())),
        )
        for path, stats in sorted(directory_stats.items(), key=lambda item: item[0].casefold())
    )
    high_signal_files = tuple(
        file.path
        for file in files
        if _is_high_signal_path(file.path, file.priority_hint)
    )[:max_high_signal_files]
    entrypoint_files = tuple(file.path for file in files if _is_entrypoint_path(file.path))[:80]
    return CompactRepositoryMap(
        root_path=str(root),
        files=files,
        directory_summaries=directory_summaries,
        language_stats=dict(sorted(language_stats.items())),
        high_signal_files=high_signal_files,
        entrypoint_files=entrypoint_files,
        skipped=tuple(sorted(skipped.values(), key=lambda item: item.path)),
    )


def read_file(
    context: RepositoryContext,
    path: str,
    *,
    start_line: int | None = None,
    end_line: int | None = None,
) -> str:
    """读取仓库内文本文件，支持 1-based 闭区间行号截取。"""
    root = _ensure_repository_root(context)
    resolved = _resolve_inside_root(root, path)
    relative_path = _relative_path(root, resolved)

    if _is_excluded(relative_path, context.exclude_paths):
        raise ValueError(f"Path is excluded by repository context: {path}")
    if not resolved.is_file():
        raise FileNotFoundError(path)

    content = resolved.read_text(encoding="utf-8", errors="replace")
    if start_line is None and end_line is None:
        return content

    lines = content.splitlines()
    start = max((start_line or 1) - 1, 0)
    end = end_line if end_line is not None else len(lines)
    return "\n".join(lines[start:end])


def search_text(
    context: RepositoryContext | RepositoryExplorationRequest,
    query: str,
    *,
    max_results: int = 20,
) -> list[SearchMatch]:
    """在已纳入上下文范围的文本文件中执行大小写不敏感搜索。"""
    normalized_query = query.casefold()
    if not normalized_query:
        return []

    if isinstance(context, RepositoryExplorationRequest):
        max_results = min(max_results, context.budget.max_search_results)
        matches: list[SearchMatch] = []
        for file in list_repository(context).files:
            try:
                read_result = read_file_range(
                    context,
                    file.path,
                    max_bytes=min(context.budget.max_bytes, 64_000),
                )
            except (OSError, UnicodeError, ValueError):
                continue
            for line_number, line in enumerate(read_result.content.splitlines(), start=1):
                if normalized_query in line.casefold():
                    preview = line.rstrip()
                    matches.append(
                        SearchMatch(
                            path=file.path,
                            line_number=line_number,
                            line=preview,
                            preview=preview,
                            truncated=read_result.truncated,
                            score_hint=_score_match(file.path, preview, normalized_query),
                        )
                    )
                    if len(matches) >= max_results:
                        return matches
        return matches

    matches: list[SearchMatch] = []
    for file in list_files(context):
        content = read_file(context, file.path)
        for line_number, line in enumerate(content.splitlines(), start=1):
            if normalized_query in line.casefold():
                matches.append(SearchMatch(file.path, line_number, line.rstrip()))
                if len(matches) >= max_results:
                    return matches
    return matches


def read_file_range(
    request: RepositoryExplorationRequest,
    path: str,
    *,
    line_start: int = 1,
    line_end: int | None = None,
    max_bytes: int | None = None,
) -> FileRangeRead:
    """渐进探索的范围读取工具。

    范围读取是 Agent 的源码访问边界：它必须先做 root_path 校验、默认排除校验、
    文件类型校验和字节预算截断，然后才返回短片段。这样即使 LLM 决定继续探索，
    也只能在受控工具能力内逐步披露代码。
    """

    root = request.resolved_root
    resolved = _resolve_inside_root(root, path)
    relative_path = _relative_path(root, resolved)
    if _is_excluded(relative_path, request.exclude_paths):
        raise ValueError(f"Path is excluded by repository exploration request: {path}")
    if not resolved.is_file():
        raise FileNotFoundError(path)
    if _looks_binary(resolved):
        raise ValueError(f"Binary file is not readable by repository exploration: {path}")

    content = resolved.read_text(encoding="utf-8", errors="replace")
    lines = content.splitlines()
    start = max(line_start, 1)
    end = max(line_end or len(lines), start)
    selected = "\n".join(lines[start - 1 : end])
    encoded = selected.encode("utf-8")
    limit = max_bytes or request.budget.max_bytes
    truncated = len(encoded) > limit
    if truncated:
        selected = encoded[:limit].decode("utf-8", errors="ignore")
        encoded = selected.encode("utf-8")
    return FileRangeRead(
        path=relative_path,
        line_start=start,
        line_end=end,
        content=selected,
        bytes_read=len(encoded),
        truncated=truncated,
    )


def build_context_pack(
    context: RepositoryContext,
    *,
    paths: Sequence[str] | None = None,
    search_queries: Sequence[str] | None = None,
) -> ContextPack:
    """把目标文件和搜索命中文件打包成受文件数、字节数限制的 LLM 上下文。"""
    root = _ensure_repository_root(context)
    selected_paths = _ordered_unique([*context.target_files, *(paths or ())])
    queries = tuple(search_queries or ())

    for query in queries:
        selected_paths.extend(match.path for match in search_text(context, query))
        selected_paths = _ordered_unique(selected_paths)

    if not selected_paths:
        selected_paths = [file.path for file in list_files(context)]

    files: list[ContextFile] = []
    inspected_files: list[str] = []
    total_bytes = 0

    # 上下文打包是给 LLM 的预算控制点：只放入有限数量和有限字节数的文本。
    for relative_path in selected_paths:
        if len(files) >= context.max_files:
            break

        resolved = _resolve_inside_root(root, relative_path)
        normalized_path = _relative_path(root, resolved)
        if not resolved.is_file() or _is_excluded(normalized_path, context.exclude_paths):
            continue

        inspected_files.append(normalized_path)
        content = read_file(context, normalized_path)
        encoded = content.encode("utf-8")
        remaining = context.max_bytes - total_bytes
        if remaining <= 0:
            break

        truncated = len(encoded) > remaining
        if truncated:
            content = encoded[:remaining].decode("utf-8", errors="ignore")
            encoded = content.encode("utf-8")

        files.append(ContextFile(normalized_path, content, truncated))
        total_bytes += len(encoded)

    return ContextPack(
        root_path=str(root),
        files=tuple(files),
        inspected_files=tuple(inspected_files),
        search_queries=queries,
        total_bytes=total_bytes,
    )


def _ensure_repository_root(context: RepositoryContext) -> Path:
    root = context.resolved_root
    if not root.exists() or not root.is_dir():
        raise ValueError(f"Repository root does not exist or is not a directory: {context.root_path}")
    return root


def _resolve_inside_root(root: Path, relative_path: str) -> Path:
    # 路径安全边界：所有传入路径必须解析到 root_path 内，避免 Agent 读取仓库外文件。
    candidate = (root / relative_path).resolve()
    if not candidate.is_relative_to(root):
        raise ValueError(f"Path escapes repository root: {relative_path}")
    return candidate


def _collect_files(
    root: Path,
    current: Path,
    context: RepositoryContext,
    discovered: dict[str, FileMatch],
) -> None:
    if len(discovered) >= context.max_files or not current.exists():
        return

    relative_path = _relative_path(root, current)
    if _is_excluded(relative_path, context.exclude_paths):
        return

    if current.is_file():
        discovered[relative_path] = FileMatch(relative_path, current.stat().st_size)
        return

    if not current.is_dir():
        return

    for child in sorted(current.iterdir(), key=lambda item: item.name.casefold()):
        if len(discovered) >= context.max_files:
            return
        _collect_files(root, child, context, discovered)


def _collect_repository_files(
    root: Path,
    current: Path,
    request: RepositoryExplorationRequest,
    discovered: dict[str, RepositoryFile],
    skipped: dict[str, SkippedFile],
) -> None:
    if not current.exists():
        return

    relative_path = _relative_path(root, current)
    if len(discovered) >= request.budget.max_files:
        skipped.setdefault(
            relative_path,
            SkippedFile(
                path=relative_path,
                reason="BUDGET_EXHAUSTED",
                detail="maxFiles reached before this path could be added",
            ),
        )
        return

    if _is_excluded(relative_path, request.exclude_paths):
        skipped[relative_path] = SkippedFile(
            path=relative_path,
            reason="EXCLUDED",
            detail="default or user exclude rule matched",
        )
        return

    if current.is_file():
        if _looks_binary(current):
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="BINARY",
                detail="binary-like file skipped during repository listing",
            )
            return
        discovered.setdefault(relative_path, _repository_file(root, current))
        return

    if not current.is_dir():
        return

    try:
        children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
    except OSError as exc:
        skipped[relative_path] = SkippedFile(
            path=relative_path,
            reason="READ_ERROR",
            detail=f"cannot list directory during repository listing: {exc}",
        )
        return

    for child in children:
        _collect_repository_files(root, child, request, discovered, skipped)


def _collect_compact_repository_map(
    root: Path,
    current: Path,
    request: RepositoryExplorationRequest,
    discovered: dict[str, RepositoryFile],
    skipped: dict[str, SkippedFile],
    directory_stats: dict[str, dict[str, Any]],
    *,
    max_map_files: int,
    max_directory_depth: int,
) -> None:
    if not current.exists():
        return

    relative_path = _relative_path(root, current)
    if _is_excluded(relative_path, request.exclude_paths):
        skipped[relative_path] = SkippedFile(
            path=relative_path,
            reason="EXCLUDED",
            detail="default or user exclude rule matched",
        )
        return

    if len(discovered) >= max_map_files:
        skipped.setdefault(
            relative_path,
            SkippedFile(
                path=relative_path,
                reason="BUDGET_EXHAUSTED",
                detail="compact repository map reached max_map_files",
            ),
        )
        return

    if current.is_file():
        if _has_binary_suffix(current):
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="BINARY",
                detail="binary-like suffix skipped during compact repository map",
            )
            return
        file = _repository_file(root, current)
        discovered.setdefault(relative_path, file)
        _record_directory_file(directory_stats, relative_path, file.language, max_directory_depth)
        return

    if not current.is_dir():
        return

    if _path_depth(relative_path) <= max_directory_depth:
        stats = directory_stats.setdefault(
            relative_path,
            {"file_count": 0, "child_directories": set(), "languages": {}},
        )
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="READ_ERROR",
                detail=f"cannot list directory during compact repository map: {exc}",
            )
            return
        for child in children:
            if child.is_dir():
                stats["child_directories"].add(child.name)
    else:
        try:
            children = sorted(current.iterdir(), key=lambda item: item.name.casefold())
        except OSError as exc:
            skipped[relative_path] = SkippedFile(
                path=relative_path,
                reason="READ_ERROR",
                detail=f"cannot list directory during compact repository map: {exc}",
            )
            return

    for child in children:
        _collect_compact_repository_map(
            root,
            child,
            request,
            discovered,
            skipped,
            directory_stats,
            max_map_files=max_map_files,
            max_directory_depth=max_directory_depth,
        )


def _record_directory_file(
    directory_stats: dict[str, dict[str, Any]],
    relative_file_path: str,
    language: str,
    max_directory_depth: int,
) -> None:
    parent = Path(relative_file_path).parent.as_posix()
    if parent == ".":
        parent = "."
    if _path_depth(parent) > max_directory_depth:
        return
    stats = directory_stats.setdefault(
        parent,
        {"file_count": 0, "child_directories": set(), "languages": {}},
    )
    stats["file_count"] = int(stats.get("file_count", 0)) + 1
    languages = stats.setdefault("languages", {})
    languages[language] = int(languages.get(language, 0)) + 1


def _is_excluded(relative_path: str, exclude_paths: Iterable[str]) -> bool:
    normalized = _normalize_path(relative_path)
    for exclude_path in _combined_exclude_paths(exclude_paths):
        excluded = _normalize_path(exclude_path)
        if normalized == excluded or normalized.startswith(f"{excluded}/"):
            return True
        if fnmatch(normalized, excluded) or fnmatch(Path(normalized).name, excluded):
            return True
    return False


def _relative_path(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def _normalize_paths(paths: Any) -> tuple[str, ...]:
    if not paths:
        return ()
    if isinstance(paths, str):
        return (_normalize_path(paths),)
    return tuple(_normalize_path(str(path)) for path in paths if str(path).strip())


def _normalize_path(path: str) -> str:
    normalized = path.replace("\\", "/").strip()
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized.rstrip("/") or "."


def _ordered_unique(paths: Iterable[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for path in paths:
        normalized = _normalize_path(path)
        if normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _combined_exclude_paths(exclude_paths: Iterable[str]) -> tuple[str, ...]:
    return tuple(_ordered_unique([*DEFAULT_EXCLUDE_PATHS, *exclude_paths]))


def _positive_int(value: Any, default: int, field_name: str) -> int:
    if value is None:
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be a positive integer")
    return parsed


def _repository_file(root: Path, path: Path, priority_hint: str = "") -> RepositoryFile:
    return RepositoryFile(
        path=_relative_path(root, path),
        size_bytes=path.stat().st_size,
        language=_language_for(path),
        priority_hint=priority_hint or _priority_hint_for(path),
    )


def _language_for(path: Path) -> str:
    suffix = path.suffix.casefold()
    return {
        ".py": "python",
        ".java": "java",
        ".ts": "typescript",
        ".tsx": "typescript",
        ".js": "javascript",
        ".json": "json",
        ".md": "markdown",
        ".yml": "yaml",
        ".yaml": "yaml",
    }.get(suffix, suffix.lstrip(".") or "text")


def _priority_hint_for(path: Path) -> str:
    normalized = path.as_posix().casefold()
    if "test" in normalized:
        return "test"
    if any(token in normalized for token in ("agent", "worker", "service", "controller")):
        return "runtime"
    if path.suffix.casefold() in (".md", ".yml", ".yaml", ".json"):
        return "metadata"
    return "source"


def _is_high_signal_path(path: str, priority_hint: str = "") -> bool:
    normalized = path.casefold()
    if priority_hint == "target":
        return True
    high_signal_tokens = (
        "agent",
        "workflow",
        "worker",
        "activity",
        "service",
        "controller",
        "repository",
        "client",
        "provider",
        "router",
        "handler",
        "config",
        "schema",
        "model",
        "test",
        "spec",
    )
    important_names = (
        "readme.md",
        "pom.xml",
        "build.gradle",
        "settings.gradle",
        "package.json",
        "pyproject.toml",
        "requirements.txt",
        "docker-compose.yml",
        "docker-compose.yaml",
    )
    return any(token in normalized for token in high_signal_tokens) or Path(normalized).name in important_names


def _is_entrypoint_path(path: str) -> bool:
    normalized = path.casefold()
    name = Path(normalized).name
    return name in {
        "main.py",
        "app.py",
        "application.java",
        "main.java",
        "index.ts",
        "main.ts",
        "server.ts",
        "package.json",
        "pom.xml",
        "docker-compose.yml",
        "docker-compose.yaml",
    } or normalized.endswith("/application.java")


def _has_binary_suffix(path: Path) -> bool:
    return path.suffix.casefold() in {
        ".png",
        ".jpg",
        ".jpeg",
        ".gif",
        ".ico",
        ".pdf",
        ".zip",
        ".gz",
        ".tar",
        ".jar",
        ".class",
        ".exe",
        ".dll",
        ".so",
        ".dylib",
    }


def _path_depth(relative_path: str) -> int:
    normalized = _normalize_path(relative_path)
    if normalized == ".":
        return 0
    return normalized.count("/") + 1


def _score_match(path: str, line: str, normalized_query: str) -> float:
    score = 0.5
    if normalized_query in path.casefold():
        score += 0.3
    if normalized_query in line.casefold():
        score += 0.2
    return min(score, 1.0)


def _looks_binary(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sample = handle.read(2048)
    except OSError:
        return True
    return b"\x00" in sample
