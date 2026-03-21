"""Tests for the Validator."""

from __future__ import annotations

from pydepend.graph import Dependency, DependencyGraph, Module
from pydepend.validator import Validator, Violation


def make_graph_with_deps(edges: list[tuple[str, str]]) -> DependencyGraph:
    g = DependencyGraph()
    mods = {n for pair in edges for n in pair}
    for name in mods:
        g.add_module(Module(name=name, path=f"{name}.py"))
    for src, tgt in edges:
        g.add_dependency(Dependency(source=src, target=tgt))
    return g


class TestValidator:
    def test_no_violations_with_allow_rule(self) -> None:
        graph = make_graph_with_deps([("a", "b")])
        rules = [
            {
                "name": "allow-a-to-b",
                "severity": "error",
                "from": {"path": "^a$"},
                "to": {"path": "^b$"},
                "allow": True,
            }
        ]
        violations = Validator(rules).validate(graph)
        assert violations == []

    def test_violation_on_forbidden_dependency(self) -> None:
        graph = make_graph_with_deps([("a", "b")])
        rules = [
            {
                "name": "no-a-to-b",
                "severity": "error",
                "from": {"path": "^a$"},
                "to": {"path": "^b$"},
                "allow": False,
            }
        ]
        violations = Validator(rules).validate(graph)
        assert len(violations) == 1
        v = violations[0]
        assert v.rule_name == "no-a-to-b"
        assert v.severity == "error"
        assert v.source == "a"
        assert v.target == "b"

    def test_violation_to_dict(self) -> None:
        v = Violation(
            rule_name="r",
            severity="warn",
            message="msg",
            source="a",
            target="b",
        )
        d = v.to_dict()
        assert d["rule"] == "r"
        assert d["severity"] == "warn"
        assert d["source"] == "a"
        assert d["target"] == "b"

    def test_pattern_does_not_match(self) -> None:
        graph = make_graph_with_deps([("a", "b")])
        rules = [
            {
                "name": "no-x-to-y",
                "severity": "error",
                "from": {"path": "^x$"},
                "to": {"path": "^y$"},
                "allow": False,
            }
        ]
        violations = Validator(rules).validate(graph)
        assert violations == []

    def test_empty_pattern_matches_all(self) -> None:
        graph = make_graph_with_deps([("a", "b"), ("c", "d")])
        rules = [
            {
                "name": "no-deps",
                "severity": "warn",
                "from": {},
                "to": {},
                "allow": False,
            }
        ]
        violations = Validator(rules).validate(graph)
        assert len(violations) == 2

    def test_no_rules(self) -> None:
        graph = make_graph_with_deps([("a", "b")])
        violations = Validator([]).validate(graph)
        assert violations == []

    def test_severity_warn(self) -> None:
        graph = make_graph_with_deps([("a", "b")])
        rules = [
            {
                "name": "warn-a-b",
                "severity": "warn",
                "from": {"path": "a"},
                "to": {"path": "b"},
                "allow": False,
            }
        ]
        violations = Validator(rules).validate(graph)
        assert violations[0].severity == "warn"
