"""Tests for the Analyzer."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from pydepend.analyzer import Analyzer, _collect_imports, _module_name_from_path


class TestModuleNameFromPath:
    def test_simple_file(self, tmp_path: Path) -> None:
        f = tmp_path / "mymodule.py"
        f.touch()
        assert _module_name_from_path(f, tmp_path) == "mymodule"

    def test_nested_file(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        f = tmp_path / "pkg" / "sub.py"
        f.touch()
        assert _module_name_from_path(f, tmp_path) == "pkg.sub"

    def test_init_file(self, tmp_path: Path) -> None:
        (tmp_path / "pkg").mkdir()
        f = tmp_path / "pkg" / "__init__.py"
        f.touch()
        assert _module_name_from_path(f, tmp_path) == "pkg"


class TestCollectImports:
    def test_import_statement(self) -> None:
        src = "import os\nimport sys\n"
        imports = _collect_imports(src, "mymod")
        assert ("os", 1) in imports
        assert ("sys", 2) in imports

    def test_from_import(self) -> None:
        src = "from pathlib import Path\n"
        imports = _collect_imports(src, "mymod")
        assert ("pathlib", 1) in imports

    def test_relative_import(self) -> None:
        src = "from . import utils\n"
        imports = _collect_imports(src, "pkg.sub")
        names = [name for name, _ in imports]
        assert "pkg.utils" in names

    def test_syntax_error_returns_empty(self) -> None:
        src = "def broken("
        imports = _collect_imports(src, "mymod")
        assert imports == []


class TestAnalyzer:
    def _make_project(self, tmp_path: Path, files: dict[str, str]) -> Path:
        for rel, content in files.items():
            p = tmp_path / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(textwrap.dedent(content))
        return tmp_path

    def test_simple_dependency(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/a.py": "from pkg import b\n",
                "pkg/b.py": "x = 1\n",
            },
        )
        graph = Analyzer(tmp_path).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "pkg" in mod_names
        assert "pkg.a" in mod_names
        assert "pkg.b" in mod_names

        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("pkg.a", "pkg") in dep_pairs or ("pkg.a", "pkg.b") in dep_pairs

    def test_external_imports_excluded(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "mod.py": "import os\nimport sys\n",
            },
        )
        graph = Analyzer(tmp_path).analyze()
        # os and sys are external – should not appear in modules or dependencies
        mod_names = {m.name for m in graph.modules}
        assert "os" not in mod_names
        assert len(graph.dependencies) == 0

    def test_multiple_modules(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "a.py": "import b\n",
                "b.py": "import c\n",
                "c.py": "x = 1\n",
            },
        )
        graph = Analyzer(tmp_path).analyze()
        assert len(graph.modules) == 3
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("a", "b") in dep_pairs
        assert ("b", "c") in dep_pairs

    def test_hidden_directories_skipped(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                ".hidden/secret.py": "import os\n",
                "visible.py": "x = 1\n",
            },
        )
        graph = Analyzer(tmp_path).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "visible" in mod_names
        # .hidden should be skipped
        assert not any(".hidden" in n or "secret" in n for n in mod_names)

    def test_pycache_skipped(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "__pycache__/cached.py": "pass\n",
                "real.py": "x = 1\n",
            },
        )
        graph = Analyzer(tmp_path).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "real" in mod_names
        assert "cached" not in mod_names
