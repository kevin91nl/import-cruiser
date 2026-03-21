#!/usr/bin/env python3
"""Ensure tests/unit mirrors src package structure at directory level."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = ROOT / "src"
TEST_ROOT = ROOT / "tests" / "unit"

SKIP_DIRS = {"__pycache__", "migrations", "migrations/versions"}
ALLOWLIST_DIRS = {""}


def _has_test_files(test_dir: Path) -> bool:
    if not test_dir.exists():
        return False
    return any(
        p.is_file() and p.name.startswith("test_") and p.suffix == ".py"
        for p in test_dir.rglob("test_*.py")
    )


def _package_roots(src_root: Path) -> list[Path]:
    if not src_root.exists():
        return []
    return sorted(
        p for p in src_root.iterdir() if p.is_dir() and (p / "__init__.py").exists()
    )


def main() -> int:
    if not TEST_ROOT.exists():
        print("Tests structure check skipped (tests/unit not found).")
        return 0

    missing: list[str] = []
    for package_root in _package_roots(SRC_ROOT):
        for path in package_root.rglob("*.py"):
            if path.name == "__init__.py":
                continue
            rel_dir = str(path.parent.relative_to(package_root))
            if rel_dir in SKIP_DIRS or rel_dir in ALLOWLIST_DIRS:
                continue
            test_dir = TEST_ROOT / package_root.name / rel_dir
            if not _has_test_files(test_dir):
                missing.append(f"{package_root.name}/{rel_dir}")

    if missing:
        unique = sorted(set(missing))
        print("Missing tests for src subdirectories:")
        for rel in unique:
            print(f" - tests/unit/{rel}")
        return 1

    print("Tests are in sync with src structure.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
