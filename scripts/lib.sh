# shellcheck shell=bash
# lib.sh - helpers shared by the agent-kit guard scripts. Sourced, never executed.
# Exit-code convention: none of its own. A failing helper calls ak_fail, which exits with
# $AK_FAIL (the sourcing script sets it first: 2 for Claude Code hook scripts because only exit 2
# blocks there, 1 for plain CLI and git-hook scripts). Diagnostics go to stderr, never stdout.
#
# Requires: bash, git, jq. Config comes from <repo>/.agentkit.json (parsed with jq, never sourced).

ak_fail() { echo "$*" >&2; exit "${AK_FAIL:-1}"; }

command -v jq >/dev/null 2>&1 || ak_fail "jq not found: install jq (https://jqlang.org/download/); the agent-kit guards need it"

AK_ROOT=$(git rev-parse --show-toplevel 2>/dev/null) || AK_ROOT=$PWD
AK_CFG="$AK_ROOT/.agentkit.json"
if [ -f "$AK_CFG" ] && ! jq -e . "$AK_CFG" >/dev/null 2>&1; then
  echo "WARN: $AK_CFG is not valid JSON; using defaults" >&2
  AK_CFG=""
fi

# cfg <dotted.path> <default>: scalar value, or the default when the key is absent or null.
# Arrays come back one element per line. false/0 are real values (jq's `//` would drop false).
cfg() {
  local v=""
  if [ -n "$AK_CFG" ] && [ -f "$AK_CFG" ]; then
    v=$(jq -r --arg p "$1" 'getpath($p | split(".")) as $v
      | if $v == null then empty
        elif ($v | type) == "array" then ($v | map(tostring) | join("\n"))
        else ($v | tostring) end' "$AK_CFG" 2>/dev/null | tr -d '\r')
  fi
  if [ -n "$v" ]; then printf '%s\n' "$v"; else printf '%s\n' "${2-}"; fi
}

# cfg_on <path> <default true|false>: success when the switch is on. A stage is off only when
# the config says false (or 0/no/off), so a typo never disables a guard silently.
cfg_on() {
  case $(cfg "$1" "$2") in false | 0 | no | off) return 1 ;; *) return 0 ;; esac
}

# Defaults live here and in .agentkit.example.json, nowhere else.
protected_list() {
  local l
  l=$(cfg git.protected_branches "")
  [ -n "$l" ] || l=$'main\nmaster\ndevelop\nrelease/*'
  printf '%s\n' "$l"
}

# is_protected <branch>: glob match against git.protected_branches
is_protected() {
  local pat
  [ -n "${AK_PROT-}" ] || AK_PROT=$(protected_list)  # cached: each cfg call spawns jq
  while IFS= read -r pat; do
    [ -n "$pat" ] || continue
    # shellcheck disable=SC2254
    case $1 in $pat) return 0 ;; esac
  done <<<"$AK_PROT"
  return 1
}

current_branch() { git symbolic-ref --quiet --short HEAD 2>/dev/null; }

# base_ref: a ref that resolves to the base branch: notes.base_branch, else origin/HEAD,
# else the first literal protected branch that exists. Prints nothing when none is found.
base_ref() {
  local b pat
  b=$(cfg notes.base_branch "")
  if [ -z "$b" ]; then
    b=$(git symbolic-ref --quiet --short refs/remotes/origin/HEAD 2>/dev/null | sed 's#^origin/##')
  fi
  if [ -z "$b" ]; then
    while IFS= read -r pat; do
      case $pat in '' | *[*?[]*) continue ;; esac
      if git rev-parse --verify -q "refs/heads/$pat" >/dev/null; then b=$pat; break; fi
    done < <(protected_list)
  fi
  [ -n "$b" ] || return 0
  if git rev-parse --verify -q "$b" >/dev/null; then echo "$b"
  elif git rev-parse --verify -q "origin/$b" >/dev/null; then echo "origin/$b"; fi
}

# slug <branch>: must match slug() in skills/keeping-notes/scripts/notes.py
slug() {
  printf '%s' "$1" | sed -E 's#/#--#g; s#[^A-Za-z0-9._-]+#-#g; s#^-+##; s#-+$##'
}

notes_dir() {
  local d
  d=$(cfg notes.dir notes)
  printf '%s\n' "$AK_ROOT/${d%/}"
}

# need <command> <stage>: fail loudly when a tool is missing. Never a silent pass.
need() {
  command -v "$1" >/dev/null 2>&1 \
    || ak_fail "$1 not found: install it, or set guards.$2 to false in .agentkit.json to skip this stage"
}

# ruff must not lint the kit's own vendored scripts or the agent settings directory
AK_RUFF_EXCLUDE=(--extend-exclude .agentkit --extend-exclude .claude)
