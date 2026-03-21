#!/usr/bin/env python3
"""Run unit tests (targeted when possible) for fast pre-commit feedback."""
from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _pytest_available() -> bool:
    return shutil.which("pytest") is not None


def main() -> int:
    if not _pytest_available():
        print("pytest not installed; skipping targeted unit tests.")
        return 0

    test_root = ROOT / "tests"
    if not test_root.exists():
        print("No tests/ directory found; skipping unit tests.")
        return 0

    cmd = [sys.executable, "-m", "pytest", "tests", "-q", "--disable-warnings", "--maxfail=1"]
    print("Running:", " ".join(cmd))
    return subprocess.call(cmd)


if __name__ == "__main__":
    raise SystemExit(main())
