---
name: grilling-designs
description: Interviews the user about a design before planning — walks the decision tree (data, interfaces, errors, storage, config, testing, rollout) one question at a time, each with a recommended answer, and records every settled answer as a decision. Use when the user types /grill, asks to be grilled or interviewed about a design, or before planning a task that adds or changes a public interface or schema, touches three or more modules, or has an open design choice.
disable-model-invocation: true
---

# Grilling designs

A short interview that turns a vague design into recorded decisions, so the plan (writing-plans) and its executor never have to guess. Manual only: it runs on `/grill <topic>` (Claude Code) or when the user asks for it. Idea credit: Matt Pocock's "grill-me".

Short names: `nt` = keeping-notes, `rm` = economizing-context. Exit codes: 0 OK · 2 WARN (tell the user) · 1 BLOCK (fix, re-run).

## Steps

```
- [ ] 1. nt brief, then rm / rm --zone <module>      learn what the code already says
- [ ] 2. Build the decision tree (silently)           list open choices; parents before children
- [ ] 3. Ask ONE question per message                recommended answer + one-line reason
- [ ] 4. nt decide after EVERY settled answer        not batched at the end
- [ ] 5. Stop on "enough" / "đủ rồi"                 open questions become listed assumptions
- [ ] 6. Hand off to writing-plans                   plan header: Decisions: D-012, D-013
```

### 1. Look before asking

Never ask the user what the code, the notes or the docs can answer. Run `nt brief` (existing decisions are not re-asked), `rm` for shape, `rm --zone <file>` for the modules a change touches, `rm --symbols <file>` for signatures. Ask only about intent, constraints and trade-offs.

### 2. Decision tree

Cover the areas that apply; skip the rest without comment:

| Area | Typical open choices |
|---|---|
| data | entities, fields, ids, ownership, sizes, sample inputs |
| interfaces | public functions/CLI/endpoints, argument and return shapes, compatibility |
| errors | invalid input, partial failure, what the caller sees |
| storage | format, location, migration of existing data |
| config | what is configurable, defaults, where it lives |
| testing | what proves it works, fixtures, what stays untested |
| rollout | flag or direct, order of changes, how to roll back |

Resolve a parent before its children: ask "SQLite or JSON file?" before "which index?". If an answer removes a branch, drop it.

### 3. One question per message

Format, exactly:

```
Q3/~8 · storage — Where should the index live?
Recommended: build in memory at startup — the dataset has 200 rows, so no file to keep in sync.
Reply "ok" to accept, or tell me what you prefer.
```

- One thing per question; never "A or B, and C?".
- The recommendation is your honest best choice with a one-line reason grounded in the code or notes.
- `ok` / `yes` / `đồng ý` accepts the recommendation. Anything else is the answer; if it contradicts an existing decision, say so and ask whether to supersede it.
- Do not summarise or recap between questions.

### 4. Record immediately

After each settled answer:

```
nt decide --topic <area> --text "<the conclusion, one or two lines>" --why "<reason>"
```

Use the area names above as topics (`data`, `interfaces`, …) or a more specific one. A changed mind uses `--supersedes D-007`. Answers that are not design choices (explanations, preferences about the chat) are not decisions.

### 5. Stopping

On "enough", "đủ rồi", "stop" or "skip the rest": stop asking. List the unresolved questions as **assumptions**, each with the default you will use. Record them as one decision: `nt decide --topic assumptions --text "…" --why "grilling stopped early"` (keep it within the script's length limit; split by topic if needed).

### 6. Hand-off

Collect the decision ids from this session and give them to writing-plans. The plan header gets one extra line:

```
Decisions: D-012, D-013, D-014
```

`pl check` BLOCKs on an id that does not exist and WARNs on a superseded one. Then continue with writing-plans as usual; do not create a branch or edit files before the plan is accepted.

## Rules

- Questions total: as few as the tree needs (typically 4–10). Skip areas the task does not touch.
- The user may answer several questions at once; record each one separately.
- Do not start implementing, and do not write the plan inside the interview.
- Question mode (`?`) and trivial tasks do not need a grill.
