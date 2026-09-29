---
name: shared-tree-coordination
description: Use when working on the Wildframe audit branch where several agents share one checkout and one branch. Covers claiming paths, the git failure modes that actually occur here, what to report to other agents and where, and what never goes in /tmp. Triggers on "other agents are working", before editing a shared branch, when a push is rejected, or when a rebase conflicts.
---

# Working in a shared tree on this branch

Several agents work `audit/fix-open-github-issues` in **one checkout**. Coordination is
not overhead; it is how work survives. Full detail in `AGENT_COORDINATION.md`.

## Before you touch anything

```bash
git fetch origin
git status --porcelain            # someone may have left it dirty
cat Message-board.md | tail -80   # what is claimed, what is in flight
```

**Claim a path before editing it** and check for an existing claim. If someone has
claimed it, do not edit — find out whether they are done, or pick another path. This is
the main way work is lost here.

## The git failure modes that actually occur

**A dirty tree you did not create will block your rebase, and therefore your push.**
This looks like a network failure: rebase refuses, push fails, and retrying fails
identically forever. It is not transient.

```bash
BEFORE=$(md5sum <their-file> | cut -d' ' -f1)
git stash push -q -- <their-file>
git pull --rebase -q origin audit/fix-open-github-issues
git push -q origin audit/fix-open-github-issues
git stash pop -q
AFTER=$(md5sum <their-file> | cut -d' ' -f1)
[ "$BEFORE" = "$AFTER" ] || echo "their work changed - stop and reconcile"
```

Do **not** commit their file into your commit, and do **not** `git checkout --` it away.
Both destroy work that exists nowhere else. Untracked files (`??`) do not block a
rebase; only tracked modifications do.

**A quiet push is not success.** A push that printed only a fast-forward hint had
already lost the commit. Verify by content:

```bash
git show origin/audit/fix-open-github-issues:<path> | grep -c '<distinctive string>'
```

Verify content, not SHA — another agent re-applying the same change moves the SHA
while preserving the change.

**The board is one shared file, so rebase silently eats appends.** A conflict resolved
by taking `origin` discards your entry and commits the reduced file. Re-append *inside*
the retry loop, after each rebase.

**Another agent may leave the tree detached mid-rebase** with conflict markers in
tracked files. Committed HEAD is often clean while the worktree is not — check both,
and check whether those files even parse. Someone left seven Python files with conflict
markers, which meant five services could not import.

## Reporting to other agents

**Board** (`Message-board.md`) is for coordination: claims, handovers, and anything
blocking another person.
**`oner-task.md`** is for anything a human must decide: reviews, product calls,
credential rulings, known limitations.
**Do not duplicate backlog on the board** — it scrolls away.

A board entry should be readable by someone with no context: the command and the
output, not the conclusion alone, and **what you already ruled out** so nobody
re-derives it. "500 with `UndefinedColumnError: column content.price_usd does not
exist`" is useful; "content is broken" is not.

**Report your own mistakes in the open.** Several of the most useful records in this
repo are an agent saying "I was wrong about X": a lead given as the likely cause and
disproven by the agent sent to test it, and a "the site is still blank" report that was
its own bad check. The wrong conclusion is what persists — a fixed bug with a wrong
explanation gets re-diagnosed slowly. State the claim, the truth, the evidence that
settled it, and what you checked afterwards. Name whose mistake it is.

## `/tmp` is not storage

`/tmp` is not versioned, not shared, and **does not survive a reboot**. Seventeen
browser harnesses sat there carrying real knowledge about driving this stack, and all
of it would have been lost.

- **Never in `/tmp`:** a reusable script, a procedure you derived by trial, a finding or
  traceback you cited, anything you would be embarrassed to explain losing.
- **Fine in `/tmp`:** a one-off probe, another agent's in-flight work you stashed
  (popped the same turn), a build log, a backup you restored from git.
- **Never credentials** — `/tmp` is world-readable and `.gitignore` does not cover it.

The test: if the next agent would have to write it again, or you would be annoyed to
explain its loss, it belongs in the repository.

## Dispatching a subagent

Dispatch one when the investigation is done and the rest is execution, when the task
needs a long verification loop, or when you want independent eyes on a diagnosis you
are unsure of. Not for something small or already understood.

**Lead the prompt with what you ruled out.** An agent told "the CSP is the cause"
re-tests the CSP; one told "the proxy sets the request header, Next reads it at
render.js:407, the nonce regex accepts our hex nonce, and both delivery mechanisms
were tried" starts somewhere new. Every real finding in the last audit came from that.

Tell it not to commit, push, or touch the board. Grant permission to report failure
honestly — a confident wrong report is worse than an honest one — then verify its
"fixed" yourself.
