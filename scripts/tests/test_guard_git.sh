#!/usr/bin/env bash
# Tests for scripts/guard-git.sh. Exit 1 when any case fails.
. "$(dirname "${BASH_SOURCE[0]}")/helpers.sh"

GG="$KIT_SCRIPTS/guard-git.sh"

# arg <command...>: argument mode      hook <command>: Claude Code PreToolUse JSON on stdin
arg() { run bash "$GG" "$@"; }
hook() {
  jq -nc --arg c "$1" '{hook_event_name: "PreToolUse", tool_name: "Bash", tool_input: {command: $c}}' >"$TMP_ROOT/in.json"
  run bash "$GG" <"$TMP_ROOT/in.json"
}
feature() { git switch -q -c feat/x; commit_file f.txt f "feat: f"; git switch -q main; }

assert_blocked_arg() { assert_eq 1 "$RC" "arg-mode rc"; assert_eq "" "$OUT" "stdout empty"; }
assert_blocked_hook() { assert_eq 2 "$RC" "hook-mode rc (only 2 blocks a Claude Code hook)"; assert_eq "" "$OUT" "stdout empty"; }
assert_silent() { assert_eq 0 "$RC" "rc"; assert_eq "" "$OUT" "stdout empty"; }

t_ff_only_into_main_blocked() {
  feature
  arg git merge --ff-only feat/x
  assert_blocked_arg
  assert_contains "$ERR" "--ff-only is not allowed" "reason"
  assert_contains "$ERR" "git merge --no-ff" "what to do"
  assert_contains "$ERR" "git log --first-parent --merges -5 main --oneline" "how to inspect the convention"
  hook "git merge --ff-only feat/x"
  assert_blocked_hook
  assert_contains "$ERR" "git log --first-parent --merges -5 main --oneline" "same message from stdin JSON"
}

t_no_ff_allowed() {
  feature
  arg git merge --no-ff feat/x -m "feat: merge"
  assert_silent
  hook "git merge --no-ff feat/x"
  assert_silent
}

t_plain_merge_into_main_blocked() {
  feature
  arg git merge feat/x
  assert_blocked_arg
  assert_contains "$ERR" "plain 'git merge' can fast-forward" "reason"
}

t_squash_always_blocked() {
  feature
  arg git merge --squash feat/x
  assert_blocked_arg
  arg git merge --no-ff --squash feat/x
  assert_blocked_arg
  assert_contains "$ERR" "--squash is not allowed" "even together with --no-ff"
}

t_merge_on_feature_branch_allowed() {
  feature
  git switch -q feat/x
  arg git merge --ff-only main
  assert_silent
  hook "git merge main"
  assert_silent
}

t_merge_control_flags_allowed() {
  feature
  arg git merge --abort
  assert_silent
  arg git merge --continue
  assert_silent
}

t_switch_earlier_in_same_command() {
  feature
  git switch -q feat/x
  hook "git switch main && git merge feat/x"
  assert_blocked_hook
  hook "git checkout main; git merge --ff-only feat/x"
  assert_blocked_hook
  git switch -q main
  hook "git switch feat/x && git merge main"
  assert_silent
  hook "git switch -c topic && git merge main"
  assert_silent
}

t_commit_on_main_asks() {
  arg git commit -m "feat: x"
  assert_eq 0 "$RC" "rc"
  assert_eq ask "$(printf '%s' "$OUT" | jq -r .hookSpecificOutput.permissionDecision)" "decision"
  assert_eq PreToolUse "$(printf '%s' "$OUT" | jq -r .hookSpecificOutput.hookEventName)" "event name"
  assert_contains "$(printf '%s' "$OUT" | jq -r .hookSpecificOutput.permissionDecisionReason)" "protected branch 'main'" "reason"
  hook 'git commit -m "feat: x"'
  assert_eq 0 "$RC" "hook rc"
  assert_eq ask "$(printf '%s' "$OUT" | jq -r .hookSpecificOutput.permissionDecision)" "hook decision"
  assert_eq "" "$ERR" "nothing on stderr"
}

t_commit_on_feature_silent() {
  git switch -q -c feat/x
  arg git commit -m "feat: x"
  assert_silent
}

t_protected_commit_policy() {
  write_cfg '{"git":{"protected_commit":"allow"}}'
  arg git commit -m x
  assert_silent
  write_cfg '{"git":{"protected_commit":"block"}}'
  arg git commit -m x
  assert_blocked_arg
  hook "git commit -m x"
  assert_blocked_hook
}

t_push_to_protected_asks() {
  git switch -q -c feat/x
  for c in "git push origin HEAD:main" "git push origin feat/x:main" "git push origin +main" "git push --force origin main" \
           "git push --all origin" "git push origin --delete main"; do
    arg $c
    assert_eq ask "$(printf '%s' "$OUT" | jq -r '.hookSpecificOutput.permissionDecision // empty')" "ask for: $c"
  done
  git switch -q main
  arg git push
  assert_eq ask "$(printf '%s' "$OUT" | jq -r '.hookSpecificOutput.permissionDecision // empty')" "bare push from main"
}

t_push_of_feature_silent() {
  git switch -q -c feat/x
  arg git push -u origin feat/x
  assert_silent
  arg git push origin HEAD
  assert_silent
  arg git push origin v1.0.0
  assert_silent
}

t_quotes_do_not_confuse_it() {
  arg echo "git merge --ff-only x"
  assert_silent
  git switch -q -c feat/x
  arg git commit -m "do not git merge --ff-only; git switch main"
  assert_silent
  git switch -q main
  hook 'echo "a && git merge --ff-only x"'
  assert_silent
}

t_compound_and_wrappers() {
  feature
  hook "cd . && git merge --ff-only feat/x"
  assert_blocked_hook
  hook "git -C . merge --ff-only feat/x"
  assert_blocked_hook
  hook "GIT_EDITOR=true git merge feat/x"
  assert_blocked_hook
  hook 'bash -c "git merge --ff-only feat/x"'
  assert_blocked_hook
  hook "git fetch && git merge --ff-only origin/main"
  assert_blocked_hook
  hook "git merge-base main feat/x"
  assert_silent
  hook "git log --first-parent --merges -5 main --oneline"
  assert_silent
}

t_dash_c_uses_that_repo_branch() {
  feature
  git switch -q feat/x
  hook "git -C . merge main"
  assert_silent
}

t_configured_branches_and_convention() {
  write_cfg '{"git":{"protected_branches":["trunk"]},"guards":{"merge_convention":"merge commit named feat(scope): merge X"}}'
  feature
  git branch -m main trunk
  arg git merge --ff-only feat/x
  assert_blocked_arg
  assert_contains "$ERR" "protected branch 'trunk'" "uses the configured branch"
  assert_contains "$ERR" "Convention: merge commit named feat(scope): merge X" "shows the configured convention"
}

t_non_git_and_odd_input() {
  hook "ls -la"
  assert_silent
  echo '{"tool_input":{}}' >"$TMP_ROOT/in.json"
  run bash "$GG" <"$TMP_ROOT/in.json"
  assert_silent
  echo 'not json' >"$TMP_ROOT/in.json"
  run bash "$GG" <"$TMP_ROOT/in.json"
  assert_eq 2 "$RC" "non-JSON stdin fails loudly"
  assert_contains "$ERR" "not Claude Code hook JSON" "message"
}

t_detached_head_not_protected() {
  git switch -q --detach
  arg git merge --ff-only main
  assert_silent
}

case_run "--ff-only into main is blocked (arg + stdin JSON)" t_ff_only_into_main_blocked
case_run "--no-ff is allowed" t_no_ff_allowed
case_run "plain merge into main is blocked" t_plain_merge_into_main_blocked
case_run "--squash is always blocked" t_squash_always_blocked
case_run "merge on a feature branch is allowed" t_merge_on_feature_branch_allowed
case_run "--abort / --continue pass" t_merge_control_flags_allowed
case_run "switch earlier in the same command line" t_switch_earlier_in_same_command
case_run "commit on main returns ask JSON" t_commit_on_main_asks
case_run "commit on a feature branch is silent" t_commit_on_feature_silent
case_run "git.protected_commit allow / block" t_protected_commit_policy
case_run "push to a protected branch asks" t_push_to_protected_asks
case_run "push of a feature branch is silent" t_push_of_feature_silent
case_run "quoted text is not a command" t_quotes_do_not_confuse_it
case_run "compound commands, -C, env, bash -c" t_compound_and_wrappers
case_run "git -C judges that repo's branch" t_dash_c_uses_that_repo_branch
case_run "configured protected branch and convention text" t_configured_branches_and_convention
case_run "non-git command, odd and invalid stdin" t_non_git_and_odd_input
case_run "detached HEAD is not protected" t_detached_head_not_protected
finish
