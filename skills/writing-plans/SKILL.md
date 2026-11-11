---
name: writing-plans
description: Frames a task (loads context, finds what is still unclear, asks only about the gaps, records decisions) and writes plans a cheaper model can execute without design decisions, checked by scripts/plan.py. Use when the user types /task, says "start a task", "new task", "bắt đầu task", asks for a plan or a design interview, starts a non-trivial feature or fix, or works in plan mode.
---

# Writing plans

The plan is the executor's prompt: a step that forces a search or a design choice is unfinished. Plan mode is the interface, the plan file the contract. `pl` = `python <this-skill>/scripts/plan.py` (exit 0 OK · 2 WARN · 1 BLOCK). Input: `/task <text>`, `task: <text>`, or any non-trivial change.

## Frame (always runs; it asks only what is still open)
1. **Load context first, in proportion.** Small task: `nt brief` (it includes knowledge.md) plus grep. Large task: also live decisions, `rmap --zone` on the files involved, `git log --oneline -10`. Never ask what a lookup answers.
2. **Sort every unclear point.** Can be looked up → look it up. Has an easy-to-reverse default → list it under `Assumptions:`. Anything else → must ask.
3. **Small branch (every task, lite included):** goal if vague; scope edges (tests, docs, help text); inputs and edge cases (empty, negative, Unicode incl. Vietnamese diacritics); callers of the code that changes; `Done when` (infer it when a clear observable check exists, else ask).
4. **Architecture branch, only if the task touches:** a new dependency · schema, storage or migration · a public API or signature · a new module or boundary · concurrency · auth, secrets or permissions · infra, deploy or CI. Walk `reference/decision-tree.md`, parents before children.
5. **Ask in rounds of at most 5 questions** (any number of rounds). Each: one thing, a recommendation, a one-line reason, what you checked. Every architecture answer → `nt decide` at once.

## Lite path (≤2 files and no architecture item)
State the frame and assumptions in 1-3 lines, then work. Stop only for a must-ask point; otherwise show the assumptions and continue. Put them in the commit body under `Assumptions:`. A third file or an architecture item → full plan.

## Plan (plan mode is read-only: output the plan in chat; `pl template`)
```
# Plan: <outcome>
Constraints: <must not change / required tech>
Done when: <observable result for the whole task>
Out of scope: <deferred>
Decisions: D-3a7f, D-91c2        (optional; ids from `nt decide`)
### Step 1: <title>
- Files: src/x.py, tests/test_x.py (new)
- Contract: <signatures, input/output, invariants, edge cases>
- Tests: test_x_ok, test_x_empty
- Out of scope: <what must not be touched>
- Verify: python -m pytest tests/test_x.py -q        (optional)
```
2-8 steps, each leaves the code working. Real paths (`(new)` for files to create). `Tests` are concrete names, written before the code. `Files` lists every file the step touches, tests included: `verify.sh` flags others. `Mode:` is ignored. `pl check` BLOCKs an unknown `Decisions` id, WARNs a superseded one.

## After the user accepts
`git switch -c feat/short-name` → save the plan to `pl path` → `pl check` (fix BLOCKs, show WARNs) → `pl apply`. Per step: `pl step N` (read only that) → tests first → implement → `vf` → `nt task --check N --done "…" --next "…"` → commit. The active step is the first unchecked item of the task note's `## Plan`; scripts read it with `scripts/plan-step.sh <field>` (`--cumulative`: steps 1..active).
Plan changes: a small fix → edit the file, `pl check`. A new direction → stop, tell the user why, update the plan, `pl check`, `pl apply`.
