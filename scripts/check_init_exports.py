#!/usr/bin/env python3
"""Check that __init__.py files export their submodules."""

import ast
import sys
from pathlib import Path


def get_python_modules(directory: Path) -> list[str]:
    """Get list of Python modules in directory (excluding __init__ and __pycache__)."""
    modules = []
    for item in directory.iterdir():
        if item.name.startswith("_"):
            continue
        if item.is_file() and item.suffix == ".py":
            modules.append(item.stem)
        elif item.is_dir() and (item / "__init__.py").exists():
            modules.append(item.name)
    return sorted(modules)


def _read_init_content(init_file: Path) -> str | None:
    if not init_file.exists() or init_file.stat().st_size == 0:
        return None
    content = init_file.read_text()
    if not content.strip() or content.strip().startswith('"""') and '"""' == content.strip():
        return None
    return content


def _parse_content(content: str) -> ast.Module | None:
    try:
        return ast.parse(content)
    except SyntaxError:
        return None


def _is_all_assignment(node: ast.AST) -> str | None:
    if isinstance(node, ast.Assign):
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id == "__all__":
                return "__all__"
    elif isinstance(node, ast.AnnAssign):
        if isinstance(node.target, ast.Name) and node.target.id == "__all__":
            return "__all__"
    return None


def _has_explicit_empty_all(tree: ast.Module) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        if _is_all_assignment(node) and isinstance(node.value, ast.List) and len(node.value.elts) == 0:
            return True
    return False


def _collect_exports(tree: ast.Module) -> set[str]:
    exports = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.names:
            for alias in node.names:
                export_name = alias.name if alias.name != "*" else node.module.split(".")[-1]
                exports.add(export_name)
    return exports


def get_init_exports(init_file: Path) -> set[str] | None:
    content = _read_init_content(init_file)
    if content is None:
        return set()
    tree = _parse_content(content)
    if tree is None:
        return set()
    if _has_explicit_empty_all(tree):
        return None
    return _collect_exports(tree)


def check_directory(src_dir: Path) -> list[str]:
    issues = []
    for init_file in src_dir.rglob("__init__.py"):
        parent = init_file.parent
        if "__pycache__" in str(parent):
            continue
        modules = get_python_modules(parent)
        if not modules:
            continue
        exports = get_init_exports(init_file)
        if exports is None:
            continue
        if not exports and modules:
            rel_path = init_file.relative_to(src_dir.parent)
            issues.append(
                f"{rel_path}: Empty __init__.py but has modules: {', '.join(modules)}"
            )
    return issues


def main() -> int:
    repo_root = Path(__file__).parent.parent
    src_dir = repo_root / "src"

    if not src_dir.exists():
        print(f"Error: {src_dir} not found")
        return 1

    issues = check_directory(src_dir)

    if issues:
        print("❌ Found __init__.py files that should export their modules:\n")
        for issue in issues:
            print(f"  • {issue}")
        print("\n💡 Add exports to __init__.py files (e.g., `from .module import Class` and `__all__ = [...]`) ")
        return 1

    print("✅ All __init__.py files properly export their modules")
    return 0


if __name__ == "__main__":
    sys.exit(main())
