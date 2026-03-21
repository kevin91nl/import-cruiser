#!/usr/bin/env python3
"""Fail when src packages import their own deeper subpackages or tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
TEST_ROOT = ROOT / "tests"


def _iter_python_files(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return sorted(root.rglob("*.py"))


def _collect_all_imports(tree: ast.AST) -> list[tuple[int, str]]:
    imports: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imports.append((node.lineno, alias.name))
        elif isinstance(node, ast.ImportFrom) and node.module:
            imports.append((node.lineno, node.module))
    return imports


def _parse(path: Path) -> ast.AST:
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _package_roots(src_root: Path) -> list[Path]:
    if not src_root.exists():
        return []
    return sorted(
        p for p in src_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    )


def _collect_parent_to_child_import_violations(
    *,
    rel: str,
    imports: list[tuple[int, str]],
    package_prefix: str,
    file_prefix: str,
) -> list[str]:
    if rel.endswith("/__init__.py") or rel == "__init__.py":
        return []
    module_parts = Path(rel).with_suffix("").parts
    if not module_parts:
        return []
    prefix = f"{package_prefix}."
    errors: list[str] = []
    for lineno, imported in imports:
        if not imported.startswith(prefix):
            continue
        target_parts = imported.removeprefix(prefix).split(".")
        if len(target_parts) <= len(module_parts):
            continue
        if tuple(target_parts[: len(module_parts)]) == module_parts:
            errors.append(
                f"{file_prefix}/{rel}:{lineno}: forbidden parent->child import ({package_prefix}.{'.'.join(module_parts)} -> {imported})"
            )
    return errors


def _collect_test_import_violations(
    *,
    rel: str,
    imports: list[tuple[int, str]],
    file_prefix: str,
) -> list[str]:
    errors: list[str] = []
    for lineno, imported in imports:
        if imported.startswith("tests"):
            errors.append(f"{file_prefix}/{rel}:{lineno}: forbidden import from tests package: {imported}")
    return errors


def check_architecture_boundaries() -> int:
    errors: list[str] = []

    for package_root in _package_roots(SRC_ROOT):
        package_prefix = package_root.name
        for path in _iter_python_files(package_root):
            rel = str(path.relative_to(package_root))
            try:
                tree = _parse(path)
            except SyntaxError as exc:
                errors.append(f"src/{package_prefix}/{rel}:{exc.lineno}: syntax error: {exc.msg}")
                continue
            imports = _collect_all_imports(tree)
            errors.extend(
                _collect_parent_to_child_import_violations(
                    rel=rel,
                    imports=imports,
                    package_prefix=package_prefix,
                    file_prefix=f"src/{package_prefix}",
                )
            )
            errors.extend(
                _collect_test_import_violations(
                    rel=rel,
                    imports=imports,
                    file_prefix=f"src/{package_prefix}",
                )
            )

    if errors:
        print("Architecture boundary check failed.")
        print(
            "Rule: src packages may not import their own deeper subpackages, and src must not import tests."
        )
        for err in errors:
            print(f"- {err}")
        return 1

    print("Architecture boundary check passed.")
    return 0


if __name__ == "__main__":
    sys.exit(check_architecture_boundaries())
