"""Dependency graph data structure for import_cruiser."""
# pylint: disable=duplicate-code

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
import os
from pathlib import Path
import re
from typing import Iterable


@dataclass
class Module:
    """Represents a Python module discovered in the project."""

    name: str
    path: str
    loc: int = 0
    imports: list[str] = field(default_factory=list)

    def __hash__(self) -> int:
        return hash(self.name)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Module):
            return NotImplemented
        return self.name == other.name


@dataclass
class Dependency:
    """Represents a directed dependency edge between two modules."""

    source: str
    target: str
    line: int = 0

    def __hash__(self) -> int:
        return hash((self.source, self.target))

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Dependency):
            return NotImplemented
        return self.source == other.source and self.target == other.target


class DependencyGraph:
    """Directed graph of module dependencies."""

    def __init__(self) -> None:
        self._modules: dict[str, Module] = {}
        self._dependencies: list[Dependency] = []
        self._dependency_pairs: set[tuple[str, str]] = set()

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_module(self, module: Module) -> None:
        """Register a module; silently replaces existing entry with same name."""
        self._modules[module.name] = module

    def add_dependency(self, dep: Dependency) -> None:
        """Add a dependency edge (duplicate source→target pairs are ignored)."""
        key = (dep.source, dep.target)
        if key not in self._dependency_pairs:
            self._dependencies.append(dep)
            self._dependency_pairs.add(key)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    @property
    def modules(self) -> list[Module]:
        return list(self._modules.values())

    @property
    def dependencies(self) -> list[Dependency]:
        return list(self._dependencies)

    def get_module(self, name: str) -> Module | None:
        return self._modules.get(name)

    def dependents_of(self, module_name: str) -> list[str]:
        """Return all modules that *import* ``module_name``."""
        return [d.source for d in self._dependencies if d.target == module_name]

    def dependencies_of(self, module_name: str) -> list[str]:
        """Return all modules that ``module_name`` imports."""
        return [d.target for d in self._dependencies if d.source == module_name]

    def edges(self) -> Iterable[tuple[str, str]]:
        for dep in self._dependencies:
            yield dep.source, dep.target


def detect_cycles(graph: DependencyGraph) -> list[list[str]]:
    """Return a list of cycles found in *graph*."""
    adj: dict[str, list[str]] = defaultdict(list)
    for source, target in graph.edges():
        adj[source].append(target)

    all_modules = {m.name for m in graph.modules}

    visited: set[str] = set()
    rec_stack: set[str] = set()
    cycles: list[list[str]] = []
    seen_cycles: set[frozenset[str]] = set()

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
                cycle_start = path.index(neighbour)
                cycle = path[cycle_start:]
                normalised = frozenset(cycle)
                if normalised not in seen_cycles:
                    cycles.append(list(cycle))
                    seen_cycles.add(normalised)

        path.pop()
        rec_stack.discard(node)

    for module in sorted(all_modules):
        if module not in visited:
            dfs(module, [])

    return cycles


def filter_graph(
    graph: DependencyGraph,
    include: list[str] | None = None,
    exclude: list[str] | None = None,
    include_paths: list[str] | None = None,
    exclude_paths: list[str] | None = None,
    focus: list[str] | None = None,
    focus_depth: int = 1,
) -> DependencyGraph:
    include_patterns = _compile_patterns(include)
    exclude_patterns = _compile_patterns(exclude)
    include_path_patterns = _compile_patterns(include_paths)
    exclude_path_patterns = _compile_patterns(exclude_paths)
    focus_patterns = _compile_patterns(focus)

    module_map = {m.name: m for m in graph.modules}
    root_path = _common_root(graph.modules)
    allowed = set(module_map.keys())

    allowed = _apply_name_filters(allowed, include_patterns, exclude_patterns)
    allowed = _apply_include_path_filters(
        graph,
        module_map,
        allowed,
        root_path,
        include_path_patterns,
    )
    allowed = _apply_exclude_path_filters(
        module_map,
        allowed,
        root_path,
        exclude_path_patterns,
    )

    if focus_patterns:
        focus_set = {name for name in allowed if _matches_any(name, focus_patterns)}
        allowed = _expand_focus(graph, focus_set, allowed, focus_depth)

    return _subgraph(graph, module_map, allowed)


def collapse_graph(graph: DependencyGraph, depth: int) -> DependencyGraph:
    if depth <= 0:
        return graph

    collapsed = DependencyGraph()
    name_map: dict[str, str] = {}
    loc_totals: dict[str, int] = {}
    path_by_name: dict[str, str] = {}
    for module in graph.modules:
        collapsed_name = _collapse_name(module.name, depth)
        name_map[module.name] = collapsed_name
        loc_totals[collapsed_name] = loc_totals.get(collapsed_name, 0) + module.loc
        path_by_name.setdefault(collapsed_name, module.path)

    for collapsed_name, total_loc in loc_totals.items():
        collapsed.add_module(
            Module(
                name=collapsed_name, path=path_by_name[collapsed_name], loc=total_loc
            )
        )

    for dep in graph.dependencies:
        src = name_map.get(dep.source)
        tgt = name_map.get(dep.target)
        if src and tgt and src != tgt:
            collapsed.add_dependency(Dependency(source=src, target=tgt, line=dep.line))

    return collapsed


def prune_orphan_init_modules(graph: DependencyGraph) -> DependencyGraph:
    """Remove modules backed by ``__init__.py`` files that have no edges."""
    module_map = {m.name: m for m in graph.modules}
    connected: set[str] = set()
    for dep in graph.dependencies:
        connected.add(dep.source)
        connected.add(dep.target)

    allowed = {
        name
        for name, module in module_map.items()
        if Path(module.path).name != "__init__.py" or name in connected
    }
    return _subgraph(graph, module_map, allowed)


def prune_isolated_modules(graph: DependencyGraph) -> DependencyGraph:
    """Remove modules that have no incoming or outgoing edges."""
    module_map = {m.name: m for m in graph.modules}
    connected: set[str] = set()
    for dep in graph.dependencies:
        connected.add(dep.source)
        connected.add(dep.target)

    allowed = {name for name in module_map if name in connected}
    return _subgraph(graph, module_map, allowed)


def aggregate_by_path(
    graph: DependencyGraph,
    depth: int,
    leaf_patterns: list[re.Pattern[str]] | None = None,
) -> DependencyGraph:
    if depth <= 0:
        return graph

    root = _common_root(graph.modules)
    if root is None:
        return graph

    init_files = {
        _dir_key(Path(module.path).resolve().parent)
        for module in graph.modules
        if module.path.endswith("__init__.py")
    }

    aggregated = DependencyGraph()
    group_map: dict[str, str] = {}
    group_path_map: dict[str, str] = {}
    group_loc_totals: dict[str, int] = {}

    for module in graph.modules:
        group_key = _group_key(module.path, root, depth)
        if not group_key:
            continue
        filename = Path(module.path).name
        if leaf_patterns and any(p.search(filename) for p in leaf_patterns):
            leaf_key = os.path.join(group_key, filename)
            group_map[module.name] = leaf_key
            group_path_map.setdefault(leaf_key, module.path)
            group_loc_totals[leaf_key] = group_loc_totals.get(leaf_key, 0) + module.loc
        else:
            group_map[module.name] = group_key
            group_loc_totals[group_key] = (
                group_loc_totals.get(group_key, 0) + module.loc
            )
            if group_key not in group_path_map:
                group_dir = Path(root) / Path(group_key)
                init_path = group_dir / "__init__.py"
                path_key = _dir_key(group_dir)
                if path_key not in init_files:
                    group_path_map[group_key] = str(init_path)
                else:
                    group_path_map[group_key] = str(init_path)

    for group_key, path in group_path_map.items():
        aggregated.add_module(
            Module(name=group_key, path=path, loc=group_loc_totals.get(group_key, 0))
        )

    for dep in graph.dependencies:
        src = group_map.get(dep.source)
        tgt = group_map.get(dep.target)
        if src and tgt and src != tgt:
            aggregated.add_dependency(Dependency(source=src, target=tgt, line=dep.line))

    return aggregated


def _compile_patterns(patterns: list[str] | None) -> list[re.Pattern[str]]:
    if not patterns:
        return []
    return [re.compile(p) for p in patterns]


def _matches_any(value: str, patterns: list[re.Pattern[str]]) -> bool:
    return any(p.search(value) for p in patterns)


def _matches_path_filters(
    path: str,
    root_path: str | None,
    patterns: list[re.Pattern[str]],
) -> bool:
    rel_path = _match_path(path, root_path)
    abs_path = path.replace("\\", "/")
    return _matches_any(rel_path, patterns) or _matches_any(abs_path, patterns)


def _apply_name_filters(
    allowed: set[str],
    include_patterns: list[re.Pattern[str]],
    exclude_patterns: list[re.Pattern[str]],
) -> set[str]:
    filtered = allowed
    if include_patterns:
        filtered = {name for name in filtered if _matches_any(name, include_patterns)}
    if exclude_patterns:
        filtered = {
            name for name in filtered if not _matches_any(name, exclude_patterns)
        }
    return filtered


def _apply_include_path_filters(
    graph: DependencyGraph,
    module_map: dict[str, Module],
    allowed: set[str],
    root_path: str | None,
    include_path_patterns: list[re.Pattern[str]],
) -> set[str]:
    if not include_path_patterns:
        return allowed
    allowed_with_path = {
        name
        for name in allowed
        if module_map[name].path
        and _matches_path_filters(
            module_map[name].path,
            root_path,
            include_path_patterns,
        )
    }
    pathless = {name for name in allowed if not module_map[name].path}
    connected_pathless = {
        dep.target
        for dep in graph.dependencies
        if dep.source in allowed_with_path and dep.target in pathless
    }
    connected_pathless.update(
        dep.source
        for dep in graph.dependencies
        if dep.target in allowed_with_path and dep.source in pathless
    )
    return allowed_with_path | connected_pathless


def _apply_exclude_path_filters(
    module_map: dict[str, Module],
    allowed: set[str],
    root_path: str | None,
    exclude_path_patterns: list[re.Pattern[str]],
) -> set[str]:
    if not exclude_path_patterns:
        return allowed
    return {
        name
        for name in allowed
        if not _matches_path_filters(
            module_map[name].path,
            root_path,
            exclude_path_patterns,
        )
    }


def _expand_focus(
    graph: DependencyGraph,
    focus_set: set[str],
    allowed: set[str],
    depth: int,
) -> set[str]:
    if not focus_set:
        return set()
    if depth <= 0:
        return focus_set

    adj_out: dict[str, set[str]] = {}
    adj_in: dict[str, set[str]] = {}
    for dep in graph.dependencies:
        if dep.source in allowed and dep.target in allowed:
            adj_out.setdefault(dep.source, set()).add(dep.target)
            adj_in.setdefault(dep.target, set()).add(dep.source)

    visited = set(focus_set)
    frontier = set(focus_set)
    for _ in range(depth):
        next_frontier: set[str] = set()
        for node in frontier:
            next_frontier.update(adj_out.get(node, set()))
            next_frontier.update(adj_in.get(node, set()))
        next_frontier = {n for n in next_frontier if n in allowed}
        new_nodes = next_frontier - visited
        if not new_nodes:
            break
        visited.update(new_nodes)
        frontier = new_nodes

    return visited


def _subgraph(
    graph: DependencyGraph,
    module_map: dict[str, Module],
    allowed: set[str],
) -> DependencyGraph:
    filtered = DependencyGraph()
    for name in sorted(allowed):
        module = module_map.get(name)
        if module:
            filtered.add_module(
                Module(name=module.name, path=module.path, loc=module.loc)
            )

    for dep in graph.dependencies:
        if dep.source in allowed and dep.target in allowed:
            filtered.add_dependency(
                Dependency(source=dep.source, target=dep.target, line=dep.line)
            )

    return filtered


def _collapse_name(name: str, depth: int) -> str:
    parts = name.split(".")
    if len(parts) <= depth:
        return name
    return ".".join(parts[:depth])


def _match_path(path: str, root_path: str | None) -> str:
    if not path:
        return ""
    if root_path:
        try:
            rel = os.path.relpath(path, root_path)
        except ValueError:
            rel = path
    else:
        rel = path
    return rel.replace(os.sep, "/")


def _common_root(modules: list[Module]) -> str | None:
    paths = [m.path for m in modules if m.path]
    if not paths:
        return None
    try:
        return os.path.commonpath(paths)
    except ValueError:
        return None


def _group_key(path: str, root: str, depth: int) -> str:
    root_path = Path(root).resolve()
    rel = Path(path).resolve().relative_to(root_path)
    parts = list(rel.parts[:-1])  # directories only
    if not parts:
        return root_path.name
    return os.path.join(*parts[: min(depth, len(parts))])


def _dir_key(path: Path) -> str:
    return str(path.resolve())
