---
name: starting-tasks
description: Entry point for a new task — turns the user's request into the task frame (goal, constraints, done-when, out-of-scope), asks only for what is missing, then hands off to writing-plans. Use when the user types /task or /starting-tasks, says "start a task", "new task", "bắt đầu task", or describes a feature/fix to build from scratch.
---

# Starting tasks

Input: `/task <free text>` (in Claude Code: `/task`; in other agents: a message starting with `/task` or `task:`).

## Steps

1. `nt brief` (keeping-notes). If an open task exists on this branch, ask: continue it, or start a new one?
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
4. Suggest `/grill` (grilling-designs) — **do not auto-run it** — when the task adds or changes a public interface or schema, touches 3 or more modules (`rm --zone`), or the user's answers left a design choice open. One line: "This has open design choices — run `/grill` first?" If the user declines, continue.
5. If not in plan mode, suggest it: plan mode with the planning model, execution with a cheaper one (Claude Code: `/model opusplan`, `Shift+Tab`).
6. Follow writing-plans to produce the plan from the frame. Put the frame's fields in the plan header.

Do not create a branch, edit files or write notes before the plan is accepted.
