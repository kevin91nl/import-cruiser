"""Validate a DependencyGraph against user-defined rules."""

from __future__ import annotations

import re
from typing import Any

from import_cruiser.graph import DependencyGraph


def _matches_pattern(name: str, pattern_obj: dict[str, Any]) -> bool:
    """Return True if *name* matches the pattern specification in *pattern_obj*.

    Supported keys:
      - ``path``: a regex applied to the module name (dot-separated)
    """
    path_pattern = pattern_obj.get("path")
    if path_pattern is not None:
        return bool(re.search(path_pattern, name))
    # Empty pattern matches everything
    return True


class Violation:
    """A single rule violation."""

    __slots__ = ("rule_name", "severity", "message", "source", "target")

    def __init__(
        self,
        rule_name: str,
        severity: str,
        message: str,
        source: str,
        target: str,
    ) -> None:
        self.rule_name = rule_name
        self.severity = severity
        self.message = message
        self.source = source
        self.target = target

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule": self.rule_name,
            "severity": self.severity,
            "message": self.message,
            "source": self.source,
            "target": self.target,
        }


class Validator:
    """Validate a DependencyGraph against a list of rules."""

    def __init__(self, rules: list[dict[str, Any]]) -> None:
        self.rules = rules

    def validate(self, graph: DependencyGraph) -> list[Violation]:
        """Run all rules against *graph* and return a list of violations."""
        violations: list[Violation] = []
        for rule in self.rules:
            violations.extend(self._apply_rule(rule, graph))
        return violations

    def _apply_rule(
        self, rule: dict[str, Any], graph: DependencyGraph
    ) -> list[Violation]:
        violations: list[Violation] = []
        rule_name: str = rule["name"]
        severity: str = rule["severity"]
        from_pattern: dict[str, Any] = rule["from"]
        to_pattern: dict[str, Any] = rule["to"]
        allow: bool = rule.get("allow", True)

        for dep in graph.dependencies:
            source_matches = _matches_pattern(dep.source, from_pattern)
            target_matches = _matches_pattern(dep.target, to_pattern)

            if source_matches and target_matches:
                if not allow:
                    msg = (
                        f"Dependency from '{dep.source}' to '{dep.target}' "
                        f"is forbidden by rule '{rule_name}'."
                    )
                    violations.append(
                        Violation(
                            rule_name=rule_name,
                            severity=severity,
                            message=msg,
                            source=dep.source,
                            target=dep.target,
                        )
                    )

        return violations
