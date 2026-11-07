#!/usr/bin/env bash
# test-kit.sh - run every scripts/tests/test_*.sh and report one total.
# Exit-code convention: plain CLI. 0 = all cases passed, 1 = a case failed or a prerequisite is missing.
# Needs bash, git, jq. No bats: each test file is a plain bash script built on tests/helpers.sh.
#   test-kit.sh [name-filter]      e.g. test-kit.sh guard-git
set -u
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
for tool in git jq; do
  command -v "$tool" >/dev/null 2>&1 || { echo "$tool not found: install it to run the kit tests" >&2; exit 1; }
done

total=0 bad=0
for t in "$here"/tests/test_*.sh; do
  case $(basename "$t") in *"${1:-}"*) ;; *) continue ;; esac
  total=$((total + 1))
  bash "$t" || bad=$((bad + 1))
done
[ "$total" -gt 0 ] || { echo "no test files match '${1:-}'" >&2; exit 1; }
if [ "$bad" -eq 0 ]; then echo "ALL OK: $total test file(s)"; else echo "FAILED: $bad of $total test file(s)" >&2; fi
[ "$bad" -eq 0 ]
