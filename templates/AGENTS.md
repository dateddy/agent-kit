# Agent rules

Skills live in `.agentkit/skills/<name>/` — read a skill's SKILL.md before the work it covers.
Short names used below (run with `python`; paths relative to `.agentkit/skills/`):
{{SHORT_NAMES}}

## Mode: question or task
- Message starts with `?` → **question mode**: read-only. Read, search and run read-only commands; do not edit files, commit, or touch task notes. Answer concisely, then stop.
- Starts with `/task` or `task:` → follow **writing-plans** (Frame first).
- No prefix and the message asks for a change (add, fix, implement, refactor, remove, rename, commit…) → **task mode**.
- No prefix and unclear → treat as a question; end with one line offering to do it.

## Task lifecycle
1. Start of session: `nt brief`. Trust it over re-exploring; read only what it points to.
2. Task → writing-plans: its Frame always runs and asks only about gaps that a lookup cannot close. ≤2 files and no architecture decision → lite path: no plan file, assumptions go in the commit body under `Assumptions:`. Otherwise plan first; after it is accepted: branch (`git switch -c feat/short-name`), save plan, `pl check`, `pl apply`.
3. Per step: `pl step N` → implement → `vf` → `nt task --check N --done "…" --next "…"` → commit (`/commit`). Before editing a module: `rmap --zone <file>`. Inner loop: `vf --affected`; the full `vf` is still required before commit.
4. Done only when `vf` exits 0 after the last edit (never pipe it; the VERDICT line is authoritative). If the guards are installed (`install.py --guards`), their `verify.sh` must exit 0 too. Never weaken tests to pass.
5. Decision settled by the user → `nt decide --topic … --text … --why …`.
6. Something fails → debugging-systematically: `dbg open --layer code|data|llm …`, no guess-and-edit.

## Failure log
- Every mistake of yours that the user corrects gets an entry in `FAILURES.md` (date, what happened, root cause, control). The entry must name a control; prefer a script or hook that always runs over a new instruction line.

## Scripts decide, you write
- Exit codes: 0 OK · 2 WARN → show the user and wait for yes · 1 BLOCK → fix and re-run. Never bypass a BLOCK.
- Destructive or irreversible commands (reset --hard, force push, deleting branches/files, dropping stashes): quote the exact command and wait for an explicit yes. With the guards installed (`install.py --guards`) `guard-git.sh` enforces this: it asks the user, and blocks a force push to a protected branch.
- Writing, editing or deleting files outside the repo: state the absolute path and wait for the user's yes.

## Git
- Commit, push and PRs go through `/commit` and `/commit-push-pr` (plugin commit-commands). Without the plugin: Conventional Commits, one concern per commit, on a task branch.
- Before merging into a protected branch run `git log --first-parent --merges -5 <target> --oneline` and follow that merge style (the guard enforces it).
- Never use `--no-verify` or any other way around a git hook; a hook BLOCK is fixed, not bypassed.

## Token discipline
- Locate before reading: `rmap`, search (rg/grep), `rmap --symbols`, then read the needed line range.
- Wrap commands that may print >60 lines in `run`. Grep saved logs instead of re-running.
- Never print lockfiles, datasets, notebook outputs, generated files or checkpoints.
- Replies: lead with the result. No recap of steps, no restating code you just wrote, no filler.
- Reply in the user's language.

<!-- Project-specific rules below this line -->
