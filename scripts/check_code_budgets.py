#!/usr/bin/env python3
"""Enforce file/function size and complexity budgets."""

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]


@dataclass
class BudgetConfig:
    max_file_loc: int = 420
    max_function_loc: int = 130
    max_function_complexity: int = 15
    paths: list[str] | None = None
    exclude_paths: list[str] | None = None
    file_allowlist: set[str] | None = None
    function_allowlist: set[str] | None = None

    def __post_init__(self) -> None:
        if self.paths is None:
            self.paths = ["src"]
        if self.exclude_paths is None:
            self.exclude_paths = []
        if self.file_allowlist is None:
            self.file_allowlist = set()
        if self.function_allowlist is None:
            self.function_allowlist = set()


def _load_config(pyproject_path: Path) -> BudgetConfig:
    if not pyproject_path.exists():
        return BudgetConfig()
    data = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    cfg = data.get("tool", {}).get("import_cruiser_code_budget", {})
    return BudgetConfig(
        max_file_loc=int(cfg.get("max_file_loc", 420)),
        max_function_loc=int(cfg.get("max_function_loc", 130)),
        max_function_complexity=int(cfg.get("max_function_complexity", 15)),
        paths=list(cfg.get("paths", ["src"])),
        exclude_paths=list(cfg.get("exclude_paths", [])),
        file_allowlist=set(cfg.get("file_allowlist", [])),
        function_allowlist=set(cfg.get("function_allowlist", [])),
    )


def _iter_python_files(paths: list[Path], exclude_paths: list[str]) -> list[Path]:
    excluded = [Path(pattern) for pattern in exclude_paths]
    files: list[Path] = []
    for root in paths:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
            continue
        for path in root.rglob("*.py"):
            if any(part in {"__pycache__", ".venv", "venv"} for part in path.parts):
                continue
            if any(str(path).startswith(str(excluded_path)) for excluded_path in excluded):
                continue
            files.append(path)
    return sorted(files)


def _non_comment_loc(path: Path) -> int:
    count = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def _module_name(path: Path, src_root: Path) -> str:
    rel = path.relative_to(src_root)
    parts = list(rel.parts)
    parts[-1] = parts[-1][:-3]
    if parts[-1] == "__init__":
        parts = parts[:-1]
    return ".".join(parts)


def _function_non_comment_loc(lines: list[str], node: ast.AST) -> int:
    if not hasattr(node, "lineno") or not hasattr(node, "end_lineno"):
        return 0
    start = max(1, int(node.lineno))
    end = max(start, int(node.end_lineno))
    count = 0
    for idx in range(start - 1, min(end, len(lines))):
        stripped = lines[idx].strip()
        if not stripped or stripped.startswith("#"):
            continue
        count += 1
    return count


def _complexity(node: ast.AST) -> int:
    score = 1
    for child in ast.walk(node):
        if isinstance(
            child,
            (
                ast.If,
                ast.For,
                ast.AsyncFor,
                ast.While,
                ast.ExceptHandler,
                ast.IfExp,
                ast.comprehension,
                ast.Match,
            ),
        ):
            score += 1
        elif isinstance(child, ast.BoolOp):
            score += max(0, len(child.values) - 1)
    return score


def _attach_parents(node: ast.AST) -> None:
    for child in ast.iter_child_nodes(node):
        child.parent = node  # type: ignore[attr-defined]
        _attach_parents(child)


def main() -> int:
    root = Path.cwd()
    config = _load_config(root / "pyproject.toml")
    scan_paths = [root / p for p in config.paths]
    files = _iter_python_files(scan_paths, config.exclude_paths)
    src_root = root / "src"
    violations: list[str] = []

    for path in files:
        rel = str(path.relative_to(root))
        file_loc = _non_comment_loc(path)
        if file_loc > config.max_file_loc and rel not in config.file_allowlist:
            violations.append(
                f"[file-loc] {rel}: {file_loc} > {config.max_file_loc} (add to allowlist or split file)"
            )

        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=rel)
        _attach_parents(tree)
        lines = source.splitlines()
        module = _module_name(path, src_root) if path.is_relative_to(src_root) else rel.replace("/", ".")

        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            if isinstance(getattr(node, "parent", None), ast.ClassDef):
                class_name = node.parent.name  # type: ignore[attr-defined]
                qualified = f"{module}.{class_name}.{node.name}"
            else:
                qualified = f"{module}.{node.name}"
            fn_loc = _function_non_comment_loc(lines, node)
            fn_complexity = _complexity(node)
            if qualified in config.function_allowlist:
                continue
            if fn_loc > config.max_function_loc:
                violations.append(
                    f"[function-loc] {qualified}: {fn_loc} > {config.max_function_loc}"
                )
            if fn_complexity > config.max_function_complexity:
                violations.append(
                    f"[function-complexity] {qualified}: {fn_complexity} > {config.max_function_complexity}"
                )

    if violations:
        print("Code budget check failed:")
        for line in sorted(violations):
            print(f"- {line}")
        return 1
    print("Code budget check passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
