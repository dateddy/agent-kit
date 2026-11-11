#!/usr/bin/env bash
# guard-git.sh - validate a shell command: git rules, destructive commands, piped verify, writes outside the repo.
# A thin wrapper: the logic lives in guard.py (Python stdlib, 3.9+) next to this file. Kept under this
# name so the Claude Code hook that `install.py --guards` wrote (templates/claude-hooks.json) keeps working.
# The interpreter is found by py.sh (python3, python, py -3; the Microsoft Store stub is skipped).
#
# Exit-code convention (Claude Code PreToolUse hook when the command comes from stdin JSON):
#   0 = allow, or ask: JSON on stdout makes Claude Code show its permission prompt
#   2 = BLOCK; stderr is fed back to the model
#   (exit 1 would NOT block a Claude Code hook, so it is never used in hook mode)
# Argument mode (`guard-git.sh git merge --ff-only x`, for other agents and manual use):
#   0 = allow or ask (same JSON on stdout), 1 = BLOCK (git-hook / kit convention), stderr = message.
# stdout carries nothing but that JSON; every other message goes to stderr.
# AGENTKIT_HEADLESS=1: nobody can answer a question, so every ask becomes a block.
# No python found: the guard cannot judge anything, so it fails loudly (2 in hook mode, 1 in argument mode).
#
# Input: Claude Code PreToolUse JSON on stdin (command in .tool_input.command, directory in .cwd), or the command as arguments.
# "ask" output follows https://code.claude.com/docs/en/hooks.md#json-output :
#   {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"..."}}
#
# Rules (see guard.py; branch names come from git.protected_branches in .agentkit.json):
#   on a protected branch: `git merge` needs --no-ff; --ff-only and --squash are always blocked;
#     --abort/--continue/--quit pass.
#   commit, or push that targets a protected branch: ask (git.protected_commit: ask | allow | block).
#   block: --no-verify (commit -n), core.hooksPath changes, force push to a protected branch, verify piped into
#     another command, any token that names a file matching fs.deny_read.
#   ask (guards.destructive: ask | allow | block): reset --hard, branch -D, clean -f, stash drop, checkout -- <path>, ...;
#     writes or deletes outside the repo (fs.outside_repo_write, fs.allow_write).
# The command is tokenized with quote awareness; `git switch/checkout` earlier in the same command line
# moves the branch the later segments are judged on. `bash -c "..."` and `eval` are looked into.
# This catches mistakes, not a determined bypass (no shell expansion, aliases or scripts are followed).
set -u
if [ $# -gt 0 ]; then rc=1; else rc=2; fi
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=py.sh
. "$here/py.sh"
# shellcheck disable=SC2154  # ak_py and ak_py_args are set by ak_find_py in py.sh; CI runs shellcheck without -x

if ak_find_py; then
  if [ -n "$ak_py_args" ]; then exec "$ak_py" "$ak_py_args" "$here/guard.py" bash "$@"; fi
  exec "$ak_py" "$here/guard.py" bash "$@"
fi

echo "python not found: install Python 3.9+ (https://www.python.org/downloads/); the agent-kit guards need it" >&2
exit "$rc"
