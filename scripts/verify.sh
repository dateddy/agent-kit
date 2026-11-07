#!/usr/bin/env bash
# verify.sh - the single "is this done?" gate: lint, tests, coverage of changed lines, plan scope.
#
# Exit-code convention:
#   default   : plain CLI / git hook. 0 = every enabled stage passed, 1 = a stage failed.
#   --push    : called by pre-push. Same exit codes as the default; selects guards.push_stages.
#   --hook    : Claude Code Stop hook. 0 = pass, 2 = fail (exit 2 stops Claude from finishing and feeds
#               stderr back to it; exit 1 would not block). Stdin (the hook JSON) is drained and ignored:
#               Claude Code itself overrides a Stop block after 8 in a row, so no stop_hook_active logic here.
#               In hook mode a tree with nothing changed against the base branch exits 0 at once.
# All output goes to stderr (stdout may be parsed as JSON by Claude Code).
#
# Stages, in order (each skippable with guards.<key>=false in .agentkit.json):
#   lint        ruff check . ; ruff format --check . (never .agentkit or .claude)  guards.ruff  guards.ruff_cmd
#   tests       pytest --cov --cov-report=xml:<guards.coverage_xml>  guards.pytest     guards.pytest_cmd, guards.pytest_args
#   diff-cover  coverage of changed lines vs the base branch         guards.diff_cover guards.diff_cover_cmd, guards.fail_under
#   scope       changed files vs the Files: of plan steps 1..active  guards.scope      guards.scope_ignore
# Which stages run (names: lint, tests, diff-cover, scope; comma or newline separated):
#   --stages <list>  wins; else guards.hook_stages with --hook, guards.push_stages with --push;
#   else every stage. A stage not selected is reported as SKIP; an unknown name is an error.
#   guards.<key>=false still switches a stage off everywhere.
# A missing tool fails its stage with a message; it never passes silently. The scope stage is skipped
# when the branch has no plan (lite path). Commands are split on spaces (no quoting inside the config value).
set -u
HOOK=0 PUSH=0 STAGES_ARG="" STAGES_SET=0
usage() { sed -n '2,23p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 1; }
while [ $# -gt 0 ]; do
  case $1 in
    --hook) HOOK=1 ;;
    --push) PUSH=1 ;;
    --stages) [ $# -ge 2 ] || usage; STAGES_ARG=$2; STAGES_SET=1; shift ;;
    --stages=*) STAGES_ARG=${1#--stages=}; STAGES_SET=1 ;;
    *) usage ;;
  esac
  shift
done
if [ "$HOOK" = 1 ]; then AK_FAIL=2; FAIL_RC=2; else AK_FAIL=1; FAIL_RC=1; fi
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=lib.sh
. "$HERE/lib.sh"
[ "$HOOK" = 1 ] && [ ! -t 0 ] && cat >/dev/null
git rev-parse --git-dir >/dev/null 2>&1 || { [ "$HOOK" = 1 ] && exit 0; ak_fail "verify.sh: not inside a git repository"; }
cd "$AK_ROOT" || exit "$FAIL_RC"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
BASE=$(base_ref)
BRANCH=$(current_branch || true)
XML=$(cfg guards.coverage_xml .agentkit/coverage.xml)

# ---------------------------------------------------------------- stage selection
ALL_STAGES="lint tests diff-cover scope"
if [ "$STAGES_SET" = 1 ]; then SEL_RAW=$STAGES_ARG; SEL_SRC="--stages"
elif [ "$HOOK" = 1 ]; then SEL_RAW=$(cfg guards.hook_stages ""); SEL_SRC="guards.hook_stages"
elif [ "$PUSH" = 1 ]; then SEL_RAW=$(cfg guards.push_stages ""); SEL_SRC="guards.push_stages"
else SEL_RAW=""; SEL_SRC=""; fi
SELECTED=$(printf '%s\n' "$SEL_RAW" | tr ', \r' '\n\n\n' | awk 'NF')
[ "$STAGES_SET" = 1 ] && [ -z "$SELECTED" ] && ak_fail "verify.sh: --stages needs at least one of: $ALL_STAGES"
while IFS= read -r s; do
  [ -n "$s" ] || continue
  case " $ALL_STAGES " in *" $s "*) ;; *) ak_fail "verify.sh: unknown stage '$s' in $SEL_SRC (choose from: $ALL_STAGES)" ;; esac
done <<<"$SELECTED"
selected() { [ -z "$SELECTED" ] || printf '%s\n' "$SELECTED" | grep -qx -- "$1"; }

# ---------------------------------------------------------------- changed files
ignore_patterns() {
  local l
  l=$(cfg guards.scope_ignore "")
  if [ -n "$l" ]; then printf '%s\n' "$l"
  else printf '%s\n' ".agentkit/*" ".agentkit.json" "$(cfg notes.dir notes)/*" "FAILURES.md" ".coverage" ".coverage.*" "coverage.xml"; fi
  printf '%s\n' "$XML"
}
IGNORES=$(ignore_patterns)

ignored() {
  local pat
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    # shellcheck disable=SC2254
    case $1 in $pat) return 0 ;; esac
  done <<<"$IGNORES"
  return 1
}

# committed (base...HEAD), uncommitted and untracked paths, minus ignored ones
changed_files() {
  local f
  {
    [ -n "$BASE" ] && git -c core.quotepath=false diff --name-only "$BASE...HEAD" 2>/dev/null
    git -c core.quotepath=false diff --name-only HEAD 2>/dev/null
    git -c core.quotepath=false ls-files --others --exclude-standard 2>/dev/null
  } | awk 'NF && !s[$0]++' | while IFS= read -r f; do ignored "$f" || printf '%s\n' "$f"; done
}

CHANGED=$(changed_files)
if [ "$HOOK" = 1 ] && [ -z "$CHANGED" ]; then exit 0; fi

# ---------------------------------------------------------------- stages
# Each stage writes its detail to $1 and returns 0 (pass), 1 (fail) or 3 (skip, reason in NOTE).
tool_missing() { # <command> <cfg key> <detail file>: true (and explains) when the tool is absent
  command -v "$1" >/dev/null 2>&1 && return 1
  echo "$1 not found: install it, or set guards.$2 to false in .agentkit.json to skip this stage" >"$3"
  return 0
}

stage_lint() {
  local R rc=0
  read -r -a R <<<"$(cfg guards.ruff_cmd ruff)"
  tool_missing "${R[0]}" ruff "$1" && return 1
  "${R[@]}" check --quiet --output-format concise "${AK_RUFF_EXCLUDE[@]}" . >"$1" 2>&1 || rc=1
  "${R[@]}" format --check "${AK_RUFF_EXCLUDE[@]}" . >>"$1" 2>&1 || rc=1
  return $rc
}

stage_tests() {
  local P PA rc=0
  read -r -a P <<<"$(cfg guards.pytest_cmd pytest)"
  read -r -a PA <<<"$(cfg guards.pytest_args "")"
  tool_missing "${P[0]}" pytest "$1" && return 1
  mkdir -p "$(dirname "$XML")"
  rm -f "$XML"
  "${P[@]}" --cov "--cov-report=xml:$XML" ${PA[@]+"${PA[@]}"} >"$1" 2>&1 || rc=1
  if grep -q "unrecognized arguments: --cov" "$1"; then
    echo "pytest-cov is not installed: pip install pytest-cov, or set guards.pytest to false" >>"$1"
  fi
  return $rc
}

stage_diffcover() {
  local D under
  if cfg_on guards.pytest true && [ "${TESTS_RC:-0}" -ne 0 ]; then NOTE="needs passing tests"; return 3; fi
  read -r -a D <<<"$(cfg guards.diff_cover_cmd diff-cover)"
  under=$(cfg guards.fail_under 90)
  tool_missing "${D[0]}" diff_cover "$1" && return 1
  [ -n "$BASE" ] || { echo "no base branch found: set notes.base_branch in .agentkit.json" >"$1"; return 1; }
  [ -f "$XML" ] || { echo "$XML is missing: diff-cover needs the coverage report of the tests stage (guards.pytest)" >"$1"; return 1; }
  "${D[@]}" "$XML" "--compare-branch=$BASE" "--fail-under=$under" >"$1" 2>&1
}

# text of the decisions logged for this branch (header: ## D-xxxx · date · topic · branch)
branch_decisions() {
  local f
  f="$(notes_dir)/decisions.md"
  [ -f "$f" ] && [ -n "$BRANCH" ] || return 0
  tr -d '\015' <"$f" | awk -v b="$BRANCH" -F ' · ' '/^## D-/ { v = $4; sub(/[ \t]+$/, "", v); cur = (v == b); next } cur { print }'
}

mentions() { # <text> <path>: the path appears as a whole token
  local esc
  esc=$(printf '%s' "$2" | sed 's/[][\\.*^$+?(){}|]/\\&/g')
  printf '%s\n' "$1" | grep -Eq "(^|[^A-Za-z0-9_./-])$esc([^A-Za-z0-9_./-]|\$)"
}

in_plan() { # <path> <allowed paths, one per line>
  local p
  while IFS= read -r p; do
    [ -n "$p" ] || continue
    [ "$1" = "$p" ] && return 0
    case $p in */) case $1 in "$p"*) return 0 ;; esac ;; esac
    # shellcheck disable=SC2254
    case $1 in $p) return 0 ;; esac
  done <<<"$2"
  return 1
}

stage_scope() {
  local plan allowed logged f n=0
  plan="$(notes_dir)/tasks/$(slug "${BRANCH:-detached}").plan.md"
  if [ -z "$BRANCH" ] || [ ! -f "$plan" ]; then NOTE="no plan for this branch (lite path)"; return 3; fi
  [ -n "$BASE" ] || { echo "no base branch found: set notes.base_branch in .agentkit.json" >"$1"; return 1; }
  allowed=$(bash "$HERE/plan-step.sh" files --cumulative 2>"$1") || return 1
  : >"$1"
  logged=$(branch_decisions)
  while IFS= read -r f; do
    [ -n "$f" ] || continue
    in_plan "$f" "$allowed" && continue
    [ -n "$logged" ] && mentions "$logged" "$f" && continue
    n=$((n + 1))
    [ "$n" -le 10 ] && echo "Out-of-plan file: $f. Log the reason in the decision log or revert." >>"$1"
  done <<<"$CHANGED"
  [ "$n" -le 10 ] || echo "(+$((n - 10)) more)" >>"$1"
  if [ "$n" -gt 0 ]; then echo "(log: nt decide --topic scope --text \"<path>: <why>\")" >>"$1"; return 1; fi
  return 0
}

# ---------------------------------------------------------------- run
FAILED=0 TESTS_RC=0 NAMES=() STATUS=() NOTES=() DETAILS=()
run_stage() { # <cfg key> <stage name> <function>
  local key=$1 name=$2 fn=$3 out="$TMP/$2.out" rc
  NAMES+=("$name"); DETAILS+=("$out"); NOTE=""
  if ! cfg_on "guards.$key" true; then STATUS+=(SKIP); NOTES+=("disabled: guards.$key=false"); return; fi
  if ! selected "$name"; then STATUS+=(SKIP); NOTES+=("not selected ($SEL_SRC)"); return; fi
  "$fn" "$out"; rc=$?
  [ "$name" = tests ] && TESTS_RC=$rc
  case $rc in
    0) STATUS+=(PASS); NOTES+=("") ;;
    3) STATUS+=(SKIP); NOTES+=("$NOTE") ;;
    *) STATUS+=(FAIL); NOTES+=(""); FAILED=1 ;;
  esac
}

run_stage ruff lint stage_lint
run_stage pytest tests stage_tests
run_stage diff_cover diff-cover stage_diffcover
run_stage scope scope stage_scope

for i in "${!NAMES[@]}"; do
  printf '%-11s %-5s %s\n' "${NAMES[i]}" "${STATUS[i]}" "${NOTES[i]}" | sed 's/ *$//' >&2
done
for i in "${!NAMES[@]}"; do
  [ "${STATUS[i]}" = FAIL ] || continue
  echo "--- ${NAMES[i]}" >&2
  # lint lists its violations first; the other tools put the useful part last
  if [ "${NAMES[i]}" = lint ]; then head -n 15 "${DETAILS[i]}" >&2; else tail -n 15 "${DETAILS[i]}" >&2; fi
done
if [ "$FAILED" -eq 0 ]; then
  echo "VERDICT: PASS (exit 0)" >&2
  exit 0
fi
echo "VERDICT: FAIL (exit $FAIL_RC) - fix the failing stage above and re-run verify.sh" >&2
exit "$FAIL_RC"
