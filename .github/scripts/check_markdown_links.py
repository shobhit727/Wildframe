#!/usr/bin/env python3
"""Check local Markdown links without requiring network access.

The checker ignores fenced code blocks, external URLs, mailto links, and
fragment-only links. It validates relative paths and repo-root absolute paths.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN = re.compile(r'\[[^\]]+\]\(([^)]+)\)')

def iter_markdown_lines(path: Path):
    """Yield Markdown lines outside fenced code blocks."""
    fenced = False
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
            continue
        if not fenced:
            yield line

def check_target(source: Path, target: str) -> str | None:
    """Return an error when a local Markdown target is missing."""
    target = target.strip().strip("<>")
    if not target or target.startswith("#"):
        return None
    parsed = urlsplit(target)
    if parsed.scheme or target.startswith("//"):
        return None
    raw_path = parsed.path
    if raw_path.startswith("/"):
        destination = ROOT / raw_path.lstrip("/")
    else:
        destination = source.parent / raw_path
    if not raw_path:
        destination = source
    destination = destination.resolve()
    try:
        destination.relative_to(ROOT)
    except ValueError:
        return f"target escapes repository root: {target}"
    if not destination.exists():
        return f"missing target: {target}"
    return None

def main() -> int:
    """Check all Markdown links and return a failing exit code on errors."""
    errors: list[str] = []
    for source in ROOT.rglob("*.md"):
        if any(part in {".git", "node_modules", ".next", ".venv", "venv"} for part in source.parts):
            continue
        for line_no, line in enumerate(iter_markdown_lines(source), start=1):
            for match in MARKDOWN.finditer(line):
                error = check_target(source, match.group(1))
                if error:
                    errors.append(f"{source.relative_to(ROOT)}:{line_no}: {error}")
    if errors:
        print("\n".join(errors))
        print(f"\nFound {len(errors)} broken local Markdown link(s).", file=sys.stderr)
        return 1
    print("All local Markdown links resolve inside the repository.")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
