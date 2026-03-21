#!/usr/bin/env python3
"""Check root directory file organization rules.

Rules:
1. Only allowed .md files in root: README.md, VERSIONING.md, AGENTS.md
2. All .md files must be ≤ 500 lines
3. No .py files in root (must be in src/, tests/, scripts/, examples/, migrations/)
4. No .sh files in root (must be in scripts/)
"""

import sys
from pathlib import Path


def _get_root_files(root: Path) -> list[Path]:
    """Get all files in root directory (excluding hidden files)."""
    return [f for f in root.iterdir() if f.is_file() and not f.name.startswith(".")]


def _check_allowed_md_files(md_files: list[Path], allowed: set[str]) -> list[str]:
    """Check if md files are in allowed list."""
    errors = []
    for md_file in md_files:
        if md_file.name not in allowed:
            errors.append(
                f"❌ {md_file.name}: Not allowed in root directory\n"
                f"   → Move to docs/ directory or remove\n"
                f"   → Only allowed .md files in root: {', '.join(sorted(allowed))}"
            )
    return errors


def _check_md_file_length(md_files: list[Path], max_lines: int) -> list[str]:
    """Check if md files exceed maximum line count."""
    errors = []
    for md_file in md_files:
        if md_file.exists():
            line_count = len(md_file.read_text().splitlines())
            if line_count > max_lines:
                errors.append(
                    f"❌ {md_file.name}: {line_count} lines (max {max_lines})\n"
                    "   → Split into multiple files in docs/ or reduce content"
                )
    return errors


def _check_forbidden_extension(files: list[Path], extension: str, target_location: str) -> list[str]:
    """Check for files with forbidden extension in root."""
    errors = []
    forbidden = [f for f in files if f.suffix == extension]

    for file in forbidden:
        errors.append(
            f"❌ {file.name}: {extension[1:].upper()} files not allowed in root\n"
            f"   → Move to {target_location}"
        )

    return errors


def _print_violations(errors: list[str], allowed_md: set[str], max_lines: int) -> None:
    """Print all violations with rules summary."""
    print("=" * 70)
    print("❌ ROOT DIRECTORY ORGANIZATION VIOLATIONS")
    print("=" * 70)
    print()
    for error in errors:
        print(error)
        print()
    print("=" * 70)
    print("Rules:")
    print("  1. Only allowed .md files in root:")
    print(f"     {', '.join(sorted(allowed_md))}")
    print(f"  2. All .md files must be ≤ {max_lines} lines")
    print("  3. No .py files in root (use src/, tests/, scripts/, examples/)")
    print("  4. No .sh files in root (use scripts/)")
    print("=" * 70)


def check_root_files() -> int:
    """Check root directory files against organization rules.

    Returns:
        0 if all checks pass, 1 if any violations found
    """
    root = Path(__file__).parent.parent
    root_files = _get_root_files(root)

    allowed_md_files = {"AGENTS.md", "README.md", "VERSIONING.md"}
    max_lines = 500

    md_files = [f for f in root_files if f.suffix == ".md"]

    errors: list[str] = []
    errors.extend(_check_allowed_md_files(md_files, allowed_md_files))
    errors.extend(_check_md_file_length(md_files, max_lines))
    errors.extend(_check_forbidden_extension(root_files, ".py", "src/, tests/, scripts/, examples/, or migrations/"))
    errors.extend(_check_forbidden_extension(root_files, ".sh", "scripts/"))

    if errors:
        _print_violations(errors, allowed_md_files, max_lines)
        return 1

    print("✅ Root directory organization: All checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(check_root_files())
