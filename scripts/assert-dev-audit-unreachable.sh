#!/usr/bin/env bash
# Guard the one deliberate exception in the frontend SCA gate.
#
# `npm audit --omit=dev --audit-level=high` gates the dependencies that actually
# ship. That is stricter than it looks for production, and it is looser for
# dev-only tooling, so this script pins the assumption that makes the looseness
# safe: the packages carrying unpatched dev advisories must never become
# reachable from the production dependency graph.
#
# Why an exception exists at all:
#   braces  GHSA-vfj7-8cjw-p6xm  stack exhaustion via deeply nested patterns
#   Upstream has published no fixed version (latest 3.0.3 is affected), and the
#   only path to it is eslint-config-next -> @next/eslint-plugin-next ->
#   fast-glob -> micromatch -> braces. micromatch has no release after 4.0.8 and
#   hard-depends on braces ^3.0.3, so the chain cannot be broken from below and
#   removing it means dropping Next's own lint rules.
#
# This is not a suppression: if braces ever reaches production, this exits
# non-zero and the job fails. Trivy also scans the root package-lock.json
# independently, so a reachable finding is caught by two mechanisms.
#
# Usage: scripts/assert-dev-audit-unreachable.sh [package ...]
# Exit:  0 = none of the named packages is in the production graph
#        1 = at least one is reachable (CI fails)
#        2 = the check itself could not run (also CI fails)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# Packages whose advisories have no upstream patch. Add a package here only
# alongside a recorded justification, and keep the list as short as possible.
DEFAULT_PACKAGES=(braces)

if [ "$#" -gt 0 ]; then
    PACKAGES=("$@")
else
    PACKAGES=("${DEFAULT_PACKAGES[@]}")
fi

if ! command -v npm >/dev/null 2>&1; then
    echo "::error::npm is not available; cannot verify audit reachability" >&2
    exit 2
fi

reachable=()

for pkg in "${PACKAGES[@]}"; do
    # `npm ls --omit=dev` exits 0 when the package is present in the production
    # graph and non-zero ("(empty)") when it is absent. That exit code is the
    # signal; the tree text is only for humans.
    if tree="$(npm ls "$pkg" --omit=dev --all 2>/dev/null)"; then
        reachable+=("$pkg")
        {
            echo "::error title=Dev advisory reachable from production::$pkg is in the production dependency graph."
            echo "The SCA gate audits production dependencies, so this finding would not be"
            echo "reported by \`npm audit --omit=dev\`. It is listed in"
            echo "scripts/assert-dev-audit-unreachable.sh as dev-only and no longer is."
            echo ""
            echo "Production tree for $pkg:"
            echo '```'
            echo "$tree"
            echo '```'
        } >&2
    fi
done

if [ "${#reachable[@]}" -ne 0 ]; then
    echo "FAIL: ${#reachable[@]} package(s) assumed dev-only are in production: ${reachable[*]}" >&2
    exit 1
fi

echo "OK: ${#PACKAGES[@]} package(s) checked, none reachable from production: ${PACKAGES[*]}"
