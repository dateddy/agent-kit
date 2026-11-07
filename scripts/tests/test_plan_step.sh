#!/usr/bin/env bash
# Tests for scripts/plan-step.sh. Exit 1 when any case fails.
. "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

PS="$KIT_SCRIPTS/plan-step.sh"


t_active_is_first_unchecked() {
  make_plan
  run bash "$PS" mode
  assert_eq 0 "$RC" "rc"; assert_eq solo "$OUT" "step 1 is active"
  run bash "$PS" title
  assert_eq "Parse input" "$OUT" "title"
}

t_active_moves_with_checklist() {
  make_plan 1
  run bash "$PS" mode
  assert_eq pipeline "$OUT" "step 2 once step 1 is checked"
}

t_all_checked_means_last_step() {
  make_plan 1 2
  run bash "$PS" title
  assert_eq "Write output" "$OUT" "last step"
}

t_files_strip_new_and_split() {
  make_plan
  run bash "$PS" files
  assert_eq "src/a.py
tests/test_a.py" "$OUT" "one path per line, (new) removed"
}

t_cumulative_files_union() {
  make_plan 1
  run bash "$PS" files --cumulative
  assert_eq "src/a.py
tests/test_a.py
src/b.py" "$OUT" "steps 1..2 without duplicates"
}

t_step_flag_overrides_note() {
  make_plan
  run bash "$PS" tests --step 2
  assert_eq test_write "$OUT" "tests of step 2"
}

t_multiline_contract() {
  make_plan
  run bash "$PS" contract
  assert_eq "parse(s) -> int
invariant: result is positive" "$OUT" "continuation line kept"
}

t_other_fields() {
  make_plan
  run bash "$PS" out-of-scope; assert_eq "src/b.py" "$OUT" "out-of-scope"
  run bash "$PS" verify; assert_eq "pytest tests/test_a.py" "$OUT" "verify"
  run bash "$PS" tests; assert_eq "test_parse_ok, test_parse_zero" "$OUT" "tests"
}

t_missing_field_is_an_error() {
  make_plan 1
  run bash "$PS" verify
  assert_eq 1 "$RC" "rc"
  assert_contains "$ERR" 'no "verify" field' "names the field"
  assert_eq "" "$OUT" "nothing on stdout"
}

t_missing_step_is_an_error() {
  make_plan
  run bash "$PS" mode --step 9
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "step 9 not found" "names the step"
}

t_missing_plan_is_an_error() {
  git switch -q -c feat/none
  run bash "$PS" files
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "no plan at" "says where"
}

t_missing_note_is_an_error() {
  make_plan
  rm notes/tasks/feat--x.md
  run bash "$PS" files
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "active step is unknown" "explains"
  run bash "$PS" files --step 1
  assert_eq 0 "$RC" "--step needs no note"
}

t_unknown_field_is_an_error() {
  make_plan
  run bash "$PS" colour
  assert_eq 1 "$RC" "rc"; assert_contains "$ERR" "unknown field" "message"
}

t_old_format_still_readable() {
  git switch -q -c feat/old
  mkdir -p notes/tasks
  printf '# Plan: old\n\n## 1. First\nFiles: x.py, y.py (new)\nDo: change\nVerify: t\nDone: ok\n' >notes/tasks/feat--old.plan.md
  run bash "$PS" files --step 1
  assert_eq "x.py
y.py" "$OUT" "old plan files"
}

t_crlf_plan() {
  make_plan
  sed -i 's/$/\r/' notes/tasks/feat--x.plan.md
  run bash "$PS" mode
  assert_eq solo "$OUT" "CR stripped"
}

case_run "active step is the first unchecked" t_active_is_first_unchecked
case_run "active step follows the checklist" t_active_moves_with_checklist
case_run "all steps checked: last step" t_all_checked_means_last_step
case_run "files: one per line, (new) stripped" t_files_strip_new_and_split
case_run "files --cumulative is the union" t_cumulative_files_union
case_run "--step overrides the note" t_step_flag_overrides_note
case_run "multi-line contract" t_multiline_contract
case_run "out-of-scope, verify, tests" t_other_fields
case_run "missing field -> error" t_missing_field_is_an_error
case_run "missing step -> error" t_missing_step_is_an_error
case_run "missing plan -> error" t_missing_plan_is_an_error
case_run "missing note -> error unless --step" t_missing_note_is_an_error
case_run "unknown field -> error" t_unknown_field_is_an_error
case_run "old-format plan still readable" t_old_format_still_readable
case_run "CRLF plan file" t_crlf_plan
finish
