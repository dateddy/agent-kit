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
set -u
AK_FAIL=1
# shellcheck source=lib.sh
. "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

usage() { sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 1; }

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
case $field in
  title | mode | files | contract | tests | out-of-scope | verify | do | done) ;;
  *) ak_fail "unknown field '$field'. Use: title mode files contract tests out-of-scope verify" ;;
esac
case $step in '' | *[!0-9]*) [ -z "$step" ] || ak_fail "--step needs a number, got '$step'" ;; esac

if [ -z "$plan" ] || [ -z "$note" ]; then
  branch=$(current_branch) || true
  [ -n "${branch:-}" ] || ak_fail "detached HEAD: pass --plan and --note, or switch to the task branch"
  base="$(notes_dir)/tasks/$(slug "$branch")"
  [ -n "$plan" ] || plan="$base.plan.md"
  [ -n "$note" ] || note="$base.md"
fi
[ -f "$plan" ] || ak_fail "no plan at $plan. Save the accepted plan there (pl path), then run pl apply."

# first unchecked checklist item of the task note, else the last item
active_step() {
  awk '
    /^## / { sec = tolower(substr($0, 4)); next }
    sec == "plan" && /^- \[[ x]\] [0-9]+\./ {
      n = $0; sub(/^- \[[ x]\] /, "", n); sub(/\..*/, "", n); last = n
      if (substr($0, 4, 1) != "x" && first == "") first = n
    }
    END { if (first != "") print first; else if (last != "") print last }' "$1"
}

if [ -z "$step" ]; then
  [ -f "$note" ] || ak_fail "no task note at $note, so the active step is unknown. Run pl apply, or pass --step N."
  step=$(active_step "$note")
  [ -n "$step" ] || ak_fail "task note $note has no plan checklist. Run pl apply, or pass --step N."
fi

want=$step
if [ "$cumulative" = 1 ]; then want=$(seq -s ' ' 1 "$step"); fi

# Reads "### Step N: title" + "- Field: value" bullets, and the old "## N. title" + "Field: value" form.
# Continuation lines extend the current field. Exit 3 = field or step missing (message on stderr).
raw=$(tr -d '\015' <"$plan" | awk -v want="$want" -v fld="$field" '
  function canon(k) { k = tolower(k); gsub(/ /, "-", k); return k }
  function warn(m) { print m | "cat 1>&2" }
  BEGIN { n = split(want, w, " "); for (i = 1; i <= n; i++) ok[w[i]] = 1; step = 0; field = "" }
  /^###[ \t]+Step[ \t]+[0-9]+[ \t]*:/ {
    s = $0; sub(/^###[ \t]+Step[ \t]+/, "", s)
    num = s; sub(/[ \t]*:.*/, "", num); title = s; sub(/^[0-9]+[ \t]*:[ \t]*/, "", title)
    step = num + 0; seen[step] = 1; field = ""
    if ((step in ok) && fld == "title") { val[step] = title; has[step] = 1 }
    next
  }
  /^##[ \t]+[0-9]+\./ {
    s = $0; sub(/^##[ \t]+/, "", s); num = s; sub(/\..*/, "", num); title = s; sub(/^[0-9]+\.[ \t]*/, "", title)
    step = num + 0; seen[step] = 1; field = ""
    if ((step in ok) && fld == "title") { val[step] = title; has[step] = 1 }
    next
  }
  /^#/ { step = 0; field = ""; next }
  step == 0 { next }
  /^(- )?(Mode|Files|Contract|Tests|Out of scope|Verify|Do|Done):/ {
    s = $0; sub(/^- /, "", s); key = s; sub(/:.*/, "", key); v = s; sub(/^[^:]*:[ \t]*/, "", v)
    sub(/[ \t]+$/, "", v); field = canon(key)
    if ((step in ok) && field == fld) { val[step] = v; has[step] = 1 }
    next
  }
  /^[ \t]*$/ { next }
  field != "" && (step in ok) && field == fld {
    c = $0; sub(/^[ \t]+/, "", c); sub(/[ \t]+$/, "", c); val[step] = val[step] "\n" c
  }
  END {
    bad = 0
    for (i = 1; i <= n; i++) {
      s = w[i] + 0
      if (!(s in seen)) { warn("step " s " not found in the plan"); bad = 1 }
      else if (!(s in has)) { warn("step " s " has no \"" fld "\" field"); bad = 1 }
      else print val[s]
    }
    exit bad ? 3 : 0
  }') && rc=0 || rc=$?
[ "$rc" -eq 0 ] || exit 1

if [ "$field" = files ]; then
  printf '%s\n' "$raw" | tr ',' '\n' \
    | sed -E 's/`//g; s/[[:space:]]*\((new|NEW|New|mới)\)[[:space:]]*$//; s/^[[:space:]]+//; s/[[:space:]]+$//' \
    | awk 'NF && !s[$0]++'
else
  printf '%s\n' "$raw"
fi
