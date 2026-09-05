#!/usr/bin/env bash
# Cut a release: bump VERSION + dashboard/package.json, stamp the CHANGELOG,
# commit and create an annotated tag. Nothing is pushed; the script prints
# the push command at the end so the tag push (which triggers the GitHub
# Release workflow) is a deliberate second step.
#
#   scripts/release.sh 0.4.0
#   scripts/release.sh 0.4.0 --dry-run
set -euo pipefail

usage() { echo "usage: $0 <MAJOR.MINOR.PATCH> [--dry-run]" >&2; exit 2; }

VERSION="${1:-}"; [[ -n "$VERSION" ]] || usage
DRY_RUN=0; [[ "${2:-}" == "--dry-run" ]] && DRY_RUN=1
[[ "$VERSION" =~ ^[0-9]+\.[0-9]+\.[0-9]+([-.][0-9A-Za-z.]+)?$ ]] || { echo "not a semver: $VERSION" >&2; exit 2; }

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"
TAG="v$VERSION"
TODAY="$(date -u +%Y-%m-%d)"

# --- preflight ---------------------------------------------------------------
if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
  echo "working tree has uncommitted changes; commit or stash first" >&2; exit 1
fi
if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
  echo "tag $TAG already exists" >&2; exit 1
fi
CURRENT="$(tr -d '[:space:]' < VERSION)"
if [[ "$CURRENT" == "$VERSION" ]]; then
  echo "VERSION already says $VERSION" >&2; exit 1
fi
if ! grep -q '^## \[Unreleased\]' CHANGELOG.md; then
  echo "CHANGELOG.md has no '## [Unreleased]' section" >&2; exit 1
fi
# Refuse to release an empty changelog section.
UNRELEASED_BODY="$(awk '/^## \[Unreleased\]/{f=1;next} /^## \[/{f=0} f' CHANGELOG.md | grep -v '^\s*$' || true)"
if [[ -z "$UNRELEASED_BODY" ]]; then
  echo "the [Unreleased] section of CHANGELOG.md is empty; write the notes first" >&2; exit 1
fi
# Secrets guard: the same check the workflow runs.
if git grep -nE 'sk-lf-[0-9a-f]{8}|pk-lf-[0-9a-f]{8}|sk-[A-Za-z0-9]{20,}|ghp_[A-Za-z0-9]{20,}' -- . ':!CHANGELOG.md' ':!scripts/release.sh' >/dev/null; then
  echo "tracked files contain what looks like an API key; refusing to release" >&2; exit 1
fi

echo "release $CURRENT -> $VERSION ($TAG, $TODAY)"
if (( DRY_RUN )); then echo "dry run: no files changed"; exit 0; fi

# --- bump --------------------------------------------------------------------
printf '%s\n' "$VERSION" > VERSION

# Mirror into the dashboard so the built bundle reports the same version.
( cd dashboard && npm version "$VERSION" --no-git-tag-version --allow-same-version >/dev/null )

# Stamp the changelog: [Unreleased] -> [Unreleased] + [VERSION] - DATE,
# and refresh the compare links at the bottom.
python3 - "$VERSION" "$TODAY" "$CURRENT" <<'PY'
import re, sys
version, today, previous = sys.argv[1:4]
p = "CHANGELOG.md"
s = open(p, encoding="utf-8").read()
s = s.replace("## [Unreleased]\n", f"## [Unreleased]\n\n## [{version}] - {today}\n", 1)
repo = "https://github.com/HSE-Pulse/med-ai"
s = re.sub(r"^\[Unreleased\]: .*$",
           f"[Unreleased]: {repo}/compare/v{version}...HEAD\n"
           f"[{version}]: {repo}/compare/v{previous}...v{version}",
           s, count=1, flags=re.M)
open(p, "w", encoding="utf-8").write(s)
PY

# --- commit + tag ------------------------------------------------------------
git add VERSION CHANGELOG.md dashboard/package.json dashboard/package-lock.json
git commit -q -m "release: $TAG"
NOTES="$(awk -v v="$VERSION" '$0 ~ "^## \\["v"\\]"{f=1;next} /^## \[/{f=0} f' CHANGELOG.md)"
git tag -a "$TAG" -m "Med AI $TAG" -m "$NOTES"

cat <<MSG

created commit $(git rev-parse --short HEAD) and tag $TAG

to publish (the tag push triggers the GitHub Release workflow):
  git push public main && git push public $TAG
MSG
