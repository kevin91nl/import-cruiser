"""Parse Python source files and build a DependencyGraph."""

from __future__ import annotations

import ast
import os
from pathlib import Path

from import_cruiser.graph import Dependency, DependencyGraph, Module


def _module_name_from_path(
    path: Path, base: Path, normalize_hyphens: bool = True
) -> str:
    """Convert a file path to a dotted module name relative to *base*."""
    rel = path.relative_to(base)
    parts = list(rel.parts)
    if normalize_hyphens:
        parts = [p.replace("-", "_") for p in parts]
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
                    resolved = (
                        ".".join(base_parts + [node.module])
                        if base_parts
                        else node.module
                    )
                    imports.append((resolved, node.lineno))
                else:
                    # `from . import name` – each name is a sub-module
                    for alias in node.names:
                        resolved = (
                            ".".join(base_parts + [alias.name])
                            if base_parts
                            else alias.name
                        )
                        imports.append((resolved, node.lineno))

    if imports:
        return imports

    parts = module_name.split(".")
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.strip()
        if not stripped.startswith("from ") or " import " not in stripped:
            continue
        target = stripped[5:].split(" import ", 1)[0].strip()
        if not target.startswith("."):
            continue

        level = len(target) - len(target.lstrip("."))
        module = target[level:]
        base_parts = parts[: max(0, len(parts) - level)]
        resolved = ".".join(base_parts + [module]) if base_parts and module else module
        if resolved:
            imports.append((resolved, lineno))

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

    def __init__(self, root: str | Path, normalize_hyphens: bool = True) -> None:
        self.root = Path(root).resolve()
        self.normalize_hyphens = normalize_hyphens

    def analyze(self) -> DependencyGraph:
        """Walk the project directory and return a fully populated DependencyGraph."""
        graph = DependencyGraph()
        py_files = _iter_python_files(self.root)
        source_roots = _find_source_roots(self.root)

        # First pass: register all modules
        module_map: dict[str, Path] = {}
        for py_file in py_files:
            base = _select_source_root(py_file, source_roots) or self.root
            mod_name = _module_name_from_path(py_file, base, self.normalize_hyphens)
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
    if imported in known_modules:
        return imported

    if any(mod.startswith(imported + ".") for mod in known_modules):
        return imported

    parts = imported.split(".")
    for i in range(len(parts) - 1, 0, -1):
        candidate = ".".join(parts[:i])
        if candidate in known_modules:
            return candidate
    return None


def _find_source_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "src":
            src_path = Path(dirpath).resolve()
            if _contains_python_files(src_path):
                roots.append(src_path)
    return roots


def _contains_python_files(directory: Path) -> bool:
    for dirpath, _, filenames in os.walk(directory):
        if any(name.endswith(".py") for name in filenames):
            return True
    return False


def _select_source_root(path: Path, roots: list[Path]) -> Path | None:
    candidates = [r for r in roots if path.is_relative_to(r)]
    if not candidates:
        return None
    return max(candidates, key=lambda p: len(p.parts))
