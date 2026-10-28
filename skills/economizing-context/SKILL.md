---
name: economizing-context
description: Cuts token use while exploring code and running commands — scripts/repomap.py shows repo shape, token cost and file outlines without reading files; scripts/run.py runs any command, saves the full log to disk and returns only exit code, errors and tail. Use when exploring an unfamiliar repo, before opening large files, and for any command with long output (tests, installs, builds, training, data scripts).
---

# Economizing context

Every tool result stays in the conversation and is re-read on every later turn. Pay for what you need, fetch the rest on demand.

Paths: `python <this-skill>/scripts/repomap.py` (`rm`), `python <this-skill>/scripts/run.py` (`run`).

## Exploring: map → search → read a range

1. `rm` (or `rm src/ --depth 3`): structure, file counts, ~token cost per dir/file.
2. `rm --big`: the most expensive files. Never read these whole.
3. Search before opening: `rg -n "<symbol>" src/` (or `grep -rn`).
4. `rm --symbols path/file.py`: classes/functions with line ranges.
5. Read only the range: `sed -n '120,180p' path/file.py` (or the Read tool with offset/limit).

Skip entirely unless the task is about them: lockfiles, datasets, notebook outputs, generated/minified files, checkpoints, logs.

## Blast radius: `rm --zone` (Python)

Shows what a change can touch, from the import graph (`ast`, nothing executed): files that import it (↑), files it imports (↓), and the tests for them. Prints paths, lines and ~tokens only, never file contents.

```
rm --zone faq/search.py [more seeds] [--hops 2] [--direction both|imports|importers] [--json]
rm --zone-from-diff           # seeds = changed + untracked .py files
rm --zone-from-log run.log    # seeds = repo files in the traceback frames
```

Use it **before editing a module**, **when planning a refactor**, and **when debugging from a traceback**. Then read zone files like any other: `rm --symbols <file>` + a line range, never whole files.
`NOTE: dynamic import …` means that edge is not traced; check it by grep. Exit 2 = not a Python repo.

## Running commands: wrap anything that may print >60 lines

```
run -- pytest -q
run -- pip install -r requirements.txt
run --name train --timeout 3600 -- python train.py --config cfg.yaml
run "python prep.py | grep -v DEBUG"        # single string = shell pipeline
```

Output: status, exit code, duration, log path, unique error lines, last traceback, tail. The exit code is the command's own, so the result is trustworthy.
Need more? Search the saved log instead of re-running: `grep -n "<pattern>" .agentkit/logs/<file>.log`, `sed -n 'A,Bp' <log>`.

Not needed for short, bounded commands (`git status`, `ls`, `cat` of a small file).

## Rules

- Do not re-read a file you read earlier in this session unless it changed.
- Prefer `--symbols` + a range over reading a whole file >200 lines.
- When a log is truncated, grep it; do not rerun the command just to see more output.
