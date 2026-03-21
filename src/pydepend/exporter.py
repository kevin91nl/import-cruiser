"""Export a DependencyGraph to JSON or DOT (Graphviz) format."""

from __future__ import annotations

import json
from typing import Any

from pydepend.detector import detect_cycles
from pydepend.graph import DependencyGraph
from pydepend.validator import Violation


def export_json(
    graph: DependencyGraph,
    violations: list[Violation] | None = None,
    cycles: list[list[str]] | None = None,
) -> str:
    """Serialize *graph* (and optional analysis results) to a JSON string."""
    if violations is None:
        violations = []
    if cycles is None:
        cycles = detect_cycles(graph)

    data: dict[str, Any] = {
        "summary": {
            "modules": len(graph.modules),
            "dependencies": len(graph.dependencies),
            "cycles": len(cycles),
            "violations": len(violations),
        },
        "modules": [
            {
                "name": m.name,
                "path": m.path,
                "imports": graph.dependencies_of(m.name),
            }
            for m in sorted(graph.modules, key=lambda m: m.name)
        ],
        "dependencies": [
            {"source": d.source, "target": d.target, "line": d.line}
            for d in sorted(graph.dependencies, key=lambda d: (d.source, d.target))
        ],
        "cycles": [cycle for cycle in cycles],
        "violations": [v.to_dict() for v in violations],
    }
    return json.dumps(data, indent=2)


def export_dot(graph: DependencyGraph, graph_name: str = "pydepend") -> str:
    """Return a DOT-format string representing the dependency graph."""
    lines: list[str] = [f'digraph "{graph_name}" {{', "    rankdir=LR;"]

    # Node declarations
    for module in sorted(graph.modules, key=lambda m: m.name):
        safe = _dot_id(module.name)
        lines.append(f'    {safe} [label="{module.name}"];')

    lines.append("")

    # Edge declarations
    for dep in sorted(graph.dependencies, key=lambda d: (d.source, d.target)):
        src = _dot_id(dep.source)
        tgt = _dot_id(dep.target)
        lines.append(f"    {src} -> {tgt};")

    lines.append("}")
    return "\n".join(lines)


def _dot_id(name: str) -> str:
    """Convert a dotted module name to a valid DOT identifier."""
    return '"' + name.replace('"', '\\"') + '"'
