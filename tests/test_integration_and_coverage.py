from __future__ import annotations

import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from click.testing import CliRunner

from import_cruiser import cli
from import_cruiser.analyzer import (
    _collect_imports,
    _contains_python_files,
    _resolve_internal,
)
from import_cruiser.config import ConfigError, validate_config
from import_cruiser.detector import detect_cycles
from import_cruiser.exporter import (
    _add_svg_padding,
    _build_clusters,
    _dot_node_lines,
    _non_empty_clusters,
    _render_cluster_tree,
    _edges_in_cycles,
    _node_cluster_key,
    _cluster_parts,
    _common_root as exporter_common_root,
    _html_with_fallback,
    _render_dot,
    _style_attrs,
    export_dot,
    export_html,
    export_svg,
)
from import_cruiser.graph import (
    Dependency,
    DependencyGraph,
    Module,
    _collapse_name,
    _common_root,
    _expand_focus,
    _group_key,
    _match_path,
    aggregate_by_path,
    filter_graph,
)
from import_cruiser.validator import Violation


@pytest.fixture()
def mini_project(tmp_path: Path) -> Path:
    pkg = tmp_path / "pkg"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "a.py").write_text("import pkg.b\n")
    (pkg / "b.py").write_text("import pkg.c\n")
    (pkg / "c.py").write_text("x = 1\n")
    return tmp_path


def _graph_with_modules() -> DependencyGraph:
    graph = DependencyGraph()
    graph.add_module(Module(name="pkg.a", path="/tmp/pkg/a.py"))
    graph.add_module(Module(name="pkg.b", path="/tmp/pkg/b.py"))
    graph.add_module(Module(name="pkg.c", path="/tmp/pkg/c.py"))
    graph.add_dependency(Dependency(source="pkg.a", target="pkg.b", line=2))
    graph.add_dependency(Dependency(source="pkg.b", target="pkg.c", line=3))
    return graph


@pytest.mark.parametrize(
    ("fmt", "expected"),
    [
        ("flake8", "pkg/b.py:1:1: IC001"),
        ("pylint", "pkg/b.py:1: [IC001]"),
        ("github", "::error file=pkg/b.py,line=1,col=1::"),
    ],
)
def test_validate_linter_output_integration(
    fmt: str, expected: str, tmp_path: Path
) -> None:
    project = tmp_path / "project"
    pkg = project / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "__init__.py").write_text("")
    (pkg / "b.py").write_text("import pkg.c\n")
    (pkg / "c.py").write_text("x = 1\n")

    cfg = tmp_path / "cfg.json"
    cfg.write_text(
        '{"rules":[{"name":"no-b-to-c","severity":"error","from":{"path":"b$"},"to":{"path":"c$"},"allow":false}]}'
    )

    env = os.environ.copy()
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "src")
    proc = subprocess.run(
        [
            sys.executable,
            "-m",
            "import_cruiser.cli",
            "validate",
            str(project),
            "--config",
            str(cfg),
            "--strict",
            "--output-format",
            fmt,
        ],
        cwd=str(Path(__file__).resolve().parents[1]),
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert proc.returncode == 1
    assert expected in proc.stdout


def test_cli_analyze_svg_and_html_branches(
    mini_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph_with_modules()
    monkeypatch.setattr(cli.Analyzer, "analyze", lambda self: graph)
    monkeypatch.setattr(cli, "_export_svg", lambda *args, **kwargs: "<svg/>")
    monkeypatch.setattr(cli, "export_html", lambda *args, **kwargs: "<html/>")

    runner = CliRunner()
    svg_result = runner.invoke(
        cli.main, ["analyze", str(mini_project), "--format", "svg"]
    )
    html_result = runner.invoke(
        cli.main, ["analyze", str(mini_project), "--format", "html"]
    )

    assert svg_result.exit_code == 0
    assert "<svg/>" in svg_result.output
    assert html_result.exit_code == 0
    assert "<html/>" in html_result.output


def test_cli_export_svg_and_html_branches(
    mini_project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    graph = _graph_with_modules()
    monkeypatch.setattr(cli.Analyzer, "analyze", lambda self: graph)
    monkeypatch.setattr(cli, "_export_svg", lambda *args, **kwargs: "<svg/>")
    monkeypatch.setattr(cli, "export_html", lambda *args, **kwargs: "<html/>")

    runner = CliRunner()
    svg_result = runner.invoke(
        cli.main, ["export", str(mini_project), "--format", "svg"]
    )
    html_result = runner.invoke(
        cli.main, ["export", str(mini_project), "--format", "html"]
    )

    assert svg_result.exit_code == 0
    assert "<svg/>" in svg_result.output
    assert html_result.exit_code == 0
    assert "<html/>" in html_result.output


def test_cli_helper_edge_cases(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    graph = _graph_with_modules()

    assert cli._extract_rules({"rules": "x"}) == []
    assert cli._format_lint_output([], graph, tmp_path, "flake8") == ""

    external = cli._to_display_path("/definitely/outside/file.py", tmp_path)
    assert external.endswith("/definitely/outside/file.py")

    bad_cfg = tmp_path / "bad.json"
    bad_cfg.write_text("{bad")
    with pytest.raises(SystemExit):
        cli._load_violations(str(bad_cfg), graph)

    good_cfg = tmp_path / "good.json"
    good_cfg.write_text(
        '{"rules":[{"name":"no-b-to-c","severity":"error","from":{"path":"b$"},"to":{"path":"c$"},"allow":false}]}'
    )
    loaded = cli._load_violations(str(good_cfg), graph)
    assert len(loaded) == 1

    monkeypatch.setattr(
        cli, "export_svg", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
    )
    with pytest.raises(SystemExit):
        cli._export_svg(graph)


def test_apply_graph_options_archi_and_auto_cluster() -> None:
    graph = DependencyGraph()
    base = Path(__file__).resolve().parents[1]
    graph.add_module(Module(name="pkg.a", path=str(base / "src" / "pkg" / "a.py")))
    graph.add_module(Module(name="pkg.b", path=str(base / "src" / "pkg" / "b.py")))
    graph.add_dependency(Dependency(source="pkg.a", target="pkg.b", line=1))

    _, layout, rankdir, cluster_depth, cluster_mode, style, edge_mode = (
        cli._apply_graph_options(
            graph,
            include=[],
            exclude=[],
            include_paths=[],
            exclude_paths=[],
            focus=[],
            focus_depth=1,
            collapse_depth=0,
            cluster_depth=0,
            cluster_mode="module",
            aggregate_depth=0,
            leaf_patterns=[],
            layout="neato",
            rankdir="LR",
            style="archi",
            edge_mode="auto",
        )
    )

    assert layout == "dot"
    assert rankdir == "TB"
    assert cluster_mode == "path"
    assert style == "archi"
    assert cluster_depth == 20
    assert edge_mode == "cluster"


def test_run_module_main_guard(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PYTHONPATH", str(Path(__file__).resolve().parents[1] / "src"))
    monkeypatch.setattr(sys, "argv", ["import-cruiser", "--version"])
    with pytest.raises(SystemExit):
        runpy.run_module("import_cruiser.cli", run_name="__main__")


def test_analyzer_edge_cases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    import ast

    node = ast.ImportFrom(module=None, names=[], level=0)
    node.lineno = 1
    monkeypatch.setattr(
        "import_cruiser.analyzer.ast.parse", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr("import_cruiser.analyzer.ast.walk", lambda tree: [node])
    assert _collect_imports("x", "pkg.mod") == []
    monkeypatch.undo()

    imports = _collect_imports("from ..name import thing\n", "pkg.mod")
    assert ("name", 1) in imports

    monkeypatch.setattr("import_cruiser.analyzer.ast.walk", lambda tree: [])
    fallback_imports = _collect_imports(
        "from pkg import x\nfrom ..rel import y\nfrom . import z\n",
        "pkg.mod",
    )
    assert ("rel", 2) in fallback_imports
    monkeypatch.undo()

    assert _resolve_internal("pkg", {"pkg.mod"}) == "pkg"
    assert _resolve_internal("a.b.c", {"a.b"}) == "a.b"

    empty = tmp_path / "empty"
    empty.mkdir()
    assert _contains_python_files(empty) is False


def test_collect_imports_fallback_parser_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "import_cruiser.analyzer.ast.parse", lambda *args, **kwargs: object()
    )
    monkeypatch.setattr("import_cruiser.analyzer.ast.walk", lambda tree: [])

    assert _collect_imports("from pkg import name\n", "pkg.mod") == []
    assert _collect_imports("from .. import name\n", "pkg.mod") == []
    assert _collect_imports("from ..name import thing\n", "pkg") == [("name", 1)]


def test_graph_edge_cases(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    module = Module("a", "")
    dep = Dependency("a", "b")
    assert module.__eq__(1) is NotImplemented
    assert dep.__eq__(1) is NotImplemented

    graph = DependencyGraph()
    p = tmp_path / "pkg"
    p.mkdir()
    (p / "a.py").write_text("x=1\n")
    (p / "b.py").write_text("x=1\n")
    graph.add_module(Module("a", str(p / "a.py")))
    graph.add_module(Module("b", str(p / "b.py")))
    graph.add_dependency(Dependency("a", "b", 1))

    filtered = filter_graph(graph, exclude_paths=[r"b\.py$"])
    assert all(m.name != "b" for m in filtered.modules)

    assert aggregate_by_path(graph, 0) is graph

    graph_empty_path = DependencyGraph()
    graph_empty_path.add_module(Module("x", ""))
    assert aggregate_by_path(graph_empty_path, 2) is graph_empty_path

    with_leaf = aggregate_by_path(graph, 2, leaf_patterns=[])
    assert with_leaf.modules

    monkeypatch.setattr("import_cruiser.graph._group_key", lambda *args, **kwargs: "")
    with_skipped_group = aggregate_by_path(graph, 2, leaf_patterns=[])
    assert with_skipped_group.modules == []
    monkeypatch.undo()

    assert _expand_focus(graph, set(), {"a", "b"}, 1) == set()
    assert _expand_focus(graph, {"a"}, {"a", "b"}, 0) == {"a"}
    assert _expand_focus(graph, {"a"}, {"a", "b"}, 3) == {"a", "b"}
    assert _expand_focus(graph, {"a"}, {"a"}, 3) == {"a"}

    assert cli._lint_code("unknown") == "IC001"
    assert cli._github_level("unknown") == "error"

    assert cli._to_display_path(str(p / "a.py"), p) == "a.py"

    assert _match_path("", None) == ""
    monkeypatch.setattr(
        "import_cruiser.graph.os.path.relpath",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("x")),
    )
    assert _match_path("abc", "root") == "abc"

    assert _common_root([]) is None
    monkeypatch.setattr(
        "import_cruiser.graph.os.path.commonpath",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("x")),
    )
    assert _common_root([Module("a", "/x.py")]) is None

    assert _match_path("abc", None) == "abc"
    assert _collapse_name("a", 3) == "a"

    root = tmp_path / "root"
    root.mkdir()
    file_path = root / "x.py"
    file_path.write_text("x=1\n")
    assert _group_key(str(file_path), str(root), 2) == root.name


def test_config_and_detector_edge_cases() -> None:
    with pytest.raises(ConfigError):
        validate_config({"rules": [1]})

    graph = DependencyGraph()
    graph.add_module(Module(name="a", path="a.py"))
    graph.add_dependency(Dependency(source="a", target="external", line=1))
    assert detect_cycles(graph) == []


def test_exporter_error_and_style_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    graph = DependencyGraph()
    graph.add_module(Module(name="a", path="/tmp/src/a.py"))
    graph.add_module(Module(name="b", path="/tmp/src/sub/b.py"))
    graph.add_dependency(Dependency(source="a", target="b", line=1))

    dot = export_dot(
        graph,
        edge_mode="cluster",
        cluster_depth=1,
        cluster_mode="module",
        style="cruiser",
    )
    assert "digraph" in dot
    assert "digraph" in export_dot(graph, cluster_depth=0, style="cruiser")

    cluster_graph = DependencyGraph()
    cluster_graph.add_module(Module(name="pkg.a", path="/work/src/pkg/a.py"))
    cluster_graph.add_module(Module(name="pkg.b", path="/work/src/pkg/b.py"))
    cluster_graph.add_module(Module(name="other.c", path="/work/src/other/c.py"))
    cluster_graph.add_dependency(Dependency(source="pkg.a", target="other.c", line=1))
    cluster_graph.add_dependency(Dependency(source="pkg.b", target="other.c", line=1))
    dot_cluster = export_dot(
        cluster_graph,
        edge_mode="cluster",
        cluster_depth=1,
        cluster_mode="module",
        style="cruiser",
    )
    assert "ltail" in dot_cluster
    assert 'subgraph "cluster_' in dot_cluster

    dot_cycle = export_dot(
        graph,
        violations=[Violation("r", "warn", "m", "a", "b")],
        edge_mode="node",
        style="cruiser",
    )
    assert "penwidth=2.2" in dot_cycle

    cycle_graph = DependencyGraph()
    cycle_graph.add_module(Module(name="a", path="/x/a.py"))
    cycle_graph.add_module(Module(name="b", path="/x/b.py"))
    cycle_graph.add_dependency(Dependency(source="a", target="b", line=1))
    cycle_graph.add_dependency(Dependency(source="b", target="a", line=1))
    dot_cycle_edge = export_dot(cycle_graph, edge_mode="node", style="cruiser")
    assert "#C0392B" in dot_cycle_edge

    html_fallback = _html_with_fallback("digraph {}", "t", "err")
    assert "Graphviz rendering failed" in html_fallback

    monkeypatch.setattr(
        "import_cruiser.exporter.subprocess.run",
        lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError("dot")),
    )
    with pytest.raises(RuntimeError):
        _render_dot("digraph{}", "svg")

    def _raise_called(*args, **kwargs):
        raise subprocess.CalledProcessError(2, ["dot"], stderr="bad")

    monkeypatch.setattr("import_cruiser.exporter.subprocess.run", _raise_called)
    with pytest.raises(RuntimeError):
        _render_dot("digraph{}", "svg")

    class _Result:
        stdout = "ok"

    monkeypatch.setattr(
        "import_cruiser.exporter.subprocess.run", lambda *a, **k: _Result()
    )
    assert _render_dot("digraph{}", "svg") == "ok"

    monkeypatch.setattr(
        "import_cruiser.exporter._render_dot",
        lambda *a, **k: "<svg/>",
    )
    assert export_svg(graph) == "<svg/>"
    assert "<svg/>" in export_html(graph)

    render_calls: list[str] = []

    def _cluster_then_node(dot: str, *args, **kwargs) -> str:
        render_calls.append(dot)
        if "ltail=" in dot:
            raise RuntimeError("Graphviz rendering failed.")
        return "<svg/>"

    monkeypatch.setattr(
        "import_cruiser.exporter._render_dot",
        _cluster_then_node,
    )
    assert (
        export_svg(
            cluster_graph,
            edge_mode="cluster",
            cluster_depth=1,
            cluster_mode="module",
            style="cruiser",
        )
        == "<svg/>"
    )
    assert len(render_calls) == 2
    assert "ltail=" in render_calls[0]
    assert "ltail=" not in render_calls[1]

    def _cluster_and_node_fail(dot: str, *args, **kwargs) -> str:
        if "ltail=" in dot:
            raise RuntimeError("cluster-fail")
        raise RuntimeError("node-fail")

    monkeypatch.setattr(
        "import_cruiser.exporter._render_dot",
        _cluster_and_node_fail,
    )
    with pytest.raises(RuntimeError, match="cluster-fail"):
        export_svg(
            cluster_graph,
            edge_mode="cluster",
            cluster_depth=1,
            cluster_mode="module",
            style="cruiser",
        )

    monkeypatch.setattr(
        "import_cruiser.exporter._render_dot",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    assert "Graphviz rendering failed" in export_html(graph)

    assert _style_attrs("archi", "LR")
    assert _style_attrs("cruiser", "LR")
    assert _style_attrs("navigator", "LR")
    assert _style_attrs("default", "LR")

    assert _cluster_parts(Module("pkg.mod", "/tmp/a.py"), "path", None) == ["pkg.mod"]
    assert _node_cluster_key("pkg/a.py", "", "path", 0, None) == ""
    assert _node_cluster_key("file.py", "", "path", 3, None) == ""
    tmp_root = str(Path("/tmp").resolve())
    assert (
        _node_cluster_key(
            "pkg/a.py",
            str(Path(tmp_root) / "pkg" / "a.py"),
            "path",
            2,
            tmp_root,
        )
        == "pkg"
    )
    assert (
        _node_cluster_key(
            "pkg/a.py",
            str(Path(tmp_root) / "pkg" / "a.py"),
            "path",
            2,
            "/other",
        )
        == ""
    )
    assert _node_cluster_key("pkg", "", "module", 2, None) == ""
    assert _node_cluster_key("pkg.mod.leaf", "", "module", 2, None) == "pkg.mod"

    assert _edges_in_cycles([["a", "b"]]) == {("a", "b"), ("b", "a")}
    assert _edges_in_cycles([["solo"]]) == set()
    node_lines = _dot_node_lines("a", "/x/a.py", True, "    ", "a.py")
    assert any("FDECEA" in line for line in node_lines)

    rc, roots, flat = _build_clusters(
        [
            Module(name="pkg.mod", path="/work/src/pkg/mod.py"),
            Module(name="pkg.other", path="/work/src/pkg/other.py"),
        ],
        1,
        "module",
    )
    assert rc
    assert roots == []
    assert flat
    assert _non_empty_clusters(flat) == set(flat.keys())

    empty_flat = {
        "a": {"id": "a", "label": "a", "parent": None, "modules": []},
        "a.b": {"id": "a.b", "label": "b", "parent": "a", "modules": []},
        "a.c": {
            "id": "a.c",
            "label": "c",
            "parent": "a",
            "modules": [Module(name="x", path="/x.py")],
        },
    }
    assert _non_empty_clusters(empty_flat) == {"a", "a.c"}
    skipped_lines = _render_cluster_tree(
        {"a": empty_flat["a"]},
        empty_flat,
        set(),
        "    ",
        "default",
        allowed={"a.c"},
        style="default",
    )
    assert skipped_lines == [""]

    monkeypatch.setattr(
        "import_cruiser.exporter.os.path.commonpath",
        lambda *a, **k: (_ for _ in ()).throw(ValueError("x")),
    )
    assert exporter_common_root([Module("a", "/a.py")]) is None


def test_exporter_common_root_src_parent(tmp_path: Path) -> None:
    src_pkg = tmp_path / "src" / "pkg"
    src_pkg.mkdir(parents=True)
    module_path = src_pkg / "m.py"
    module_path_2 = src_pkg / "n.py"
    module_path.write_text("x=1\n")
    module_path_2.write_text("x=2\n")
    root = exporter_common_root(
        [
            Module("pkg.m", str(module_path)),
            Module("pkg.n", str(module_path_2)),
        ]
    )
    assert root == str(tmp_path)


def test_add_svg_padding_helper() -> None:
    svg = '<svg width="100pt" height="80pt" viewBox="0 0 100 80"></svg>'
    padded = _add_svg_padding(svg, padding=10)
    assert 'viewBox="-10.00 -10.00 120.00 100.00"' in padded
    assert 'width="120pt"' in padded
    assert 'height="100pt"' in padded

    unchanged = _add_svg_padding('<svg width="100" height="80"></svg>')
    assert unchanged == '<svg width="100" height="80"></svg>'

    viewbox_only = _add_svg_padding('<svg viewBox="0 0 10 10"></svg>', padding=5)
    assert 'viewBox="-5.00 -5.00 20.00 20.00"' in viewbox_only


def test_exporter_common_root_when_src_is_common(tmp_path: Path) -> None:
    pkg = tmp_path / "src" / "pkg"
    other = tmp_path / "src" / "other"
    pkg.mkdir(parents=True)
    other.mkdir(parents=True)
    a = pkg / "a.py"
    b = other / "b.py"
    a.write_text("x=1\n")
    b.write_text("x=2\n")

    root = exporter_common_root(
        [
            Module("pkg.a", str(a)),
            Module("other.b", str(b)),
        ]
    )
    assert root == str(tmp_path)
