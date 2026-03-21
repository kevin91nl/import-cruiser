"""Parse Python source files and build a DependencyGraph."""

from __future__ import annotations

import ast
import os
from pathlib import Path

from pydepend.graph import Dependency, DependencyGraph, Module


def _module_name_from_path(path: Path, root: Path) -> str:
    """Convert a file path to a dotted module name relative to *root*."""
    rel = path.relative_to(root)
    parts = list(rel.parts)
    if parts[-1] == "__init__.py":
        parts = parts[:-1]
    else:
        parts[-1] = parts[-1].removesuffix(".py")
    return ".".join(parts)


def _collect_imports(source: str, module_name: str) -> list[tuple[str, int]]:
    """Return list of (import_name, line_no) extracted from *source*."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []

    imports: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                # Absolute import: `from pkg import name`
                if node.module:
                    imports.append((node.module, node.lineno))
                else:
                    # `from . import name` with level 0 shouldn't happen, skip
                    pass
            else:
                # Relative import – resolve against current module
                parts = module_name.split(".")
                base_parts = parts[: max(0, len(parts) - node.level)]
                if node.module:
                    resolved = ".".join(base_parts + [node.module]) if base_parts else node.module
                    imports.append((resolved, node.lineno))
                else:
                    # `from . import name` – each name is a sub-module
                    for alias in node.names:
                        resolved = ".".join(base_parts + [alias.name]) if base_parts else alias.name
                        imports.append((resolved, node.lineno))
    return imports


def _iter_python_files(directory: Path) -> list[Path]:
    py_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        # Skip hidden directories and common noise
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in ("__pycache__",)
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                py_files.append(Path(dirpath) / filename)
    return py_files


class Analyzer:
    """Analyze a Python project directory and produce a DependencyGraph."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root).resolve()

    def analyze(self) -> DependencyGraph:
        """Walk the project directory and return a fully populated DependencyGraph."""
        graph = DependencyGraph()
        py_files = _iter_python_files(self.root)

        # First pass: register all modules
        module_map: dict[str, Path] = {}
        for py_file in py_files:
            mod_name = _module_name_from_path(py_file, self.root)
            module_map[mod_name] = py_file
            graph.add_module(Module(name=mod_name, path=str(py_file)))

        known_modules = set(module_map.keys())

        # Second pass: resolve imports → edges
        for mod_name, py_file in module_map.items():
            source = py_file.read_text(encoding="utf-8", errors="replace")
            raw_imports = _collect_imports(source, mod_name)
            for imported, lineno in raw_imports:
                # Only keep dependencies that are internal to the project
                target = _resolve_internal(imported, known_modules)
                if target is not None:
                    graph.add_dependency(
                        Dependency(source=mod_name, target=target, line=lineno)
                    )

        return graph


def _resolve_internal(imported: str, known_modules: set[str]) -> str | None:
    """Return the best-matching known module for *imported*, or None."""
    # Exact match
    if imported in known_modules:
        return imported
    # Check if any known module is a prefix (package import)
    for mod in known_modules:
        if mod.startswith(imported + ".") or imported.startswith(mod + "."):
            # Pick the most specific match that exists
            if imported in known_modules:
                return imported
            # Walk up the imported name to find the deepest known ancestor
            parts = imported.split(".")
            for i in range(len(parts), 0, -1):
                candidate = ".".join(parts[:i])
                if candidate in known_modules:
                    return candidate
    return None
