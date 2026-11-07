#!/usr/bin/env bash
# Tests for scripts/verify.sh and scripts/ruff-fix.sh, with stub ruff / pytest / diff-cover.
# Exit 1 when any case fails.
. "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

V="$KIT_SCRIPTS/verify.sh"
RF="$KIT_SCRIPTS/ruff-fix.sh"

# stubs: log their arguments to $STUB_LOG; exit codes come from STUB_RUFF_RC / STUB_PYTEST_RC / STUB_DC_RC
STUB_BIN="$TMP_ROOT/bin"
mkdir -p "$STUB_BIN"
cat >"$STUB_BIN/ruff" <<'EOF'
#!/usr/bin/env bash
echo "ruff $*" >>"$STUB_LOG"
if [ "${STUB_RUFF_RC:-0}" -ne 0 ]; then echo "ruff: E501 line too long (stub)"; fi
exit "${STUB_RUFF_RC:-0}"
EOF
cat >"$STUB_BIN/pytest" <<'EOF'
#!/usr/bin/env bash
echo "pytest $*" >>"$STUB_LOG"
for a in "$@"; do case $a in --cov-report=xml:*) mkdir -p "$(dirname "${a#--cov-report=xml:}")"; echo "<coverage/>" >"${a#--cov-report=xml:}" ;; esac; done
if [ "${STUB_PYTEST_RC:-0}" -ne 0 ]; then echo "1 failed (stub)"; fi
exit "${STUB_PYTEST_RC:-0}"
EOF
cat >"$STUB_BIN/diff-cover" <<'EOF'
#!/usr/bin/env bash
echo "diff-cover $*" >>"$STUB_LOG"
if [ "${STUB_DC_RC:-0}" -ne 0 ]; then echo "Failure. Coverage is 50%, below 90% (stub)"; fi
exit "${STUB_DC_RC:-0}"
EOF
chmod +x "$STUB_BIN"/*
export PATH="$STUB_BIN:$PATH"

setup() {
  export STUB_LOG="$TMP_ROOT/stub.log"
  : >"$STUB_LOG"
  unset STUB_RUFF_RC STUB_PYTEST_RC STUB_DC_RC
}

# change <path>: commit a file on the current branch
change() { commit_file "$1" "x" "feat: $1"; }

assert_clean_stdout() { assert_eq "" "$OUT" "stdout must stay empty"; }

t_all_pass_and_tool_arguments() {
  setup; make_plan
  change src/a.py
  run bash "$V"
  assert_eq 0 "$RC" "rc"; assert_clean_stdout
  assert_contains "$ERR" "VERDICT: PASS" "verdict"
  log=$(cat "$STUB_LOG")
  assert_contains "$log" "ruff check --quiet --output-format concise ." "ruff check, concise output"
  assert_contains "$log" "ruff format --check ." "ruff format --check"
  assert_contains "$log" "pytest --cov --cov-report=xml:.agentkit/coverage.xml" "pytest with coverage xml"
  assert_contains "$log" "diff-cover .agentkit/coverage.xml --compare-branch=main --fail-under=90" "diff-cover defaults"
}

t_out_of_plan_file_fails() {
  setup; make_plan
  change src/a.py
  change src/other.py
  run bash "$V"
  assert_eq 1 "$RC" "default exit code is 1"; assert_clean_stdout
  assert_contains "$ERR" "Out-of-plan file: src/other.py. Log the reason in the decision log or revert." "message"
  assert_not_contains "$ERR" "Out-of-plan file: src/a.py" "in-plan file is fine"
  assert_contains "$ERR" "scope       FAIL" "summary line"
}

t_hook_mode_exits_2() {
  setup; make_plan
  change src/other.py
  run bash "$V" --hook </dev/null
  assert_eq 2 "$RC" "--hook blocks with exit 2"
  assert_contains "$ERR" "VERDICT: FAIL (exit 2)" "verdict says 2"
}

t_hook_mode_passes_with_0() {
  setup; make_plan
  change src/a.py
  run bash "$V" --hook </dev/null
  assert_eq 0 "$RC" "rc"; assert_eq "" "$OUT" "stdout"
}

t_hook_mode_nothing_changed_is_instant() {
  setup
  run bash "$V" --hook </dev/null
  assert_eq 0 "$RC" "rc"; assert_eq "" "$ERR" "silent"
  assert_eq "" "$(cat "$STUB_LOG")" "no tool ran"
}

t_logged_in_decision_log_passes() {
  setup; make_plan
  change src/a.py
  change src/other.py
  mkdir -p notes
  printf '# Decisions\n\n## D-3f9a · 2026-11-07 · scope · feat/x\nsrc/other.py: shared helper is needed by step 1\nWhy: avoids a copy\n' >notes/decisions.md
  run bash "$V"
  assert_eq 0 "$RC" "logged file passes: $ERR"
}

t_decision_for_another_branch_does_not_count() {
  setup; make_plan
  change src/a.py
  change src/other.py
  mkdir -p notes
  printf '# Decisions\n\n## D-3f9a · 2026-11-07 · scope · feat/elsewhere\nsrc/other.py: needed\n' >notes/decisions.md
  run bash "$V"
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "Out-of-plan file: src/other.py" "still out of plan"
}

t_decision_must_name_the_whole_path() {
  setup; make_plan
  change src/a.py
  change src/o.py
  mkdir -p notes
  printf '# Decisions\n\n## D-3f9a · 2026-11-07 · scope · feat/x\nsrc/o.py.bak and xsrc/o.py are mentioned, not the file\n' >notes/decisions.md
  run bash "$V"
  assert_eq 1 "$RC" "substring of a longer name is not enough"
}

t_active_step_widens_scope_cumulatively() {
  setup; make_plan 1
  change src/a.py
  change src/b.py
  run bash "$V"
  assert_eq 0 "$RC" "step 2 active: files of steps 1 and 2 allowed: $ERR"
}

t_later_step_files_fail_early() {
  setup; make_plan
  change src/b.py
  run bash "$V"
  assert_eq 1 "$RC" "step 2's file while step 1 is active"
  assert_contains "$ERR" "Out-of-plan file: src/b.py" "message"
}

t_uncommitted_and_untracked_count() {
  setup; make_plan
  change src/a.py
  echo y >>src/a.py
  echo z >new_untracked.py
  run bash "$V"
  assert_eq 1 "$RC" "rc"
  assert_contains "$ERR" "Out-of-plan file: new_untracked.py" "untracked file"
  assert_not_contains "$ERR" "Out-of-plan file: src/a.py" "modified in-plan file"
}

t_plan_directory_and_glob_entries() {
  setup; make_plan
  sed -i 's#^- Files: src/a.py, tests/test_a.py (new)#- Files: src/pkg/, docs/*.md#' notes/tasks/feat--x.plan.md
  change src/pkg/deep/mod.py
  change docs/guide.md
  run bash "$V"
  assert_eq 0 "$RC" "dir prefix and glob entries allow files: $ERR"
}

t_no_plan_skips_scope() {
  setup
  git switch -q -c feat/y
  change anything.py
  run bash "$V"
  assert_eq 0 "$RC" "rc"
  assert_contains "$ERR" "no plan for this branch" "says why scope was skipped"
}

t_missing_tool_fails_loudly() {
  setup; make_plan
  change src/a.py
  write_cfg '{"guards":{"ruff_cmd":"definitely-not-installed-ruff"}}'
  run bash "$V"
  assert_eq 1 "$RC" "a missing tool is a failure, never a silent pass"
  assert_contains "$ERR" "definitely-not-installed-ruff not found: install it, or set guards.ruff to false" "message"
  assert_contains "$ERR" "lint        FAIL" "summary"
}

t_missing_diff_cover_fails_loudly() {
  setup; make_plan
  change src/a.py
  write_cfg '{"guards":{"diff_cover_cmd":"no-such-diff-cover"}}'
  run bash "$V"
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "no-such-diff-cover not found" "message"
}

t_stages_can_be_switched_off() {
  setup; make_plan
  change src/a.py
  write_cfg '{"guards":{"ruff":false,"pytest":false,"diff_cover":false,"scope":false}}'
  run bash "$V"
  assert_eq 0 "$RC" "rc"
  assert_eq "" "$(cat "$STUB_LOG")" "no tool was called"
  assert_contains "$ERR" "lint        SKIP  disabled: guards.ruff=false" "summary"
}

t_failing_tests_skip_diff_cover() {
  setup; make_plan
  change src/a.py
  STUB_PYTEST_RC=1 run bash "$V"
  assert_eq 1 "$RC" "rc"
  assert_contains "$ERR" "tests       FAIL" "tests fail"
  assert_contains "$ERR" "diff-cover  SKIP  needs passing tests" "diff-cover not judged on a failed run"
  assert_not_contains "$(cat "$STUB_LOG")" "diff-cover" "not called"
}

t_diff_cover_failure_and_threshold_config() {
  setup; make_plan
  change src/a.py
  write_cfg '{"guards":{"fail_under":75,"coverage_xml":"out/cov.xml"}}'
  STUB_DC_RC=1 run bash "$V"
  assert_eq 1 "$RC" "rc"
  assert_contains "$ERR" "diff-cover  FAIL" "summary"
  assert_contains "$(cat "$STUB_LOG")" "diff-cover out/cov.xml --compare-branch=main --fail-under=75" "configured threshold and xml path"
}

t_lint_failure() {
  setup; make_plan
  change src/a.py
  STUB_RUFF_RC=1 run bash "$V"
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "E501" "ruff output is shown"
}

t_not_a_repo_hook_is_quiet_cli_fails() {
  d=$(mktemp -d "$TMP_ROOT/plain.XXXXXX"); cd "$d"
  run bash "$V" --hook </dev/null
  assert_eq 0 "$RC" "hook outside a repo does nothing"
  run bash "$V"
  assert_eq 1 "$RC" "cli says so"; assert_contains "$ERR" "not inside a git repository" "message"
}

# ---- ruff-fix.sh
rf() { # <file_path>
  jq -nc --arg p "$1" '{tool_name: "Edit", tool_input: {file_path: $p}}' >"$TMP_ROOT/in.json"
  run bash "$RF" <"$TMP_ROOT/in.json"
}

t_ruff_fix_runs_on_the_edited_py_file_only() {
  setup
  echo "x=1" >m.py
  rf "$PWD/m.py"
  assert_eq 0 "$RC" "rc"; assert_clean_stdout
  log=$(cat "$STUB_LOG")
  # the path may be /tmp/x or C:/.../x depending on how MSYS converted it on the way through jq
  case $log in *"ruff check --fix --quiet "*"m.py"*) ;; *) echo "no check --fix on m.py: $log" >&2; return 1 ;; esac
  case $log in *"ruff format --quiet "*"m.py"*) ;; *) echo "no format on m.py: $log" >&2; return 1 ;; esac
  assert_eq 2 "$(grep -c . "$STUB_LOG")" "exactly two ruff calls, both for m.py"
}

t_ruff_fix_ignores_other_files() {
  setup
  echo "# x" >README.md
  rf "$PWD/README.md"
  assert_eq 0 "$RC" "rc"; assert_eq "" "$(cat "$STUB_LOG")" "ruff not called for .md"
  rf "$PWD/missing.py"
  assert_eq 0 "$RC" "nonexistent file is a no-op"
  echo '{"tool_input":{}}' >"$TMP_ROOT/in.json"
  run bash "$RF" <"$TMP_ROOT/in.json"
  assert_eq 0 "$RC" "no file_path is a no-op"
}

t_ruff_fix_reports_leftovers_and_missing_tool() {
  setup
  echo "x=1" >m.py
  STUB_RUFF_RC=1 rf "$PWD/m.py"
  assert_eq 2 "$RC" "exit 2 shows stderr to Claude"; assert_contains "$ERR" "ruff left problems in" "message"
  write_cfg '{"guards":{"ruff_cmd":"no-such-ruff"}}'
  rf "$PWD/m.py"
  assert_eq 2 "$RC" "missing ruff is loud"; assert_contains "$ERR" "no-such-ruff not found" "message"
  write_cfg '{"guards":{"ruff":false}}'
  rf "$PWD/m.py"
  assert_eq 0 "$RC" "switched off"
}

case_run "all stages pass; tools get the documented arguments" t_all_pass_and_tool_arguments
case_run "out-of-plan file fails with the documented message" t_out_of_plan_file_fails
case_run "--hook exits 2 on failure (default exits 1)" t_hook_mode_exits_2
case_run "--hook exits 0 on success" t_hook_mode_passes_with_0
case_run "--hook with nothing changed is instant" t_hook_mode_nothing_changed_is_instant
case_run "file logged in this branch's decision passes" t_logged_in_decision_log_passes
case_run "decision of another branch does not count" t_decision_for_another_branch_does_not_count
case_run "decision must name the whole path" t_decision_must_name_the_whole_path
case_run "active step widens scope cumulatively" t_active_step_widens_scope_cumulatively
case_run "files of later steps fail early" t_later_step_files_fail_early
case_run "uncommitted and untracked files are checked" t_uncommitted_and_untracked_count
case_run "directory and glob entries in Files" t_plan_directory_and_glob_entries
case_run "no plan: scope skipped, rest runs" t_no_plan_skips_scope
case_run "missing ruff fails loudly" t_missing_tool_fails_loudly
case_run "missing diff-cover fails loudly" t_missing_diff_cover_fails_loudly
case_run "stages switch off via config" t_stages_can_be_switched_off
case_run "failing tests: diff-cover skipped" t_failing_tests_skip_diff_cover
case_run "diff-cover failure; fail_under and xml path from config" t_diff_cover_failure_and_threshold_config
case_run "lint failure shows ruff output" t_lint_failure
case_run "outside a repo: hook quiet, cli fails" t_not_a_repo_hook_is_quiet_cli_fails
case_run "ruff-fix touches only the edited .py file" t_ruff_fix_runs_on_the_edited_py_file_only
case_run "ruff-fix ignores other files" t_ruff_fix_ignores_other_files
case_run "ruff-fix reports leftovers and a missing tool" t_ruff_fix_reports_leftovers_and_missing_tool
finish
