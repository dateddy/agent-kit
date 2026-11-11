# Decisions

Append-only. Never edit or delete an entry; to change a decision, add a new one
with `Supersedes: D-xxx`. Written by notes.py decide.

## D-9152 · 2026-11-10 · guards-language · refactor/guard-py
Guard logic moves to Python stdlib (3.9+); the .sh files stay as thin wrappers so installed hooks keep working. Wrapper finds python3, python, py -3 via command -v, skipping */WindowsApps/* stubs; no python = fail like missing jq (exit 2 hook, 1 arg).
Why: testable tokenizer and rules, no jq on the guard path, room for 1b rules
