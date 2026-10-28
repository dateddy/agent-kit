---
name: managing-git
description: Runs git workflows (commit, branch, stash, merge, rebase, push, conflict resolution) through deterministic checks in scripts/gitkit.py, producing Conventional Commits. Use whenever the user asks to commit, write a commit message, create or switch branches, stash, merge, rebase, pull, push, or resolve conflicts.
---

# Managing git

`scripts/gitkit.py` decides; you write prose. Run it, obey exit codes:
`0` OK → continue · `2` WARN → quote the warning and the exact next command to the user, continue only after an explicit yes · `1` BLOCK → stop, fix the cause, re-run.

Path: `python <this-skill>/scripts/gitkit.py <cmd>` (shown below as `gk`). Config: repo `.agentkit.json` → `git` section.

## Decision boundary

| Script decides (never override) | You decide | User decides (always ask) |
|---|---|---|
| message format, allowed types/scopes | how to group files into commits | anything in "Destructive ops" |
| secret block, preflight safety checks | commit subject/body wording | splitting into >1 commit |
| when to ask (protected branch = WARN) | | committing directly on a protected branch |
| allowed types, subject rules | which type (feat, fix, refactor, …) | merge vs rebase strategy |
| conflict prediction, branch naming | how to resolve a conflict hunk | false-positive secret override |

## Commit

```
- [ ] 1. git status -sb && git log --oneline -5
- [ ] 2. Group changes by concern. >1 group → propose groups to user, one commit each
      (code + its tests + its doc line = one commit;
       kit-managed files — notes/, .agentkit/, .agentkit.json — never count as a concern)
- [ ] 3. git add <explicit paths>      (never `git add -A`/`.` unless user said "all")
- [ ] 4. gk preflight commit           (WARN on protected branch → ask: commit here or new branch?)
- [ ] 5. gk scan --staged
- [ ] 6. gk diff --staged              (capped; read only what you need)
- [ ] 7. pick the type with reference/commit-examples.md; write the message to .git/AGENT_MSG
         → gk lint-msg .git/AGENT_MSG → fix until 0
- [ ] 8. git commit -F .git/AGENT_MSG && git log --oneline -1
```

**Rule: code + its tests + its doc line = one commit.** Never split a feature from its own tests, and never ask the user about it.

Message rules (lint enforces the rest): subject says *what* in imperative; body (optional, for non-trivial changes) says *why* and side effects, not a file list. Examples: [reference/commit-examples.md](reference/commit-examples.md).

**Hooks:** if the repo has the agent-kit git hooks (`install.py --git-hooks`), `git commit` itself runs `scan --staged` and `lint-msg`. Never pass `--no-verify`; a hook BLOCK is fixed, not bypassed.

Amend only the last **unpushed** commit (`git commit --amend`), and only if user asked.

## Branch

One task = one branch (keeping-notes keys task notes by branch name).

1. `gk branch-name <type> <short description> [--ticket ABC-12]` → prints name.
2. `git switch -c <name>` (from the intended base; ask if base is unclear).
3. Uncommitted work on a protected branch moves with `switch -c` — no stash needed.

## Stash

- Save: `git stash push -m "<why>" [-- paths]` (always with message; add `-u` only for untracked files the user wants kept).
- List: `git stash list`. Restore: `gk preflight stash-pop` → `git stash apply stash@{n}` → verify → `git stash drop stash@{n}`.

## Merge / rebase / pull / push

1. `git fetch --prune`
2. `gk preflight merge <target>` (or `rebase`, `pull`, `push`). Read predicted conflicts.
3. Default strategy: merge into feature branch from base; rebase only unpushed local branches. Ask user if unsure.
4. Conflicts → follow [reference/conflicts.md](reference/conflicts.md).
5. Push: `git push -u origin <branch>` first time; after a rebase of a pushed branch: `git push --force-with-lease` (ask first).

## Destructive ops — ask the user, quote the exact command, wait for "yes"

`reset --hard`, `push --force*`, `branch -D`, `clean -fd`, `stash drop/clear`, `rebase` of a pushed branch, `checkout -- <file>`/`restore` of uncommitted work, `filter-repo`, deleting remote branches.
Before any of them: `git branch backup/<name>-<yyyymmdd>` or note the SHA from `git reflog`, so it is recoverable.

## Recovering

Lost commit / bad reset → `git reflog -n 20`, then `git branch rescue <sha>`. Details: [reference/recovery.md](reference/recovery.md).
