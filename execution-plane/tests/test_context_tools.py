
from __future__ import annotations

import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from shutil import rmtree

from src.context import (
    RepositoryContext,
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
