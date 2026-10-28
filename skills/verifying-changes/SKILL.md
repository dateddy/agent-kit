---
name: verifying-changes
description: Gates "done" on evidence — scripts/verify.py auto-detects and runs the project's format, lint, typecheck and test commands, returns one PASS/FAIL verdict with only the failing lines, and flags deleted tests, added skips and removed assertions. Use after every code change, after each plan step, before committing, and before telling the user a task is finished.
---

# Verifying changes

Path: `python <this-skill>/scripts/verify.py` (`vf`).

## The rule

You may say a task or step is **done** only when `vf` exits `0` in this session, after your last edit.
"Should work", "looks correct", or tests you did not run are not evidence.

| Exit | Verdict | What you do |
|---|---|---|
| 0 | PASS | Report done; quote the verdict line |
| 1 | FAIL | Fix the cause, re-run. Never report done |
| 2 | NOTHING VERIFIED | Tell the user no gate ran; ask whether to configure `verify` in `.agentkit.json` |
| 2 | PASS WITH TEST CHANGES | List the flagged test changes to the user; wait for their OK |

## Workflow

```
- [ ] vf --detect            once per repo/session: see which gates exist
- [ ] edit code
- [ ] vf --only lint,test    fast loop while iterating (skip slow gates)
- [ ] vf                     full run before commit / before saying "done"
```

On FAIL: read only the error lines shown. If they are not enough, `grep -n "<test name>" <log path>` — do not rerun just to see more.

## Never make tests pass by weakening them

Do not delete tests, add `skip`/`xfail`, loosen or remove assertions, or change expected values to match wrong output.
If a test is genuinely wrong (spec changed), say so, explain why, and get the user's OK first. `vf` flags these edits either way.

## When a gate is missing or wrong

- Tool not installed → install it if the project declares it (requirements/devDependencies), otherwise tell the user.
- Detection is wrong → the user sets `"verify": {"test": "pytest tests/unit -q"}` in `.agentkit.json` (`null` disables a gate).
- No tests cover the change → say so explicitly; offer to add a test for the new behavior.
