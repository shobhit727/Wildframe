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
#: Captures the entry ID, so duplicates can be collapsed. See _entry_count.
ENTRY_ID_RE = re.compile(r"^### \[(M-[^\]]+)\]", re.MULTILINE)


def _git(*args: str) -> str:
    result = subprocess.run(["git", *args], cwd=REPO, capture_output=True, text=True, check=False)
    return result.stdout


def _entry_count(text: str) -> int:
    """Count *distinct* entries, not header lines.

    A retry loop of mine appended the same entry nine times under one ID, which
    inflated this count to 160. The count is meant to answer "how many entries
    exist", and nine copies of one entry are not nine entries -- so counting raw
    headers both over-reports and, worse, poisons the high-water mark that the
    regression test below compares against.

    Uses a separate regex that captures the ID. ``ENTRY_RE`` deliberately has no
    capture group (it matches the literal ``"### ["``), so ``set()`` over its
    ``findall`` collapses every entry to a single element and silently reports 1.
    """
    return len(set(ENTRY_ID_RE.findall(text)))


def test_board_uses_a_union_merge_driver() -> None:
    """Concurrent appends must auto-merge instead of needing hand resolution.

    Without `merge=union` every simultaneous append is a manual conflict block
    spanning the whole file, and dropping a side during that resolution is how
    claims and notices go missing.
    """
    attributes = GITATTRIBUTES.read_text(encoding="utf-8")
    assert GITATTRIBUTES.exists(), ".gitattributes is missing entirely"

    board_rules = [
        line for line in attributes.splitlines() if line.strip().startswith("Message-board.md")
    ]
    assert board_rules, "no git attribute rule for Message-board.md"
    assert any(
        "merge=union" in line for line in board_rules
    ), f"Message-board.md is not set to merge=union; got {board_rules}"


def test_git_reports_the_union_driver_for_the_board() -> None:
    """Assert git itself resolves the attribute, not just that the text exists."""
    reported = _git("check-attr", "merge", "--", "Message-board.md")
    assert (
        "union" in reported
    ), f"git does not resolve a union driver for the board: {reported.strip()!r}"


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
    anchor = _git(
        "log", "--format=%H", "--diff-filter=A", "--", str(Path(__file__).relative_to(REPO))
    ).split()
    if not anchor:
        pytest.skip("cannot locate the commit that introduced this guard")
    guard_commit = anchor[0]

    commits = _git(
        "log", "--format=%H", "--reverse", f"{guard_commit}^..HEAD", "--", "Message-board.md"
    ).split()
    if len(commits) < 2:
        pytest.skip("not enough board history since the guard landed")

    # The rule is "the board must not currently hold fewer entries than it has ever
    # held", not "no historical commit may ever have dipped".
    #
    # Comparing consecutive commits makes a regression permanent: the offending
    # commit fails forever, so the branch can never go green again, because
    # repairing the loss only proves the repair on a *later* commit. On this branch
    # fdd5f7676 dropped three entries (109 -> 106). They were restored, and the
    # board is back at 117 -- above the 109 that preceded the drop -- yet the test
    # stayed red purely because of that one historical commit. Rewriting it is not
    # available on a shared branch.
    #
    # A test that can never pass is worse than no test: it is a permanent red that
    # every agent learns to ignore, including the next real lost update. This
    # version still fails on live data loss -- if the board holds fewer entries now
    # than the maximum ever committed, content is genuinely missing right now -- and
    # passes once the loss is repaired, which is the state the branch is in now.
    counts: list[tuple[str, int, str]] = []
    for commit in commits:
        text = _git("show", f"{commit}:Message-board.md")
        if not text:
            continue
        subject = _git("log", "--format=%s", "-1", commit).strip()
        counts.append((commit[:9], _entry_count(text), subject))

    if not counts:
        pytest.skip("no board history since the guard landed")

    high_water = max(count for _, count, _ in counts)
    current = _entry_count(BOARD.read_text(encoding="utf-8"))
    if current < high_water:
        dip = next(
            ((short, count, subject) for short, count, subject in counts if count < high_water),
            ("<unknown>", current, ""),
        )
        pytest.fail(
            f"the board holds {current} entries now, but {high_water} have existed in "
            f"history, so entries are missing right now. The lowest recorded count was "
            f"{dip[1]} in {dip[0]} ({dip[2]!r}). A concurrent agent most likely "
            "committed a snapshot it read before another push. Re-read the board "
            "immediately before committing, and append rather than rewriting the file."
        )


def test_board_keeps_the_agent_registry_and_notices() -> None:
    """The protocol sections are the part everyone relies on; never lose them."""
    text = BOARD.read_text(encoding="utf-8")
    for section in ("## 3.", "## 4.", "## 5."):
        assert section in text, f"board is missing the {section!r} section"


#: Duplicate entry ids tolerated at the time this guard was added.
#:
#: Two agents independently claimed the id ``M-0003`` before the id scheme
#: switched to timestamped ids, so the board carries genuinely duplicated ids
#: that no one can renumber without rewriting shared history -- and renumbering
#: would destroy attribution, which is the one thing an audit log must not do.
#: The count is a floor, not a target: this test exists to stop it *growing*.
#: A timestamped id (``M-<UTC>-<agent>``) makes a fresh collision very unlikely;
#: the bare ``M-00NN`` form is what produced these.
DUPLICATE_ID_BASELINE = 16


def _duplicate_entry_ids(text: str) -> dict[str, int]:
    """Map each repeated entry id to how many times it appears."""
    ids = re.findall(r"^### \[([^\]]+)\]", text, re.MULTILINE)
    return {i: ids.count(i) for i in set(ids) if ids.count(i) > 1}


def _redundant_entry_count(text: str) -> int:
    """Total surplus entries caused by repeated ids.

    Counting *distinct* duplicated ids is not enough: appending a third copy of
    an id that is already duplicated raises no new distinct id, so the count
    would not move. Summing the surplus catches both a fresh collision and an
    existing one getting worse.
    """
    return sum(n - 1 for n in _duplicate_entry_ids(text).values())


def test_duplicate_entry_ids_do_not_increase() -> None:
    """Stop new id collisions, without failing on the ones already on the board.

    A duplicated id makes an entry ambiguous to read back: "what did the agent
    who filed M-0012 say?" has two answers. That ambiguity is what let two
    agents believe they each owned the same task earlier on this branch.
    """
    text = BOARD.read_text(encoding="utf-8")
    surplus = _redundant_entry_count(text)
    assert surplus <= DUPLICATE_ID_BASELINE, (
        f"{surplus} redundant entries from repeated ids, baseline is "
        f"{DUPLICATE_ID_BASELINE}. Worst offenders: "
        f"{sorted(_duplicate_entry_ids(text).items(), key=lambda kv: -kv[1])[:5]}. "
        "Use a timestamped id (M-<UTC timestamp>-<agent>) for new entries; the "
        "bare M-00NN form is what collided. Do not renumber the existing ones -- "
        "that rewrites attribution."
    )
