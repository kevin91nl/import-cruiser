"""Tests for the Analyzer."""

from __future__ import annotations

import ast
import textwrap
from pathlib import Path


from import_cruiser.analyzer import (
    Analyzer,
    _count_loc,
    _collect_http_hosts,
    _http_host_from_text,
    _http_host_from_expr,
    _is_url_like_target,
    _render_joined_str,
    _collect_imports,
    _header_lines,
    _module_name_from_path,
)


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

    def test_nested_imports_in_function_and_try_except(self) -> None:
        src = (
            "def run():\n"
            "    import os\n"
            "    from pkg import util\n"
            "    try:\n"
            "        import httpx\n"
            "    except ImportError:\n"
            "        import requests\n"
        )
        imports = _collect_imports(src, "mymod")
        names = {name for name, _ in imports}
        assert "os" in names
        assert "pkg" in names
        assert "httpx" in names
        assert "requests" in names


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
        # os and sys are external.
        # They should not appear in modules or dependencies.
        mod_names = {m.name for m in graph.modules}
        assert "os" not in mod_names
        assert len(graph.dependencies) == 0

    def test_db_external_imports_included_when_configured(
        self,
        tmp_path: Path,
    ) -> None:
        self._make_project(
            tmp_path,
            {
                "mod.py": "import sqlalchemy.orm\nimport psycopg\n",
            },
        )
        graph = Analyzer(
            tmp_path,
            include_external_patterns=[
                r"(^|\.)sqlalchemy(\.|$)",
                r"(^|\.)psycopg(\.|$)",
            ],
        ).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "sqlalchemy" in mod_names
        assert "psycopg" in mod_names
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("mod", "sqlalchemy") in dep_pairs
        assert ("mod", "psycopg") in dep_pairs

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

    def test_imports_inside_function_and_try_except_are_included(
        self,
        tmp_path: Path,
    ) -> None:
        self._make_project(
            tmp_path,
            {
                "pkg/__init__.py": "",
                "pkg/main.py": (
                    "def run():\n"
                    "    import pkg.helper\n"
                    "    try:\n"
                    "        from pkg import extra\n"
                    "    except ImportError:\n"
                    "        import pkg.fallback\n"
                ),
                "pkg/helper.py": "x = 1\n",
                "pkg/extra.py": "x = 1\n",
                "pkg/fallback.py": "x = 1\n",
            },
        )
        graph = Analyzer(tmp_path).analyze()
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("pkg.main", "pkg.helper") in dep_pairs
        assert ("pkg.main", "pkg") in dep_pairs or (
            "pkg.main",
            "pkg.extra",
        ) in dep_pairs
        assert ("pkg.main", "pkg.fallback") in dep_pairs

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

    def test_include_path_prefilters_file_walk(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "pkg/keep.py": "x = 1\n",
                "pkg/skip.py": "x = 2\n",
            },
        )
        graph = Analyzer(tmp_path, include_paths=[r"keep\.py$"]).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "pkg.keep" in mod_names
        assert "pkg.skip" not in mod_names

    def test_exclude_path_prefilters_file_walk(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "pkg/keep.py": "x = 1\n",
                "pkg/skip.py": "x = 2\n",
            },
        )
        graph = Analyzer(tmp_path, exclude_paths=[r"skip\.py$"]).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "pkg.keep" in mod_names
        assert "pkg.skip" not in mod_names

    def test_http_hosts_detected_when_enabled(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "api_client.py": (
                    "import requests\n"
                    "requests.get('https://api.github.com/users')\n"
                    "requests.post('http://example.org/v1/items')\n"
                )
            },
        )
        graph = Analyzer(tmp_path, include_http_hosts=True).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "api.github.com" in mod_names
        assert "example.org" in mod_names
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("api_client", "api.github.com") in dep_pairs
        assert ("api_client", "example.org") in dep_pairs

    def test_http_hosts_detected_from_url_variable(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "client.py": (
                    "import httpx\n"
                    "url = 'https://api.c99.nl/subdomainfinder'\n"
                    "async def run():\n"
                    "    async with httpx.AsyncClient() as c:\n"
                    "        await c.get(url)\n"
                )
            },
        )
        graph = Analyzer(tmp_path, include_http_hosts=True).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "api.c99.nl" in mod_names
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("client", "api.c99.nl") in dep_pairs

    def test_http_hosts_detected_from_fstring_variable(self, tmp_path: Path) -> None:
        self._make_project(
            tmp_path,
            {
                "client.py": (
                    "import httpx\n"
                    "ip = '1.1.1.1'\n"
                    "url = f'https://api.shodan.io/shodan/host/{ip}'\n"
                    "async def run():\n"
                    "    async with httpx.AsyncClient() as c:\n"
                    "        await c.get(url)\n"
                )
            },
        )
        graph = Analyzer(tmp_path, include_http_hosts=True).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "api.shodan.io" in mod_names
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("client", "api.shodan.io") in dep_pairs

    def test_http_hosts_detected_from_base_url_variable_in_fstring(
        self,
        tmp_path: Path,
    ) -> None:
        self._make_project(
            tmp_path,
            {
                "client.py": (
                    "_LOGO_DEV_BASE_URL = 'https://img.logo.dev'\n"
                    "domain = 'example.com'\n"
                    "url = f'{_LOGO_DEV_BASE_URL}/{domain}'\n"
                    "x = url\n"
                )
            },
        )
        graph = Analyzer(tmp_path, include_http_hosts=True).analyze()
        mod_names = {m.name for m in graph.modules}
        assert "img.logo.dev" in mod_names
        dep_pairs = {(d.source, d.target) for d in graph.dependencies}
        assert ("client", "img.logo.dev") in dep_pairs


def test_source_root_detection(tmp_path: Path) -> None:
    src = tmp_path / "proj" / "src" / "pkg"
    src.mkdir(parents=True)
    (src / "__init__.py").write_text("")
    (src / "mod.py").write_text("import pkg\n")

    graph = Analyzer(tmp_path).analyze()
    assert "pkg.mod" in {m.name for m in graph.modules}


def test_collect_http_hosts_handles_syntax_error() -> None:
    assert _collect_http_hosts("def broken(") == []


def test_collect_http_hosts_detects_annotated_and_scoped_urls() -> None:
    source = (
        "url: str = 'https://api.example.com/v1'\n"
        "class C:\n"
        "    endpoint = 'https://class.example.com/v1'\n"
        "def fn():\n"
        "    uri = 'https://fn.example.com/v1'\n"
        "    return uri\n"
    )
    hosts = {host for host, _ in _collect_http_hosts(source)}
    assert "api.example.com" in hosts
    assert "class.example.com" in hosts
    assert "fn.example.com" in hosts


def test_http_host_text_edge_cases() -> None:
    assert _http_host_from_text("https:///path") is None
    assert _http_host_from_text("https://:443/path") is None


def test_render_joined_str_and_url_target_edges() -> None:
    scheme_only = _render_joined_str(
        ast.parse("f'https://{host}'", mode="eval").body,
        [{"host": "api.shodan.io"}],
    )
    assert scheme_only == "https://api.shodan.io"

    resolved = _render_joined_str(
        ast.parse("f'https://api.{domain}/v1'", mode="eval").body,
        [{"domain": "shodan.io"}],
    )
    assert resolved == "https://api.shodan.io/v1"

    invalid_joined = ast.JoinedStr(values=[ast.Name(id="value", ctx=ast.Load())])
    assert _render_joined_str(invalid_joined, [{"value": "x"}]) is None
    assert _http_host_from_expr(invalid_joined, [{"value": "x"}]) is None
    assert _http_host_from_expr(ast.parse("1 + 2", mode="eval").body, []) is None
    assert not _is_url_like_target(
        ast.Attribute(
            value=ast.Name(id="x", ctx=ast.Load()),
            attr="url",
            ctx=ast.Load(),
        )
    )


def test_count_loc_excludes_headers_docstrings_and_comments() -> None:
    source = (
        '"""module doc"""\n'
        "import os\n"
        "\n"
        "class A:\n"
        '    """class doc"""\n'
        "    def run(self):\n"
        '        """fn doc"""\n'
        "        # comment\n"
        "        x = 1\n"
        "        if x:\n"
        "            y = 2\n"
        "        return y\n"
    )
    assert _count_loc(source) == 5


def test_count_loc_returns_zero_for_invalid_python() -> None:
    assert _count_loc("def broken(:\n    pass\n") == 0


def test_header_lines_skips_nodes_without_start_line() -> None:
    function_node = ast.FunctionDef(
        name="f",
        args=ast.arguments(
            posonlyargs=[],
            args=[],
            kwonlyargs=[],
            kw_defaults=[],
            defaults=[],
        ),
        body=[ast.Pass(lineno=2, col_offset=0)],
        decorator_list=[],
        lineno=0,
        col_offset=0,
    )
    module = ast.Module(body=[function_node], type_ignores=[])
    assert _header_lines(module) == set()
