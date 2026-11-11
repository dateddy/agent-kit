#!/usr/bin/env bash
# plan-step.sh - print one field of the active step of the current branch's plan.
# Exit-code convention: plain CLI, not a hook. 0 = value printed, 1 = error. The value goes to
# stdout; every diagnostic goes to stderr.
#
#   plan-step.sh <field> [--step N] [--cumulative] [--plan FILE] [--note FILE]
#   fields: title mode files contract tests out-of-scope verify   (old plans: do done)
#
# The plan is <notes.dir>/tasks/<branch-slug>.plan.md (writing-plans). The active step is the first
# unchecked item of the "## Plan" checklist in the task note <notes.dir>/tasks/<branch-slug>.md,
# which `notes.py task --check N` maintains; when every item is checked it is the last step.
# `files` prints one path per line with "(new)" removed; --cumulative covers steps 1..active.
# Since 0.5.0 this is a wrapper: the parser lives in `plan.py field` (one implementation, no jq).
set -u
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=py.sh
. "$here/py.sh"

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 1; }
fail() { echo "$*" >&2; exit 1; }

field=${1:-}
[ -n "$field" ] || usage
shift
step="" cumulative=0 plan="" note=""
while [ $# -gt 0 ]; do
  case $1 in
    --step) step=${2:-}; shift 2 || usage ;;
    --cumulative) cumulative=1; shift ;;
    --plan) plan=${2:-}; shift 2 || usage ;;
    --note) note=${2:-}; shift 2 || usage ;;
    *) usage ;;
  esac
done

# Same layout in the kit (scripts/ next to skills/) and in a project (.agentkit/scripts next to .agentkit/skills).
plan_py="$here/../skills/writing-plans/scripts/plan.py"
[ -f "$plan_py" ] || fail "plan-step.sh: $plan_py not found; run install.py again"
ak_find_py || fail "plan-step.sh: python not found (tried python3, python, py -3)"

args=(field "$field")
[ -z "$step" ] || args+=(--step "$step")
[ "$cumulative" = 0 ] || args+=(--cumulative)
[ -z "$plan" ] || args+=(--plan "$plan")
[ -z "$note" ] || args+=(--note "$note")
exec "$ak_py" ${ak_py_args:+"$ak_py_args"} "$plan_py" "${args[@]}"
