#!/usr/bin/env python3
"""Disallow Any and untyped collection annotations."""
from __future__ import annotations

import argparse
import ast
from dataclasses import dataclass
from pathlib import Path

FORBIDDEN_UNSUBSCRIPTED = {
    "dict",
    "list",
    "set",
    "tuple",
    "Sequence",
    "Mapping",
    "MutableMapping",
    "MutableSequence",
    "Iterable",
    "Iterator",
}
FORBIDDEN_NAMES = {"Any"}


@dataclass
class Violation:
    path: Path
    lineno: int
    col: int
    message: str


class AnnotationScanner(ast.NodeVisitor):
    def __init__(self, path: Path) -> None:
        self.path = path
        self.parents: list[ast.AST] = []
        self.violations: list[Violation] = []

    def visit(self, node: ast.AST) -> None:
        self.parents.append(node)
        super().visit(node)
        self.parents.pop()

    def _is_subscript_value(self, node: ast.AST) -> bool:
        if len(self.parents) < 2:
            return False
        parent = self.parents[-2]
        return isinstance(parent, ast.Subscript) and parent.value is node

    def _add_violation(self, node: ast.AST, message: str) -> None:
        lineno = getattr(node, "lineno", 1)
        col = getattr(node, "col_offset", 0)
        self.violations.append(Violation(self.path, lineno, col, message))

    def visit_Name(self, node: ast.Name) -> None:
        if node.id in FORBIDDEN_NAMES:
            self._add_violation(node, f"{node.id} is not allowed in type annotations")
        elif node.id in FORBIDDEN_UNSUBSCRIPTED and not self._is_subscript_value(node):
            self._add_violation(
                node,
                f"Untyped collection '{node.id}' is not allowed in type annotations",
            )
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        if node.attr in FORBIDDEN_NAMES:
            self._add_violation(node, f"{node.attr} is not allowed in type annotations")
        elif node.attr in FORBIDDEN_UNSUBSCRIPTED and not self._is_subscript_value(node):
            self._add_violation(
                node,
                f"Untyped collection '{node.attr}' is not allowed in type annotations",
            )
        self.generic_visit(node)


def iter_python_files(paths: list[Path], exclude: set[Path]) -> list[Path]:
    files: list[Path] = []
    for root in paths:
        if root.is_file() and root.suffix == ".py":
            files.append(root)
            continue
        if not root.is_dir():
            continue
        for path in root.rglob("*.py"):
            parts = {p for p in path.parts}
            if "__pycache__" in parts or ".venv" in parts or "venv" in parts:
                continue
            if any(excl in path.parts for excl in exclude):
                continue
            files.append(path)
    return files


def iter_annotation_nodes(tree: ast.AST) -> list[ast.AST]:
    annotations: list[ast.AST] = []
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for arg in (*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs):
                if arg.annotation is not None:
                    annotations.append(arg.annotation)
            if node.args.vararg and node.args.vararg.annotation is not None:
                annotations.append(node.args.vararg.annotation)
            if node.args.kwarg and node.args.kwarg.annotation is not None:
                annotations.append(node.args.kwarg.annotation)
            if node.returns is not None:
                annotations.append(node.returns)
        elif isinstance(node, ast.AnnAssign):
            if node.annotation is not None:
                annotations.append(node.annotation)
            if (
                node.annotation
                and isinstance(node.annotation, (ast.Name, ast.Attribute))
                and getattr(node.annotation, "id", getattr(node.annotation, "attr", "")) == "TypeAlias"
                and node.value is not None
            ):
                annotations.append(node.value)
    return annotations


def check_file(path: Path) -> list[Violation]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError as exc:  # pragma: no cover
        return [Violation(path, exc.lineno or 1, exc.offset or 0, f"SyntaxError: {exc.msg}")]

    violations: list[Violation] = []
    for annotation in iter_annotation_nodes(tree):
        scanner = AnnotationScanner(path)
        scanner.visit(annotation)
        violations.extend(scanner.violations)
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--paths", nargs="*", default=["src", "tests"])
    parser.add_argument("--exclude", nargs="*", default=["migrations", "scripts", "examples"])
    args = parser.parse_args()

    paths = [Path(p) for p in args.paths]
    exclude = {Path(p).name for p in args.exclude}

    violations: list[Violation] = []
    for path in iter_python_files(paths, exclude):
        violations.extend(check_file(path))

    if violations:
        print("Typed collection check failed:")
        for violation in violations:
            print(f"- {violation.path}:{violation.lineno}:{violation.col} {violation.message}")
        return 1
    print("Typed collection check OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
