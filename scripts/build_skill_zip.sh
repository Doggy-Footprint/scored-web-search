#!/usr/bin/env bash
# Build the claude.ai upload zip. The zip root is the skill folder itself.
set -euo pipefail
cd "$(dirname "$0")/../skills"
out="${1:-../dist/scored-web-search.zip}"
mkdir -p "$(dirname "$out")"
rm -f "$out"
zip -r -X "$out" scored-web-search -x '*/__pycache__/*' '*.pyc' '*/.DS_Store'
