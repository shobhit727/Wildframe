#!/usr/bin/env python3
"""Assert the OpenTelemetry floor holds in every committed poetry.lock.

Why this exists separately from `poetry check --lock`
---------------------------------------------------
`poetry check --lock` compares the lock's content-hash against the *directly
declared* dependencies of its own pyproject.toml. Most services do not declare
the OpenTelemetry packages at all -- they arrive transitively through the
`wildframe-observability` path dependency. So when wildframe_observability
raised its floor to 0.64b0, `poetry check --lock` stayed green on five service
locks that were still pinning opentelemetry-instrumentation-fastapi 0.49b2.

0.49b2 is the release that breaks the application: its `_get_route_details`
reads `starlette_route.path` unguarded, so any route added through
`include_router` raises `AttributeError: '_IncludedRouter' object has no
attribute 'path'` and answers HTTP 500. That is a runtime failure in a
production path, delivered by a lockfile that every existing check called
consistent. See issue #977.

The floors are the ones the observability SDK documents:
  * instrumentation / semantic-conventions 0.64b0 -- first release whose
    _get_route_details() tolerates an _IncludedRouter in scope["route"].
    0.50b0 and 0.55b0 carry the identical unguarded code, so "bump one release"
    is not a fix.
  * API / SDK 1.43.0 -- moves with the above: 0.64b0 instrumentations require
    semantic-conventions==0.64b0, and every SDK below 1.43.0 pins
    semantic-conventions to an older 0.4x, so a lower floor cannot install.

The Jaeger exporter is banned outright. It was discontinued upstream at 1.21.0
with no later release; spans ship over OTLP, and nothing in the tree imports
it. It is listed because it is exactly the kind of package that survives in a
lockfile after the code that used it is gone, and the Trivy `fs` scan reads
every committed lock.

Usage:
    python .github/scripts/check-lockfile-floors.py [--root .]

Exits 0 when every lockfile satisfies the floors, 1 otherwise, printing every
violation with its file and line so the finding is reviewable in the log.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# package name -> minimum (major, minor). Compared on the leading numeric pair
# only, so a prerelease such as 0.64b0 satisfies the 0.64 floor and 0.49b2
# does not.
FLOORS: dict[str, tuple[int, int]] = {
    "opentelemetry-api": (1, 43),
    "opentelemetry-sdk": (1, 43),
    "opentelemetry-exporter-otlp": (1, 43),
    "opentelemetry-instrumentation": (0, 64),
    "opentelemetry-instrumentation-fastapi": (0, 64),
    "opentelemetry-instrumentation-asgi": (0, 64),
    "opentelemetry-semantic-conventions": (0, 64),
}

# Discontinued upstream at 1.21.0 with no later release; superseded by OTLP.
BANNED_PREFIX = "opentelemetry-exporter-jaeger"

_PACKAGE_RE = re.compile(
    r'^\[\[package\]\]\s*\nname = "([^"]+)"\s*\nversion = "([^"]+)"', re.MULTILINE
)
_LEADING_NUMERIC_RE = re.compile(r"(\d+)(?:\.(\d+))?")


def numeric_prefix(version: str) -> tuple[int, int]:
    """Leading (major, minor) of a version string, ignoring any prerelease tag.

    '0.64b0' -> (0, 64) and '1.43.0' -> (1, 43). A version with no leading digit
    is reported as (0, 0) so it cannot accidentally satisfy a floor.
    """
    match = _LEADING_NUMERIC_RE.match(version)
    if not match:
        return (0, 0)
    return (int(match.group(1)), int(match.group(2) or 0))


def find_lockfiles(root: Path) -> list[Path]:
    lockfiles = [root / "poetry.lock"]
    lockfiles += sorted((root / "services").glob("*/poetry.lock"))
    lockfiles += sorted((root / "packages" / "sdk").glob("*/poetry.lock"))
    return [path for path in lockfiles if path.is_file()]


def check(path: Path, root: Path) -> list[str]:
    text = path.read_text(encoding="utf-8")
    relative = path.relative_to(root) if path.is_relative_to(root) else path
    problems: list[str] = []

    for match in _PACKAGE_RE.finditer(text):
        name, version = match.group(1), match.group(2)
        # A real 1-based line number, not a match index: the message is meant to
        # be clickable, and an index would point at the wrong package.
        line_number = text.count("\n", 0, match.start()) + 1
        if name.startswith(BANNED_PREFIX):
            problems.append(
                f"{relative}:{line_number}: {name} {version} is locked. The Jaeger "
                "exporter was discontinued at 1.21.0; spans ship over OTLP and "
                "nothing imports this package. Remove the declaration and relock."
            )
            continue

        floor = FLOORS.get(name)
        if floor is None:
            continue

        found = numeric_prefix(version)
        if found < floor:
            problems.append(
                f"{relative}:{line_number}: {name} {version} is below the required "
                f"floor {floor[0]}.{floor[1]}. At this version every "
                "`include_router` route raises AttributeError and answers 500 "
                "(issue #977). Run `poetry lock` in that package."
            )

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Assert the OpenTelemetry floor in every committed poetry.lock."
    )
    parser.add_argument(
        "--root",
        default=".",
        help="Repository root to scan (default: the current directory).",
    )
    args = parser.parse_args(argv)

    root = Path(args.root).resolve()
    lockfiles = find_lockfiles(root)
    if not lockfiles:
        print(f"No poetry.lock found under {root}", file=sys.stderr)
        return 1

    problems: list[str] = []
    for path in lockfiles:
        problems.extend(check(path, root))

    if problems:
        print(
            f"OpenTelemetry floor violations in {len(problems)} place(s) "
            f"across {len(lockfiles)} lockfile(s):\n",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "\nThese locks ship a release that 500s every include_router route, "
            "or a package that was discontinued upstream. `poetry check --lock` "
            "does not catch either case: the constraint lives in a path "
            "dependency, which the content-hash does not cover.",
            file=sys.stderr,
        )
        return 1

    print(
        f"OpenTelemetry floor holds across {len(lockfiles)} lockfile(s): "
        "instrumentation >= 0.64, API/SDK >= 1.43, no EOL Jaeger exporter."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
