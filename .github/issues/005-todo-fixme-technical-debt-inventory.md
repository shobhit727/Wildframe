Title: Inventory and triage of `TODO` / `FIXME` markers across repository

Description:
Repository contains numerous `TODO` and `FIXME` markers across docs, services, and scripts. These indicate outstanding work or known issues that should be triaged and prioritized.

Severity: Low → Medium (depends on marker contents)

Summary:
- Grep found a large number of `TODO` / `FIXME` occurrences across ~96 files. Examples include AGENTS.md notes, docs, and code comments.

Recommendations:
- Create a short-lived task force to triage all TODO/FIXME items and create explicit GitHub issues per actionable item.
- Prioritize items that affect security, startup, or data integrity first.

Notes: This issue is an umbrella to turn informal markers into tracked work items.


---

## Documentation verification — 2026-09-27

Rechecked against the Wildframe source tree at commit `70e04e6417e83a15fe2ca91d993a2c9408adf0a8` (the `main` baseline used for this documentation refresh). Repository paths and referenced project structure are maintained against source; when this document conflicts with code, the code is authoritative.
