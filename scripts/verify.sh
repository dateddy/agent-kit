#!/usr/bin/env bash
# verify.sh - thin wrapper: runs verify.py (the one verify engine) with the same arguments and stdin.
#
# Exit-code convention: whatever verify.py returns (CLI: 0 pass, 1 fail, 2 pass with test changes; --hook: 2 blocks
# a Claude Code Stop; --push: 1 rejects the push). A missing python is exit 2 under --hook, 1 otherwise.
# Documentation: `verify.py --help`. Kept so hooks, git hooks and scripts that call verify.sh keep working.
set -u
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=py.sh
. "$HERE/py.sh"
if ! ak_find_py; then
  echo "verify.sh: python not found (tried python3, python, py -3)" >&2
  case " $* " in *" --hook "*) exit 2 ;; *) exit 1 ;; esac
fi
# shellcheck disable=SC2154  # ak_py and ak_py_args are set by ak_find_py in py.sh
exec "$ak_py" ${ak_py_args:+"$ak_py_args"} "$HERE/verify.py" "$@"
