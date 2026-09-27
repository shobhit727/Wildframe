"""Integrity guards for the shared agent Message-board.

The board is an append-only log that several agents write concurrently in one
checkout. That has two distinct failure modes and they need different fixes.

**1. Conflicting appends.** Two agents append at once and git sees a conflict
across the whole file. `.gitattributes` sets `Message-board.md merge=union` so
git keeps both sides instead of emitting markers for a human to resolve --
verified for both `git merge` and `git rebase`. If that attribute is lost the
board silently returns to hand-resolved conflicts, so it is asserted here.

**2. Lost updates, which the driver cannot see.** An agent reads the board,
then another agent pushes, then the first agent commits its now-stale
snapshot. There is no conflict, so no merge driver is consulted, and the
pushing agent's entry disappears. This is not hypothetical: walking the last
25 board commits on this branch found two such regressions, one of which cost
three entries. The count-based check below detects it.

The regression window is anchored to the commit that introduced this file,
because history cannot be rewritten on a shared branch. The check polices
every board commit from that point forward.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
BOARD = REPO / "Message-board.md"
GITATTRIBUTES = REPO / ".gitattributes"

ENTRY_RE = re.compile(r"^### \[", re.MULTILINE)


def _git(*args: str) -> str:
    result = subprocess.run(
        ["git", *args], cwd=REPO, capture_output=True, text=True, check=False
    )
    return result.stdout


def _entry_count(text: str) -> int:
    return len(ENTRY_RE.findall(text))


def test_board_uses_a_union_merge_driver() -> None:
    """Concurrent appends must auto-merge instead of needing hand resolution.

    Without `merge=union` every simultaneous append is a manual conflict block
    spanning the whole file, and dropping a side during that resolution is how
    claims and notices go missing.
    """
    attributes = GITATTRIBUTES.read_text(encoding="utf-8")
    assert GITATTRIBUTES.exists(), ".gitattributes is missing entirely"

    board_rules = [
        line
        for line in attributes.splitlines()
        if line.strip().startswith("Message-board.md")
    ]
    assert board_rules, "no git attribute rule for Message-board.md"
    assert any(
        "merge=union" in line for line in board_rules
    ), f"Message-board.md is not set to merge=union; got {board_rules}"


def test_git_reports_the_union_driver_for_the_board() -> None:
    """Assert git itself resolves the attribute, not just that the text exists."""
    reported = _git("check-attr", "merge", "--", "Message-board.md")
    assert "union" in reported, (
        f"git does not resolve a union driver for the board: {reported.strip()!r}"
    )


def test_board_tip_has_no_conflict_markers() -> None:
    """A committed conflict block breaks every other agent's rebase."""
    text = BOARD.read_text(encoding="utf-8")
    for marker in ("<<<<<<<", "=======", ">>>>>>>"):
        assert marker not in text, (
            f"Message-board.md contains a committed conflict marker {marker!r}; "
            "every concurrent rebase will hit it"
        )


def test_board_entry_count_never_regresses() -> None:
    """Detect lost updates: a commit that removes entries is a stale overwrite.

    Anchored to the commit that added this guard, since the two historical
    regressions cannot be rewritten on a shared branch. From here on, any board
    commit that drops an entry fails CI.
    """
    anchor = _git("log", "--format=%H", "--diff-filter=A", "--", str(
        Path(__file__).relative_to(REPO)
    )).split()
    if not anchor:
        pytest.skip("cannot locate the commit that introduced this guard")
    guard_commit = anchor[0]

    commits = _git(
        "log", "--format=%H", "--reverse", f"{guard_commit}^..HEAD", "--", "Message-board.md"
    ).split()
    if len(commits) < 2:
        pytest.skip("not enough board history since the guard landed")

    previous: tuple[str, int] | None = None
    for commit in commits:
        text = _git("show", f"{commit}:Message-board.md")
        if not text:
            continue
        count = _entry_count(text)
        subject = _git("log", "--format=%s", "-1", commit).strip()
        short = commit[:9]
        if previous is not None and count < previous[1]:
            pytest.fail(
                f"board entry count regressed {previous[1]} -> {count} in {short} "
                f"({subject!r}). A concurrent agent committed a snapshot it read "
                "before another push, so those entries were silently dropped. "
                "Re-read the board immediately before committing, and append "
                "rather than rewriting the file."
            )
        previous = (commit, count)


def test_board_keeps_the_agent_registry_and_notices() -> None:
    """The protocol sections are the part everyone relies on; never lose them."""
    text = BOARD.read_text(encoding="utf-8")
    for section in ("## 3.", "## 4.", "## 5."):
        assert section in text, f"board is missing the {section!r} section"
