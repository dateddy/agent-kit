# Knowledge

Facts about this repo and its environment that took effort to find out. The agent adds a line here when it needed
3 or more tries to find the right way, or when you corrected a wrong claim about the repo.
Line format: `- <fact>; <the right way> (verified YYYY-MM-DD: <command or evidence>)`.
Not here: bugs (dbg journal), decisions (decisions.md), agent mistakes (FAILURES.md).

- A heredoc through the Bash tool eats backslashes (`\\n` in a Python string becomes a real newline); write such files with Write/Edit, or build the character with chr(92) (verified 2026-11-11: `python - <<'EOF'` printing "a\\nb" gives two lines; install.py got a SyntaxError that way in 2a step 1)
- A full `vf` on Windows takes 110-170 s, over the Bash tool's 120 s default timeout; call Bash with timeout >=300000 ms, and use `vf --affected` in the inner loop (verified 2026-11-11: 1b audit; `bash scripts/test-kit.sh` also ran past 400 s in 2a)
- shellcheck installed with pip (shellcheck-py) is not on bash's PATH here; run the CI lint job or call the pip script by full path, not bare `shellcheck` (verified 2026-11-11: `command -v shellcheck` finds nothing in Git bash; CI pins shellcheck-py==0.11.0.1 since c042b3d)
- `python3` on Windows can be the Microsoft Store stub (rc=49, prints "Python was not found"); find Python through scripts/py.sh, which skips */WindowsApps/* (verified 2026-11-11: `python3 -c pass` -> rc=49, D-9152, scripts/py.sh)
- CI: Ubuntu keeps python3 next to bash and the Windows runner finds WSL's bash first; wrapper tests must be hermetic (own PATH, skip when only the WSL launcher exists) and run with `shell: bash` (verified 2026-11-11: commit 14b1198)
