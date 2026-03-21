"""Detect circular dependencies using DFS."""

from __future__ import annotations

from collections import defaultdict

from import_cruiser.graph import DependencyGraph


def detect_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Return a list of cycles found in *graph*.

    Each cycle is represented as an ordered list of module names forming the
    cycle (the first element repeats at the end for readability but is *not*
    included – callers should close the loop themselves if needed).
    """
    # Build adjacency list
    adj: dict[str, list[str]] = defaultdict(list)
    for source, target in graph.edges():
        adj[source].append(target)

    all_modules = {m.name for m in graph.modules}

    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[list[str]] = []

    def dfs(node: str, path: list[str]) -> None:
        visited.add(node)
        rec_stack.add(node)
        path.append(node)

        for neighbour in adj.get(node, []):
            if neighbour not in all_modules:
                continue
            if neighbour not in visited:
                dfs(neighbour, path)
            elif neighbour in rec_stack:
                # Found a cycle – extract the cycle segment
                cycle_start = path.index(neighbour)
                cycle = path[cycle_start:]
                # Avoid duplicate cycles (same nodes, different starting point)
                normalised = _normalise_cycle(cycle)
                if normalised not in [_normalise_cycle(c) for c in cycles]:
                    cycles.append(list(cycle))

        path.pop()
        rec_stack.discard(node)

    for module in sorted(all_modules):
        if module not in visited:
            dfs(module, [])

    return cycles


def _normalise_cycle(cycle: list[str]) -> frozenset[str]:
    return frozenset(cycle)
