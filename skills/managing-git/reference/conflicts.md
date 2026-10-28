# Resolving conflicts

1. List: `git diff --name-only --diff-filter=U`
2. Per file, look at only the conflict hunks: `git diff <file>` (combined diff), or the three sides:
   `git show :1:<file>` (base) · `:2:` (ours / current branch) · `:3:` (theirs / incoming).
   During a **rebase**, "ours" is the branch being rebased onto and "theirs" is your commit — the reverse of merge.
3. Classify each hunk:
   - **Mechanical** (both sides added different imports/entries, formatting, lockfile) → resolve yourself.
     Lockfiles: take one side, then regenerate (`npm install`, `uv lock`, `poetry lock --no-update`).
   - **Semantic** (both changed the same logic differently) → show the user both versions in ≤10 lines each and a proposed merge; wait for approval.
4. Edit, remove all markers, then check none remain: `git diff --check` and `grep -rn '^<<<<<<<\|^>>>>>>>' <files>`.
5. Run the verifying-changes gate if available (tests must pass on the merged result).
6. `git add <file>` per file, then `git merge --continue` / `git rebase --continue`.
7. Abort anytime with `git merge --abort` / `git rebase --abort` — safe, restores pre-op state.

Never resolve by blanket `git checkout --ours/--theirs .` without user approval.
