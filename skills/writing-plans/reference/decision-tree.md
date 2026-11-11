# Decision tree (architecture branch of the Frame)

Walk it only when Frame step 4 triggers. Idea credit: Matt Pocock's "grill-me". Short names as in SKILL.md; `nt` = keeping-notes, `rmap` = economizing-context.

## 1. Look before asking
Never ask what the code, the notes or the docs can answer. `nt brief` (live decisions are not re-asked), `rmap` for shape, `rmap --zone <file>` for the modules a change touches, `rmap --symbols <file>` for signatures. Ask only about intent, constraints and trade-offs.

## 2. Build the tree silently
List the open choices; cover the areas that apply and skip the rest without comment. A parent is resolved before its children ("SQLite or a JSON file?" before "which index?"); an answer that removes a branch drops it.

| Area | Typical open choices |
|---|---|
| data | entities, fields, ids, ownership, sizes, sample inputs |
| interfaces | public functions/CLI/endpoints, argument and return shapes, compatibility with callers |
| dependencies | new package or service, version pin, why the stdlib or an existing one is not enough |
| errors | invalid input, partial failure, what the caller sees |
| storage | format, location, schema, migration of existing data |
| concurrency | what runs in parallel, shared state, ordering, locking |
| security | auth, secrets, permissions, who may call it |
| config | what is configurable, defaults, where it lives |
| infra | deploy, CI, environments, rollback |
| testing | what proves it works, fixtures, what stays untested |

## 3. Ask in rounds
At most 5 questions per message, as many rounds as the tree needs (typically 4-10 questions in all). Each question asks one thing and carries a recommendation, a one-line reason and what you checked:

```
Q3 · storage — Where should the index live?
Recommended: build in memory at startup. Reason: 200 rows, nothing to keep in sync.
Checked: rmap shows no existing store; D-3a7f rules out new dependencies.
```

`ok` / `yes` / `đồng ý` accepts the recommendation; anything else is the answer. If it contradicts a live decision, say so and ask whether to supersede it. No recaps between rounds.

## 4. Record at once
After each settled architecture answer, before the next round:

```
nt decide --topic <area> --text "<the conclusion, one or two lines>" --why "<reason>"
```

Topics are the area names above or something more specific. A changed mind uses `--supersedes D-xxxx`. Answers that are not design choices (explanations, chat preferences) are not decisions.

## 5. Stopping
On "enough", "đủ rồi", "stop" or "skip the rest": stop asking. Unresolved questions become `Assumptions:` with the default you will use, recorded as one decision (`nt decide --topic assumptions …`; split by topic if it exceeds the length limit).

## 6. Hand-off
Collect the decision ids of this session into the plan header: `Decisions: D-3a7f, D-91c2`. `pl check` BLOCKs an id that does not exist and WARNs on a superseded one. Do not create a branch or edit files before the plan is accepted.
