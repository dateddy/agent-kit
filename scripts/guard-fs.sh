#!/usr/bin/env bash
# guard-fs.sh - judge the file an Edit/Write/MultiEdit/NotebookEdit is about to change.
# A thin wrapper around `guard.py edit` (Python stdlib, 3.9+); the interpreter is found by py.sh.
#
# Input: Claude Code PreToolUse JSON on stdin (.tool_input.file_path, or .tool_input.notebook_path; .cwd is the directory
# relative paths are taken from), or the path as the only argument.
# A file in the repo (or in a worktree of the same repo) passes. Outside it: fs.allow_write passes, everything else follows
# fs.outside_repo_write in .agentkit.json (ask | block, default ask). The message names the absolute path.
#
# Exit codes, as for guard-git.sh:
#   0 = allow, or ask (JSON on stdout makes Claude Code show its permission prompt)
#   2 = BLOCK in hook mode (stderr is fed back to the model); 1 = BLOCK in argument mode
# AGENTKIT_HEADLESS=1: every ask becomes a block.
# No python found: fails loudly (2 in hook mode, 1 in argument mode).
set -u
if [ $# -gt 0 ]; then rc=1; else rc=2; fi
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=py.sh
. "$here/py.sh"
# shellcheck disable=SC2154  # ak_py and ak_py_args are set by ak_find_py in py.sh; CI runs shellcheck without -x

if ak_find_py; then
  if [ -n "$ak_py_args" ]; then exec "$ak_py" "$ak_py_args" "$here/guard.py" edit "$@"; fi
  exec "$ak_py" "$here/guard.py" edit "$@"
fi

echo "python not found: install Python 3.9+ (https://www.python.org/downloads/); the agent-kit guards need it" >&2
exit "$rc"
