# Changelog

Notable changes to agent-kit, newest first. Versions follow semver 0.x (`VERSION`); the section before 0.3 predates versioning.
Links to `README.md#...` point at the section of the README that documents the feature.

## Unreleased

No behaviour change.

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
