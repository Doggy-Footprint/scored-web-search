#!/usr/bin/env bash
# Publish the skill zip to the rolling `latest` release, and once per
# plugin.json version to a vX.Y.Z release that is never touched again.
# Usage: publish_release.sh <zip> <commit-sha>
set -euo pipefail
root="$(cd "$(dirname "$0")/.." && pwd)"
zip="$1"
sha="$2"
notes="Built from $sha"

if gh release view latest >/dev/null 2>&1; then
  # Uploading does not move the tag; without this the Source code archives stay at the first build.
  gh api -X PATCH "repos/{owner}/{repo}/git/refs/tags/latest" -f sha="$sha" -F force=true >/dev/null
  gh release upload latest "$zip" --clobber
  gh release edit latest --notes "$notes"
else
  gh release create latest "$zip" --title "latest" --notes "$notes" --target "$sha"
fi

version="v$(python3 -c 'import json, sys; print(json.load(open(sys.argv[1]))["version"])' "$root/.claude-plugin/plugin.json")"
if ! gh release view "$version" >/dev/null 2>&1; then
  # README links /releases/latest/download/, so GitHub's Latest mark must stay on the rolling release.
  gh release create "$version" "$zip" --title "$version" --notes "$notes" --target "$sha" --latest=false
fi
