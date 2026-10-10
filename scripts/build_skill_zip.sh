#!/usr/bin/env bash
# Build the claude.ai upload zip. The zip root is the skill folder itself.
# A relative output path is resolved against the caller's cwd.
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
out="${1:-$root/dist/scored-web-search.zip}"
mkdir -p "$(dirname "$out")"
out="$(cd "$(dirname "$out")" && pwd)/$(basename "$out")"
rm -f "$out"
cd "$root/skills"
zip -r -X "$out" scored-web-search -x '*/__pycache__/*' '*.pyc' '*/.DS_Store'
