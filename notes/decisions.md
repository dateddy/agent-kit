# Decisions

Append-only. Never edit or delete an entry; to change a decision, add a new one
with `Supersedes: D-xxx`. Written by notes.py decide.

## D-9152 · 2026-11-10 · guards-language · refactor/guard-py
Guard logic moves to Python stdlib (3.9+); the .sh files stay as thin wrappers so installed hooks keep working. Wrapper finds python3, python, py -3 via command -v, skipping */WindowsApps/* stubs; no python = fail like missing jq (exit 2 hook, 1 arg).
Why: testable tokenizer and rules, no jq on the guard path, room for 1b rules

## D-0274 · 2026-11-11 · guards-force-push · feat/guard-fs
Force push to a non-protected branch: ask (follows guards.destructive). Protected: always BLOCK.
Why: Force push can drop other people's commits; protected branches never allow it.

## D-502c · 2026-11-11 · guards-read-outside · feat/guard-fs
Reading files outside the repo is allowed by default; only fs.deny_read is blocked.
Why: The agent must read library source.

## D-43a9 · 2026-11-11 · guards-read-deny · feat/guard-fs
Sensitive reads via Read/Grep/Glob use Claude Code permissions.deny, not a hook.
Why: A hook costs ~390 ms per read.

## D-6983 · 2026-11-11 · guards-headless · feat/guard-fs
Headless mode only via explicit AGENTKIT_HEADLESS=1; every ask becomes block.
Why: No guessing: an unset variable must never change safety behaviour.

## D-68d3 · 2026-11-11 · guards-unresolved-var · feat/guard-fs
Destructive command whose target holds an unresolved variable (rm -rf $BUILD_DIR): ask.
Why: The real path is unknown.

## D-4b60 · 2026-11-11 · guards-old-test · feat/guard-fs
test_guard_git.sh: only the 2 force-push-to-main expectations (+main, --force origin main) change ask to BLOCK; logged as a Deviation.
Why: New force-push rule blocks them on protected branches (spec Part 2); no other old case may change.

## D-31bb · 2026-11-11 · guards-cd-tracking · feat/guard-fs
Track literal 'cd <dir>' in a command line; cd $X / cd - / bare cd make cwd unknown, so later relative targets count as unresolved (ask).
Why: Relative paths need the right cwd to be judged.

## D-87d0 · 2026-11-11 · guards-py-sh · feat/guard-fs
scripts/py.sh (ak_find_py) is the single interpreter finder; guard-git.sh and guard-fs.sh source it; install.py inlines its body into hook_block.
Why: One place skips the WindowsApps stub; git hooks also work without --guards.

## D-16f3 · 2026-11-11 · guards-edit-argmode · feat/guard-fs
Add arg mode 'guard.py edit <path>' (BLOCK exit 1), like 'guard.py bash'.
Why: Other agents and tests need it without hook JSON.

## D-2806 · 2026-11-11 · guards-verify-pipe · feat/guard-fs
Pipe-verify rule blocks | and |& only; || passes (vf || true is a known gap). New lex() carries operator kinds; tokenize() output unchanged.
Why: vf || echo FAIL is legitimate; old tokenizer tests must not change.

## D-4c0c · 2026-11-11 · guards-perm-deny-file · feat/guard-fs
permissions.deny rules are written to the same settings file as the hooks (settings.local.json; --team: settings.json).
Why: Same scope and same uninstall story.

## D-45a3 · 2026-11-11 · guards-fp-sweep · feat/guard-fs
tools/fp_sweep.py scans doc code blocks, runs in CI, exits 1 on any BLOCK.
Why: Keeps false-positive BLOCKs at 0 as rules grow.

## D-baf8 · 2026-11-11 · guards-perm-deny-syntax · feat/guard-fs
permissions.deny from fs.deny_read: Read(~/x/**) and relative Read(**/.env) as is, absolute /x as //x, C:/x as //c/x; fs.allow_read becomes Read(!pattern) (relative patterns only). Docs (code.claude.com/docs/en/permissions): a Read deny also applies best-effort to Grep/Glob and Edit/Write, deny beats allow, ! carves out of earlier same-file relative rules only, relative rules match under the project dir only.
Why: Read official docs instead of guessing; //-anchored rules cannot take a ! exception, so .env.example stays readable only with relative patterns.

## D-8437 · 2026-11-11 · slim-a · refactor/slim-down
Remove crafting-prompts and tracking-experiments entirely.
Why: Unused optional skills add installer and doc weight.

## D-6480 · 2026-11-11 · slim-b · refactor/slim-down
Git workflow goes to plugin commit-commands@claude-plugins-official; kit keeps only gitkit scan (pre-commit) and lint-msg (commit-msg) as git hooks.
Why: Plugin covers commit/push/PR; hooks are agent-independent.

## D-4c92 · 2026-11-11 · slim-c · refactor/slim-down
Keep writing-plans: plan mode is the interface, the plan file is the contract.
Why: Plan file is machine-checked by pl check.

## D-ccad · 2026-11-11 · slim-d · refactor/slim-down
Kit stays a skill set installed by install.py, not packaged as a plugin.
Why: Keeps AGENTS.md and git hooks usable by other agents.

## D-be72 · 2026-11-11 · slim-e · refactor/slim-down
grilling-designs merged into writing-plans Frame: grill always runs, asks only when gaps remain.
Why: One path instead of a manual optional step.

## D-1dee · 2026-11-11 · slim-f · refactor/slim-down
debugging-systematically becomes a core skill; AGENTS rule 7 unconditional.
Why: Failure handling should not depend on --with.

## D-2d4d · 2026-11-11 · slim-g · refactor/slim-down
Lite path stops only for must-ask points; assumptions are shown, work continues, and they go in the commit body under Assumptions:.
Why: Avoid blocking on reversible defaults.

## D-1aa6 · 2026-11-11 · slim-h · refactor/slim-down
notes/knowledge.md (committed) holds repo/env facts; agent writes and reports 1 line.
Why: Facts found after >=3 tries should not be rediscovered.

## D-7867 · 2026-11-11 · slim-q1 · refactor/slim-down
README positioning: Claude Code fully supported; other agents get AGENTS.md plus git hooks only.
Why: Matches what is actually enforced.

## D-1931 · 2026-11-11 · slim-q2 · refactor/slim-down
Installer removes deleted skills/commands if hash equals a shipped version; modified ones warn and are kept; experiments/ and prompts/ never touched.
Why: Safe migration of downstream repos.

## D-5c12 · 2026-11-11 · slim-q3 · refactor/slim-down
Drop --with and <!--if:skill-->; --with <any> exits 1 'removed in 0.5.0, see CHANGELOG'.
Why: No optional skills remain.

## D-defc · 2026-11-11 · slim-q4 · refactor/slim-down
guard allows git branch -D when git branch -vv shows upstream [gone]; never on protected branches.
Why: /clean_gone needs it; gone upstream means merged or deleted remotely.

## D-217d · 2026-11-11 · slim-q5 · refactor/slim-down
Plan field Mode is ignored; pl check only WARNs 'Mode is ignored'.
Why: Field is dead.

## D-0196 · 2026-11-11 · slim-i · refactor/slim-down
Removed skill whose short name (ex, pk) still appears in the project section of AGENTS.md/CLAUDE.md counts as in use: warn and keep.
Why: cascade-lad still uses ex run; silent removal would break it.

## D-b795 · 2026-11-11 · slim-j · refactor/slim-down
enabledPlugins commit-commands@claude-plugins-official goes in the same settings file as hooks (settings.local.json, --team: settings.json); installer prints 'claude plugin install ... --scope project'. Source: code.claude.com/docs/en/plugins, /plugins/loading, /settings.
Why: Docs: project-enabled plugins still need a per-machine install.

## D-cbd4 · 2026-11-11 · slim-k · refactor/slim-down
Tag v0.4.0 on the 1b merge cd4c178.
Why: Migration hashes of shipped files need a reproducible reference.

## D-4b92 · 2026-11-12 · 2b-q1 · refactor/verify-engine
Single engine scripts/verify.py; skill verifying-changes deleted; AGENTS keeps 3 lines on verify.
Why: Hooks enforce the verdict.

## D-364a · 2026-11-12 · 2b-q2 · refactor/verify-engine
One verify config block; old guards.* and verify.* keys still read for one version with WARN deprecated.
Why: cascade-lad uses both.

## D-a67b · 2026-11-12 · 2b-q3 · refactor/verify-engine
Affected tests at Stop chosen by rmap import graph (stdlib); pytest-testmon later.
Why: No new dependency.

## D-54c8 · 2026-11-12 · 2b-q4 · refactor/verify-engine
Integrity (deleted test, skip/xfail, lost assert) blocks Stop once with a list; agent must tell the user; stop_hook_active respected.
Why: Avoid stop loops.

## D-aa99 · 2026-11-12 · 2b-q5 · refactor/verify-engine
Git hooks call a copy in <git-common-dir>/agentkit/ placed by the installer; config read from working-tree .agentkit.json, defaults if missing.
Why: Branches older than the kit install can commit.

## D-a2a8 · 2026-11-12 · 2b-q6 · refactor/verify-engine
Kit's own Stop runs test-kit + fast unit tests; full selftest only at pre-push and CI.
Why: Selftest takes >100 s.

## D-59f3 · 2026-11-12 · 2b-q7 · refactor/verify-engine
nt task --check BLOCKs unless verify recorded a full PASS for the current tree; --force --why logs the reason; nt task --uncheck added.
Why: Control for 2a step 8 ticked before CI green.

## D-5983 · 2026-11-12 · 2b-q8 · refactor/verify-engine
pre-push ported to Python (prepush.py); jq and lib.sh removed from runtime; test_pre_push.sh must pass unchanged.
Why: One implementation, no jq.

## D-0afe · 2026-11-12 · 2b-a · refactor/verify-engine
format is its own stage; guards.ruff=false disables lint and format; summary format and argv stay those of verify.sh.
Why: test_verify.sh pins them.

## D-4ca6 · 2026-11-12 · 2b-b · refactor/verify-engine
verify.<stage> = auto|command string|false plus tool cmds, hook_stages, push_stages, fail_under, coverage_xml, scope_ignore; new keys win; verify.test maps to tests.
Why: Two old schemas must both run.

## D-24fa · 2026-11-12 · 2b-c · refactor/verify-engine
Integrity diffs base...HEAD plus worktree; each finding blocks Stop once then is acked by hash in state; CLI exit 2; push only reports.
Why: diff HEAD misses committed weakening.

## D-9901 · 2026-11-12 · 2b-d · refactor/verify-engine
Only a full-mode PASS (CLI or --push) on the current tree key unlocks nt task --check; key ignores scope_ignore paths.
Why: Notes edits must not invalidate the key.

## D-53be · 2026-11-12 · 2b-e · refactor/verify-engine
Stage cache key adds argv and tool path+mtime; --no-cache and AK_VERIFY_NO_CACHE=1 disable it.
Why: Env-driven stubs are invisible to the tree key.

## D-560d · 2026-11-12 · 2b-f · refactor/verify-engine
Stop default stages: lint(changed), tests(affected, no --cov), integrity, scope; explicit hook_stages honoured as-is.
Why: Keeps cascade-lad on lint+scope.

## D-3c57 · 2026-11-12 · scope · refactor/verify-engine
tools/selftest.py: step 1 test for the dbg hint (DB4 asserts no promptkit) lives in selftest; file not listed in step 1 Files. tools/bench/perf.py: edit case restores the file it reformats.
Why: Plan said selftest or an existing dbg test file; selftest holds the DB scenarios.

## D-fa10 · 2026-11-12 · scope · refactor/verify-engine
Step 2 also touches skills/writing-plans/scripts/plan.py (active_step ignores a trailing CR: task notes written by nt on Windows are CRLF, so plan.py field found no checklist and the scope stage failed) and tools/install.py (GUARD_SCRIPTS gains akcore.py and verify.py, which verify.sh now needs; the rest of the installer work stays in step 5).
Why: vf must exit 0 on the kit itself after step 2; both are latent from 0.5.0 / forced by the new wrapper.

## D-f4b8 · 2026-11-12 · 2b-h · refactor/verify-engine
akcore.py holds only helpers shared by scripts: config load + defaults + legacy key folding, repo root and git-common-dir, git runner, in_repo, tool lookup and process run, utf8 stdio. No stage or rule logic: default_ignores, changed_files, RUFF_EXCLUDE and the stage switch names live in verify.py. guard.py does NOT use akcore yet (own git runner, RepoInfo.in_repo, resolve_path with symlink and worktree handling); test_guard_py.py needs no change. Moving guard.py onto akcore goes to BACKLOG.
Why: Keep the shared module small and stable; guard.py is already tested and covers more path cases.

## D-c0a1 · 2026-11-12 · 2b-i · refactor/verify-engine
Worktree ak-baseline (the v0.5.0 checkout used for the baseline perf) stays until the step 8 measurements, then git worktree remove; the AUDIT notes it.
Why: perf.py --root needs the v0.5.0 tree for the before/after table.

## D-293a · 2026-11-12 · scope · refactor/verify-engine
2b fixup touches: .github/workflows/selftest.yml (clean-tree step), tools/gen_shipped_hashes.py (--help must not write; LF), tools/bench/perf.py, .gitignore, .claude/settings.json (new: deny Bash(ruff format:*)), FAILURES.md, scripts/akcore.py, scripts/verify.py, scripts/tests/test_verify_py.py.
Why: Requested by the user after step 2.

## D-dcde · 2026-11-12 · 2b-g · refactor/verify-engine
Stage defaults follow the 0.5.0 skill for every stage incl. diff_cover: "auto" (default) runs when the tool exists, else SKIP with a one-line note; true, a custom command string or an explicitly set tool key (ruff_cmd, pytest_cmd, diff_cover_cmd) = required: missing tool FAILs; false/null = off. Missing coverage report: SKIP under auto, FAIL when required. test_verify.sh t_missing_* set the tool keys, so they hold unchanged.
Why: User asked for the skill's semantics; explicit tool keys reconcile the two old tests without editing them.

## D-275e · 2026-11-12 · 2b-j · refactor/verify-engine
A stage switch (true/auto/false) decides whether a stage runs; *_cmd keys only say which command. A *_cmd equal to the 0.5.0 shipped default (ruff, pytest, diff-cover) is not explicit. A guards.* switch equal to its 0.5.0 default (true) reads as auto during deprecation; verify.<stage>: true written in the new block stays required. New .agentkit.example.json: every switch auto, *_cmd only in comments.
Why: A config copied unchanged from the 0.5.0 example held the shipped defaults, not the user's intent, and made a missing diff-cover a FAIL.

## D-c375 · 2026-11-12 · scope · refactor/verify-engine
2b example-config fixup touches .agentkit.example.json, scripts/akcore.py, scripts/verify.py, scripts/tests/test_verify_py.py and the new scripts/tests/fixtures/agentkit.example.0.5.0.json (the 0.5.0 example, copied unchanged, the fixture of the downstream-copy test).
Why: Requested by the user after the step 2 fixups.
