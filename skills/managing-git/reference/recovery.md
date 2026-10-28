# Recovery recipes

| Situation | Command |
|---|---|
| Undo last commit, keep changes staged | `git reset --soft HEAD~1` |
| Unstage a file | `git restore --staged <file>` |
| Committed on main by mistake (unpushed) | `git branch <new>` → `git reset --soft origin/main` → `git switch <new>` → commit |
| Lost commit after reset/rebase | `git reflog -n 20` → `git branch rescue <sha>` |
| Dropped stash | `git fsck --unreachable \| grep commit` → `git stash apply <sha>` |
| Secret already committed (unpushed) | remove from file, `git commit --amend` (or interactive rebase), rotate the key anyway |
| Secret already pushed | **Rotate the key first** (it is compromised). Then history rewrite (`git filter-repo`) only with user approval |
| Wrong commit message (unpushed) | `git commit --amend` → lint again |
| Revert a pushed commit | `git revert <sha>` (never reset shared history) |
