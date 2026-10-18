# Agent coordination guide

How to work on this repository when you are not working alone. Extracted from
`AGENTS.md` §23, which had grown to 378 lines — 39% of that file — and was pushing
the engineering rules, which are the part everyone actually needs, out of reach.

**Read `AGENTS.md` first** for the engineering rules: service layout, FastAPI
conventions, testing, security, and the troubleshooting table. This file covers the
coordination layer on top of that.

The two other files you need:

- `ONBOARDING.md` — the path: what to do, in what order, which commands work
- `oner-task.md` — what a human still owes, and what agents deliberately did not fix

---

1. Read this file and any nearer service-specific AGENTS.md.
2. Read `Message-board.md` when another agent may be active in the same tree. It
   carries standing notices, per-file work claims, and verification recipes.
3. Locate the entry point and trace the complete execution path.
4. Inspect models, schemas, settings, repositories, events, and tests.
5. Inspect cross-service contracts for cross-service changes.
6. Make the smallest coherent change.
7. Add or update a regression test.
8. Run focused tests and applicable lint/type checks.
9. Run contract/integration tests when an external contract changed.
10. Review the final diff for accidental security, API, dependency, or documentation changes.
11. Update current documentation if behavior or setup changed.
12. Re-read `Message-board.md` before pushing, and post a `Files:` claim for
    any path you edited or are about to edit.

Do not mix unrelated cleanup into the same PR.

### 23.1 Concurrent agents in a shared working tree

When more than one agent works this branch at the same time, in the same
checkout:

- `Message-board.md` is the coordination point. It lives on
  `audit/fix-open-github-issues`, **not on `main`**, so read it with an explicit
  ref:
  ```bash
  gh api "repos/shobhit727/Wildframe/contents/Message-board.md?ref=audit/fix-open-github-issues" --jq '.content' | base64 -d
  ```
  Prefer this over `raw.githubusercontent.com`, which is CDN-cached and has been
  observed serving a stale copy minutes after a successful push.
- **Claim paths before editing them.** Post a `Files:` entry and check for an
  existing claim. Overlap is the main way work is lost here.
- **`git add` does not clear an already-staged change.** Another agent's staged
  deletions or edits will be swept into your commit. Always
  `git reset && git add <exactly your paths>`, then confirm with
  `git show --stat HEAD --format=""`.
- **A quiet push is not success.** A push that printed only a fast-forward hint
  had already lost the commit to a concurrent reset. Confirm with
  `git log origin/<branch> --oneline | head` and, better, verify your *content*
  arrived: `git show origin/<branch>:Message-board.md | grep -c '<distinctive string>'`.
  Verify by content, not by SHA — re-application by another agent moves the SHA
  while preserving the change.
- **The board is one shared file, so rebase silently eats appends.** A conflict
  resolved by taking `origin`'s version discards your appended entry, and
  `rebase --continue` then commits that reduced file. Your commit lands with the
  text missing. Re-append *inside* the retry loop, after each rebase, not once
  before it.
- **Do not use `git revert` or `git checkout <base> -- Message-board.md` to undo
  your own entries.** Both re-derive the whole file instead of removing just your
  lines, so they silently drop *other* agents' entries that landed after your base
  commit, and the loss only surfaces as a red entry-count guard. Append a short
  correction instead. This has already cost an agent a colleague's entry.
- **`git pull --rebase` refuses outright on a dirty tree.** That refusal is
  protective. For a merge, confirm the incoming commits do not touch the dirty
  files, fingerprint those files before and after, and compare — then it is safe
  while their work stays byte-identical.
- **A dirty tree you did not create will block your rebase, and therefore your
  push.** This is the same event as the bullet above seen from the other side, and
  it is the one that actually stops you: you cannot rebase, so you cannot push, so
  you re-run the same failing push until you give up. It is not a network failure
  and retrying will never clear it. Do not "fix" it by committing their file into
  your commit, and do not `git checkout --` it away.
  Fingerprint, stash, rebase, restore, verify:
  ```bash
  BEFORE=$(md5sum <their-file> | cut -d' ' -f1)
  git stash push -q -- <their-file>
  git pull --rebase -q origin <branch>
  git push -q origin <branch>
  git stash pop -q
  AFTER=$(md5sum <their-file> | cut -d' ' -f1)
  [ "$BEFORE" = "$AFTER" ] || echo "their work changed — stop and reconcile"
  ```
  Untracked files (`??`) do not block a rebase; only tracked modifications do. Check
  `git status --porcelain` first to see which you are dealing with.
- **Stash is not a safe parking space.** A stash left behind by a crashed agent is
  invisible to the next one. Pop what you stash, in the same turn, and verify the
  fingerprint.
- Another agent may leave the checkout **detached mid-rebase** with the board
  conflicted. Re-attach before concluding anything about your own work:
  `git rebase --abort; git merge --abort; git checkout -B <branch> origin/<branch>`.
- **`git worktree add --detach <dir> <sha>` is the only reliable way to learn what
  CI actually sees.** The shared dirty tree tells you about *someone's* WIP.
  Fetch first: a SHA read from the GitHub API is not a local ref, and
  `git worktree add` fails on it with `invalid reference`.

### 23.2 Verified failure modes

Each item below cost real work on `audit/fix-open-github-issues`. They are
recorded here because the symptom is misleading in every case, so the instinct is
to trust the wrong signal.

**Landing work under churn**

- A quiet `git push` is not success. A push that printed only a fast-forward hint
  had already lost the commit to a concurrent reset. Confirm with
  `git log origin/<branch> --oneline | head` and, better, verify your *content*
  arrived: `git show origin/<branch>:Message-board.md | grep -c '<distinctive string>'`.
  Verify by content, not by SHA — re-application by another agent moves the SHA
  while preserving the change.
- **The board is one shared file, so rebase silently eats appends.** A conflict
  resolved by taking `origin`'s version discards your appended entry, and
  `rebase --continue` then commits that reduced file. Your commit lands with the
  text missing. Re-append *inside* the retry loop, after each rebase, not once
  before it.
- `git pull --rebase` refuses outright on a dirty tree. That refusal is
  protective. For a merge, confirm the incoming commits do not touch the dirty
  files, fingerprint those files before and after, and compare — then it is safe
  while their work stays byte-identical.
- Another agent may leave the checkout **detached mid-rebase** with the board
  conflicted. Re-attach before concluding anything about your own work:
  `git rebase --abort; git merge --abort; git checkout -B <branch> origin/<branch>`.
- `git worktree add --detach <dir> <sha>` is the only reliable way to learn what
  CI actually sees. The shared dirty tree tells you about *someone's* WIP.
  Fetch first: a SHA read from the GitHub API is not a local ref, and
  `git worktree add` fails on it with `invalid reference`.

**`cancel-in-progress` and the push/review deadlock**

`ci-cd.yml` uses a per-ref concurrency group with `cancel-in-progress: true`, so
every push kills the run in flight. Under multi-agent push rates this yields
*zero* verdicts — not failures, no signal at all. Worse, posting a board update
is itself a commit and therefore itself a push, so the coordination mechanism
causes the CI it is coordinating. The cure is a quiet window or deliberate
batching (one push per ~10 minutes), not weakening the guard. Do not silence
`cancel-in-progress` to make the dashboard look greener: that hides real
regressions behind stale runs.

**Conclusions drawn from a bad check**
- **Do not report a lead as a finding.** A strong hypothesis was presented to the
  user as the likely cause, and it turned out to be disproven by the very agent
  dispatched to test it. Say "this is the strongest lead" until it is proven, and
  correct it publicly when it is not.
- **A check that cannot fail is worse than no check.** Reading `self.__next_f: 0`
  as "the script never ran" produced a confident "the site is still blank" when the
  page was rendering perfectly. React consumes those pushes; the check was wrong, the
  conclusion built on it was wrong, and it nearly caused a working fix to be
  reverted. Verify the check itself before trusting its output.
- **Grepping a rendered page for markup is not a test.** A curl-and-grep for
  `<input` said "0 inputs" on a page that a real browser showed with five. Use a
  browser for anything the browser renders.
- **Mocking an environment that can raise can mask the real error.** A test that
  patched a module whose import had broken passed, because the patch replaced the
  failure with a stub. The app was fully broken underneath.

**Local verification that lied**

- **Install before you type-check or test.** CI runs
  `poetry install --no-interaction --with dev` in each service directory before
  mypy and pytest. Running either against a fresh or empty service venv produces
  confident, entirely fictional errors — an `import-untyped` storm across 13
  services from a package that was never installed, in this case.
- Use a clean worktree plus a real `poetry install` to reproduce CI. A venv
  inherited from the repo root is drifted and will disagree with the pipeline in
  both directions.
- A scratch venv you built by hand can invent vulnerabilities. A "redis 5.3.1
  has 2 HIGH CVEs" claim came from `msgpack` and `setuptools` in that scratch
  venv, not from redis. Confirm the package name in the finding before reporting
  it.
- Trivy over the working tree flags locally generated dev certificates. Those are
  gitignored (`.gitignore`: `apps/web/certificates/*.pem`) and never committed, so
  a local non-zero exit is not a CI result. `verify-supply-chain.py` is the check
  that reflects committed content.
- Do not hide a tool's error output while debugging. `2>/dev/null` on
  `git worktree add` hid a failure, and the next command then analysed an empty
  directory and produced meaningless output.
- `grep | head -N` truncated away the match that mattered and produced the
  opposite conclusion. Read the full match list before concluding a file is
  clean.
- Bash `seq` is `seq FIRST INCREMENT LAST`. `seq 10 60 10` is "start 10, step 60,
  stop 10" and yields a single value. A watcher built on it ran one poll and
  reported nine minutes of quiet. Prove a polling loop iterates: emit a
  per-iteration heartbeat and check elapsed wall time.
- Simulate a regression with the *actual* defect. Setting `algorithms=['HS256']`
  on a call whose key still comes from JWKS is not the vulnerability, and a gate
  correctly ignoring it looked like a broken gate.
- Expect fixing one failure to expose the next. `set -euo pipefail` aborts the
  per-service mypy loop at the first error, so each fix uncovers the following
  latent one. Budget for a sequence of them rather than assuming one fix ends
  the job.

### 23.3 Human-only tasks

Some work must not be done by an agent, because it is a decision rather than an
implementation. The current list lives in `oner-task.md` at the repository root.

Read it before declaring work complete. It records what is blocked, what needs
review, what needs a product or security decision, and which limitations are
deliberate. Leaving an item off that file is how it becomes a surprise three
commits later.

Do not resolve these yourself:

- **Credentials and secrets.** Whether a committed credential is authorized is a
  human decision. Redaction is not remediation; rotation is. See `SECURITY.md`.
- **Merging.** No agent merges to `main` or force-pushes a protected branch.
- **Product behaviour.** Changing onboarding, redirects, or user-visible copy to
  make a check pass is a product decision, not a bug fix.
- **Review sign-off.** PR #938 and any successor need a human reviewer.

When you finish a task, add anything a human still owes to `oner-task.md` rather
than leaving it only in a commit message or on the board. If you deliberately do
not fix something, say so there and say why.

- **Re-typing a command sequence instead of scripting it.** A loop that must run in a
  specific order, with specific flags, will eventually lose a flag. Every serious fix
  in the last audit needed the same build/recreate/wait/probe loop, and hand-retyping
  is how a missing `--force-recreate` shipped a stale image. See `AGENTS.md` 19.1b.

### 23.4 Mistakes that are specific to this repository

Not traps in general — mistakes that have actually been made here, with the
consequence each one produced.

- **Forgetting to register a route.** A correct route that is never added to the
  aggregator returns 404 while the code looks right. Check `openapi.json` on the
  running service.
- **One env var for both sides of a trust boundary.** `NEXT_PUBLIC_API_URL` served
  both browser code (needs the public host) and in-container server code (needs the
  docker-internal name). Server-side calls got 502 on every request.
- **A per-request nonce against prerendered HTML.** Strict CSP plus static
  prerendering means the nonce has nothing to attach to, the inline script is blocked,
  and the whole app renders a blank body. See #981.
- **Trusting `create_all` to keep the schema current.** It is `checkfirst`: it creates
  missing tables and never touches existing ones, so nine of twenty content tables had
  never been created on a fresh volume. Use `scripts/init_schemas.py`.
- **Hardcoding a machine-specific host in committed config.** A LAN IP on Caddy's
  cleartext port made the browser unable to reach the API at all, and it was
  overridden a correct `.env` value.
- **Bumping a dependency without checking the family.** See 20 — two resolutions here
  fixed one conflict by creating another.
- **Silencing a tool to make a check pass.** `except: pass`, `ignoreBuildErrors`,
  `continue-on-error`, `|| true`, blanket skips. AGENTS.md forbids it, and a silenced
  failure is how a total outage shipped through a green pipeline.
- **Leaving conflict markers behind.** A shared checkout has been left with
  syntactically invalid Python in seven files, which stopped five services importing
  and hid every regression in them. If a merge or rebase is interrupted, restore the
  tree rather than leaving it for the next agent.
- **Resolving a board or docs conflict by taking `origin`.** That silently discards
  your appended text while `rebase --continue` commits the reduced file anyway.

### 23.5 Working with other agents

Other agents are working this branch in the same checkout, right now. Coordination
is not overhead here; it is how work survives.

**Read the board before you start and before you finish.** The `Files:` claims are the
only record of who owns what. An agent that never reads it will eventually edit a file
someone else is mid-way through, and the conflict will surface as a broken file rather
than as a conflict.

**Post a `Files:` claim before you edit, and check for an existing claim first.** If
someone has already claimed the path, do not edit it — find out whether their work is
finished, or pick a different path. Overlap is the main way work is lost in this repo.

**What goes on the board** is coordination: claims, handovers, and findings that block
other people. **What goes in `oner-task.md`** is anything a human must decide. Do not
put backlog on the board; it scrolls away and duplicates. Do not put a human decision
on the board; see 23.3.

**A board entry should be readable by someone with no context.** State what you
observed, with the command and the output, not the conclusion alone. "500 with
`UndefinedColumnError: column content.price_usd does not exist`" is useful; "content
is broken" is not. Include what you already ruled out, so the next agent does not
re-derive it — that is the single most valuable part of a handover.

**Hand off rather than silently absorbing.** If a task is outside what you were asked
to do, or needs a decision that is not yours, dispatch an agent for it or record it in
`oner-task.md`. Do not quietly leave it undone, and do not quietly expand into another
agent's claimed path. This repo has real multi-agent coordination available; using it
beats writing a paragraph about why you did not.

**Report your own mistakes, in the open, on the board.** This is the entry that has
paid for itself most. Several of the most useful records in this repo are an agent
saying "I was wrong about X" rather than quietly fixing it and moving on:

- A strong lead was given to the user as the likely cause of the blank page. The
  agent dispatched to test it **disproved it**. The lead was recorded as a hypothesis,
  not a finding, and the correction stated plainly.
- The same session reported "the site is still blank" on a page that was rendering
  perfectly, because the check itself was wrong: `self.__next_f: 0` was read as "the
  script never ran", and React consumes those pushes. The conclusion built on a bad
  check nearly caused a working fix to be reverted.
- Three harnesses were written to the board as finished and each had failed from its
  own bug first.
- A merge conflict on the board was resolved by taking `origin`, silently discarding
  the appended entry, and the loss was only found by verifying content afterwards.

**Why bother, when the code is fixed either way?** Because the wrong conclusion is
what persists. A fixed bug with a wrong explanation in the history will be
re-diagnosed by the next agent, slowly, with the same false lead. A correction on the
board is a few lines and saves that.

Three rules for a good correction:

1. **State what you claimed, what is actually true, and what evidence settled it.**
2. **Say what you checked afterwards**, so nobody re-does it. "Verified by content
   with `git show origin/<b>:<path> | grep -c`" is more useful than "fixed".
3. **Do not bury it.** A quiet correction in a commit body is found by nobody.

**Do not report someone else's mistake anonymously.** If another agent's conclusion is
wrong, say whose and what the evidence is. A correction with no owner is useless, and
an unnamed one looks like an accusation.

**Take only what you can finish.** If you claim ten paths and finish three, the other
seven are now a trap for the next agent. Claim narrowly, release explicitly, and say in
your final entry which claims you have released.

**Verify another agent's report rather than repeating it.** An agent saying a bug is
fixed is a claim, not evidence. Re-run the check yourself against the running system.
In this repo an agent reported a green end-to-end flow that another agent then broke
by rebuilding the image with a stale cache, so the report was true when written and
false an hour later.

**When you are wrong, say so on the board, in the open.** Several of the most useful
entries in this repo are corrections: a wrong root cause, a bad verification command,
an overstated scope. Record them where the next agent will read them instead of
quietly fixing the code and leaving the wrong conclusion in the history.

### 23.6 Scratch space: what belongs in /tmp and what does not

`/tmp` is for scratch. It is not versioned, not reviewed, not shared with the next
agent, and **it does not survive a reboot**. Everything you have learned, and
everything reusable, must end up in the repository.

**Never leave these in /tmp:**

- **A reusable script, harness, or tool.** Anything another agent could run. These
  belong in `scripts/`, with a line in `scripts/README.md` and a pointer from
  `AGENTS.md` 19.2. Seventeen one-off browser harnesses sat in `/tmp` during the last
  audit and carried real knowledge — how to find a Chromium build, that the dev cert
  is self-signed, that `curl` cannot tell you whether a page hydrated — all of it
  lost on reboot. That knowledge is what `scripts/browser-check.mjs`,
  `causation-check.mjs` and `auth-flow-check.mjs` now preserve.
- **A procedure you had to work out.** If you derived a command sequence, a
  diagnostic order, or a set of steps by trial, that is repository knowledge. Put it
  in `ONBOARDING.md`, `docs/`, or a script — not in a shell history.
- **Findings, evidence, or a diagnosis.** Commit it, or put it in
  `oner-task.md`, or post it to the board. A conclusion that exists only in `/tmp`
  will be re-derived from scratch by the next agent, slowly.
- **Anything that would embarrass you to lose.** Test output you cited as proof,
  a traceback you quoted, a before/after comparison.

**Fine to keep in /tmp:**

- Genuinely disposable probes: a one-off `node -e`, a scratch query, a log dump.
- Another agent's in-flight work you had to move aside to rebase. **Stash or copy it,
  finish your push, and put it straight back** — see 23.1. A stash is not storage.
- Large generated output: a build log, a heap snapshot, a full dump you only need
  while debugging.
- A backup of a tree you restored from git. Note its location in your board entry so
  the owner can retrieve it, and say plainly that `/tmp` will not survive a reboot.

**The test to apply:** if the next agent would have to write it again, it belongs in
the repository. If you would be annoyed to explain its loss, it belongs in the
repository. If it is dead the moment you close the terminal, `/tmp` is fine.

**Never put credentials in `/tmp` either** — it is world-readable on most systems and
is not covered by `.gitignore`, which only protects the repository. See 24.

**Use `tem/` inside the repository, not `/tem` and not `/tmp`.** The scratch directory
for this project is `tem/` *inside* the repo root. It is gitignored
(`.gitignore:176`), so nothing there can be committed by accident.

- **`<repo>/tem/scratch/`** — logs, dumps, and disposable probes.
- **`<repo>/tem/wt/`** — git worktrees for parallel agents, one per agent, named
  `wt01`…`wt24`. A worktree is a checkout, not scratch: create it under `tem/wt/`,
  never in `/tmp`.
- **`<repo>/tem/agent-scratch/`** — per-agent scratch, so two agents cannot collide.

`/tem` — with a leading slash — **does not exist and must never be created.** A path
like `/tem/wt` or `/tem/scratch` is a bare filesystem-root directory outside the repo:
it is not gitignored, not reviewed, not shared with the next agent, and invisible to
`git status`. Eight `git worktree` registrations stranded under `/tmp/opencode/` were
found during the October 2026 issue sweep; the paths still pointed at the deleted
directories until `git worktree prune` ran.

If you find a scratch path outside the repo, move it in and re-register:

```bash
mv /tmp/opencode/wf-x tem/agent-scratch/wf-x
git worktree prune          # registrations for moved/deleted paths
```

**Tell every subagent this explicitly in its prompt.** A subagent has no memory of
your session and will reach for `/tmp` by default — and several will reach for `/tem`,
which looks like a typo of `tem/` and reads as a filesystem root. Give the absolute
repo path, and say which directory it may write to.

### 23.7 When to use a subagent

This repo has real multi-agent dispatch available. Use it — a paragraph explaining
why you did not is worth less than a 15-line prompt.

**Dispatch one when:**

- **The investigation is already done and the remaining work is execution.** This is
  the best case. You hand over a diagnosis plus the hypotheses you ruled out, and the
  agent implements without re-deriving anything. All four dispatches during the last
  audit were this shape.
- **The task needs a full verification loop** — rebuild, restart, drive a browser,
  prove red/green. That is a lot of mechanical work that does not need your context.
- **You want an independent second pair of eyes** on a diagnosis you are not fully
  confident in. Prefer a separate agent over re-reading your own work: it will not
  notice what you have already convinced yourself of.
- **Two tasks are genuinely independent** and could run in parallel. Do not dispatch
  for work that touches the same files.

**Do not dispatch when** the task is small, when you already know the fix, or when
the bottleneck is a decision rather than an implementation. Overhead is real: a
subagent has no memory of your session and you must write its prompt from scratch.

**Write the prompt as a handover, not a task ticket.** The most valuable thing you
can include is **what you have already ruled out**, with the evidence. An agent told
"CSP is the cause" re-tests the CSP; an agent told "the proxy sets the request
header, Next reads it at render.js:407, the nonce regex accepts our hex nonce, CSP is
not in `ipcForbiddenHeaders`, and both header-delivery mechanisms were tried and
neither worked" starts somewhere new. Every real finding during the audit came from
that.

Also state, explicitly:

- **The hard constraints**, including what must not be done. "Do not weaken the CSP",
  "do not use `ignoreBuildErrors`", "these uncommitted changes are someone else's and
  must not be reverted".
- **What you have already changed**, so the agent builds on it instead of
  reimplementing or reverting it.
- **That it must not commit, push, or touch the board.** Other agents share the
  tree. You own git and coordination.
- **That an honest failure report beats a confident wrong one.** Say it. Agents given
  permission to report "this does not work because X" return far more useful reports
  than ones pushed toward a green result.

**The agent's report is a claim, not evidence.** Re-run its key check yourself
against the running system before you tell the user it is fixed. In this repo every
agent that reported a fix was correct about the code and we were still wrong once
about the cause — one was sent after a lead I had already presented as most likely,
and it disproved it.

**A good report tells you what it did not do.** "I did not verify this", "this is
unverified", "these are pre-existing failures I confirmed by stashing" — that is
what makes a report trustworthy enough to act on.

**Expect the agent to find things you missed.** That is the point. In this audit a
dispatched agent found a rate-limit confound that had invalidated a comparison, a
test that passed vacuously against a real certificate, and a latent
`node:https` bug exposed by a config change. None were visible from my side.
