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
