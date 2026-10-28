# Agent rules

## Mode: question or task
- Message starts with `?` → question mode: read-only. Read, search and run read-only commands; do not edit files or commit. Answer concisely, then stop.
- The message asks for a change (add, fix, implement, refactor, remove, rename, commit…) → task mode.
- Unclear → treat as a question; end with one line offering to do it.

## Working rules
- Done only when the project's tests pass after your last edit; run them and quote the result. Never weaken, skip or delete tests to make them pass.
- A failing command is a signal to find the cause, not to retry blindly.
- Commit with Conventional Commit messages (`type(scope): imperative subject`), one concern per commit, on a task branch, not on main/master.

## Safety
- Never commit secrets, keys or `.env` files.
- Never use `--no-verify` or otherwise bypass a git hook or check.
- Destructive or irreversible commands (reset --hard, force push, deleting branches/files, dropping stashes): quote the exact command and wait for an explicit yes.

## Token discipline
- Locate before reading: search first, then read only the needed line range.
- Do not print long command output, lockfiles, datasets, generated files or checkpoints; look at the tail or grep.
- Replies: lead with the result. No recap of steps, no restating code you just wrote, no filler.
- Reply in the user's language.
