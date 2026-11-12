#!/usr/bin/env bash
# ruff-fix.sh - Claude Code PostToolUse hook (matcher Edit|Write): thin wrapper for `verify.py --edit`, which runs
# `ruff check --fix` and `ruff format` on the one .py file that was just edited, if it belongs to this repository.
#
# Exit-code convention (Claude Code PostToolUse hook): 0 = done or nothing to do; 2 = stderr is shown to Claude
# (the edit already happened, so this reports what could not be fixed or that ruff is missing). Never 1 (silent).
# Switch off with verify.lint=false and verify.format=false (0.5.0: guards.ruff=false) in .agentkit.json.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=py.sh
. "$HERE/py.sh"
if ! ak_find_py; then
  echo "ruff-fix.sh: python not found (tried python3, python, py -3)" >&2
  exit 2
fi
# shellcheck disable=SC2154  # ak_py and ak_py_args are set by ak_find_py in py.sh
exec "$ak_py" ${ak_py_args:+"$ak_py_args"} "$HERE/verify.py" --edit
