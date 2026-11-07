#!/usr/bin/env bash
# Tests for scripts/pre-push, driven by real `git push` into a bare remote. Exit 1 when any case fails.
. "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"
install_stubs

# remote: bare repo "origin" + the hook, which forwards to the kit's pre-push (stdin included)
remote() {
  local r
  r=$(mktemp -d "$TMP_ROOT/remote.XXXXXX")
  git init -q --bare -b main "$r"
  git remote add origin "$r"
  cat >.git/hooks/pre-push <<EOF
#!/bin/sh
exec bash "$KIT_SCRIPTS/pre-push" "\$@"
EOF
  chmod +x .git/hooks/pre-push
  write_cfg '{"guards":{"verify_on_push":false}}'
  reset_stubs
}

# published: remote with main already pushed once (first push of an empty remote is exempt)
published() {
  remote
  git push -q origin main 2>/dev/null
}

# feature: branch feat/x with one commit; ends on main
feature() {
  git switch -q -c feat/x
  commit_file f.txt f "feat: f"
  git switch -q main
}

t_ff_merged_main_is_blocked() {
  published; feature
  git merge -q --ff-only feat/x
  run git push origin main
  [ "$RC" -ne 0 ] || { echo "push should have been rejected" >&2; return 1; }
  assert_contains "$ERR" "BLOCK: push to protected branch 'main'" "reason"
  assert_contains "$ERR" "1 non-merge commit(s)" "count"
  assert_contains "$ERR" "feat: f" "names the offending commit"
  assert_contains "$ERR" "git merge --no-ff <branch>" "what to do"
  assert_contains "$ERR" "git log --first-parent --merges -5 main --oneline" "how to inspect the convention"
  assert_eq "$(git rev-parse origin/main)" "$(git ls-remote origin refs/heads/main | cut -f1)" "remote main not advanced"
}

t_merge_commit_is_allowed() {
  published; feature
  git merge -q --no-ff feat/x -m "feat(x): merge feature"
  run git push origin main
  assert_eq 0 "$RC" "push of a --no-ff merge: $ERR"
  assert_eq "$(git rev-parse HEAD)" "$(git ls-remote origin refs/heads/main | cut -f1)" "remote main advanced"
}

t_several_merges_in_one_push() {
  published; feature
  git merge -q --no-ff feat/x -m "feat: merge one"
  git switch -q -c feat/y
  commit_file g.txt g "feat: g"
  git switch -q main
  git merge -q --no-ff feat/y -m "feat: merge two"
  run git push origin main
  assert_eq 0 "$RC" "two merge commits: $ERR"
}

t_merge_then_direct_commit_is_blocked() {
  published; feature
  git merge -q --no-ff feat/x -m "feat: merge"
  commit_file direct.txt d "fix: sneaked straight onto main"
  run git push origin main
  [ "$RC" -ne 0 ] || { echo "should be rejected" >&2; return 1; }
  assert_contains "$ERR" "sneaked straight onto main" "names it"
}

t_first_push_to_empty_remote() {
  remote
  run git push origin main
  assert_eq 0 "$RC" "rc: $ERR"
  assert_contains "$ERR" "has no branches yet" "says why it was not checked"
}

t_feature_branch_push_is_allowed() {
  published; feature
  run git push origin feat/x
  assert_eq 0 "$RC" "rc: $ERR"
}

t_new_protected_branch_from_plain_commits_is_blocked() {
  published; feature
  run git push origin feat/x:refs/heads/develop
  [ "$RC" -ne 0 ] || { echo "new protected branch with non-merge commits must be rejected" >&2; return 1; }
  assert_contains "$ERR" "protected branch 'develop'" "uses the remote ref's name"
}

t_new_protected_branch_at_a_known_commit_is_allowed() {
  published
  run git push origin main:refs/heads/develop
  assert_eq 0 "$RC" "nothing new to merge: $ERR"
}

t_delete_and_tag_are_allowed() {
  published; feature
  git push -q origin feat/x 2>/dev/null
  run git push origin :feat/x
  assert_eq 0 "$RC" "delete: $ERR"
  git tag v1
  run git push origin v1
  assert_eq 0 "$RC" "tag: $ERR"
}

t_switch_off_by_config() {
  published; feature
  git merge -q --ff-only feat/x
  write_cfg '{"guards":{"require_merge_commits":false,"verify_on_push":false}}'
  run git push origin main
  assert_eq 0 "$RC" "guard off: $ERR"
}

t_configured_branch_and_convention_text() {
  published; feature
  git merge -q --ff-only feat/x
  write_cfg '{"git":{"protected_branches":["main"]},"guards":{"verify_on_push":false,"merge_convention":"feat(<scope>): merge X"}}'
  run git push origin main
  [ "$RC" -ne 0 ] || return 1
  assert_contains "$ERR" "Convention: feat(<scope>): merge X" "convention text"
}

t_verify_runs_on_push_and_can_block() {
  published; feature
  write_cfg '{}'
  run git push origin feat/x
  assert_eq 0 "$RC" "verify passes -> push ok: $ERR"
  assert_contains "$(cat "$STUB_LOG")" "pytest --cov" "verify.sh ran the tests"
  git switch -q feat/x
  commit_file h.txt h "feat: h"
  STUB_PYTEST_RC=1 run git push origin feat/x
  [ "$RC" -ne 0 ] || { echo "failing verify must reject the push" >&2; return 1; }
  assert_contains "$ERR" "verify.sh failed" "message"
}

t_verify_not_run_for_delete_only() {
  published; feature
  git push -q origin feat/x 2>/dev/null
  write_cfg '{}'
  reset_stubs
  STUB_PYTEST_RC=1 run git push origin :feat/x
  assert_eq 0 "$RC" "deleting a branch does not need a green build: $ERR"
}

case_run "fast-forwarded main is blocked" t_ff_merged_main_is_blocked
case_run "--no-ff merge commit is allowed" t_merge_commit_is_allowed
case_run "several merge commits in one push" t_several_merges_in_one_push
case_run "a direct commit after a merge is blocked" t_merge_then_direct_commit_is_blocked
case_run "first push to an empty remote" t_first_push_to_empty_remote
case_run "feature branch push is allowed" t_feature_branch_push_is_allowed
case_run "new protected branch from plain commits is blocked" t_new_protected_branch_from_plain_commits_is_blocked
case_run "new protected branch at a known commit is allowed" t_new_protected_branch_at_a_known_commit_is_allowed
case_run "branch delete and tag push are allowed" t_delete_and_tag_are_allowed
case_run "guards.require_merge_commits=false" t_switch_off_by_config
case_run "configured branches and convention text" t_configured_branch_and_convention_text
case_run "verify.sh runs on push and can block it" t_verify_runs_on_push_and_can_block
case_run "delete-only push skips verify" t_verify_not_run_for_delete_only
finish
