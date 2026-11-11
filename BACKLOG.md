# Backlog

Planned work, newest release first. An item moves to `CHANGELOG.md` when it ships; an item that
comes from a corrected agent mistake also gets a `FAILURES.md` entry naming the control.

## 2b (next release)

### Verify chains: verify may stand alone, be joined with `&&`, or be redirected
- Gap: the 1b rule blocks `vf | tail` and `vf |& tee`, but `vf || true` and `vf; echo done` also hide a failing verdict.
- Plan: a verify-chain rule in `guard.py`: verify only alone, after `&&`, or with a redirect; block `vf || …`, `vf; …`, `vf | …`.
- Tests: a table in `test_guard_py.py`; `fp_sweep` stays at BLOCK=0 ASK=0.

### `GIT_CONFIG_*` and `--config-env` for `core.hooksPath`
- Gap: `guard.py` blocks `git -c core.hooksPath=…` and `git config … core.hooksPath`, but `GIT_CONFIG_COUNT/KEY_n/VALUE_n` and `--config-env=core.hooksPath=VAR` redirect the hooks too.
- Plan: block both forms in `guard_hooks_bypass` (env prefixes are already tracked).

### Log every guard ask/block decision
- Gap: nobody can measure false-positive asks and blocks in real use.
- Plan: `guard.py` appends each ask/block decision (rule, command, branch, time) to `.agentkit/logs/guard.jsonl` (gitignored), with a `guards.log` switch.
- Tests: a decision is logged once; allow decisions are not; the file is created under `.agentkit/logs/`.

Also for 2b (from the slim-down plan): the tiered verify engine and hooks, an `rmap` cache, `REPO_MAP`; a gate in `pl check` for `Done when` and decision ids (step 3); bench tasks T6 and T7.

## v0.4

### Merge message pattern in guard-git and pre-push
- Gap: `guard-git.sh` blocks a merge into a protected branch without `--no-ff`, and `pre-push` requires merge commits
  on the first-parent line, but neither checks the merge **message**. A `--no-ff` merge with any message passes, so a
  repo convention such as `<type>(<scope>): merge <what>` is enforced only by the agent remembering it.
- Plan: optional `guards.merge_message_regex` (null = off). `guard-git.sh`: when a `git merge` into a protected branch
  carries `-m`/`--message`/`-F`, block if the message does not match; a merge without a message (editor) gets "ask".
  `pre-push`: every merge commit on the pushed first-parent range must match its subject against the regex; print the
  offenders and the regex. Shown in block messages together with `guards.merge_convention`.
- Tests: guard-git matching/non-matching `-m`, `--message=`, `-F <file>`; pre-push with one bad subject in a range.

### Plans required on chosen branches (scope must not SKIP silently)
- Gap: `verify.sh` reports `scope SKIP no plan for this branch (lite path)` and still passes. On branches that must
  follow a plan (e.g. `feat/*`), a missing or misnamed plan file silently turns the scope check off.
- Plan: `guards.require_plan_for` (list of branch globs, default `[]`). On a matching branch with no plan,
  the scope stage FAILs with "branch <b> requires a plan (guards.require_plan_for): pl path / pl check".
  Non-matching branches keep the lite-path SKIP.
- Tests: matching branch without plan → FAIL (exit 1, exit 2 with `--hook`); matching with plan → normal check;
  non-matching without plan → SKIP.

### Every hook that writes files stays inside its own repository
- Done in v0.4.0, for what the agent does: `guard.py edit` / `guard-fs.sh` (Edit, Write, MultiEdit, NotebookEdit) and the
  Bash write targets and redirects of `guard.py bash` ask or block outside the repo, with path resolution (symlinks,
  `..`, `~`, drive letters, worktrees of the same repo). See `CHANGELOG.md`.
- Gap that remains: `ruff-fix.sh` and any other hook that itself writes, formats or deletes a file from hook input.
  `ruff-fix.sh` has its own check since v0.3.1 (FAILURES.md, 2026-11-07), but there is no shared helper and nothing
  forces a new hook to use one.
- Plan: a `lib.sh` helper `in_repo <path>` (the file's `git rev-parse --show-toplevel` equals `$AK_ROOT`, after
  Windows path normalisation; or reuse `guard.py`'s `RepoInfo.in_repo` through a small CLI) used by every writing hook;
  `ruff-fix.sh` switches to it. A test in `test_lib.sh`, plus a kit-level check (bash test or selftest) that every
  script under `scripts/` that writes a path from hook input calls it first, so a new hook cannot skip it.
- Tests: `in_repo` for a loose file, another repo, a subfolder, a symlink out of the repo, a Windows `C:\` path.
