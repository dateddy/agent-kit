#!/usr/bin/env bash
# ruff-fix.sh - Claude Code PostToolUse hook (matcher Edit|Write): `ruff check --fix` and `ruff format`
# on the one .py file that was just edited, if it belongs to this repository. Anything else is a no-op:
# a file in another repo or outside any repo follows that project's own formatting rules, not ours.
#
# Exit-code convention (Claude Code PostToolUse hook): 0 = done or nothing to do; 2 = stderr is shown to
# Claude (the edit already happened, so this reports what could not be fixed or that ruff is missing).
# Exit 1 would be a silent non-blocking error, so it is never used. Nothing is written to stdout.
# Switch off with guards.ruff=false in .agentkit.json; the tool is guards.ruff_cmd (default: ruff).
set -u
AK_FAIL=2
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

file=$(cat | jq -r '.tool_input.file_path // empty' 2>/dev/null | tr -d '\015')
[ -n "$file" ] || exit 0
file=${file//\\//}  # Windows paths from the hook JSON
case $file in *.py) ;; *) exit 0 ;; esac
cfg_on guards.ruff true || exit 0
[ -f "$file" ] || exit 0
top=$(git -C "$(dirname "$file")" rev-parse --show-toplevel 2>/dev/null) || exit 0
[ "$top" = "$(git rev-parse --show-toplevel 2>/dev/null)" ] || exit 0

read -r -a R <<<"$(cfg guards.ruff_cmd ruff)"
need "${R[0]}" ruff

out=$("${R[@]}" check --fix --quiet --force-exclude "${AK_RUFF_EXCLUDE[@]}" "$file" 2>&1) && rc=0 || rc=$?
"${R[@]}" format --quiet --force-exclude "${AK_RUFF_EXCLUDE[@]}" "$file" >/dev/null 2>&1
if [ "$rc" -ne 0 ]; then
  echo "ruff left problems in $file:" >&2
  printf '%s\n' "$out" | tail -n 15 >&2
  exit 2
fi
exit 0
