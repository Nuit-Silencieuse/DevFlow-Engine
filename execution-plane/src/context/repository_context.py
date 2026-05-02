from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any


DEFAULT_MAX_FILES = 200
DEFAULT_MAX_BYTES = 1_048_576


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
class SearchMatch:
    path: str
    line_number: int
    line: str


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
    context: RepositoryContext,
    query: str,
    *,
    max_results: int = 20,
) -> list[SearchMatch]:
    """在已纳入上下文范围的文本文件中执行大小写不敏感搜索。"""
    normalized_query = query.casefold()
    if not normalized_query:
        return []

    matches: list[SearchMatch] = []
    for file in list_files(context):
        content = read_file(context, file.path)
        for line_number, line in enumerate(content.splitlines(), start=1):
            if normalized_query in line.casefold():
                matches.append(SearchMatch(file.path, line_number, line.rstrip()))
                if len(matches) >= max_results:
                    return matches
    return matches


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


def _is_excluded(relative_path: str, exclude_paths: Iterable[str]) -> bool:
    normalized = _normalize_path(relative_path)
    for exclude_path in exclude_paths:
        excluded = _normalize_path(exclude_path)
        if normalized == excluded or normalized.startswith(f"{excluded}/"):
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
