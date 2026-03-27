"""Parse Python source files and build a DependencyGraph."""

from __future__ import annotations

import ast
import io
import os
import re
import token
import tokenize
from urllib.parse import urlsplit
import warnings
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
        # Some real-world files trigger SyntaxWarning (e.g. invalid escape
        # sequences in strings); keep CLI output quiet by default.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
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


def _iter_python_files(
    directory: Path,
    include_paths: list[re.Pattern[str]] | None = None,
    exclude_paths: list[re.Pattern[str]] | None = None,
) -> list[Path]:
    py_files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(directory):
        # Skip hidden directories and common noise
        dirnames[:] = [
            d for d in dirnames if not d.startswith(".") and d not in ("__pycache__",)
        ]
        for filename in filenames:
            if filename.endswith(".py"):
                path = Path(dirpath) / filename
                path_text = str(path).replace("\\", "/")
                if include_paths and not any(
                    pattern.search(path_text) for pattern in include_paths
                ):
                    continue
                if exclude_paths and any(
                    pattern.search(path_text) for pattern in exclude_paths
                ):
                    continue
                py_files.append(path)
    return py_files


class Analyzer:
    """Analyze a Python project directory and produce a DependencyGraph."""

    def __init__(
        self,
        root: str | Path,
        normalize_hyphens: bool = True,
        include_paths: list[str] | None = None,
        exclude_paths: list[str] | None = None,
        include_external_patterns: list[str] | None = None,
        include_http_hosts: bool = False,
    ) -> None:
        # Keep the user-provided path form (e.g. /private/tmp vs /tmp) so
        # include/exclude path regexes match what users pass on the CLI.
        self.root = Path(root).expanduser().absolute()
        self.normalize_hyphens = normalize_hyphens
        self.include_paths = [re.compile(p) for p in include_paths or []]
        self.exclude_paths = [re.compile(p) for p in exclude_paths or []]
        self.include_external_patterns = [
            re.compile(p) for p in include_external_patterns or []
        ]
        self.include_http_hosts = include_http_hosts

    def analyze(self) -> DependencyGraph:
        """Walk the project directory and return a populated graph."""
        graph = DependencyGraph()
        py_files = _iter_python_files(
            self.root,
            include_paths=self.include_paths or None,
            exclude_paths=self.exclude_paths or None,
        )
        source_roots = _find_source_roots(self.root)

        # First pass: register all modules
        module_map: dict[str, Path] = {}
        source_map: dict[str, str] = {}
        for py_file in py_files:
            base = _select_source_root(py_file, source_roots) or self.root
            mod_name = _module_name_from_path(
                py_file,
                base,
                self.normalize_hyphens,
            )
            source = py_file.read_text(encoding="utf-8", errors="replace")
            module_map[mod_name] = py_file
            source_map[mod_name] = source
            graph.add_module(
                Module(name=mod_name, path=str(py_file), loc=_count_loc(source))
            )

        known_modules = set(module_map.keys())

        # Second pass: resolve imports → edges
        for mod_name, py_file in module_map.items():
            source = source_map.get(mod_name, "")
            raw_imports = _collect_imports(source, mod_name)
            for imported, lineno in raw_imports:
                # Only keep dependencies that are internal to the project
                target = _resolve_internal(imported, known_modules)
                if target is not None:
                    graph.add_dependency(
                        Dependency(source=mod_name, target=target, line=lineno)
                    )
                    continue

                external = _resolve_external(
                    imported,
                    self.include_external_patterns,
                )
                if external is None:
                    continue
                if graph.get_module(external) is None:
                    graph.add_module(Module(name=external, path=""))
                graph.add_dependency(
                    Dependency(source=mod_name, target=external, line=lineno)
                )

            if not self.include_http_hosts:
                continue
            for host, lineno in _collect_http_hosts(source):
                if graph.get_module(host) is None:
                    graph.add_module(Module(name=host, path=""))
                graph.add_dependency(
                    Dependency(source=mod_name, target=host, line=lineno)
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


def _resolve_external(
    imported: str,
    patterns: list[re.Pattern[str]],
) -> str | None:
    if not patterns:
        return None
    if not any(pattern.search(imported) for pattern in patterns):
        return None
    return imported.split(".", 1)[0]


def _collect_http_hosts(source: str) -> list[tuple[str, int]]:
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", SyntaxWarning)
            tree = ast.parse(source)
    except SyntaxError:
        return []

    collector = _HttpHostCollector()
    collector.visit(tree)
    return collector.hosts


class _HttpHostCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.hosts: list[tuple[str, int]] = []
        self._scopes: list[dict[str, str]] = [{}]

    def visit_Assign(self, node: ast.Assign) -> None:
        host = _http_host_from_expr(node.value, self._scopes)
        if host:
            for target in node.targets:
                self._bind_target(target, host)
                if _is_url_like_target(target):
                    self.hosts.append((host, getattr(node, "lineno", 0)))
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if node.value is not None:
            host = _http_host_from_expr(node.value, self._scopes)
            if host:
                self._bind_target(node.target, host)
                if _is_url_like_target(node.target):
                    self.hosts.append((host, getattr(node, "lineno", 0)))
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        values = list(node.args)
        values.extend(kw.value for kw in node.keywords if kw.arg is not None)
        for value in values:
            host = _http_host_from_expr(value, self._scopes)
            if host:
                lineno = getattr(value, "lineno", node.lineno)
                self.hosts.append((host, lineno))
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_scoped(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_scoped(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._visit_scoped(node)

    def _visit_scoped(
        self,
        node: ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef,
    ) -> None:
        self._scopes.append({})
        for child in node.body:
            self.visit(child)
        self._scopes.pop()

    def _bind_target(self, target: ast.expr, host: str) -> None:
        if isinstance(target, ast.Name):
            self._scopes[-1][target.id] = host


def _http_host_from_text(value: str) -> str | None:
    text = value.strip()
    if not (text.startswith("http://") or text.startswith("https://")):
        return None
    parsed = urlsplit(text)
    if not parsed.netloc:
        return None
    host = parsed.netloc.split("@")[-1].split(":")[0].lower().strip()
    if not host:
        return None
    return host


def _http_host_from_expr(expr: ast.AST, scopes: list[dict[str, str]]) -> str | None:
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str):
        return _http_host_from_text(expr.value)

    if isinstance(expr, ast.JoinedStr):
        rendered = _render_joined_str(expr, scopes)
        if rendered is None:
            return None
        return _http_host_from_text(rendered)

    if isinstance(expr, ast.Name):
        return _lookup_scoped_host(expr.id, scopes)

    return None


def _render_joined_str(expr: ast.JoinedStr, scopes: list[dict[str, str]]) -> str | None:
    pieces: list[str] = []
    for value in expr.values:
        if isinstance(value, ast.Constant) and isinstance(value.value, str):
            pieces.append(value.value)
            continue
        if isinstance(value, ast.FormattedValue):
            if isinstance(value.value, ast.Name):
                resolved = _lookup_scoped_host(value.value.id, scopes)
                if resolved:
                    current = "".join(pieces)
                    if current.endswith("http://") or current.endswith("https://"):
                        pieces.append(resolved)
                    elif "http://" in current or "https://" in current:
                        pieces.append(resolved)
                    else:
                        pieces.append(f"https://{resolved}")
                    continue
            pieces.append("x")
            continue
        return None
    return "".join(pieces)


def _lookup_scoped_host(name: str, scopes: list[dict[str, str]]) -> str | None:
    for scope in reversed(scopes):
        host = scope.get(name)
        if host:
            return host
    return None


def _is_url_like_target(target: ast.expr) -> bool:
    if not isinstance(target, ast.Name):
        return False
    lowered = target.id.lower()
    return "url" in lowered or "uri" in lowered or "endpoint" in lowered


def _count_loc(source: str) -> int:
    """Count executable code lines excluding headers/docstrings/comments."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return 0
    docstring_lines = _docstring_lines(tree)
    header_lines = _header_lines(tree)
    excluded = docstring_lines | header_lines
    lines: set[int] = set()
    stream = io.StringIO(source).readline
    for tok in tokenize.generate_tokens(stream):
        if tok.type in {
            token.ENCODING,
            token.NL,
            token.NEWLINE,
            token.INDENT,
            token.DEDENT,
            token.COMMENT,
            token.ENDMARKER,
        }:
            continue
        if tok.start[0] in excluded:
            continue
        lines.add(tok.start[0])
    return len(lines)


def _docstring_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if not isinstance(body, list) or not body:
            continue
        first = body[0]
        if not isinstance(first, ast.Expr):
            continue
        value = first.value
        if not isinstance(value, ast.Constant) or not isinstance(value.value, str):
            continue
        start = getattr(first, "lineno", 0)
        end = getattr(first, "end_lineno", start)
        lines.update(range(start, end + 1))
    return lines


def _header_lines(tree: ast.AST) -> set[int]:
    lines: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        start = getattr(node, "lineno", 0)
        if not start:
            continue
        body = getattr(node, "body", [])
        first_body_line = getattr(body[0], "lineno", start + 1) if body else start + 1
        end = max(start, first_body_line - 1)
        lines.update(range(start, end + 1))
    return lines


def _find_source_roots(root: Path) -> list[Path]:
    roots: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        if os.path.basename(dirpath) == "src":
            src_path = Path(dirpath).absolute()
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
