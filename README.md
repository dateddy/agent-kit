# agent-kit

A shared skill set for software and AI projects worked on with coding agents (Claude Code, Codex, Cursor, Gemini CLI, Copilot CLI, …).

It is built around three goals:

- **Spend fewer tokens.** The agent reads maps, outlines and summaries instead of whole files and full logs.
- **Engineering over judgment.** Anything that has a rule (commit format, branch policy, secrets, plan completeness, test results) is decided by a Python script, not by the model.
- **Automate the repetitive parts.** Starting tasks, planning, committing, verifying, tracking experiments, writing prompts.

All scripts are Python 3.9+ standard library only (PyYAML optional for YAML configs). Git ≥ 2.38 is recommended for merge-conflict prediction.

---

## Contents

1. [How it works](#1-how-it-works)
2. [Install](#2-install)
3. [A complete task, start to finish](#3-a-complete-task-start-to-finish)
4. [Conventions you will see everywhere](#4-conventions-you-will-see-everywhere)
5. [Skills](#5-skills)
   - [starting-tasks](#starting-tasks--task) · [grilling-designs](#grilling-designs-grill) · [writing-plans](#writing-plans) · [keeping-notes](#keeping-notes)
   - [managing-git](#managing-git) · [verifying-changes](#verifying-changes) · [debugging-systematically](#debugging-systematically)
   - [economizing-context](#economizing-context) · [tracking-experiments](#tracking-experiments) · [crafting-prompts](#crafting-prompts)
6. [Side questions without polluting the task context](#6-side-questions-without-polluting-the-task-context)
7. [Configuration reference](#7-configuration-reference)
8. [Using other agents](#8-using-other-agents)
9. [Troubleshooting](#9-troubleshooting)
10. [Adding your own skill](#10-adding-your-own-skill)

---

## 1. How it works

A **skill** is a folder with a `SKILL.md` (instructions for the agent) and optional `scripts/` and `reference/` files.

```
agent-kit/
├── skills/
│   ├── starting-tasks/        /task entry point: frame the request, ask what is missing
│   ├── grilling-designs/      /grill design interview before planning, decisions recorded
│   ├── writing-plans/         executor-ready plans, validated by plan.py
│   ├── keeping-notes/         task state per branch + decision log (notes.py)
│   ├── managing-git/          commit/branch/stash/merge/rebase/push with checks (gitkit.py)
│   ├── verifying-changes/     one PASS/FAIL verdict from lint/typecheck/test (verify.py)
│   ├── debugging-systematically/  code / data / llm debugging + bug journal (dbg.py)
│   ├── economizing-context/   repo map, import-graph zones, capped command runner (repomap.py, run.py)
│   ├── tracking-experiments/  training/eval run log tied to git commits (exp.py)
│   └── crafting-prompts/      prompts for a target model from a spec, with tests (promptkit.py)
├── templates/                 AGENTS.md, CLAUDE.md, commands/task.md, commands/grill.md
├── tools/                     install.py, validate_skills.py, selftest.py
└── .agentkit.example.json     default configuration
```

Token cost is layered (progressive disclosure):

| Layer | When it is in context | Cost |
|---|---|---|
| `name` + `description` of each skill | Always | ~80–115 tokens per skill, ~1000 for all ten |
| `SKILL.md` body | Only when the task matches | ~250–900 tokens |
| `reference/*.md` | Only when that detail is needed | varies |
| `scripts/*.py` | Never; they are run, and only their output enters context | output only |

`AGENTS.md` (always loaded, ~45 lines) holds the cross-cutting rules: question vs task mode, the task lifecycle, exit-code handling and token discipline.

## 2. Install

```bash
git clone <this repo> agent-kit
python agent-kit/tools/install.py path/to/your-project
```

What it does (safe to re-run; it never overwrites your files):

| Path in your project | Purpose |
|---|---|
| `.agentkit/skills/<skill>/` | Canonical copy of every skill (agent-neutral; `AGENTS.md` points here) |
| `.claude/skills/<skill>` | Symlink to the above so Claude Code discovers the skills (copied if symlinks are unavailable) |
| `.claude/commands/task.md`, `grill.md` | The `/task` and `/grill` commands for Claude Code |
| `AGENTS.md` | Rules for every agent (created if missing; warns you to merge if you already have one) |
| `CLAUDE.md` | `@AGENTS.md` plus Claude Code extras (prepends `@AGENTS.md` to an existing file) |
| `.agentkit.json` | Configuration (created if missing) |
| `notes/decisions.md`, `.gitignore` rule for `notes/tasks/` | Notes layout |

Commit `.agentkit/skills`, `.claude/`, `AGENTS.md`, `CLAUDE.md`, `.agentkit.json` and `notes/decisions.md`. Logs (`.agentkit/logs/`, `experiments/logs/`, `prompts/runs/`) ignore themselves.

To update the kit later, pull it and run `install.py` again.

**Optional SessionStart hook (Claude Code only):** `python agent-kit/tools/install.py path/to/your-project --hooks` merges a hook into `.claude/settings.json` that runs `notes.py brief` when a session starts, resumes, clears or compacts, so the brief is already in context. It never overwrites existing keys or hooks, adds nothing if the same hook exists, and leaves a `settings.json` it cannot parse untouched (exit 2). Trade-off: one fewer turn per session (no manual `nt brief`) versus a Claude-Code-only mechanism and a few hundred tokens added to every session start, even for a question that does not need the brief. Without `--hooks` nothing changes, and the rule in `AGENTS.md` ("start of session: `nt brief`") works in every agent.

User-wide install (skills only, every project, Claude Code): `python agent-kit/tools/install.py --user`.

Check that the skills are well-formed and see their token cost: `python agent-kit/tools/validate_skills.py`.
Prove the kit works on your machine: `python agent-kit/tools/selftest.py` (and `--cp1252` to simulate a Windows console). It builds throwaway git repos and runs the real scripts.

## 3. A complete task, start to finish

Example: add a BM25 fallback to a RAG retriever, in Claude Code.

**You:** switch to plan mode (`Shift+Tab`, model `opusplan`) and type

```
/task Add BM25 as a fallback when the dense retriever returns nothing.
Constraints: keep Retriever.search() signature, use rank_bm25.
Done when: a query with a rare drug name returns ≥1 passage; existing tests pass.
Out of scope: tuning hybrid weights.
```

**Agent (planning model, read-only):**
1. `nt brief` → no open task on this branch; one earlier decision about retrieval is shown.
2. Maps the code (`rm`, `rm --symbols`), reads two functions by line range.
3. Outputs a plan: 3 steps, each with exact files, the change, a verify command and a done criterion.

**You:** read it, ask for a change if needed, then accept.

**Agent (execution model):**
1. `gk branch-name feat "bm25 fallback"` → `git switch -c feat/bm25-fallback`
2. Saves the plan to the path from `pl path`, runs `pl check` (all paths exist, no placeholders) and `pl apply` (the steps become the task note's checklist).
3. For each step: `pl step N` → edit → `vf` → `nt task --check N --done "…" --next "…"` → commit through gitkit (secret scan, Conventional Commit lint).
4. Final `vf` → `VERDICT: PASS` → reports done with the verdict line.

**Next morning, new chat, any agent:** type `làm tiếp` / `continue`. The agent runs `nt brief`, sees "step 3 of 3, next: write the merge test", and continues with ~30 lines of context instead of yesterday's whole conversation.

## 4. Conventions you will see everywhere

**Exit codes (every script):**

| Code | Meaning | What the agent must do |
|---|---|---|
| `0` | OK | Continue |
| `2` | WARN | Show you the warning and the next command; continue only after your explicit yes |
| `1` | BLOCK | Stop, fix the cause, re-run. Never bypass |

**Message prefixes (any agent):**

| You type | Mode |
|---|---|
| `? why is the reranker slow` | Question mode: read-only. No edits, commits or note updates |
| `/task …` or `task: …` | Start a task via starting-tasks |
| `add caching to the reranker` | Task mode |
| Something ambiguous | Treated as a question; the agent offers to do it |

**Where things live in your project:**

| Path | Content | In git? |
|---|---|---|
| `notes/tasks/<branch>.md` | Task note: goal, plan checklist, next step, log | No (personal) |
| `notes/tasks/<branch>.plan.md` | The accepted plan | No |
| `notes/decisions.md` | Append-only decision log | Yes |
| `experiments/runs.jsonl` | Experiment records | Yes |
| `prompts/*.spec.json`, `prompts/*.<target>.md` | Prompt specs and rendered prompts | Yes |
| `.agentkit/logs/`, `experiments/logs/`, `prompts/runs/` | Full command/test/run logs | No |

---

## 5. Skills

Short names used below (all are `python .agentkit/skills/<skill>/scripts/<file>`):
`nt` notes.py · `gk` gitkit.py · `pl` plan.py · `vf` verify.py · `run` run.py · `rm` repomap.py · `ex` exp.py · `pk` promptkit.py · `dbg` dbg.py.

You rarely type these yourself; the agent runs them. They are listed so you know what is happening and can run them when you want.

### starting-tasks (`/task`)

**What it does:** turns a request into a task frame (goal, constraints, done-when, out-of-scope), asks only for missing pieces, then hands off to writing-plans. Nothing is created or edited before you accept a plan.

**How to invoke:** `/task <request>` in Claude Code; in other agents, start the message with `/task` or `task:`. Writing a plain feature request also works; `/task` just makes it explicit.

**The frame:**

```
<Goal: the end result>                    required
Constraints: <must not change / required tech>
Done when: <how you will know it is finished>
Out of scope: <what not to do now>
```

**Scenarios**

| Situation | You type | What happens |
|---|---|---|
| Clear request | `/task Add BM25 fallback … Done when: …` | No questions; goes straight to planning |
| Vague request | `/task make retrieval better` | One message with ≤3 numbered questions, each asking exactly one thing and ending with `Recommended: X — reply 'ok' to accept`; always includes "how will we know it is done?" |
| New public interface or schema, ≥3 modules, or an open design choice | `/task …` | After the frame, one line suggests `/grill`; it never runs on its own |
| A task is already open on this branch | `/task …` | Asks: continue the open task, or start a new one (new branch)? |
| You do not care about a field | Answer `skip` | The field is left empty |

### grilling-designs (`/grill`)

**What it does:** a short design interview before planning. The agent first reads what the code and notes already say (`nt brief`, `rm`, `rm --zone`) and never asks you what it can look up. Then it walks the decision tree (data, interfaces, errors, storage, config, testing, rollout), parents before children, **one question per message**, each with a recommended answer and a one-line reason. Every settled answer is recorded at once with `nt decide`, and the plan that follows lists them in a `Decisions:` header.

**How to invoke:** `/grill <feature>` (manual only: the skill has `disable-model-invocation: true`, so the agent never starts it by itself; `starting-tasks` just suggests it). In other agents, ask to be grilled on the design.

**An exchange**

```
Q2/~6 · storage — Where should the index live?
Recommended: build in memory at startup — 200 rows, so there is no file to keep in sync.
Reply "ok" to accept, or tell me what you prefer.
```

Reply `ok` to accept. Reply `đủ rồi` or `enough` to stop: the questions left open become listed assumptions (recorded as one decision) and the agent moves on to the plan.

**Commands used:** `nt decide --topic <area> --text … --why …` after each answer; `pl check` later validates the `Decisions: D-012, D-013` header (missing id → BLOCK, superseded id → WARN naming the replacement).

**Scenarios**

| Situation | What happens |
|---|---|
| `/grill add a feedback endpoint` | Reads the code first, then 4–8 single questions with recommendations |
| You accept every default | Reply `ok` each time; each answer is already in `notes/decisions.md` |
| You change your mind later | `nt decide … --supersedes D-012`; `pl check` warns if a plan still cites D-012 |
| You are out of patience | `đủ rồi`: stops, lists assumptions, hands off to writing-plans |
| A decision already exists for the topic | It is not asked again; the agent cites it |

### writing-plans

**What it does:** produces a plan a cheaper model can execute without exploring the repo or making design decisions, and validates it with `pl check` before any code is written.

**Plan format:**

```
# Plan: <one-line outcome>
Constraints: …
Done when: …
Out of scope: …
Decisions: D-012, D-013          (optional, from /grill or nt decide)

## 1. <step title>
Files: src/retrieval/bm25.py (new), tests/test_bm25.py (new)
Do: create BM25Index(docs) with search(query, k) -> list[str] using rank_bm25.BM25Okapi
Verify: python -m pytest tests/test_bm25.py -q
Done: search("aspirin", 2) returns 2 docs containing "aspirin"
```

**What `pl check` enforces:**

| Rule | Result |
|---|---|
| Every step has Files, Do, Verify, Done | BLOCK if missing |
| Every listed file exists, or is marked `(new)` | BLOCK otherwise (no hallucinated paths) |
| No `TODO`, `TBD`, `???`, `<placeholder text>` | BLOCK |
| Steps numbered 1..N | BLOCK |
| `Decisions:` id not in `notes/decisions.md` | BLOCK |
| `Decisions:` id that was superseded | WARN (names the replacement) |
| Vague "Done" ("works correctly") | WARN |
| Manual verification ("check visually") | WARN |
| More than 8 steps, or a step over 800 characters | WARN (split it) |

**Commands:** `pl template` (skeleton) · `pl path` (where the plan file goes) · `pl check` · `pl apply` (load steps into the task note; replaces the old plan and logs its progress) · `pl step N` (print only step N for the executor).

**Scenarios**

| Situation | What happens |
|---|---|
| Plan mode with Opus, execution with Sonnet (`/model opusplan`) | Opus writes the plan in chat (plan mode is read-only). After you accept, Sonnet saves it, checks it, applies it and executes step by step, reading only `pl step N` |
| The plan names a file that does not exist | `pl check` blocks; the agent fixes the path or marks it `(new)` |
| Halfway through, the approach must change | The agent stops and tells you why, updates the plan, then runs `pl check` and `pl apply` again. The old progress is logged in the task note |
| A small correction (a path) | Edit the plan file, run `pl check`, continue |

### keeping-notes

**What it does:** keeps task state in files instead of chat history, so any session (any agent, any model, after `/compact`, next week) can resume from a short brief.

**Two stores:**
- `notes/tasks/<branch>.md`: one note per task branch. Holds the goal, plan checklist, next action, touched files and a log. Personal, gitignored. Switching branch switches task, and notes are never overwritten.
- `notes/decisions.md`: project-wide, append-only, committed. Each entry has an id (`D-007`), date, topic and branch. To change a decision, add a new one with `--supersedes D-007`; the old one stays as history.

**Commands**

| Command | Purpose |
|---|---|
| `nt init` | Create the layout (the installer does this) |
| `nt brief [--topic T]` | ~30-line context pack: task, plan, next, recent log, uncommitted files, relevant decisions. On a branch with no task, lists open tasks |
| `nt task --goal "…" --plan "…" --plan "…"` | Create the task note |
| `nt task --check 2 --done "…" --next "…" [--files …]` | Record step progress |
| `nt task --status blocked --next "need API key"` | Mark blocked |
| `nt task` | Print the current note |
| `nt decide --topic retrieval --text "…" --why "…" [--supersedes D-003]` | Record a decision (≤10 lines / 600 chars, enforced) |
| `nt show --topic retrieval` · `nt show --id D-007` · `nt show --all` | Read decisions (active ones by default) |
| `nt tasks [--all]` | List task notes |
| `nt close [--status abandoned]` · `nt sync` | Close the current task; auto-close tasks whose branch was merged or deleted |

**Scenarios**

| Situation | You type | What happens |
|---|---|---|
| New chat, continue work | `continue` / `làm tiếp` | `nt brief` → the agent picks up at "next" |
| You settle a direction | `chốt: use BM25 only as fallback, not hybrid` | `nt decide --topic retrieval …` |
| "Why did we choose X?" | `? why did we drop the reranker` | `nt show --topic retrieval` → answers from the log with the decision id |
| Juggling two tasks | `git switch fix/tokenizer` | `nt brief` now shows the tokenizer task; the other note is untouched |
| A task branch was merged | (automatic) / `nt sync` | Its note is marked done, not deleted |
| Working in a team | set `notes.commit_tasks: true` | Task notes are committed too |

The agent updates the task note at plan-step boundaries and before commits, not after every action, so the overhead is a few dozen tokens per step.

### managing-git

**What it does:** runs git workflows through checks in `gitkit.py`. The script decides format and safety; the agent writes the commit text; you decide anything risky.

**Who decides what**

| Script decides | Agent decides | You decide |
|---|---|---|
| Commit message format, allowed types/scopes | How to group files into commits | Destructive operations |
| Secret / large-file block | Subject and body wording | Committing directly on a protected branch |
| Commit type when all files match a rule | feat vs fix vs refactor when the script says UNDECIDED | Merge vs rebase |
| Conflict prediction, branch names | How to resolve a conflict hunk | Overriding a secret false-positive |

**Commands**

| Command | Purpose |
|---|---|
| `gk snapshot` | Compact state: branch, upstream ahead/behind, staged/unstaged/untracked, in-progress merge/rebase, stashes, recent log |
| `gk diff [--staged] [paths]` | Size-capped diff that skips lockfiles, notebooks, generated and binary files |
| `gk suggest --staged` | Rule-based type/scope (e.g., only `.md` files → `docs`) or `UNDECIDED` with hints |
| `gk lint-msg FILE` / `-m "…"` | Conventional Commit check: type, scope, imperative mood, length, no trailing period, blank line 2, BREAKING footer |
| `gk scan --staged` | Blocks private keys, API tokens (OpenAI/Anthropic/HF/GitHub/AWS/Google/Slack), hard-coded passwords, `.env`/`.pem` files, files over 5 MB |
| `gk branch-name feat "Thêm BM25 fallback" --ticket VMR-12` | → `feat/vmr-12-them-bm25-fallback` (Vietnamese diacritics stripped) |
| `gk preflight commit\|switch\|merge X\|rebase X\|pull\|push\|stash-pop` | Safety check before the operation; predicts merge conflicts without touching the working tree |
| `gk stashes` | Compact stash list |

**Commit flow** (what happens when you say "commit"): snapshot → group changes by concern (asks you if more than one commit is needed) → stage explicit paths → `preflight commit` → `scan --staged` → `suggest` → capped diff → write message → `lint-msg` until clean → commit.

**Scenarios**

| Situation | What happens |
|---|---|
| "commit" while on `main` | `preflight` returns WARN; the agent asks: commit to `main`, or create a task branch? (configurable: `git.protected_commit` = `ask` / `allow` / `block`) |
| An API key is in a staged file | `scan` blocks; the agent unstages it and suggests loading it from an env var. If you say it is a false positive, you decide |
| Changes mix a feature, a docs fix and a dependency bump | `suggest` warns about mixed categories; the agent proposes three commits |
| "merge develop into my branch" | `preflight merge develop` lists files that will conflict before anything happens |
| Rebasing a branch you already pushed | WARN: rewrites shared history; needs `--force-with-lease`, which the agent asks before using |
| Need to switch branches with unfinished work | `preflight switch` warns; the agent stashes with a message and restores later with apply → verify → drop |
| "undo my last commit" / lost commits | Recovery recipes in `reference/recovery.md` (soft reset, reflog rescue); destructive steps are confirmed with you first |
| Merge conflict | `reference/conflicts.md`: mechanical hunks resolved by the agent, semantic hunks shown to you with a proposed merge |

Destructive commands (`reset --hard`, any force push, `branch -D`, `clean -fd`, `stash drop/clear`, discarding uncommitted work) are always quoted to you first, with a backup branch or SHA noted.

### verifying-changes

**What it does:** stops the agent from saying "done" without evidence. `vf` detects the project's quality gates, runs them, and prints one verdict with only the failing lines.

**Example output**

```
format     SKIP   not detected
lint       PASS   ruff check --output-format concise .   0.8s
typecheck  SKIP   not detected
test       FAIL   python -m pytest -q                   12.3s  log: .agentkit/logs/verify-test-….log
--- test (5 lines; full log …)
FAILED tests/test_hybrid.py::test_merge - AssertionError: expected fallback doc
1 failed, 41 passed in 12.1s
VERDICT: FAIL (exit 1) — fix the cause, re-run verify. Do not report the task as done.
```

The verdict line carries its exit code (`PASS (exit 0)`, `FAIL (exit 1)`, `NOTHING VERIFIED (exit 2)`, `PASS WITH TEST CHANGES (exit 2)`) because agents lose exit codes through pipes. **Never pipe `vf`**; the VERDICT line is authoritative.

**Auto-detection:** ruff / flake8 / black / mypy / pyright (Python, when configured); tests in `tests/` or `test/` are found even with no config file at all, and the runner is chosen by probing: if `python -m pytest --version` works it uses pytest, otherwise (for example a broken `pytest-cov` plugin) it falls back to `unittest discover` when the tests use `unittest.TestCase`, and says why in `--detect`; `package.json` scripts and `tsc` (JS/TS, using the right package manager); `go vet`/`go test`; `cargo clippy`/`cargo test`; Makefile targets. Override any gate in `.agentkit.json`.

**Test-integrity guard:** compared to `HEAD`, it flags deleted test files, newly added `skip`/`xfail`/`.skip(` markers and removed assertions. If tests "pass" because they were weakened, the verdict is `PASS WITH TEST CHANGES` (exit 2) and the agent must show you the changes.

**Fast inner loop:** `vf --affected` asks `rm --zone-from-diff` which tests can be affected by your changed files and runs only those (pytest paths or unittest modules). The verdict says `(affected only)`. No affected tests → `test SKIP no affected tests`. If repomap is missing or the runner is not pytest/unittest it runs the full test gate and says why. It is for iterating only: **a full `vf` is still required before a commit and before saying "done".**

**Commands:** `vf` (all gates) · `vf --only lint,test` · `vf --affected` (inner loop) · `vf --detect` (what would run and why) · `vf --no-guard`.

**Scenarios**

| Situation | What happens |
|---|---|
| Agent finishes a change | Runs `vf`; reports done only on `VERDICT: PASS`, quoting it |
| A test fails | The agent sees only the failing lines, fixes the code, re-runs. It greps the saved log if it needs more, instead of re-running |
| The agent "fixes" a test by adding `@pytest.mark.skip` | Flagged; you are asked to approve or reject |
| Project has no tests | `NOTHING VERIFIED` (exit 2): the agent tells you and offers to add a test |
| Wrong command detected | Set `"verify": {"test": "pytest tests/unit -q"}`, or `null` to skip a gate |
| Slow integration tests | Agent iterates with `vf --only lint,test` on a unit command and runs the full `vf` before commit |
| Large repo, one module edited | `vf --affected` runs only that module's tests; the full `vf` follows before commit |
| Tests are `unittest` and pytest is broken | `vf` falls back to `unittest discover`; `vf --detect` shows the pytest error that caused it |

### debugging-systematically

**What it does:** stops guess-and-edit debugging. The agent classifies the failure first, follows that layer's procedure, and records the bug in an append-only journal (`notes/debug.jsonl`, committed) so a recurring bug is recognised.

| Layer | Procedure |
|---|---|
| **code** (exception, wrong logic) | Reproduce with `run`, find the zone from the log (`rm --zone-from-log`), write a failing test first, fix, full `vf` |
| **data** (bad or shifted inputs) | Inspect the real inputs first (schema, counts, a few samples) before touching code |
| **llm** (wrong or invalid model output) | Capture the verbatim prompt, raw output and failing field; turn it into a prompt test case; fix the prompt, never loosen the parser |

**Commands (`dbg`):**

| Command | Purpose |
|---|---|
| `dbg open --layer code\|data\|llm --symptom "…" [--repro "<cmd>"]` | Start a bug (`B-001`); prints similar past bugs, a repeat is a WARN (exit 2) |
| `dbg hypo B-001 "…" [--result confirmed\|rejected]` | Record one hypothesis and what testing it showed |
| `dbg close B-001 --cause "…" [--fix <sha>]` | Close with the root cause; without `--cause` it BLOCKs (exit 1) |
| `dbg list [--open] [--layer L]` | Show bugs |
| `dbg similar "<text>"` | Keyword overlap with past bugs |
| `dbg llm-case B-001 --spec prompts/x.spec.json --vars '{…}' --check '{…}'` | Append a test case to the crafting-prompts spec (vars must match the declared inputs; check types are validated) |

**Scenarios**

| Situation | What happens |
|---|---|
| Test fails with a traceback | `dbg open --layer code`; `run` the repro; `rm --zone-from-log` lists the files that matter; a failing test is written before the fix |
| Retrieval returns nothing for Vietnamese queries | `--layer data`: inspect the documents (encoding, normalisation, counts) before changing the retriever |
| The model invents a price | `--layer llm`: `dbg llm-case` adds the failing question with `not_contains` as a case; the prompt spec is changed and `pk test` re-run |
| Same error as three weeks ago | `dbg open` prints the earlier bug and its cause as a WARN; the agent checks that cause first |
| You try to close without knowing why | `dbg close` BLOCKs until a root cause is stated |

### economizing-context

**What it does:** two tools that keep the context small.

**`repomap.py`** shows a repo's shape without reading file contents:

```
$ rm --depth 3
proj/  214 files · ~180k tok of text
src/  (120 files, ~95k tok)
  vimed/  (118 files, ~94k tok)
    retrieval/  (14 files, ~21k tok)
data/  (6 files, ~60k tok, 3 binary)
…
$ rm --symbols src/vimed/retrieval/hybrid.py
    3-41    class HybridRetriever
    4-9       def __init__(self, dense, sparse, alpha)
   11-41      def search(self, query, k)
  → read a symbol with: sed -n 'START,ENDp' src/vimed/retrieval/hybrid.py
```

**Blast radius, `rm --zone` (Python):** before editing a module, planning a refactor or debugging from a traceback, ask which files can be affected. The graph comes from `ast` (nothing is executed; `import a.b`, `from a import b`, relative imports and `src/` layouts are resolved).

```
$ rm --zone faq/search.py --hops 2
SEEDS  faq/search.py
dist  dir  path  lines  ~tok   (↑ imported-by · ↓ imports)
   1  ↑    faq/__main__.py  38  ~371
TESTS
  tests/test_search.py
zone 2 of 17 .py files · ~900 tok
```

`rm --zone-from-diff` uses the changed and untracked files as seeds; `rm --zone-from-log run.log` uses the repo files in a Python traceback; `--direction imports|importers|both`, `--hops N`, `--json` for other scripts. It prints paths, line counts and ~tokens only; the agent then reads zone files with `rm --symbols` and a line range. Dynamic imports (`importlib.import_module(name)`) are reported as `NOTE:` because they cannot be traced. Non-Python repos exit 2 with "zone supports Python only".

Also `rm --big` lists the most expensive files (never read these whole). It honors `.gitignore` and skips `node_modules`, venvs, caches, `wandb`, `mlruns`, `checkpoints` and binaries.

**`run.py`** runs any command, writes the full output to `.agentkit/logs/`, and prints only status, exit code, duration, unique error lines, the last traceback and the tail. Outputs of 60 lines or fewer are shown whole. It exits with the command's own code.

```
$ run --name train -- python train.py --config cfg.yaml
FAILED exit 1 · 412.3s · 18,240 lines · log: .agentkit/logs/train-20261023-1830.log
ERRORS (2 unique):
 18231: RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB
LAST TRACEBACK:
…
TAIL (20):
…
```

**Scenarios**

| Situation | Without the skill | With it |
|---|---|---|
| Agent explores a new repo | Opens file after file | `rm` → `rg` → `rm --symbols` → reads one line range |
| `pip install -r requirements.txt` | Hundreds of "Collecting…" lines in context | One status line, or the error |
| Training run with per-step logs | 10k+ lines in context for the rest of the session | Error + traceback + tail; grep the log for more |
| Test suite with verbose output | Whole log repeated on every later turn | The failing tests only |
| About to change `retrieval/hybrid.py` | Greps for callers, opens each | `rm --zone retrieval/hybrid.py`: importers, imports and their tests, as paths |

### tracking-experiments

**What it does:** records every training/eval/benchmark run (git SHA, branch, uncommitted-change count, config, params, metrics, duration, exit code, log) in `experiments/runs.jsonl`, so questions about results are answered from data.

**Commands**

| Command | Purpose |
|---|---|
| `ex run --name dense-bge --config cfg.yaml --metrics out/metrics.json [--tag baseline] -- python train.py …` | Run and record |
| `ex log --name bm25-only --param retriever=bm25 --metric f1=0.61 --note "colab"` | Record a run done elsewhere |
| `ex list [--sort eval.f1] [--cols eval.f1,recall@5] [--where train.lr=0.0003] [--name dense]` | Table of runs (`*` = uncommitted changes at run time) |
| `ex compare E-0003 E-0007` | Params that differ, metric deltas, code diff hint |
| `ex best --metric eval.f1 [--asc]` | Best run, its params and how to reproduce it |
| `ex show E-0007` | Full record |

Your training script only has to write its final metrics to a JSON/YAML file (or a `.jsonl` whose last line is the final metrics). Nested config keys are flattened (`train.lr`).

**Scenarios**

| Situation | You type | What happens |
|---|---|---|
| Launch a run | `train with lr 3e-4` | `ex run … --param train.lr=0.0003 -- python train.py …` |
| Uncommitted code during a run | (automatic) | WARN: the run cannot be reproduced from its SHA; the agent suggests committing first |
| "Which config gave the best F1?" | `? best f1 so far` | `ex best --metric eval.f1` with the run id and reproduce command |
| "Why did E-12 beat E-9?" | `? compare E-0009 E-0012` | Param diff and metric deltas; points to `git diff` if the code changed |
| Results from Colab / a GPU server | `log this run: f1 0.81, lr 1e-4, colab` | `ex log …` |

### crafting-prompts

**What it does:** builds prompts for the **target model your code calls** (not the agent) from a JSON spec. The script finds missing information and turns it into precise questions, renders a layout suited to the model family, lints the wording, and tests cases against an API.

**Workflow:** `pk init <name> --target <t>` → agent fills `prompts/<name>.spec.json` from your request → `pk check` (missing fields become "ASK THE USER" questions, asked in one message) → `pk render` → `pk lint` → `pk test`.

**Targets** (`pk targets`; add your own in `prompts/profiles.json`):

| Target | Use for | Rendering differences |
|---|---|---|
| `claude` | Claude models | XML-tagged sections; long documents before the question |
| `gpt` | GPT-style chat models | Markdown sections |
| `open-small` | Qwen, Llama, Gemma, Vistral, other small open models | Examples required; ≤8 numbered rules; format restated at the end |
| `reasoning` | Models that reason internally | Warns on "step by step"/"think carefully" |
| `generic` | Unknown / mixed | Plain markdown |

**Output:** `prompts/<name>.<target>.md` with a `=== SYSTEM ===` part and a `=== USER ===` part containing `{{placeholders}}` your code fills in.

**Lint checks:** token estimate (Vietnamese-aware), vague words ("appropriate", "relevant", "etc", "phù hợp"), ALL-CAPS emphasis overuse, prohibition-heavy rules, missing output format, duplicated lines, phrases that hurt the chosen target.

**Testing:** `pk test prompts/<name>.spec.json --model <m>` uses `$OPENAI_API_KEY` (any OpenAI-compatible server: OpenAI, OpenRouter, vLLM, Ollama, LM Studio via `--base-url`) or `--provider anthropic` with `$ANTHROPIC_API_KEY`. Check types: `contains`, `not_contains`, `regex`, `equals`, `max_words`, `max_chars`, `json_keys`, `valid_json`. Outputs are saved to `prompts/runs/`.

**Scenarios**

| Situation | You type | What happens |
|---|---|---|
| New RAG prompt | `write the answer prompt for ViMed-RAG, model is Qwen2.5-7B` | `pk init medical-qa --target open-small`; asks for anything missing (output schema, examples, test cases); renders, lints, tests |
| Porting a prompt to another model | `make this prompt work for Claude` | Same spec, `pk render --target claude`, re-run tests |
| A prompt gives bad answers | `case 2 returns an answer when the docs don't contain one` | Adds or adjusts an instruction and an example in the spec, re-renders, runs `pk test --case 2` |
| Reviewing an existing prompt file | `review prompts/old.txt` | `pk lint prompts/old.txt --target gpt` and concrete fixes |
| No API key | — | Spec, check, render and lint still work; testing is skipped and the agent says so |

---

## 6. Side questions without polluting the task context

| Question type | Do this | Effect on the task context |
|---|---|---|
| Short, about what the agent already knows | `? …` in the same chat; in Claude Code, `/btw …` | Tiny / none (`/btw` is not saved to history and has no tools) |
| Needs code reading or a long discussion | New chat (any agent) → `nt brief` → discuss → `chốt …` → `nt decide`. Back in the task chat: `nt show --topic <topic>` | None; only the decision comes back |
| Claude Code only | `/branch <name>` for the discussion, `/resume <task-session>` to return | None; the original session is untouched |

Rule of thumb: for one or two exchanges, just ask in place. Split the chat when the side discussion would add thousands of tokens that the task does not need.

## 7. Configuration reference

`.agentkit.json` in the project root. Every key is optional; see `.agentkit.example.json` for all defaults.

| Key | Default | Meaning |
|---|---|---|
| `git.protected_branches` | `main, master, develop, release/*` | Branches that need care |
| `git.protected_commit` | `ask` | `ask` (WARN, needs your yes) · `allow` · `block` |
| `git.commit_types` | Conventional Commits types | Allowed commit types |
| `git.scopes`, `git.strict_scopes` | `[]`, `false` | Allowed scopes; strict → BLOCK on unknown scopes, else WARN |
| `git.subject_max`, `git.body_wrap` | 72, 100 | Commit message limits |
| `git.branch_pattern`, `git.branch_types` | `{type}/{ticket}{slug}` | Branch naming |
| `git.max_file_mb` | 5 | Block larger files from commits |
| `git.diff_ignore` | lockfiles, notebooks, build output | Never shown as diffs to the agent |
| `notes.dir`, `notes.commit_tasks` | `notes`, `false` | Notes location; whether task notes are committed |
| `notes.max_decision_chars/lines` | 600 / 10 | Decision size limit |
| `notes.base_branch` | auto | Branch used by `nt sync` to detect merges |
| `context.max_output_lines` | 60 | `run.py` prints outputs up to this length whole |
| `context.ignore_dirs` | `[]` | Extra directories for `repomap.py` to skip (e.g., `"data"`) |
| `context.zone_max_files` | 40 | Zone file lines `rm --zone` prints before "… +k more" |
| `debug.min_similarity` | 0.4 | Keyword overlap (0–1) at which `dbg` calls two bugs similar |
| `verify.format/lint/typecheck/test` | `auto` | `auto`, a command, or `null` to skip |
| `experiments.dir`, `experiments.default_metrics` | `experiments`, `[]` | Run log location; columns for `ex list` |
| `prompts.dir/provider/model/base_url/key_env` | `prompts`, `openai`, — | Defaults for `pk test` |
| `prompts.max_tokens_warn` | 3000 | Lint warning threshold |

## 8. Using other agents

- **Rules:** most agents read `AGENTS.md` at the repo root. Claude Code reads `CLAUDE.md`, which imports `AGENTS.md`.
- **Skills:** `AGENTS.md` refers to scripts by path under `.agentkit/skills/`, so any agent that can run a terminal command can use them. Agents that support the SKILL.md format natively can also be pointed at `.agentkit/skills/`.
- **Read-only mode:** for a series of questions, use your agent's read-only mode on top of the `?` prefix (Claude Code plan mode, Cursor Ask mode).
- **Claude-Code-only extras:** `/task` and `/grill` as slash commands, the optional SessionStart hook (`install.py --hooks`), `/btw`, `/branch`/`/resume`, `opusplan`. Everything else is agent-neutral.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| A skill does not trigger | Name it in the request ("use managing-git to commit") or check `validate_skills.py`. In Claude Code you can also call `/managing-git` |
| `verify.py` says "tool missing" | Install the tool declared by the project, or set the gate's command in `.agentkit.json` |
| `gitkit preflight merge` cannot predict conflicts | Needs git ≥ 2.38 (`merge-tree --write-tree`) |
| YAML config in `ex run` fails | `pip install pyyaml`, or use a JSON config |
| `nt brief` shows no task | You are on a branch without a task note (e.g., `main`); it lists open tasks to switch to |
| Windows: no symlinks | The installer copies instead; re-run it after updating the kit |
| Windows: garbled Vietnamese or `UnicodeEncodeError` | Update the kit: every script now switches stdout/stderr to UTF-8; `python tools/selftest.py --cp1252` checks it |
| `vf` says `NOTHING VERIFIED` though tests exist | `vf --detect` shows the reason (for example `pytest not runnable: …`); set `verify.test` in `.agentkit.json` if detection is wrong |
| `pk test` cannot connect | Check `--base-url` (include `/v1` for OpenAI-compatible servers) and that the key env var is set |

## 10. Adding your own skill

1. Create `skills/<gerund-name>/SKILL.md` with frontmatter: `name` (equals the folder name, lowercase-hyphen) and `description` (third person, what it does + "Use when …", ≤1024 characters). The description is the only part that is always loaded, so make every word count.
2. Keep the body under ~150 lines. Move detail to `reference/*.md` (one level deep, linked from SKILL.md).
3. Put rule-based logic in `scripts/` (stdlib only, `--help`, exit codes 0/1/2, explicit error messages that say how to fix). Let the model handle only what needs judgment.
4. Run `python tools/validate_skills.py`, then `python tools/install.py <project>`.

## Credits

Patterns adapted from Anthropic's skill authoring best practices (progressive disclosure, degrees of freedom, utility scripts, validation loops), the `anthropics/skills` repository (SKILL.md format), `obra/superpowers` (plan-then-execute, verify before claiming done) and community commit skills (`awesome-copilot` conventional-commit, `principled-git-commit`).
