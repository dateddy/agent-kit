# py.sh - the one place that finds a Python interpreter for the agent-kit shell scripts. Source it (`. py.sh`); POSIX sh.
# tools/install.py copies the marked function below into the git hooks it writes, so keep it POSIX and self-contained.
#
# ak_find_py sets ak_py (and ak_py_args, "-3" for the Windows launcher, else empty) and returns 0, or returns 1 when no
# python was found. Order: python3, python, py -3. A command that only resolves to the Microsoft Store stub
# (*/WindowsApps/*) is skipped: the stub does not run Python, it opens the Store.

# shellcheck shell=sh
# shellcheck disable=SC2034  # ak_py and ak_py_args are read by the scripts that source this file
# >>> ak_find_py
ak_usable() { case $1 in '' | */WindowsApps/*) return 1 ;; esac; }
ak_find_py() {
  ak_py="" ak_py_args=""
  for ak_c in python3 python; do
    ak_p=$(command -v "$ak_c" 2>/dev/null) || ak_p=""
    if ak_usable "$ak_p"; then ak_py=$ak_p; return 0; fi
  done
  ak_p=$(command -v py 2>/dev/null) || ak_p=""
  if ak_usable "$ak_p"; then ak_py=$ak_p; ak_py_args="-3"; return 0; fi
  return 1
}
# <<< ak_find_py
