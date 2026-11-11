# Decisions

One entry per design choice: what was chosen, what was rejected, why, and what breaks at 100x load.

---

## D-001 — Working branch name

**Chosen:** all work happens on `claude/rebuild-6bbd3f`, and nothing is pushed to `main`.

**Rejected:** a branch literally named `rebuild`.

**Why:** the brief asks for a branch called `rebuild`. This session was provisioned with `claude/rebuild-6bbd3f` as the only branch it may push to, so a branch named `rebuild` could not be pushed. The name carries the same intent. It can be renamed or merged under any name at PR time.

**At 100x load:** not applicable.

---

## D-002 — How Phase 0 evidence was gathered

**Chosen:** read every file; confirm claims with small probe scripts and test runs kept **outside the repo** (session scratchpad), using the dependencies pinned in `requirements.txt` in a throw-away virtualenv.

**Rejected:** (a) editing code or adding test scaffolding inside the repo during Phase 0, which the brief forbids; (b) asserting defects from reading alone when a 10-line probe could settle them; (c) calling live services, which need keys and URLs only the owner has.

**Why:** the owner needs to be able to explain the system, so each claim in `HOW_IT_WORKS.md` is marked as read-from-code or actually run, and the list of things not verified is explicit.

**At 100x load:** not applicable (analysis only).
