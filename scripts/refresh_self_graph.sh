#!/usr/bin/env bash
set -euo pipefail

make self-graph

git add \
  artifacts/self-graph/import-cruiser-self-graph.dot \
  artifacts/self-graph/import-cruiser-self-graph.html \
  artifacts/self-graph/import-cruiser-self-graph.svg
