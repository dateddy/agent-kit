# Changelog

Notable changes to agent-kit, newest first. Versions follow semver 0.x (`VERSION`); the section before 0.3 predates versioning.
Links to `README.md#...` point at the section of the README that documents the feature.

## Unreleased

Version 0.5.0: the kit gets smaller. Two optional skills and the git skill are gone, grilling moves inside `writing-plans`, `debugging-systematically` becomes core, and `notes/knowledge.md` is new. The verify engine is unchanged (next release). Skill descriptions that are always loaded: 888 -> 469 tokens (`validate_skills.py` prints both); `README.md` 857 -> 480 lines.

**Removed**
- `crafting-prompts` and `tracking-experiments` (skills, short names `pk` and `ex`, the `prompts` and `experiments` config blocks, their README sections and tests). Your own `experiments/` and `prompts/` data is never touched.
- `managing-git`. The git workflow is the `commit-commands@claude-plugins-official` plugin (`/commit`, `/commit-push-pr`, `/clean_gone`). `gitkit.py` keeps only `scan --staged` (pre-commit) and `lint-msg` (commit-msg), now at `scripts/gitkit.py` and installed to `.agentkit/scripts/gitkit.py`. `diff`, `branch-name`, `preflight` and the `git.branch_pattern`, `git.branch_types`, `git.diff_ignore` keys are gone; `git.protected_branches` and `git.protected_commit` stay (the guards read them).
- `grilling-designs` and the `/grill` command: the interview is now part of the `writing-plans` Frame and always runs.
- The `--with` flag (exits 1, "removed in 0.5.0") and the `<!--if:skill-->` conditionals in `AGENTS.md` and `CLAUDE.md`.
- `Mode:` in plan steps is no longer required; `pl check` only WARNs "Mode is ignored".

**Added**
- `writing-plans` Frame: loads context in proportion to the task, sorts unclear points (look up / assume / must ask), a small branch for every task, an architecture branch for listed areas (`reference/decision-tree.md`), rounds of at most 5 questions, `nt decide` at once. Lite path: stop only for must-ask points; assumptions go in the commit body under `Assumptions:` (`lint-msg` accepts the block). `SKILL.md` is 38 lines.
- `plan.py field NAME [--cumulative] [--step N]`: the parser behind `scripts/plan-step.sh`, which is now a wrapper (no `jq`). Output, stderr and exit codes match the 0.4.0 script on 140 compared cases (selftest PF1).
- `notes/knowledge.md` (created by the installer, header only): repo and environment facts, one line each; `nt brief` prints them and WARNs above `notes.max_knowledge_facts` (20); `AGENTS.md` says when to add a line.
- Guard: `git branch -D` is allowed when every operand is a local branch whose upstream `git branch -vv` shows as gone (never a protected branch, never `-r`, not under `guards.destructive=block`).
- Installer: enables the plugin in the settings file next to the hooks (`enabledPlugins`; `--team` for the shared file) and prints `claude plugin install commit-commands@claude-plugins-official --scope project`. `--team` no longer requires `--guards`.
- `tools/gen_shipped_hashes.py` and `tools/shipped_hashes.json` (what the installer compares removed skills against); `validate_skills.py` prints the description-token total now and at `v0.4.0`.
- `debugging-systematically` is core (AGENTS.md rule 6 is unconditional). `dbg.py` is unchanged.

**Migration (0.4.0 -> 0.5.0)**: pull the kit and run `install.py` again; add `--git-hooks` if you use the git hooks.
- Removed skills and `grill.md` are deleted when every file matches a shipped version (CRLF-insensitive). An edited copy, or one whose short name (`ex`, `pk`, `gk`) or skill name your own rules in `AGENTS.md` still use, is kept with a WARN and the installer exits 2.
- Git hooks: a 0.4.0 hook calls `.agentkit/skills/managing-git/scripts/gitkit.py`. `install.py --git-hooks` rewrites the kit's block to `.agentkit/scripts/gitkit.py` in place (one block, your lines kept). Until then the removed skill leaves a small shim at the old path, so the hook fails every commit with "run install.py again" instead of skipping the check, and the installer WARNs. A hook whose `gitkit.py` is missing fails the same way. If you switch to a branch that predates `.agentkit/scripts/gitkit.py`, run `--git-hooks` again or `--uninstall-git-hooks`.
- `notes/knowledge.md` is created if missing. `--with …` in scripts or bench `meta.json` files must be dropped. `scan` no longer skips `experiments/runs.jsonl` and `prompts/runs/`.
- Plans keep working; a `Mode:` line now WARNs. A downstream `AGENTS.md` that still says `ex run` in its project section keeps working: the experiments skill stays until you remove it.

## v0.4.0

Behaviour change, hence `VERSION` 0.4.0: the guards block and ask in more places. A repo that relied on the old behaviour can set `guards.destructive` and `fs.outside_repo_write` to `allow`, or `fs.deny_read` to `[]` ([rules](README.md#guard-rules)).

**Added (guards)**
- `guard.py bash` rules: **BLOCK** `--no-verify` and `commit -n`, `git -c core.hooksPath=…`, `git config … core.hooksPath`, force push (`-f`, `--force`, `--force-with-lease`, `--force-if-includes`, `+refspec`) to a protected branch, and `verify.py`/`vf`/`verify.sh` piped (`|`, `|&`) into another command. **Ask** (`guards.destructive`: ask | block | allow) on `reset --hard|--merge|--keep`, `branch -D`, `clean -f`, `stash drop|clear`, `checkout -- <path>`, `restore` without `--staged`, `push --delete`, `filter-branch`, `filter-repo`, `update-ref -d`, `reflog expire`, `gc --prune=now` and a force push to an unprotected branch.
- Writes outside the repo: `guard.py edit` / `scripts/guard-fs.sh` (new `PreToolUse` hook for `Edit|Write|MultiEdit|NotebookEdit`) and the same check in `guard.py bash` for `rm`, `rmdir`, `mv`, `cp`, `ln`, `touch`, `mkdir`, `chmod`, `chown`, `truncate`, `tee`, `dd of=`, `install`, `sed -i`, `perl -i` and the redirects `>`, `>>`, `2>`, `&>`. Paths are resolved (`~`, `$HOME`, `..`, symlinks even for new files, drive letters, `/c/…`, the hook's `cwd`, literal `cd`); a worktree of the same repo counts as the repo. Settings: `fs.outside_repo_write`, `fs.allow_write`. An unresolved variable in the target of `rm`/`mv`/`truncate`/`dd`/`sed -i`/`perl -i`/`>` asks.
- `fs.deny_read` / `fs.allow_read`: a Bash word that names a protected file (`cat ~/.ssh/id_rsa`) is blocked; `install.py --guards` adds matching `Read(...)` rules to `permissions.deny` (merged, idempotent). Claude Code applies a Read deny rule to Grep and Glob best effort; see the README for the syntax limits.
- `AGENTKIT_HEADLESS=1`: every ask becomes a block.
- `guard.py`: `lex()` with operator and redirect kinds (`2>&1` is no longer split on `&`), `seg_ops()`.
- `scripts/py.sh`: the single interpreter finder; `guard-git.sh`, `guard-fs.sh` and the git hooks written by `install.py` use it, so the hooks no longer pick the Microsoft Store `python3` stub.
- `tools/fp_sweep.py`: runs every shell command of the README, templates and skills through the guard (CI step; must report BLOCK=0), and `--perf`.
- Tests: `test_guard_py.py` tables for every rule and for path resolution, selftest G7, wrapper and hook-block tests.

**Changed (guards)**
- `test_guard_git.sh`: two expectations moved from ask to BLOCK (`git push origin +main` and `git push --force origin main` from a feature branch).
- `install.py --guards` copies `py.sh` and `guard-fs.sh`; re-running it on an older install adds the new hook once. `templates/AGENTS.md` says the destructive-command rule is enforced and asks for the absolute path before a write outside the repo.
- `scripts/pre-push`: the unused local-ref field is read into `_` (the SC2034 `disable` is gone).

**Added**
- A `lint` CI job (Ubuntu): `shellcheck --severity=warning` on `scripts/*.sh`, `scripts/pre-push` and `scripts/tests/*.sh`, and `ruff check tools skills`. The workflow now declares `permissions: contents: read`.
- `ruff.toml`: rules `E`, `F`, `W`, line length 180 (the longest existing line is 179). Compact style (`E702`, `E741`, `E731`) is ignored on purpose; `ruff format` is not used here (FAILURES.md 2026-11-07).
- `CHANGELOG.md`: the changelog moved out of the README.
- `scripts/guard.py` (Python standard library, 3.9+): the logic of `guard-git.sh` ported one to one (quote-aware tokenizer, `bash -c`/`eval` up to three levels, `git -C`, switch/checkout tracking, merge/commit/push rules, `.agentkit.json` reading with the same defaults). Entry point `guard.py bash [COMMAND...]`; without arguments it reads the Claude Code hook JSON from stdin. `scripts/tests/test_guard_py.py` (unittest) covers it and runs in CI on every OS and Python version.

**Changed**
- `guard-git.sh` is now a thin wrapper that runs `guard.py`; same name, so hooks installed by `install.py --guards` and `templates/claude-hooks.json` keep working. Behaviour is unchanged: same exit codes, same messages, stdout carries only the "ask" JSON. The wrapper looks for `python3`, `python`, then `py -3` with `command -v` and skips the Microsoft Store `python3` stub (`*/WindowsApps/*`). With no python it fails like a missing `jq` did: stderr message, exit 2 in hook mode, 1 in argument mode.
- `guard-git.sh` no longer needs `jq`. **`jq` is still required by the installer**: `verify.sh`, `pre-push` and `plan-step.sh` (and `lib.sh`, which they source) read `.agentkit.json` with it, so `install.py --guards` keeps refusing to run without it. The guards now also need Python 3.9+.
- `install.py --guards` copies `guard.py` to `.agentkit/scripts/`; the existing `.agentkit/scripts/** text eol=lf` rule in `.gitattributes` covers it. The lint job also runs `ruff check scripts`.
- Lint findings fixed in selftest, `exp.py` and `gitkit.py` (unused import and variable, duplicate imports, four scenario functions that shadowed others); intentional `shellcheck` findings carry a `disable` with a reason.
- The unversioned "v3" changelog is now the historical section "pre-0.3 (internal v3)".

## v0.3.2

**Fixed**
- `verify.sh --hook` no longer loops the Stop hook while the agent waits for the user. It reads `stop_hook_active` from the hook input and never exits 2 when it is true (summary still printed, exit 0). A plan that is saved but not applied (no task note) is `SKIP` in hook mode instead of `FAIL`; the plain CLI and `--push` are unchanged ([hook mode](README.md#verify-hook-mode), FAILURES.md 2026-11-07).

## v0.3.1

**Added**
- `verify.sh --stages <list>` and `--push`; `guards.hook_stages` (Stop hook) and `guards.push_stages` (`pre-push`) pick which stages each caller runs, e.g. lint + scope on every turn, tests + diff-cover before a push. Unknown names fail.
- `install.py --guards` checks that bash finds `jq` before writing anything, and exits 1 with install hints if not. `jq` is documented as a prerequisite in [Install](README.md#2-install).

**Fixed**
- The installer writes `.agentkit/scripts/** text eol=lf` to `.gitattributes`: with `core.autocrlf=true` git checked the scripts out with CRLF and bash failed on them.
- `ruff-fix.sh` no longer formats `.py` files outside the repository it is installed in (it reformatted another project's file with ruff defaults).

## v0.3.0

The kit's first versioned release (`VERSION`); it follows the unversioned "v3" below.

**Added**
- `scripts/` guards: `guard-git.sh`, `verify.sh`, `pre-push`, `plan-step.sh`, `ruff-fix.sh`, `lib.sh`, and `test-kit.sh` with bash tests ([Guards](README.md#13-guards-scripts-that-always-run-v030)).
- `install.py --guards [--team]` (scripts, Claude Code hooks merge, `pre-push` block, `merge.ff` prompt); `--uninstall-git-hooks` also removes the `pre-push` block.
- `FAILURES.md` and the failure-log rule in `AGENTS.md`; the installer creates a header-only `FAILURES.md` in your repo.
- `guards` section in `.agentkit.example.json`; `templates/claude-hooks.json`.
- managing-git: mandatory pre-merge history check (`git log --first-parent --merges -5 <target> --oneline`).

**Changed**
- Plan steps are `### Step N:` with `Mode`, `Files`, `Contract`, `Tests`, `Out of scope` (and optional `Verify`). `plan.py` reads both formats; `pl check` WARNs on the old one (exit 2), so scripts that expected exit 0 for an old-format plan need the new format.

## pre-0.3 (internal v3)

**Fixed (Windows field test)** — most of this landed in the v2 branch and is covered by the selftest:
- Every script switches stdout/stderr to UTF-8 (`errors="replace"`); `validate_skills.py` fails a script that does not. Child processes are decoded as UTF-8 and given `PYTHONUTF8=1` / `PYTHONIOENCODING=utf-8`.
- `verify.py` finds `tests/`, `test/` or root `test_*.py` without any config file, probes `python -m pytest --version`, falls back to `unittest discover`, and prints verdicts with their exit code (`PASS (exit 0)`, `NOTHING VERIFIED (exit 2)`, …).
- `gk scan` and `gk diff` ignore kit-managed paths; the false "mixed categories" WARN is gone with `suggest`.
- Clarifying questions: one thing each, a recommended default, at most 3.

**Changed (breaking)** — re-run `install.py` on each project to migrate:
- `starting-tasks` is merged into `writing-plans` (Frame phase and lite path). The installer removes the old skill and rewrites `.claude/commands/task.md`.
- `gitkit.py suggest`, `snapshot` and `stashes` are removed; use `git status -sb`, `git log --oneline -5`, `git stash list`.
- The default install is the five core skills; `tracking-experiments`, `crafting-prompts`, `grilling-designs` and `debugging-systematically` need `--with experiments,prompts,grill,debug`. A re-run keeps optional skills that are already installed.
- `AGENTS.md` is generated from the installed skills.

**Added**
- `install.py --git-hooks` / `--uninstall-git-hooks` (see [Git hooks](README.md#11-git-hooks)); `lint-msg` accepts `fixup!`, `squash!`, `amend!` and ignores the `git commit -v` scissors section.
- `tools/selftest.py` (35 scenarios, `--cp1252`) and GitHub Actions CI on 3 OSes × Python 3.9 / 3.13.
- `tools/bench/` benchmark harness with a fake `claude` for tests ([Benchmark](README.md#12-benchmark-does-the-kit-help)).
