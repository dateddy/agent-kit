# Stash, merge, rebase, pull, push

`gk` = `python <managing-git>/scripts/gitkit.py`.

## Stash

- Save: `git stash push -m "<why>" [-- paths]` (always with a message; add `-u` only for untracked files the user wants kept).
- List: `git stash list`.
- Restore: `gk preflight stash-pop` → `git stash apply stash@{n}` → verify → `git stash drop stash@{n}`.

## Merge / rebase / pull / push

1. `git fetch --prune`
2. `gk preflight merge <target>` (or `rebase`, `pull`, `push`). Read the predicted conflicts.
3. Default strategy: merge the base into the feature branch; rebase only unpushed local branches. Ask the user if unsure.
4. Conflicts → follow `reference/conflicts.md`.
5. Push: `git push -u origin <branch>` the first time; after a rebase of a pushed branch:
   `git push --force-with-lease` (ask first).
