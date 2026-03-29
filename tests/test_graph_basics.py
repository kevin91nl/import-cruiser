"""Basic graph behavior smoke tests."""

from import_cruiser.graph import Dependency, DependencyGraph, Module


def test_add_dependency() -> None:
    """Verify that a dependency can be added between two modules."""
    graph = DependencyGraph()
    module_a = Module(name="app.core", path="app/core.py")
    module_b = Module(name="app.utils", path="app/utils.py")
    graph.add_module(module_a)
    graph.add_module(module_b)

    graph.add_dependency(Dependency(source="app.core", target="app.utils"))

    assert "app.utils" in graph.dependencies_of("app.core")
    assert "app.core" in graph.dependents_of("app.utils")
    assert len(graph.dependencies) == 1
