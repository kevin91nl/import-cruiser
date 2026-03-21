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
        if "tests" in path.parts or "scripts" in path.parts or path.parts[0] == ".venv":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for pattern in PATTERNS:
            if pattern.search(text):
                print(f"Hardcoded path or sys.path hack in {path}")
                return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
