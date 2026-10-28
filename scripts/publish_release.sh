#!/usr/bin/env bash
# Publish the skill zip as a vX.Y.Z release, once per plugin.json version.
# A published version is never touched again; bump plugin.json to ship a new zip.
# Usage: publish_release.sh <zip> <commit-sha>
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
zip="$1"
sha="$2"

version="v$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$root/.claude-plugin/plugin.json")"
if gh release view "$version" >/dev/null 2>&1; then
  echo "$version is already released; bump .claude-plugin/plugin.json to publish a new zip"
  exit 0
fi
# README links /releases/latest/download/, so the newest version must carry GitHub's Latest mark.
gh release create "$version" "$zip" --title "$version" --notes "Built from $sha" --target "$sha" --latest
