"""Tests for the circular-dependency detector."""
# pylint: disable=duplicate-code

from __future__ import annotations

from import_cruiser.detector import detect_cycles
from import_cruiser.graph import Dependency, DependencyGraph, Module


def make_graph(edges: list[tuple[str, str]]) -> DependencyGraph:
    g = DependencyGraph()
    modules = {name for pair in edges for name in pair}
    for name in modules:
        g.add_module(Module(name=name, path=f"{name}.py"))
    for src, tgt in edges:
        g.add_dependency(Dependency(source=src, target=tgt))
    return g


class TestDetectCycles:
    def test_no_cycle(self) -> None:
        g = make_graph([("a", "b"), ("b", "c")])
        assert detect_cycles(g) == []

    def test_simple_cycle(self) -> None:
        g = make_graph([("a", "b"), ("b", "a")])
        cycles = detect_cycles(g)
        assert len(cycles) == 1
        cycle = cycles[0]
        assert set(cycle) == {"a", "b"}

    def test_self_loop(self) -> None:
        g = make_graph([("a", "a")])
        cycles = detect_cycles(g)
        assert len(cycles) == 1
        assert cycles[0] == ["a"]

    def test_triangle_cycle(self) -> None:
        g = make_graph([("a", "b"), ("b", "c"), ("c", "a")])
        cycles = detect_cycles(g)
        assert len(cycles) == 1
        assert set(cycles[0]) == {"a", "b", "c"}

    def test_disconnected_graph_no_cycle(self) -> None:
        g = make_graph([("a", "b"), ("c", "d")])
        assert detect_cycles(g) == []

    def test_empty_graph(self) -> None:
        g = DependencyGraph()
        assert detect_cycles(g) == []

    def test_multiple_independent_cycles(self) -> None:
        # Two separate cycles: a→b→a and c→d→c
        g = make_graph([("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")])
        cycles = detect_cycles(g)
        assert len(cycles) == 2
