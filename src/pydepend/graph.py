"""Dependency graph data structure for pydepend."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable


@dataclass
class Module:
    """Represents a Python module discovered in the project."""

    name: str
    path: str
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

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_module(self, module: Module) -> None:
        """Register a module; silently replaces existing entry with same name."""
        self._modules[module.name] = module

    def add_dependency(self, dep: Dependency) -> None:
        """Add a dependency edge (duplicate source→target pairs are ignored)."""
        if not any(
            d.source == dep.source and d.target == dep.target
            for d in self._dependencies
        ):
            self._dependencies.append(dep)

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
