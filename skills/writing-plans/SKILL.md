---
name: writing-plans
description: Frames a task (goal, constraints, done-when, out-of-scope) and writes plans a cheaper model can execute without design decisions: every step names files, change, verify command and done-criterion, checked by scripts/plan.py. Use when the user types /task, says "start a task", "new task", "bắt đầu task", asks for a plan, starts a non-trivial feature or fix, or works in plan mode.
---

# Writing plans

The plan is the prompt for the executor. If a step forces the executor to search the repo or choose a design, the plan is not finished.
Path: `python <this-skill>/scripts/plan.py` (`pl`). Exit codes: 0 OK · 2 WARN · 1 BLOCK.
Input is `/task <free text>` (Claude Code), a message starting with `task:`, or any request for a non-trivial change.

## Frame (before planning)

1. `nt brief`. If an open task exists on this branch, ask: continue it, or start a new one?
2. Fill the frame from the user's text. Do not invent values:

```
Goal:          <outcome, one line>              (required)
Constraints:   <must not change / required tech>
Done when:     <observable check for the whole task>
Out of scope:  <deferred>
```

3. Ask only for missing fields, in **one** message, **at most 3 questions**, ranked by how much the answer changes the plan. Always ask for `Done when` if absent. Each question:
   - asks exactly **one** thing (never "A or B? and C?"), and
   - ends with a default: `Recommended: X — reply 'ok' to accept`.

   Bad: "Which search method, and CLI or web?" Good: "1. Search method? Recommended: BM25 — reply 'ok'. 2. Interface? Recommended: CLI — reply 'ok'."
   If the user answers "skip", leave the field empty.
4. If the grilling-designs skill is installed and the task adds or changes a public interface or schema, touches 3 or more modules (`rmap --zone`), or left a design choice open: suggest `/grill` in one line. **Never auto-run it.** If the user declines, continue.
5. If not in plan mode, suggest it: plan mode with the planning model, execution with a cheaper one (Claude Code: `/model opusplan`, `Shift+Tab`).

Do not create a branch, edit files or write notes before the plan is accepted.

## Lite path (no plan file, no task note)

Use it when the change touches **≤2 files** and needs **no design decision**. State the frame in one or two lines, make the change, then the normal rules still apply: `vf` exits 0 after the last edit, and commit through managing-git. If you discover a third file or an open choice, stop and switch to the full plan.

## Planning (plan mode is read-only — explore, then output the plan in chat)

1. Explore with `repomap`/grep and line ranges (economizing-context).
2. Resolve remaining open questions with the user **now**, under the same question rules. The executor must not decide architecture.
3. Output the plan in exactly this format (see `pl template`):

```
# Plan: <one-line outcome>
Constraints: <must not change / required tech>
Done when: <observable result for the whole task>
Out of scope: <deferred>
Decisions: D-012, D-013        (optional: ids from a /grill session or nt decide)

### Step 1: <step title>
- Mode: solo | pipeline
- Files: src/x.py, tests/test_x.py (new)
- Contract: <signatures, input/output, invariants, edge cases>
- Tests: test_x_ok, test_x_empty
- Out of scope: <what must not be touched>
- Verify: python -m pytest tests/test_x.py -q        (optional)
```

`Decisions:` lists decision ids the plan relies on. `pl check` BLOCKs an id missing from `notes/decisions.md` and WARNs on a superseded one (it names the replacement). Do not re-ask the user about a listed decision.

Step rules: 2–8 steps; each step leaves the code working and verifiable; real paths only (`(new)` for files to create); `Mode`, `Files`, `Contract`, `Tests` and `Out of scope` are required (one line each, extra lines indented).
- `Tests` names the tests that define done. **Write them before the code**; the step is finished when exactly those pass. Name them concretely (`test_drops_null_rows`), never "add tests".
- `Files` is a contract: `verify.sh` fails any changed file outside the `Files` of steps 1..active unless a decision in `notes/decisions.md` logs why. List every file the step touches, tests included.
- The old `## 1.` / Do / Done format still parses; `pl check` WARNs on it. Write new plans in the format above.

## Where the active step lives

The accepted plan is `pl path` (`notes/tasks/<branch>.plan.md`). The active step is the first unchecked item of the task note's `## Plan` checklist (`nt task --check N` ticks them); with every item ticked it is the last step. Scripts read it with `scripts/plan-step.sh <field>` (`files`, `tests`, `mode`, `contract`, `out-of-scope`, `verify`, `title`; `--cumulative` for steps 1..active). There is no second marker to maintain: tick the step with `nt` as you finish it.

## After the user accepts (executor, write mode)

```
- [ ] 1. Create the task branch (managing-git: gk branch-name …)
- [ ] 2. Save the accepted plan verbatim to the path from `pl path`
- [ ] 3. pl check   → fix BLOCKs (wrong paths, placeholders); report WARNs to the user
- [ ] 4. pl apply   → loads steps into the task note
- [ ] 5. For each step N:  pl step N → write its Tests first (they fail) → implement → run its Verify (or vf)
                         → nt task --check N --done "…" --next "…" → commit
```

Read only `pl step N` for the current step — not the whole plan again.

## Changing the plan mid-task

Small fix (a path, a detail): edit the plan file, `pl check`, continue.
Direction change: stop, tell the user what changed and why, update the plan, `pl check`, `pl apply` (replaces steps, logs the old progress).
