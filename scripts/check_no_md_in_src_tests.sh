#!/bin/sh
if find src tests -type f -name "*.md" 2>/dev/null | grep -q .; then
  echo "Markdown files are not allowed in src/ or tests/."
  exit 1
fi
exit 0
