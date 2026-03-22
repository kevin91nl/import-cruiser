"""Tests for JSON and DOT exporters."""

from __future__ import annotations

import json
from pathlib import Path


from import_cruiser.analyzer import Analyzer
from import_cruiser.exporter import export_dot, export_json
from import_cruiser.graph import Dependency, DependencyGraph, Module, filter_graph
from import_cruiser.validator import Violation


COMPLEX_FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "complex_project"


def simple_graph() -> DependencyGraph:
    g = DependencyGraph()
    g.add_module(Module(name="a", path="a.py"))
    g.add_module(Module(name="b", path="b.py"))
    g.add_dependency(Dependency(source="a", target="b", line=1))
    return g


class TestExportJson:
    def test_valid_json(self) -> None:
        result = export_json(simple_graph())
        data = json.loads(result)
        assert "modules" in data
        assert "dependencies" in data
        assert "cycles" in data
        assert "violations" in data
        assert "summary" in data

    def test_summary_counts(self) -> None:
        g = simple_graph()
        data = json.loads(export_json(g))
        assert data["summary"]["modules"] == 2
        assert data["summary"]["dependencies"] == 1

    def test_violations_included(self) -> None:
        v = Violation(
            rule_name="r",
            severity="error",
            message="msg",
            source="a",
            target="b",
        )
        data = json.loads(export_json(simple_graph(), violations=[v]))
        assert len(data["violations"]) == 1
        assert data["violations"][0]["rule"] == "r"

    def test_cycles_included(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="a", path="a.py"))
        g.add_module(Module(name="b", path="b.py"))
        g.add_dependency(Dependency(source="a", target="b"))
        g.add_dependency(Dependency(source="b", target="a"))
        data = json.loads(export_json(g))
        assert data["summary"]["cycles"] == 1

    def test_empty_graph(self) -> None:
        data = json.loads(export_json(DependencyGraph()))
        assert data["summary"]["modules"] == 0
        assert data["summary"]["dependencies"] == 0


class TestExportDot:
    def test_contains_digraph(self) -> None:
        result = export_dot(simple_graph())
        assert "digraph" in result

    def test_contains_nodes(self) -> None:
        result = export_dot(simple_graph())
        assert '"a"' in result
        assert '"b"' in result

    def test_contains_edge(self) -> None:
        result = export_dot(simple_graph())
        assert "->" in result

    def test_rankdir_lr(self) -> None:
        result = export_dot(simple_graph())
        assert "rankdir=LR" in result

    def test_empty_graph(self) -> None:
        result = export_dot(DependencyGraph())
        assert "digraph" in result
        assert "->" not in result

    def test_cluster_edges_for_path_mode_use_real_paths(self) -> None:
        graph = Analyzer(COMPLEX_FIXTURE_ROOT).analyze()
        filtered = filter_graph(
            graph,
            exclude_paths=[r"__init__\.py$"],
        )
        result = export_dot(
            filtered,
            style="cruiser",
            cluster_mode="path",
            cluster_depth=4,
            edge_mode="cluster",
        )
        assert "cluster_src_risk_like_app" in result
        assert "cluster_src_risk_like_domain" in result
        assert "cluster_src_risk_like_infra" in result
        assert 'ltail="cluster_src_risk_like_app"' in result
        assert (
            'lhead="cluster_src_risk_like_domain"' in result
            or 'lhead="cluster_src_risk_like_infra"' in result
        )

    def test_cluster_edge_mode_skips_nonexistent_top_level_clusters(self) -> None:
        graph = DependencyGraph()
        graph.add_module(Module(name="examples.a", path="/tmp/examples/a.py"))
        graph.add_module(Module(name="pkg.b", path="/tmp/src/pkg/b.py"))
        graph.add_dependency(Dependency(source="examples.a", target="pkg.b", line=1))

        result = export_dot(
            graph,
            style="cruiser",
            cluster_mode="path",
            cluster_depth=2,
            edge_mode="cluster",
        )
        assert '"examples.a" -> "pkg.b";' in result
        assert "ltail=" not in result
        assert "lhead=" not in result

    def test_cluster_edge_mode_skips_ancestor_cluster_links(self) -> None:
        graph = DependencyGraph()
        graph.add_module(Module(name="pkg.mod", path="/tmp/src/pkg/mod.py"))
        graph.add_module(Module(name="pkg.sub.leaf", path="/tmp/src/pkg/sub/leaf.py"))
        graph.add_dependency(
            Dependency(source="pkg.sub.leaf", target="pkg.mod", line=1)
        )

        result = export_dot(
            graph,
            style="cruiser",
            cluster_mode="path",
            cluster_depth=3,
            edge_mode="cluster",
        )
        assert '"pkg.sub.leaf" -> "pkg.mod";' in result
        assert "ltail=" not in result
        assert "lhead=" not in result
