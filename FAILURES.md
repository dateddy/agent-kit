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

## 2026-11-07 · Guard scripts would be checked out with CRLF
- What happened: installing v0.3.0 into a Windows repo with `core.autocrlf=true`, git warned that `.agentkit/scripts/*.sh` would get CRLF on the next checkout. bash fails on CR, so the hooks would break on the next clone, branch switch or checkout.
- Root cause: the installer wrote the scripts with LF but set no line-ending rule, so git's autocrlf decided at checkout.
- Control: `install.py --guards` appends `.agentkit/scripts/** text eol=lf` to `.gitattributes`; selftest G1 commits the scripts under `core.autocrlf=true`, checks one out again and fails on any CR.

## 2026-11-07 · ruff-fix hook reformatted a file in another project
- What happened: with the guards installed in a downstream repo, the agent edited `tools/install.py` in the agent-kit clone; the PostToolUse hook ran `ruff check --fix` and `ruff format` on it with ruff defaults (88 columns) and rewrote ~350 lines.
- Root cause: `ruff-fix.sh` acted on every edited `.py` path the hook received, not only on files of its own repository.
- Control: `ruff-fix.sh` exits 0 unless `git rev-parse --show-toplevel` of the file equals the repo's own top level; `scripts/tests/test_verify.sh` covers a loose file, a file in another repo and a file in a subfolder.
- Follow-up (v0.4): generalise to every hook that writes files (`lib.sh` `in_repo`); see BACKLOG.md.

## 2026-11-07 · Stop hook loops while the agent is correctly waiting for the user
- What happened: during guard Test C in a downstream repo (cascade-lad) the agent wrote a one-step plan on branch test/scope and ran `pl check`, which returned exit 2 (WARN: "step 1: no tests"). Following AGENTS.md (WARN → show the user and wait for yes), the agent stopped before `pl apply`. The Stop hook (`verify.sh --hook`) then blocked the stop 9 times in a row with `scope FAIL — no task note at notes/tasks/test--scope.md, so the active step is unknown. Run pl apply, or pass --step N.` (VERDICT: FAIL, exit 2). The only fix it suggested was the step waiting for the user, so the agent could not satisfy the hook without breaking the WARN rule. The loop ended only when Claude Code overrode the block / the user interrupted.
- Root cause: the scope stage treated "plan file exists, task note missing" as FAIL in every mode. In hook mode that exits 2, which blocks the stop; `verify.sh` drained and ignored the hook JSON (relying on Claude Code's 8-block cap) instead of checking `stop_hook_active`. Nothing told "waiting for the user" apart from "skipped a step".
- Control: `scripts/verify.sh` — (1) in `--hook` mode, a plan with no task note is `SKIP  plan not applied yet` (plain CLI and `--push` still fail); (2) `--hook` reads `stop_hook_active` from stdin and, when it is true, prints the summary and exits 0, so a stop is blocked at most once per chain. Rule: hook mode blocks only on failures the agent can fix alone. Tests in `scripts/tests/test_verify.sh`: `t_stop_hook_active_never_blocks`, `t_plan_not_applied_skips_in_hook_mode`, `t_plan_not_applied_still_fails_in_cli`, `t_regression_stop_loop_while_waiting_for_user`.
