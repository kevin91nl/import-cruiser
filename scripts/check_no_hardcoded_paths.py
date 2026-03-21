from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATTERNS = [
    re.compile(r"/Users/"),
    re.compile(r"/home/"),
    re.compile(r"[A-Za-z]:\\\\"),
    re.compile(r"sys\.path\.(append|insert)\("),
]


def main() -> int:
    for path in ROOT.rglob("*.py"):
        parts = set(path.parts)
        if {"tests", "scripts"} & parts:
            continue
        if ".venv" in parts or "venv" in parts or "site-packages" in parts:
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            if pattern.search(text):
                print(f"Hardcoded path or sys.path hack in {path}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
