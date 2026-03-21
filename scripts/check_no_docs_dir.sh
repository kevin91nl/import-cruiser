#!/bin/sh
if [ -d "docs" ]; then
  echo "docs/ directory is not allowed in this repo."
  exit 1
fi
exit 0
