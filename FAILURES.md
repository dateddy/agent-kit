# Failure log

Every agent mistake the user corrects gets an entry here, newest last, no ids (so two branches can both add entries and merge cleanly). An entry must name a **control**.
Prefer a computational control (script, hook, check that always runs) over a new line of instruction.

Entry format:

```
## <YYYY-MM-DD> · <short title>
- What happened: <observable facts>
- Root cause: <why it was possible>
- Control: <what now prevents it; file or script names>
```

## 2026-11-07 · Fast-forward merge into main
- What happened: in a downstream repo (LAD) the agent ran `git merge --ff-only` to bring a stage branch into `main`. The repo convention is a `--no-ff` merge commit with message `feat(<scope>): merge Stage N, …`. The agent never looked at the existing history first.
- Root cause: the convention lived only in history, not in any rule, and nothing checked the merge command or the pushed range.
- Control: `scripts/guard-git.sh` (PreToolUse) blocks `--ff-only`, `--squash` and plain merges into protected branches and prints `git log --first-parent --merges -5 <branch> --oneline`; `scripts/pre-push` rejects non-merge first-parent commits pushed to protected branches; the installer offers `git config merge.ff false`; managing-git has a mandatory pre-merge history check.

## 2026-11-07 · Code drifted from the plan; transforms shipped untested
- What happened: review of the same downstream repo found many deviations from the plan and data transformations with no tests or weak ones.
- Root cause: plans were prose, so scope and "done" were not checkable; test coverage of changed code was never measured.
- Control: plan steps carry machine-readable `Files:` and `Tests:` fields (writing-plans, `scripts/plan-step.sh`); `scripts/verify.sh` fails on files outside the plan's cumulative `Files:` list unless logged in the decision log, and runs `diff-cover` so changed lines must be covered (default 90%).
