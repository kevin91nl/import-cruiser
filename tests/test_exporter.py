"""Tests for JSON and DOT exporters."""

from __future__ import annotations

import json

import pytest

from pydepend.exporter import export_dot, export_json
from pydepend.graph import Dependency, DependencyGraph, Module
from pydepend.validator import Violation


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
