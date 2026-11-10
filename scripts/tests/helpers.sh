# shellcheck shell=bash
# helpers.sh - tiny test framework for the agent-kit guard scripts. Sourced by test_*.sh.
# Exit-code convention: not a guard. A test file exits 1 when any case failed, 0 otherwise.
#
#   case_run "name" fn      run fn in a subshell inside a fresh throwaway git repo, record PASS/FAIL
#   run cmd args...         run a command; sets OUT (stdout), ERR (stderr) and RC (exit status)
#   assert_eq want got msg  / assert_contains haystack needle msg / assert_not_contains ...
# Inside fn `set -e` is on, so a failed assertion aborts that case only.

# shellcheck disable=SC2034  # read by all test_*.sh (test_guard_git, test_lib, test_plan_step, test_pre_push, test_verify)
KIT_SCRIPTS=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
PASSED=0
FAILED=0
TMP_ROOT=$(mktemp -d)
trap 'rm -rf "$TMP_ROOT"' EXIT

# git must not depend on the developer's own config (signing, templates, default branch)
export GIT_CONFIG_GLOBAL=/dev/null GIT_CONFIG_SYSTEM=/dev/null GIT_TERMINAL_PROMPT=0
unset GIT_DIR GIT_WORK_TREE

# shellcheck disable=SC2034  # OUT: test_guard_git, test_lib, test_plan_step, test_verify; RC: test_guard_git, test_plan_step, test_pre_push, test_verify; ERR: all five
run() {
  OUT=$("$@" 2>"$TMP_ROOT/err") && RC=0 || RC=$?
  ERR=$(cat "$TMP_ROOT/err")
}

assert_eq() { [ "$1" = "$2" ] || { echo "    $3: want [$1] got [$2]" >&2; return 1; }; }
assert_contains() { case $1 in *"$2"*) ;; *) echo "    $3: [$2] not in [$1]" >&2; return 1 ;; esac; }
assert_not_contains() { case $1 in *"$2"*) echo "    $3: unexpected [$2] in [$1]" >&2; return 1 ;; esac; }

# mkrepo: new repo with one commit on main, cwd moved into it
mkrepo() {
  local d
  d=$(mktemp -d "$TMP_ROOT/repo.XXXXXX")
  cd "$d"
  git init -q -b main .
  git config user.name tester
  git config user.email tester@example.com
  git config commit.gpgsign false
  commit_file README.md "hello" "docs: init"
}

# commit_file <path> <content> <message>
commit_file() {
  mkdir -p "$(dirname "$1")"
  printf '%s\n' "$2" >"$1"
  git add -- "$1"
  git commit -q -m "$3"
}

write_cfg() { printf '%s\n' "$1" >.agentkit.json; }

case_run() {
  local name=$1 fn=$2 rc
  # The subshell must not sit in an `if`/`&&`/`||` condition: bash ignores `set -e` there, and a failed
  # assertion would then not abort the case.
  ( set -e; mkrepo; "$fn" ) >"$TMP_ROOT/case.out" 2>&1
  rc=$?
  if [ "$rc" -eq 0 ]; then
    PASSED=$((PASSED + 1))
    echo "PASS $name"
  else
    FAILED=$((FAILED + 1))
    echo "FAIL $name"
    sed 's/^/    /' "$TMP_ROOT/case.out"
  fi
}

finish() {
  echo "-- $(basename "$0"): $PASSED passed, $FAILED failed"
  [ "$FAILED" -eq 0 ]
}

# make_plan [checked-steps...]: plan + task note for branch feat/x; each argument checks that step
make_plan() {
  git switch -q -c feat/x
  mkdir -p notes/tasks
  cat >notes/tasks/feat--x.plan.md <<'EOF'
# Plan: demo
Constraints: none
Done when: tests pass
Out of scope: everything else

### Step 1: Parse input
- Mode: solo
- Files: src/a.py, tests/test_a.py (new)
- Contract: parse(s) -> int
  invariant: result is positive
- Tests: test_parse_ok, test_parse_zero
- Out of scope: src/b.py
- Verify: pytest tests/test_a.py

### Step 2: Write output
- Mode: pipeline
- Files: src/b.py, src/a.py
- Contract: write(n) -> None
- Tests: test_write
- Out of scope: src/a.py signatures
EOF
  {
    echo "# Task: demo"; echo "- branch: feat/x"; echo; echo "## Plan"
    local i mark
    for i in 1 2; do
      mark=" "
      for c in "$@"; do [ "$c" = "$i" ] && mark=x; done
      echo "- [$mark] $i. step $i"
    done
    echo; echo "## Next"; echo "x"
  } >notes/tasks/feat--x.md
}

# install_stubs: ruff / pytest / diff-cover stand-ins first on PATH. They log their arguments to $STUB_LOG and
# take their exit codes from STUB_RUFF_RC / STUB_PYTEST_RC / STUB_DC_RC (default 0).
install_stubs() {
  local bin="$TMP_ROOT/bin"
  mkdir -p "$bin"
  cat >"$bin/ruff" <<'EOF'
#!/usr/bin/env bash
echo "ruff $*" >>"$STUB_LOG"
if [ "${STUB_RUFF_RC:-0}" -ne 0 ]; then echo "ruff: E501 line too long (stub)"; fi
exit "${STUB_RUFF_RC:-0}"
EOF
  cat >"$bin/pytest" <<'EOF'
#!/usr/bin/env bash
echo "pytest $*" >>"$STUB_LOG"
for a in "$@"; do case $a in --cov-report=xml:*) mkdir -p "$(dirname "${a#--cov-report=xml:}")"; echo "<coverage/>" >"${a#--cov-report=xml:}" ;; esac; done
if [ "${STUB_PYTEST_RC:-0}" -ne 0 ]; then echo "1 failed (stub)"; fi
exit "${STUB_PYTEST_RC:-0}"
EOF
  cat >"$bin/diff-cover" <<'EOF'
#!/usr/bin/env bash
echo "diff-cover $*" >>"$STUB_LOG"
if [ "${STUB_DC_RC:-0}" -ne 0 ]; then echo "Failure. Coverage is 50%, below 90% (stub)"; fi
exit "${STUB_DC_RC:-0}"
EOF
  chmod +x "$bin"/*
  export PATH="$bin:$PATH"
}

# reset_stubs: empty the call log and clear the exit-code switches
reset_stubs() {
  export STUB_LOG="$TMP_ROOT/stub.log"
  : >"$STUB_LOG"
  unset STUB_RUFF_RC STUB_PYTEST_RC STUB_DC_RC
}
