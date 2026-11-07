#!/usr/bin/env bash
# guard-git.sh - validate a git command against the protected-branch merge convention.
#
# Exit-code convention (Claude Code PreToolUse hook when the command comes from stdin JSON):
#   0 = allow, or ask: JSON on stdout makes Claude Code show its permission prompt
#   2 = BLOCK; stderr is fed back to the model
#   (exit 1 would NOT block a Claude Code hook, so it is never used in hook mode)
# Argument mode (`guard-git.sh git merge --ff-only x`, for other agents and manual use):
#   0 = allow or ask (same JSON on stdout), 1 = BLOCK (git-hook / kit convention), stderr = message.
# stdout carries nothing but that JSON; every other message goes to stderr.
#
# Input: Claude Code PreToolUse JSON on stdin (command in .tool_input.command), or the command as arguments.
# "ask" output follows https://code.claude.com/docs/en/hooks.md#json-output :
#   {"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"..."}}
#
# Rules (branch names come from git.protected_branches in .agentkit.json):
#   on a protected branch: `git merge` needs --no-ff; --ff-only and --squash are always blocked;
#     --abort/--continue/--quit pass.
#   commit, or push that targets a protected branch: ask (git.protected_commit: ask | allow | block).
# The command is tokenized with quote awareness; `git switch/checkout` earlier in the same command line
# moves the branch the later segments are judged on. `bash -c "..."` and `eval` are looked into.
# This catches mistakes, not a determined bypass (no shell expansion, aliases or scripts are followed).
set -u
if [ $# -gt 0 ]; then MODE=arg; AK_FAIL=1; BLOCK_RC=1; else MODE=hook; AK_FAIL=2; BLOCK_RC=2; fi
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

if [ "$MODE" = arg ]; then
  CMD="$*"
else
  input=$(cat)
  printf '%s' "$input" | jq -e . >/dev/null 2>&1 \
    || ak_fail "guard-git: stdin is not Claude Code hook JSON. Pass the command as arguments instead."
  CMD=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' | tr -d '\015')
fi
case $CMD in *git*) ;; *) exit 0 ;; esac  # nothing to judge: skip config reads

SEP=$'\037'
BR=$(current_branch || true)
BLOCK="" ASK=""
block() { [ -n "$BLOCK" ] || BLOCK=$1; }
ask() { [ -n "$ASK" ] || ASK=$1; }

# tokenize <string>: words in TOKS, with SEP between commands (&& || ; | & newline ( ) `)
tokenize() {
  local s=$1 i c n=${#1} cur="" q="" have=0
  TOKS=()
  for ((i = 0; i < n; i++)); do
    c=${s:i:1}
    if [ -n "$q" ]; then
      if [ "$c" = "$q" ]; then q=""
      elif [ "$q" = '"' ] && [ "$c" = '\' ] && [ $((i + 1)) -lt "$n" ]; then i=$((i + 1)); cur+=${s:i:1}
      else cur+=$c; fi
      continue
    fi
    case $c in
      "'" | '"') q=$c; have=1 ;;
      '\') if [ $((i + 1)) -lt "$n" ]; then i=$((i + 1)); cur+=${s:i:1}; have=1; fi ;;
      ' ' | $'\t') [ "$have" = 1 ] && TOKS+=("$cur"); cur=""; have=0 ;;
      $'\n' | ';' | '(' | ')' | '`')
        [ "$have" = 1 ] && TOKS+=("$cur"); cur=""; have=0; TOKS+=("$SEP") ;;
      '&' | '|')
        [ "$have" = 1 ] && TOKS+=("$cur"); cur=""; have=0; TOKS+=("$SEP")
        [ "${s:i+1:1}" = "$c" ] && i=$((i + 1)) ;;
      *) cur+=$c; have=1 ;;
    esac
  done
  [ "$have" = 1 ] && TOKS+=("$cur")
  return 0
}

# scan <command string> <depth>: judge every git command in it, in order
scan() {
  local toks seg=() t
  tokenize "$1"
  toks=("${TOKS[@]}")
  for t in "${toks[@]}" "$SEP"; do
    if [ "$t" = "$SEP" ]; then
      [ ${#seg[@]} -gt 0 ] && check_segment "$2" "${seg[@]}"
      seg=()
    else
      seg+=("$t")
    fi
  done
  return 0
}

merge_hint() { # <branch>
  local conv
  conv=$(cfg guards.merge_convention "")
  printf '%s\nInspect the convention first: git log --first-parent --merges -5 %s --oneline' \
    "${conv:+Convention: $conv}" "$1" | sed '/^$/d'
}

protected_policy() { # ask | allow | block, from git.protected_commit
  case $(cfg git.protected_commit ask) in allow) echo allow ;; block) echo block ;; *) echo ask ;; esac
}

guard_merge() { # <branch> args...
  local b=$1 a why=""; shift
  for a in "$@"; do
    case $a in --abort | --continue | --quit) return 0 ;; esac
  done
  for a in "$@"; do
    case $a in
      --squash) why="--squash is not allowed" ;;
      --ff-only) [ -n "$why" ] || why="--ff-only is not allowed" ;;
    esac
  done
  if [ -z "$why" ]; then
    for a in "$@"; do [ "$a" = "--no-ff" ] && return 0; done
    why="a plain 'git merge' can fast-forward"
  fi
  block "BLOCK: $why on protected branch '$b'. Use: git merge --no-ff <branch>
$(merge_hint "$b")"
}

guard_commit() { # <branch>
  case $(protected_policy) in
    allow) ;;
    block) block "BLOCK: commit on protected branch '$1' (git.protected_commit=block). Create a task branch: git switch -c <type>/<name>" ;;
    *) ask "Commit on protected branch '$1'. Approve only if the user asked for it; otherwise create a task branch first." ;;
  esac
}

guard_push() { # <branch> args...
  local b=$1 a s dst remote="" all=0 skip=0 hit="" specs=(); shift
  for a in "$@"; do
    if [ "$skip" = 1 ]; then skip=0; continue; fi
    case $a in
      -o | --push-option | --repo | --receive-pack | --exec) skip=1 ;;
      --all | --mirror) all=1 ;;
      -*) ;;
      *) if [ -z "$remote" ]; then remote=$a; else specs+=("$a"); fi ;;
    esac
  done
  [ "$all" = 1 ] && hit="every branch (--all/--mirror)"
  if [ ${#specs[@]} -eq 0 ]; then
    [ -n "$b" ] && is_protected "$b" && hit=$b
  else
    for s in "${specs[@]}"; do
      s=${s#+}; dst=${s##*:}
      [ "$dst" = HEAD ] && dst=$b
      dst=${dst#refs/heads/}
      [ -n "$dst" ] && is_protected "$dst" && hit=$dst
    done
  fi
  [ -n "$hit" ] || return 0
  case $(protected_policy) in
    allow) ;;
    block) block "BLOCK: push to protected branch '$hit' (git.protected_commit=block). Push a task branch and merge with --no-ff." ;;
    *) ask "Push to protected branch '$hit'. Approve only if the user asked for it; prefer pushing a task branch." ;;
  esac
}

# track_switch <sub> args...: move BR the way `git switch/checkout` would
track_switch() {
  local sub=$1 a new="" target="" detach=0; shift
  while [ $# -gt 0 ]; do
    a=$1; shift
    case $a in
      --) break ;;
      -b | -B | -c | -C | --create | --force-create | --orphan) new=${1:-}; shift ;;
      -d | --detach) detach=1 ;;
      -*) ;;
      *) [ -n "$target" ] || target=$a ;;
    esac
  done
  if [ -n "$new" ]; then BR=$new
  elif [ "$detach" = 1 ]; then BR=""
  elif [ -n "$target" ]; then
    if [ "$sub" = switch ] || git rev-parse --verify -q "refs/heads/$target" >/dev/null 2>&1; then BR=$target; fi
  fi
}

check_segment() { # <depth> words...
  local depth=$1 i=0 j dir="" sub b; shift
  local w=("$@")
  while [ $i -lt ${#w[@]} ]; do
    case ${w[i]} in
      [A-Za-z_]*=* | env | command | exec | nohup | time | sudo) i=$((i + 1)) ;;
      *) break ;;
    esac
  done
  [ $i -lt ${#w[@]} ] || return 0
  case ${w[i]##*/} in
    bash | sh | zsh | dash)
      for ((j = i + 1; j < ${#w[@]}; j++)); do
        case ${w[j]} in
          -c | -[a-zA-Z]*c) [ $((j + 1)) -lt ${#w[@]} ] && [ "$depth" -lt 3 ] && scan "${w[j+1]}" $((depth + 1)) ;;
        esac
      done
      return 0 ;;
    eval) [ "$depth" -lt 3 ] && scan "${w[*]:i+1}" $((depth + 1)); return 0 ;;
    git | git.exe) ;;
    *) return 0 ;;
  esac
  i=$((i + 1))
  while [ $i -lt ${#w[@]} ]; do
    case ${w[i]} in
      -C) dir=${w[i+1]:-}; i=$((i + 2)) ;;
      -c | --git-dir | --work-tree | --namespace | --super-prefix) i=$((i + 2)) ;;
      -*) i=$((i + 1)) ;;
      *) break ;;
    esac
  done
  [ $i -lt ${#w[@]} ] || return 0
  sub=${w[i]}
  w=("${w[@]:i+1}")
  b=$BR
  [ -z "$dir" ] || b=$(git -C "$dir" symbolic-ref --quiet --short HEAD 2>/dev/null || true)
  case $sub in
    switch | checkout) [ -n "$dir" ] || track_switch "$sub" ${w[@]+"${w[@]}"} ;;
    merge) [ -n "$b" ] && is_protected "$b" && guard_merge "$b" ${w[@]+"${w[@]}"} ;;
    commit) [ -n "$b" ] && is_protected "$b" && guard_commit "$b" ;;
    push) guard_push "$b" ${w[@]+"${w[@]}"} ;;
  esac
  return 0
}

scan "$CMD" 0

if [ -n "$BLOCK" ]; then
  echo "$BLOCK" >&2
  exit "$BLOCK_RC"
fi
if [ -n "$ASK" ]; then
  jq -nc --arg r "$ASK" '{hookSpecificOutput: {hookEventName: "PreToolUse", permissionDecision: "ask", permissionDecisionReason: $r}}' \
    | tr -d '\015'
fi
exit 0
