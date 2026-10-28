---
name: writing-plans
description: Writes implementation plans that a different, cheaper model can execute without re-exploring or making design decisions — every step names exact files, the change, a verify command and an observable done-criterion — and validates them with scripts/plan.py before any code is written. Use in plan mode, when the user asks for a plan, starts a non-trivial task, or before handing work from a planning model to an executing model.
---

# Writing plans

The plan is the prompt for the executor. If a step forces the executor to search the repo or choose a design, the plan is not finished.
Path: `python <this-skill>/scripts/plan.py` (`pl`). Exit codes: 0 OK · 2 WARN · 1 BLOCK.

## Planning (plan mode is read-only — explore, then output the plan in chat)

1. `nt brief` for current state; explore with `repomap`/grep and line ranges (economizing-context).
2. Resolve open questions with the user **now** (ask ≤3 questions). The executor must not decide architecture.
3. Output the plan in exactly this format (see `pl template`):

```
# Plan: <one-line outcome>
Constraints: <must not change / required tech>
Done when: <observable result for the whole task>
Out of scope: <deferred>
Decisions: D-012, D-013        (optional: ids from a /grill session or nt decide)

## 1. <step title>
Files: src/x.py, tests/test_x.py (new)
Do: <exact change: function names, signatures, behavior, edge cases>
Verify: python -m pytest tests/test_x.py -q
Done: <observable: value returned, test name passing, endpoint status>
```

`Decisions:` lists decision ids the plan relies on. `pl check` BLOCKs an id missing from `notes/decisions.md` and WARNs on a superseded one (it names the replacement). Do not re-ask the user about a listed decision.

Step rules: 2–8 steps; each step leaves the code working and verifiable; tests in the same step as the behavior they cover; real paths only (`(new)` for files to create); `Done` is observable, never "works correctly".

## After the user accepts (executor, write mode)

```
- [ ] 1. Create the task branch (managing-git: gk branch-name …)
- [ ] 2. Save the accepted plan verbatim to the path from `pl path`
- [ ] 3. pl check   → fix BLOCKs (wrong paths, placeholders); report WARNs to the user
- [ ] 4. pl apply   → loads steps into the task note
- [ ] 5. For each step N:  pl step N → implement → run its Verify (or vf)
                         → nt task --check N --done "…" --next "…" → commit
```

Read only `pl step N` for the current step — not the whole plan again.

## Changing the plan mid-task

Small fix (a path, a detail): edit the plan file, `pl check`, continue.
Direction change: stop, tell the user what changed and why, update the plan, `pl check`, `pl apply` (replaces steps, logs the old progress).
