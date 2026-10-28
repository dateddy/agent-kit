---
name: managing-git
description: Runs git workflows (commit, branch, stash, merge, rebase, push, conflict resolution) through deterministic checks in scripts/gitkit.py, producing Conventional Commits. Use whenever the user asks to commit, write a commit message, create or switch branches, stash, merge, rebase, pull, push, or resolve conflicts.
---

# Managing git

`scripts/gitkit.py` decides; you write prose. Obey its exit codes: `0` OK → continue · `2` WARN → quote the warning and the exact next command, continue only after an explicit yes · `1` BLOCK → stop, fix the cause, re-run.
Run `python <this-skill>/scripts/gitkit.py <cmd>` (below: `gk`). Config: `.agentkit.json` → `git`. Who decides what (script, you, user): [reference/boundaries.md](reference/boundaries.md).

## Commit

```
1. git status -sb && git log --oneline -5
2. Group changes by concern; >1 group → propose the groups to the user, one commit each.
   Kit files (notes/, .agentkit/, .agentkit.json) never count as a concern.
3. git add <explicit paths>     (never -A / . unless the user said "all")
4. gk preflight commit          (protected-branch WARN → ask: commit here or new branch?)
5. gk scan --staged
6. gk diff --staged             (capped; read only what you need)
7. m=$(git rev-parse --git-path AGENT_MSG)   (never a hard-coded .git/: in a worktree .git is a file)
   write the message to "$m" (type: reference/commit-examples.md) → gk lint-msg "$m" until 0
8. git commit -F "$m" && git log --oneline -1
```

- Code + its tests + its doc line = one commit. Never split them and never ask about it.
- Subject says *what*, imperative; body (non-trivial changes) says *why* and side effects, not a file list. [Examples](reference/commit-examples.md).
- Installed git hooks run `scan --staged` and `lint-msg` on `git commit`. Never pass `--no-verify`; fix a hook BLOCK.
- `git commit --amend` only on **your own latest, unpushed** commit (`git branch -r --contains HEAD` prints nothing),
  and say in your reply that you amended it. Never amend a commit you did not make in this task, or a pushed one.

## Branch

One task = one branch (task notes are keyed by branch name). `gk branch-name <type> <short description> [--ticket ABC-12]`
→ `git switch -c <name>` from the intended base (ask if unclear). Uncommitted work moves with `switch -c`; no stash needed.

## Stash, merge, rebase, pull, push

`gk preflight <op>` first (`stash-pop`, `merge <target>`, `rebase`, `pull`, `push`) and read the predicted conflicts.
Steps and default strategy: [reference/sync.md](reference/sync.md). Conflicts: [reference/conflicts.md](reference/conflicts.md).

## Destructive ops — ask the user, quote the exact command, wait for "yes"

`reset --hard`, `push --force*`, `branch -D`, `clean -fd`, `stash drop/clear`, `rebase` of a pushed branch, `checkout -- <file>`/`restore` of uncommitted work, `filter-repo`, deleting remote branches.
Before any of them: `git branch backup/<name>-<yyyymmdd>` or note the SHA from `git reflog`.
Lost commit / bad reset → `git reflog -n 20`, then `git branch rescue <sha>`; more in [reference/recovery.md](reference/recovery.md).
