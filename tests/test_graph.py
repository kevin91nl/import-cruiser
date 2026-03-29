"""Tests for the DependencyGraph data structure."""
# pylint: disable=duplicate-code

from import_cruiser.graph import (
    detect_cycles,
    Dependency,
    DependencyGraph,
    Module,
    collapse_graph,
    filter_graph,
    prune_orphan_init_modules,
)


def make_graph(edges: list[tuple[str, str]]) -> DependencyGraph:
    g = DependencyGraph()
    modules = {name for pair in edges for name in pair}
    for name in modules:
        g.add_module(Module(name=name, path=f"{name}.py"))
    for src, tgt in edges:
        g.add_dependency(Dependency(source=src, target=tgt))
    return g


class TestModule:
    def test_hash_and_eq(self) -> None:
        m1 = Module(name="foo", path="foo.py")
        m2 = Module(name="foo", path="other.py")
        assert m1 == m2
        assert hash(m1) == hash(m2)

    def test_inequality(self) -> None:
        assert Module(name="foo", path="foo.py") != Module(name="bar", path="bar.py")


class TestDependency:
    def test_hash_and_eq(self) -> None:
        d1 = Dependency(source="a", target="b", line=1)
        d2 = Dependency(source="a", target="b", line=99)
        assert d1 == d2
        assert hash(d1) == hash(d2)


class TestDependencyGraph:
    def test_add_and_retrieve_module(self) -> None:
        g = DependencyGraph()
        m = Module(name="pkg.mod", path="pkg/mod.py")
        g.add_module(m)
        assert g.get_module("pkg.mod") == m
        assert len(g.modules) == 1

    def test_add_module_replaces_existing(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="a", path="a.py"))
        g.add_module(Module(name="a", path="new_a.py"))
        assert g.get_module("a").path == "new_a.py"
        assert len(g.modules) == 1

    def test_add_dependency_deduplication(self) -> None:
        g = DependencyGraph()
        g.add_dependency(Dependency(source="a", target="b"))
        g.add_dependency(Dependency(source="a", target="b"))
        assert len(g.dependencies) == 1

    def test_dependencies_of(self) -> None:
        g = make_graph([("a", "b"), ("a", "c"), ("b", "c")])
        assert set(g.dependencies_of("a")) == {"b", "c"}
        assert set(g.dependencies_of("b")) == {"c"}

    def test_dependents_of(self) -> None:
        g = make_graph([("a", "c"), ("b", "c")])
        assert set(g.dependents_of("c")) == {"a", "b"}

    def test_edges(self) -> None:
        g = make_graph([("x", "y"), ("y", "z")])
        assert set(g.edges()) == {("x", "y"), ("y", "z")}

    def test_get_module_missing(self) -> None:
        g = DependencyGraph()
        assert g.get_module("nonexistent") is None


class TestGraphFiltering:
    def test_include_exclude(self) -> None:
        g = make_graph([("a.core", "b.core"), ("a.core", "c.util")])
        filtered = filter_graph(g, include=["^a\\."], exclude=["util$"])
        assert {m.name for m in filtered.modules} == {"a.core"}
        assert set(filtered.edges()) == set()

    def test_focus_depth(self) -> None:
        g = make_graph([("a", "b"), ("b", "c"), ("c", "d")])
        focused = filter_graph(g, focus=["^b$"], focus_depth=1)
        assert {m.name for m in focused.modules} == {"a", "b", "c"}

    def test_include_path(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="a", path="/repo/src/a.py"))
        g.add_module(Module(name="b", path="/repo/tests/b.py"))
        filtered = filter_graph(g, include_paths=[r"src/"])
        assert {m.name for m in filtered.modules} == {"a"}

    def test_include_path_matches_absolute_pattern(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="a", path="/private/tmp/repo/src/a.py"))
        g.add_module(Module(name="b", path="/private/tmp/repo/tests/b.py"))
        filtered = filter_graph(g, include_paths=[r"/private/tmp/repo/src/"])
        assert {m.name for m in filtered.modules} == {"a"}

    def test_filter_graph_preserves_loc(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="a", path="/repo/src/a.py", loc=11))
        g.add_module(Module(name="b", path="/repo/src/b.py", loc=5))
        g.add_dependency(Dependency(source="a", target="b"))

        filtered = filter_graph(g, include=[r"^a$"])
        module = filtered.get_module("a")
        assert module is not None
        assert module.loc == 11


class TestGraphCollapse:
    def test_collapse_depth(self) -> None:
        g = make_graph([("pkg.a", "pkg.b"), ("pkg.b", "other.c")])
        collapsed = collapse_graph(g, depth=1)
        assert {m.name for m in collapsed.modules} == {"pkg", "other"}
        assert set(collapsed.edges()) == {("pkg", "other")}

    def test_collapse_depth_aggregates_loc(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="pkg.a", path="/repo/pkg/a.py", loc=4))
        g.add_module(Module(name="pkg.b", path="/repo/pkg/b.py", loc=6))
        g.add_module(Module(name="other.c", path="/repo/other/c.py", loc=3))
        g.add_dependency(Dependency(source="pkg.a", target="other.c"))
        g.add_dependency(Dependency(source="pkg.b", target="other.c"))

        collapsed = collapse_graph(g, depth=1)
        pkg = collapsed.get_module("pkg")
        other = collapsed.get_module("other")
        assert pkg is not None and pkg.loc == 10
        assert other is not None and other.loc == 3


class TestPruneOrphanInitModules:
    def test_prunes_orphan_init_module(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="pkg", path="/repo/pkg/__init__.py"))
        g.add_module(Module(name="pkg.mod", path="/repo/pkg/mod.py"))

        pruned = prune_orphan_init_modules(g)

        assert {m.name for m in pruned.modules} == {"pkg.mod"}

    def test_keeps_init_when_connected(self) -> None:
        g = DependencyGraph()
        g.add_module(Module(name="pkg", path="/repo/pkg/__init__.py"))
        g.add_module(Module(name="pkg.mod", path="/repo/pkg/mod.py"))
        g.add_dependency(Dependency(source="pkg", target="pkg.mod"))

        pruned = prune_orphan_init_modules(g)

        assert {m.name for m in pruned.modules} == {"pkg", "pkg.mod"}
        assert set(pruned.edges()) == {("pkg", "pkg.mod")}


class TestDetectCycles:
    def test_circular_dependency_detection(self) -> None:
        g = make_graph([("a", "b"), ("b", "c"), ("c", "a")])
        cycles = detect_cycles(g)
        assert len(cycles) == 1
        assert set(cycles[0]) == {"a", "b", "c"}

    def test_no_cycle(self) -> None:
        g = make_graph([("a", "b"), ("b", "c")])
        assert detect_cycles(g) == []

    def test_self_loop(self) -> None:
        g = make_graph([("a", "a")])
        cycles = detect_cycles(g)
        assert len(cycles) == 1
        assert cycles[0] == ["a"]

    def test_two_independent_cycles(self) -> None:
        g = make_graph([("a", "b"), ("b", "a"), ("c", "d"), ("d", "c")])
        cycles = detect_cycles(g)
        assert len(cycles) == 2
        cycle_sets = [set(c) for c in cycles]
        assert {"a", "b"} in cycle_sets
        assert {"c", "d"} in cycle_sets
