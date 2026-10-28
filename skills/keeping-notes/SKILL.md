---
name: keeping-notes
description: Keeps task state in files instead of chat history, via scripts/notes.py — one note per git branch plus an append-only project decision log — so any session or agent can resume with a ~30-line brief. Use when starting or resuming a session, after finishing each plan step, when a direction is decided, or when the user asks to resume, hand off, record a decision, or recall why something was chosen.
---

# Keeping notes

Chat is disposable; `notes/` is the memory. `scripts/notes.py` owns the format — never hand-edit these files.
Path: `python <this-skill>/scripts/notes.py <cmd>` (shown as `nt`). Exit codes: 0 OK · 2 WARN (tell user) · 1 BLOCK.

```
notes/tasks/<branch>.md   one per task branch, gitignored (personal working state)
notes/decisions.md        append-only, committed (project knowledge)
```

## When to run what

| Moment | Command |
|---|---|
| First use in a repo | `nt init` |
| Session start / resuming / new chat | `nt brief` — read it instead of re-exploring the repo |
| Starting a task (after creating its branch) | `nt task --goal "<outcome>" --plan "<step>" --plan "<step>"` |
| A plan step finished | `nt task --check <N> --done "<what changed, 1 line>" --next "<next action>" [--files a.py b.py]` |
| Blocked / waiting on user | `nt task --status blocked --next "<what is needed>"` |
| User settles a direction ("chốt", "go with X") | `nt decide --topic <area> --text "<conclusion>" --why "<reason>"` |
| A decision changes | `nt decide ... --supersedes D-007` (never edit the old one) |
| Recall why | `nt show --topic <area>` · `nt show --id D-007` |
| Task merged / dropped | `nt close` (or `nt sync` to auto-close merged/deleted branches) |

## Rules

- Update the task note at **plan-step boundaries and before each commit**, not after every tool call.
- `--done` and `--next` are one line each: facts and the next concrete action, no narrative.
- Record a decision only when it changes what will be built. Explanations and Q&A are not decisions.
- A decision is a conclusion (≤10 lines, ≤600 chars; the script rejects longer). Put the reason in `--why`.
- Read with `brief`/`show`, which filter to active, relevant entries. Do not `cat` the files.
- In question mode (`?` prefix, see AGENTS.md) do not touch task notes; only `decide` if the user says to record it.

## Side questions in a separate chat

New chat: run `nt brief` → answer → if the user settles something, `nt decide`.
Back in the task chat: `nt show --topic <area>` (or `nt brief`) and continue. No copy-pasting transcripts.
