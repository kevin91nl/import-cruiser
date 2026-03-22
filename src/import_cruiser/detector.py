"""Detect circular dependencies using DFS."""

from __future__ import annotations

from import_cruiser.graph import DependencyGraph, detect_cycles as _detect_cycles


def detect_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Backward-compatible wrapper around graph.detect_cycles."""
    cycles = _detect_cycles(graph)
    return [list(cycle) for cycle in cycles]
