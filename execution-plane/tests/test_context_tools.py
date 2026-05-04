
from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree

from src.context import (
    DEFAULT_EXCLUDE_PATHS,
    DEFAULT_MAX_BYTES,
    DEFAULT_MAX_FILES,
    DEFAULT_MAX_ROUNDS,
    BudgetUsage,
    CodeContextSummary,
    EvidenceItem,
    ExplorationSession,
    ExplorationStep,
    RepositoryExplorationRequest,
    RepositoryContext,
    SkippedFile,
    build_context_pack,
    list_files,
    read_file,
    search_text,
)


class RepositoryContextToolsTest(unittest.TestCase):
    @contextmanager
    def temporary_repository(self):
        scratch_dir = Path(__file__).resolve().parents[1] / ".test_tmp"
        scratch_dir.mkdir(exist_ok=True)
        repo_dir = scratch_dir / f"repo-{uuid.uuid4().hex}"
        repo_dir.mkdir()
        try:
            yield str(repo_dir)
        finally:
            rmtree(repo_dir, ignore_errors=True)

    def test_repository_exploration_request_defaults_and_budget(self):
        with self.temporary_repository() as repo_dir:
            request = RepositoryExplorationRequest.from_mapping({"rootPath": repo_dir})

            self.assertEqual(request.resolved_root, Path(repo_dir).resolve())
            self.assertEqual(request.include_paths, ())
            self.assertEqual(request.effective_include_paths, (".",))
            self.assertIn("node_modules", DEFAULT_EXCLUDE_PATHS)
            self.assertIn("logs", DEFAULT_EXCLUDE_PATHS)
            self.assertGreaterEqual(request.budget.max_rounds, DEFAULT_MAX_ROUNDS)
            self.assertEqual(request.budget.max_files, DEFAULT_MAX_FILES)
            self.assertEqual(request.budget.max_bytes, DEFAULT_MAX_BYTES)
            self.assertEqual(BudgetUsage().rounds_used, 0)

    def test_repository_exploration_request_rejects_invalid_or_escaping_paths(self):
        with self.temporary_repository() as repo_dir:
            missing_root = Path(repo_dir) / "missing"
            with self.assertRaises(ValueError):
                RepositoryExplorationRequest.from_mapping({"rootPath": str(missing_root)})

            with self.assertRaises(ValueError):
                RepositoryExplorationRequest.from_mapping(
                    {"rootPath": repo_dir, "includePaths": ["../outside"]}
                )

            with self.assertRaises(ValueError):
                RepositoryExplorationRequest.from_mapping(
                    {"rootPath": repo_dir, "targetFiles": ["../outside.txt"]}
                )

    def test_repository_exploration_request_exposes_progressive_entities(self):
        with self.temporary_repository() as repo_dir:
            request = RepositoryExplorationRequest.from_mapping(
                {
                    "rootPath": repo_dir,
                    "maxRounds": 2,
                    "maxFiles": 3,
                    "maxBytes": 128,
                    "maxSearchResults": 5,
                    "privacyMode": "strict",
                }
            )

            step = ExplorationStep(
                step_index=1,
                round_index=1,
                action_type="PLAN",
                reason="根据需求生成初始探索计划",
                result_summary="优先搜索 health 和 worker",
            )
            evidence = EvidenceItem(
                file_path="src/health_service.py",
                relevance_reason="健康检查需求的入口模块",
                supports=("健康检查",),
            )
            skipped = SkippedFile(path="logs/runtime.log", reason="EXCLUDED")
            summary = CodeContextSummary(
                root_path=str(request.resolved_root),
                search_queries=("health",),
                inspected_files=("src/health_service.py",),
                evidence=(evidence,),
                skipped_paths=(skipped,),
                budget_usage=BudgetUsage(rounds_used=1, files_read=1),
                confidence=0.75,
            )
            session = ExplorationSession(
                session_id="pipeline-1:REQUIREMENT_ANALYSIS",
                pipeline_id="pipeline-1",
                stage_name="REQUIREMENT_ANALYSIS",
                requirement="增加健康检查页面",
                status="PLANNED",
                budget=request.budget,
            )

            self.assertEqual(request.privacy_mode, "strict")
            self.assertEqual(request.budget.max_rounds, 2)
            self.assertEqual(request.budget.max_search_results, 5)
            self.assertEqual(step.action_type, "PLAN")
            self.assertEqual(summary.evidence[0].supports, ("健康检查",))
            self.assertEqual(session.status, "PLANNED")

    def test_lists_reads_searches_and_packs_temporary_repository(self):
        with self.temporary_repository() as repo_dir:
            root = Path(repo_dir)
            (root / "src").mkdir()
            (root / "docs").mkdir()
            (root / "target").mkdir()
            (root / "src" / "service.py").write_text(
                "class PipelineService:\n    pass\n", encoding="utf-8"
            )
            (root / "docs" / "design.md").write_text(
                "# Design\nPipelineService reads repository context.\n", encoding="utf-8"
            )
            (root / "target" / "generated.txt").write_text(
                "PipelineService should be ignored\n", encoding="utf-8"
            )

            context = RepositoryContext(
                root_path=root,
                include_paths=("src", "docs"),
                exclude_paths=("target",),
                target_files=("docs/design.md",),
                max_files=10,
                max_bytes=80,
            )

            files = list_files(context)
            self.assertEqual([file.path for file in files], ["docs/design.md", "src/service.py"])

            self.assertEqual(
                read_file(context, "src/service.py", start_line=1, end_line=1),
                "class PipelineService:",
            )

            matches = search_text(context, "pipelineservice")
            self.assertEqual({match.path for match in matches}, {"docs/design.md", "src/service.py"})
            self.assertEqual(matches[0].line_number, 2)

            pack = build_context_pack(
                context,
                paths=("src/service.py",),
                search_queries=("repository context",),
            )

            self.assertLessEqual(pack.total_bytes, 80)
            self.assertEqual(pack.search_queries, ("repository context",))
            self.assertEqual(
                {context_file.path for context_file in pack.files},
                {"docs/design.md", "src/service.py"},
            )

    def test_rejects_paths_outside_repository_root(self):
        with self.temporary_repository() as repo_dir:
            context = RepositoryContext(root_path=Path(repo_dir))

            with self.assertRaises(ValueError):
                read_file(context, "../outside.txt")

    def test_tools_can_inspect_the_actual_project_repository(self):
        repo_root = Path(__file__).resolve().parents[2]
        context = RepositoryContext(
            root_path=repo_root,
            include_paths=(
                "specs/001-devflow-engine",
                "control-plane/devflow-engine/src/main/java/com/devflow/engine/api",
            ),
            exclude_paths=(".git", "node_modules", "target", "venv", "__pycache__"),
            target_files=(
                "specs/001-devflow-engine/tasks.md",
                "control-plane/devflow-engine/src/main/java/com/devflow/engine/api/RepositoryContext.java",
            ),
            max_files=50,
            max_bytes=200_000,
        )

        files = list_files(context)
        file_paths = {file.path for file in files}
        self.assertIn("specs/001-devflow-engine/tasks.md", file_paths)
        self.assertIn(
            "control-plane/devflow-engine/src/main/java/com/devflow/engine/api/RepositoryContext.java",
            file_paths,
        )

        java_source = read_file(
            context,
            "control-plane/devflow-engine/src/main/java/com/devflow/engine/api/RepositoryContext.java",
        )
        self.assertIn("RepositoryContext", java_source)

        matches = search_text(context, "RepositoryContext")
        self.assertTrue(
            any(match.path.endswith("RepositoryContext.java") for match in matches),
            "应能在真实项目代码中检索到 RepositoryContext.java",
        )


if __name__ == "__main__":
    unittest.main()
