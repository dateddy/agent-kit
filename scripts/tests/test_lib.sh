#!/usr/bin/env bash
# Tests for scripts/lib.sh. Exit 1 when any case fails.
. "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

load_lib() { . "$KIT_SCRIPTS/lib.sh"; }

t_false_is_a_value() {
  write_cfg '{"guards":{"scope":false,"fail_under":0}}'
  load_lib
  assert_eq false "$(cfg guards.scope true)" "false is kept, not replaced by the default"
  assert_eq 0 "$(cfg guards.fail_under 90)" "0 is kept"
  assert_eq 90 "$(cfg guards.missing 90)" "absent key gives the default"
  ! cfg_on guards.scope true || return 1
  cfg_on guards.absent true
}

t_defaults_without_config() {
  load_lib
  is_protected main
  is_protected release/1.2
  ! is_protected feat/x || return 1
}

t_protected_from_config() {
  write_cfg '{"git":{"protected_branches":["trunk","rel/*"]}}'
  load_lib
  is_protected trunk
  is_protected rel/9
  ! is_protected main || return 1
}

t_invalid_json_warns_and_uses_defaults() {
  printf '{ nope' >.agentkit.json
  run bash -c ". '$KIT_SCRIPTS/lib.sh'; cfg guards.x fallback"
  assert_eq fallback "$OUT" "default used"
  assert_contains "$ERR" "not valid JSON" "warns on stderr"
}

t_slug_matches_notes_py() {
  load_lib
  assert_eq "feat--x-y" "$(slug 'feat/x y')" "slash and space"
  assert_eq "a--b--c" "$(slug 'a/b/c')" "nested"
}

t_base_ref_prefers_notes_config() {
  git branch develop
  write_cfg '{"notes":{"base_branch":"develop"}}'
  load_lib
  assert_eq develop "$(base_ref)" "notes.base_branch wins"
}

t_base_ref_falls_back_to_protected() {
  load_lib
  assert_eq main "$(base_ref)" "first existing protected branch"
}

case_run "cfg keeps false and 0" t_false_is_a_value
case_run "protected defaults without config" t_defaults_without_config
case_run "protected branches from config" t_protected_from_config
case_run "invalid config warns on stderr" t_invalid_json_warns_and_uses_defaults
case_run "slug matches notes.py" t_slug_matches_notes_py
case_run "base_ref uses notes.base_branch" t_base_ref_prefers_notes_config
case_run "base_ref falls back to a protected branch" t_base_ref_falls_back_to_protected
finish
