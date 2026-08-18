"""No archive extraction anywhere in the platform (#536).

Audit finding: "Verify archive symlinks/hardlinks cannot redirect extracted
writes outside the worker sandbox."

The platform has NO archive-unpacking code path (no tarfile/zipfile/
unpack_archive in any service, worker, or infra script) — the attack surface
does not exist. This test pins that invariant: if archive extraction is ever
introduced, it must come with a reviewed sandboxed extraction design
(reject symlinks/hardlinks/absolute members, extract strictly under the
worker sandbox root), not silently land.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]

ARCHIVE_APIS = re.compile(
    r"extractall\s*\(|"
    r"tarfile\.(open|extract|extractall)|"
    r"zipfile\.ZipFile|"
    r"shutil\.unpack_archive|"
    r"ZipFile\s*\(|"
    r"from\s+(?:tarfile|zipfile)\s+import"
)

TARGET_DIRS = [REPO / "services", REPO / "apps"]


def _archive_api_hits() -> list[tuple[str, str]]:
    hits: list[tuple[str, str]] = []
    for base in TARGET_DIRS:
        for path in sorted(base.rglob("*")):
            if not path.is_file() or path.suffix not in (
                ".py", ".ts", ".tsx", ".sh", ".yml", ".yaml", ".json",
            ):
                continue
            if any(
                part in str(path)
                for part in ("node_modules", ".mypy_cache", "__pycache__", ".venv", ".git")
            ):
                continue
            text = path.read_text(errors="ignore")
            for match in ARCHIVE_APIS.finditer(text):
                hits.append((str(path.relative_to(REPO)), match.group(0)))
    return hits


def test_no_archive_extraction_apis_anywhere() -> None:
    hits = _archive_api_hits()
    assert not hits, (
        "Archive extraction APIs found (possible symlink-escape path, #536):\n"
        + "\n".join(f"{path}: {api}" for path, api in hits)
        + "\n\nIf intentional, extraction MUST be sandboxed: reject symlinks/"
        "hardlinks/absolute members and extract strictly under the worker "
        "sandbox root."
    )


def test_no_archive_processing_in_critical_services() -> None:
    """Defense in depth: media-pipeline and uploads-service must not grow
    archive handling without explicit review. Scans runtime code only;
    tests/ are excluded (they may legitimately reference archive strings
    while pinning the invariant)."""
    suspicious = []
    for svc in ("media-pipeline", "uploads-service"):
        base = REPO / "services" / svc
        for path in sorted(base.rglob("*.py")):
            if "tests" in path.parts:
                continue
            text = path.read_text(errors="ignore")
            if re.search(r"\.zip|\.tar|\.gz", text):
                suspicious.append(str(path.relative_to(REPO)))
    assert not suspicious, f"archive references in worker services: {suspicious}"