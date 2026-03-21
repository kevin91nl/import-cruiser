#!/usr/bin/env python3
"""Reject raw SQL usage outside migrations."""
from __future__ import annotations

import argparse
import ast
import re
import sys
from pathlib import Path
from typing import Iterable

SQL_KEYWORDS = re.compile(r"\b(SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP|WITH)\b", re.I)


def iter_python_files(paths: Iterable[Path]) -> Iterable[Path]:
    for root in paths:
        if root.is_file():
            if root.suffix == ".py":
                yield root
            continue
        for path in root.rglob("*.py"):
            parts = set(path.parts)
            if "__pycache__" in parts or ".venv" in parts or "venv" in parts:
                continue
            yield path


def is_in_migrations(path: Path) -> bool:
    return "migrations" in path.parts


def extract_string(node: ast.AST) -> str | None:
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.JoinedStr):
        parts: list[str] = []
        for value in node.values:
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                parts.append(value.value)
        if parts:
            return "".join(parts)
    return None


def call_name(node: ast.AST) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return None


def find_raw_sql(path: Path) -> list[tuple[int, str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except SyntaxError as exc:
        return [(exc.lineno or 1, f"syntax error: {exc.msg}")]

    hits: list[tuple[int, str]] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func_name = call_name(node.func)
        if func_name == "text":
            hits.append((node.lineno, "raw SQL via sqlalchemy.text()"))
            continue
        if func_name == "exec_driver_sql":
            hits.append((node.lineno, "raw SQL via exec_driver_sql()"))
            continue
        if func_name == "execute" and node.args:
            sql_literal = extract_string(node.args[0])
            if sql_literal and SQL_KEYWORDS.search(sql_literal):
                hits.append((node.lineno, "raw SQL literal passed to execute()"))

    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description="Fail if raw SQL is used outside migrations.")
    parser.add_argument("paths", nargs="*", help="Files or directories to scan.")
    args = parser.parse_args()

    if args.paths:
        roots = [Path(p) for p in args.paths]
    else:
        roots = [Path("src"), Path("tests"), Path("scripts"), Path("examples")]

    failures: list[str] = []
    for path in iter_python_files(roots):
        if is_in_migrations(path):
            continue
        for lineno, reason in find_raw_sql(path):
            failures.append(f"{path}:{lineno}: {reason}")

    if failures:
        print("Raw SQL usage detected outside migrations:", file=sys.stderr)
        for line in failures:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
