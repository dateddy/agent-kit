# agent-kit

A shared skill set for software and AI projects worked on with coding agents. **Claude Code is fully supported** (slash commands, SessionStart and guard hooks, plugin settings). **Other agents** (Codex, Cursor, Gemini CLI, Copilot CLI, …) get `AGENTS.md` plus the git hooks, and the scripts when they can run a terminal command.

It is built around three goals:

- **Spend fewer tokens.** The agent reads maps, outlines and summaries instead of whole files and full logs.
- **Engineering over judgment.** Anything that has a rule (commit format, branch policy, secrets, plan completeness, test results) is decided by a Python script, not by the model.
- **Automate the repetitive parts.** Starting tasks, planning, verifying, debugging, remembering what was decided.

All skill scripts are Python 3.9+ standard library only. The optional [guards](#13-guards-scripts-that-always-run-v030) (`scripts/`) are bash wrappers around one Python script (`guard.py`): the `PreToolUse` guards need `bash` and Python, `verify.sh`, `pre-push` and the installer's guard step also need `git` and `jq`; `ruff`, `pytest`, `pytest-cov` and `diff-cover` are used only when present and configured.

---

## Contents

1. [How it works](#1-how-it-works)
2. [Install](#2-install)
3. [A complete task, start to finish](#3-a-complete-task-start-to-finish)
4. [Conventions you will see everywhere](#4-conventions-you-will-see-everywhere)
5. [Skills](#5-skills): [writing-plans](#writing-plans) · [keeping-notes](#keeping-notes) · [verifying-changes](#verifying-changes) · [debugging-systematically](#debugging-systematically) · [economizing-context](#economizing-context)
6. [Side questions without polluting the task context](#6-side-questions-without-polluting-the-task-context)
7. [Configuration reference](#7-configuration-reference)
8. [Using other agents](#8-using-other-agents)
9. [Troubleshooting](#9-troubleshooting)
10. [Adding your own skill](#10-adding-your-own-skill)
11. [Git: the plugin and two git hooks](#11-git-hooks)
12. [Benchmark: does the kit help?](#12-benchmark-does-the-kit-help)
13. [Guards: scripts that always run](#13-guards-scripts-that-always-run-v030)

Release history and the 0.4 → 0.5 migration: [CHANGELOG.md](CHANGELOG.md).

---

## 1. How it works

A **skill** is a folder with a `SKILL.md` (instructions for the agent) and optional `scripts/` and `reference/` files. Every skill is core: the installer copies all of them.

```
agent-kit/
├── skills/
│   ├── writing-plans/         /task: Frame (context, gaps, decisions), lite path, executor-ready plans (plan.py)
│   ├── keeping-notes/         task state per branch, decision log, knowledge.md (notes.py)
│   ├── verifying-changes/     one PASS/FAIL verdict from lint/typecheck/test (verify.py)
│   ├── debugging-systematically/  code / data / llm debugging + bug journal (dbg.py)
│   └── economizing-context/   repo map, import-graph zones, capped command runner (repomap.py, run.py)
├── scripts/                   gitkit.py (the two git hooks) and the guards: guard.py, guard-git.sh, guard-fs.sh, py.sh,
│                              verify.sh, pre-push, plan-step.sh, ruff-fix.sh, lib.sh, test-kit.sh + tests/
├── templates/                 AGENTS.md, AGENTS.rules-only.md, CLAUDE.md, commands/task.md, claude-hooks.json
├── tools/                     install.py, validate_skills.py, selftest.py, fp_sweep.py, gen_shipped_hashes.py, bench/
├── .agentkit.example.json     default configuration
├── FAILURES.md                log of corrected agent mistakes and the control that now prevents each
├── BACKLOG.md                 planned work for the next release
└── VERSION                    0.5.0
```

Token cost is layered (progressive disclosure):

| Layer | When it is in context | Cost |
|---|---|---|
| `name` + `description` of each skill | Always | ~85–100 tokens per skill, ~470 for the five (`validate_skills.py` prints the total and the v0.4.0 baseline) |
| `SKILL.md` body | Only when the task matches | ~250–1000 tokens |
| `reference/*.md` | Only when that detail is needed | varies |
| `scripts/*.py` | Never; they are run, and only their output enters context | output only |

`AGENTS.md` (always loaded, ~45 lines) holds the cross-cutting rules: question vs task mode, the task lifecycle, git, knowledge, exit-code handling and token discipline.

## 2. Install

```bash
git clone <this repo> agent-kit
python agent-kit/tools/install.py path/to/your-project                       # all skills, AGENTS.md, /task, plugin setting
python agent-kit/tools/install.py path/to/your-project --git-hooks           # plus pre-commit and commit-msg git hooks
python agent-kit/tools/install.py path/to/your-project --guards              # plus the guards (needs bash, python, jq)
```

**Prerequisites:** Python 3.9+ and git. `--guards` also needs `bash` and **`jq`** on bash's PATH (check: `bash -c 'command -v jq'`); the installer refuses `--guards` without them. See [Guards](#13-guards-scripts-that-always-run-v030).

| Flag | Effect |
|---|---|
| `--git-hooks` | Install `pre-commit` (`gitkit.py scan --staged`) and `commit-msg` (`gitkit.py lint-msg`), copying `gitkit.py` to `.agentkit/scripts/`; re-running rewrites the kit's block in place; see [Git](#11-git-hooks) |
| `--uninstall-git-hooks` | Remove only the blocks the kit added to the git hooks |
| `--hooks` | Claude Code SessionStart hook that runs `notes.py brief` (below) |
| `--guards` | The bash guards, their Claude Code hooks (`.claude/settings.local.json`) and a `pre-push` git hook; see [Guards](#13-guards-scripts-that-always-run-v030) |
| `--team` | Write the Claude Code settings (the plugin, and with `--guards` the hooks) to the shared `.claude/settings.json` instead of the uncommitted `.claude/settings.local.json` |
| `--force` | Overwrite existing `.claude/skills/<name>` entries that are not links |
| `--user` | Skills only, into `~/.claude/skills` |
| `--with …` | **Removed in 0.5.0** (exit 1): every skill is core now |

What it does (safe to re-run; it never overwrites your files):

| Path in your project | Purpose |
|---|---|
| `.agentkit/skills/<skill>/` | Canonical copy of the skills (agent-neutral; `AGENTS.md` points here) |
| `.claude/skills/<skill>` | Symlink to the above so Claude Code discovers the skills (copied if symlinks are unavailable) |
| `.claude/commands/task.md` | The `/task` command for Claude Code |
| `.claude/settings.local.json` (`settings.json` with `--team`) | `enabledPlugins` for `commit-commands@claude-plugins-official`; the installer prints `claude plugin install commit-commands@claude-plugins-official --scope project`, which each person runs once |
| `AGENTS.md`, `CLAUDE.md` | Rules for every agent (created if missing; the part above the `Project-specific rules` marker is regenerated, yours is never touched); `CLAUDE.md` is `@AGENTS.md` plus Claude Code extras |
| `.agentkit.json` | Configuration (created if missing) |
| `notes/decisions.md`, `notes/knowledge.md`, `.gitignore` rule for `notes/tasks/` | Notes layout; `knowledge.md` starts as a header |

Commit `.agentkit/skills`, `.claude/`, `AGENTS.md`, `CLAUDE.md`, `.agentkit.json`, `notes/decisions.md` and `notes/knowledge.md`. Logs (`.agentkit/logs/`) ignore themselves.

**Upgrading from 0.4.0:** pull the kit and run `install.py` again (add `--git-hooks` if you use them). Skills and the `/grill` command the kit no longer ships (`crafting-prompts`, `tracking-experiments`, `grilling-designs`, `managing-git`, `grill.md`) are deleted when every file still matches what the kit shipped; an edited copy, or one your own rules in `AGENTS.md` still call (`ex`, `pk`, `gk`), is kept with a WARN. `experiments/` and `prompts/` are never touched. See the migration notes in [CHANGELOG.md](CHANGELOG.md).

**Optional SessionStart hook (Claude Code only):** `--hooks` merges a hook into `.claude/settings.json` that runs `notes.py brief` when a session starts, resumes, clears or compacts. It never overwrites existing keys, and leaves a `settings.json` it cannot parse untouched (exit 2). Trade-off: one fewer turn per session versus a few hundred tokens at every session start. Without `--hooks` the rule in `AGENTS.md` ("start of session: `nt brief`") works in every agent.

Check the skills and their token cost: `python agent-kit/tools/validate_skills.py`. Prove the kit works on your machine: `python agent-kit/tools/selftest.py` (`--cp1252` simulates a Windows console); it builds throwaway git repos, including a real 0.4.0 install from the `v0.4.0` tag (so the clone needs the tags). CI runs the validator and both selftests on Ubuntu, Windows and macOS with Python 3.9 and 3.13.

## 3. A complete task, start to finish

The loop is: **load context → grill (only the gaps) → plan, or the lite path → execute under the guards and hooks → close the task.** Example: add a BM25 fallback to a RAG retriever, in Claude Code.

**You:** switch to plan mode (`Shift+Tab`, model `opusplan`) and type

```
/task Add BM25 as a fallback when the dense retriever returns nothing.
Constraints: keep Retriever.search() signature, use rank_bm25.
Done when: a query with a rare drug name returns at least 1 passage; existing tests pass.
```

**Agent (planning model, read-only):**
1. *Load context*, in proportion to the task: `nt brief` (task, live decisions, `knowledge.md`), grep; for a large task also `rmap --zone` on the files and `git log --oneline -10`.
2. *Grill, only the gaps.* Every unclear point is looked up, or becomes an `Assumptions:` default, or is a must-ask. The small branch (goal, scope edges, inputs and edge cases, callers, Done when) runs for every task. The architecture branch (new dependency, schema, public API, new module, concurrency, auth, infra) walks `reference/decision-tree.md`; each answer is recorded with `nt decide` at once. Questions come in rounds of at most 5, each with a recommendation, a reason and what was checked.
3. *Plan:* 2–8 steps, each with files, contract, tests and a verify command. A change of 2 files or fewer with no architecture decision takes the **lite path** instead: the frame and assumptions in 1–3 lines, no plan file, assumptions repeated in the commit body under `Assumptions:`.

**You:** read it, ask for a change if needed, then accept.

**Agent (execution model):**
1. `git switch -c feat/bm25-fallback`, saves the plan to the path from `pl path`, runs `pl check` and `pl apply` (the steps become the task note's checklist).
2. For each step: `pl step N` → tests first → implement → `vf` → `nt task --check N --done "…" --next "…"` → `/commit`. The guards and git hooks run on the way: unsafe git commands are asked or blocked, `pre-commit` scans for secrets, `commit-msg` lints the message, the `Stop` hook runs `verify.sh`.
3. Final `vf` → `VERDICT: PASS` → reports done with the verdict line. If it took 3 or more tries to find something about the environment, one line goes into `notes/knowledge.md`.

**Next morning, new chat, any agent:** type `continue`. The agent runs `nt brief`, sees "step 3 of 3, next: write the merge test", and continues with ~30 lines of context instead of yesterday's conversation.

## 4. Conventions you will see everywhere

**Exit codes (every script):** `0` OK → continue · `2` WARN → show you the warning and the next command, continue only after your explicit yes · `1` BLOCK → stop, fix the cause, re-run, never bypass.

| You type | Mode |
|---|---|
| `? why is the reranker slow` | Question mode: read-only. No edits, commits or note updates |
| `/task …` or `task: …` | Start a task: writing-plans, Frame first |
| `add caching to the reranker` | Task mode |
| Something ambiguous | Treated as a question; the agent offers to do it |

| Path | Content | In git? |
|---|---|---|
| `notes/tasks/<branch>.md` | Task note: goal, plan checklist, next step, log | No (personal) |
| `notes/tasks/<branch>.plan.md` | The accepted plan | No |
| `notes/decisions.md` | Append-only decision log | Yes |
| `notes/knowledge.md` | Repo and environment facts, one line each | Yes |
| `FAILURES.md` | Agent mistakes you corrected, with the control that prevents each | Yes |
| `.agentkit/logs/` | Full command/test/run logs | No |

---

## 5. Skills

Short names used below (all are `python .agentkit/skills/<skill>/scripts/<file>`):
`nt` notes.py · `pl` plan.py · `vf` verify.py · `run` run.py · `rmap` repomap.py · `dbg` dbg.py. You rarely type these yourself; the agent runs them.

### writing-plans

**What it does:** frames a task, then produces a plan a cheaper model can execute without exploring the repo or making design decisions, and validates it with `pl check` before any code is written. Plan mode is the interface; the plan file is the contract. **Invoke:** `/task <request>` (Claude Code), or `task:` / a plain change request in other agents.

**Frame (always runs, asks only what is still open):** load context in proportion to the task; sort every unclear point (look up / assume / must ask); the small branch for every task; the architecture branch only when the task touches a listed area; ask in rounds of at most 5 questions; `nt decide` after every architecture answer. The decision tree, question format and stop rule ("enough" turns open questions into assumptions) are in `skills/writing-plans/reference/decision-tree.md`. An open task on the branch is offered for continuing.

**Plan format** (`pl template`):

```
# Plan: <one-line outcome>
Constraints: …
Done when: …
Out of scope: …
Decisions: D-3a7f, D-91c2          (optional, from nt decide)

### Step 1: <step title>
- Files: src/retrieval/bm25.py (new), tests/test_bm25.py (new)
- Contract: BM25Index(docs).search(query, k) -> list[str] using rank_bm25.BM25Okapi
- Tests: test_search_rare_term, test_search_empty_query
- Out of scope: src/retrieval/hybrid.py
- Verify: python -m pytest tests/test_bm25.py -q        (optional)
```

`Mode:` lines are ignored (`pl check` WARNs "Mode is ignored"); the old `## 1.` / Do / Done shape still parses with a WARN.

| `pl check` rule | Result |
|---|---|
| Every step has Files, Contract, Tests, Out of scope | BLOCK if missing |
| Every listed file exists, or is marked `(new)` | BLOCK otherwise (no hallucinated paths) |
| No `TODO`, `TBD`, `???`, `<placeholder text>`; steps numbered 1..N | BLOCK |
| `Decisions:` id not in `notes/decisions.md` | BLOCK; a superseded id WARNs and names the replacement |
| Manual verification, no tests, glob in Files, more than 8 steps or a step over 800 characters | WARN |

**Commands:** `pl template` · `pl path` · `pl check` · `pl apply` (load steps into the task note; replaces the old plan and logs its progress) · `pl step N` (only step N, for the executor) · `pl field NAME [--cumulative] [--step N]` (one field of the active step; `scripts/plan-step.sh` is a wrapper around it). The **active step** is the first unchecked item of the task note's `## Plan` checklist.

### keeping-notes

**What it does:** keeps task state in files instead of chat history, so any session (any agent, any model, after `/compact`, next week) can resume from a short brief.

- `notes/tasks/<branch>.md`: one note per task branch (goal, plan checklist, next action, files, log). Personal, gitignored.
- `notes/decisions.md`: project-wide, append-only, committed. Ids are `D-3f9a` (4 random hex, so branches never collide). To change a decision add a new one with `--supersedes D-007`.
- `notes/knowledge.md`: committed, one line per fact: `- <fact>; <right way> (verified YYYY-MM-DD: <command or evidence>)`. The agent adds a line after 3 or more tries to find the right way with the environment or a tool, or after you correct a wrong claim about the repo, and says so in one line. Not for bugs (`dbg`), decisions or agent mistakes (`FAILURES.md`).

| Command | Purpose |
|---|---|
| `nt init` | Create the layout (the installer does this) |
| `nt brief [--topic T]` | ~30-line pack: task, plan, next, recent log, uncommitted files, `KNOWLEDGE` facts (WARN "consider scoped format" above `notes.max_knowledge_facts`, default 20; exit stays 0), relevant decisions |
| `nt task --goal "…" --plan "…" --plan "…"` · `nt task --check 2 --done "…" --next "…"` · `nt task --status blocked --next "…"` | Create the note · record step progress · mark blocked |
| `nt decide --topic retrieval --text "…" --why "…" [--supersedes D-003]` | Record a decision (≤10 lines / 600 chars, enforced) |
| `nt show --topic T` · `--id D-007` · `--all` | Read decisions (active ones by default) |
| `nt tasks [--all]` · `nt close [--status abandoned]` · `nt sync` | List · close · auto-close tasks whose branch was merged or deleted |

### verifying-changes

**What it does:** stops the agent from saying "done" without evidence. `vf` detects the project's quality gates, runs them, and prints one verdict with only the failing lines.

```
format     SKIP   not detected
lint       PASS   ruff check --output-format concise .   0.8s
test       FAIL   python -m pytest -q                   12.3s  log: .agentkit/logs/verify-test-….log
--- test (5 lines; full log …)
FAILED tests/test_hybrid.py::test_merge - AssertionError: expected fallback doc
VERDICT: FAIL (exit 1) — fix the cause, re-run verify. Do not report the task as done.
```

The verdict line carries its exit code (`PASS (exit 0)`, `FAIL (exit 1)`, `NOTHING VERIFIED (exit 2)`, `PASS WITH TEST CHANGES (exit 2)`) because agents lose exit codes through pipes. **Never pipe `vf`**; the guards block it.

**Auto-detection:** ruff / flake8 / black / mypy / pyright; tests in `tests/` even with no config (pytest, else `unittest discover`, and `vf --detect` says why); `package.json` scripts and `tsc`; `go vet`/`go test`; `cargo clippy`/`cargo test`; Makefile targets. Override any gate in `.agentkit.json`. **Test-integrity guard:** against `HEAD` it flags deleted test files, new `skip`/`xfail` markers and removed assertions (`PASS WITH TEST CHANGES`). **Inner loop:** `vf --affected` runs only the tests `rmap --zone-from-diff` says can be affected; a full `vf` is still required before a commit and before saying "done".

**Commands:** `vf` · `vf --only lint,test` · `vf --affected` · `vf --detect` · `vf --no-guard`.

### debugging-systematically

**What it does:** stops guess-and-edit debugging. The agent classifies the failure first (code, data, llm), follows that layer's procedure (`reference/layers.md`) and records the bug in an append-only journal so a recurring bug is recognised. It is a core skill: when something fails, `dbg open` comes first.

| Command | Writes | Where |
|---|---|---|
| `dbg open --layer code\|data\|llm --symptom "…" [--repro "<cmd>"]` | appends an `open` event (prints similar past bugs; a repeat is a WARN) | `notes/debug.jsonl` |
| `dbg hypo B-001 "…" [--result confirmed\|rejected]` | appends a `hypo` event | `notes/debug.jsonl` |
| `dbg close B-001 --cause "…" [--fix <sha>]` | appends a `close` event (no `--cause` is a BLOCK, nothing written) | `notes/debug.jsonl` |
| `dbg list [--open] [--layer L]` | nothing (reads) | |
| `dbg similar "<text>"` | nothing (reads) | |
| `dbg llm-case B-001 --spec <spec.json> --vars '{…}' --check '{…}'` | appends a test case to your prompt spec, then an `llm-case` event | the `--spec` file (rewritten) and `notes/debug.jsonl` |

The journal path follows `notes.dir`. `llm-case` expects a JSON spec with `inputs` and `cases`; the kit no longer ships the tool that made such specs, so the spec and its test runner are the project's own.

### economizing-context

**What it does:** two tools that keep the context small. **`repomap.py`** shows a repo's shape without reading file contents (`rmap --depth 3`, `rmap --symbols <file>` then read one line range). **`rmap --zone <file>`** (Python) lists the blast radius from the `ast` import graph: importers, imports and their tests, as paths and token estimates; `--zone-from-diff` and `--zone-from-log run.log` seed it from your changes or a traceback; `--hops N`, `--direction`, `--json`. `rmap --big` lists the most expensive files. **`run.py`** runs any command, writes the full output to `.agentkit/logs/`, and prints only status, exit code, duration, unique error lines, the last traceback and the tail (outputs of 60 lines or fewer are shown whole); it exits with the command's own code.

```
$ run --name train -- python train.py --config cfg.yaml
FAILED exit 1 · 412.3s · 18,240 lines · log: .agentkit/logs/train-20261023-1830.log
ERRORS (2 unique):
 18231: RuntimeError: CUDA out of memory. Tried to allocate 2.00 GiB
```

---

## 6. Side questions without polluting the task context

| Question type | Do this | Effect on the task context |
|---|---|---|
| Short, about what the agent already knows | `? …` in the same chat; in Claude Code, `/btw …` | Tiny / none (`/btw` is not saved to history and has no tools) |
| Needs code reading or a long discussion | New chat → `nt brief` → discuss → record with `nt decide`. Back in the task chat: `nt show --topic <topic>` | None; only the decision comes back |
| Claude Code only | `/branch <name>` for the discussion, `/resume <task-session>` to return | None; the original session is untouched |

## 7. Configuration reference

`.agentkit.json` in the project root. Every key is optional; see `.agentkit.example.json` for all defaults.

| Key | Default | Meaning |
|---|---|---|
| `git.protected_branches` | `main, master, develop, release/*` | Branches the guards protect |
| `git.protected_commit` | `ask` | `ask` (you approve) · `allow` · `block` for a commit or push to a protected branch |
| `git.commit_types`, `git.scopes`, `git.strict_scopes` | Conventional Commits types, `[]`, `false` | `commit-msg` hook: allowed types and scopes (strict → BLOCK on an unknown scope, else WARN) |
| `git.subject_max`, `git.body_wrap` | 72, 100 | Commit message limits |
| `git.max_file_mb` | 5 | `pre-commit` hook: block larger files |
| `notes.dir`, `notes.commit_tasks` | `notes`, `false` | Notes location; whether task notes are committed |
| `notes.max_decision_chars/lines` | 600 / 10 | Decision size limit |
| `notes.max_knowledge_facts` | 20 | `nt brief` warns above this many facts |
| `notes.base_branch` | auto | Branch used by `nt sync` to detect merges |
| `context.max_output_lines`, `context.ignore_dirs`, `context.zone_max_files` | 60, `[]`, 40 | `run.py` whole-output limit; extra dirs for `repomap.py` to skip; `rmap --zone` file lines |
| `debug.min_similarity` | 0.4 | Keyword overlap (0–1) at which `dbg` calls two bugs similar |
| `verify.format/lint/typecheck/test` | `auto` | `auto`, a command, or `null` to skip |
| `guards.*`, `fs.*` | see [Guards](#configuration-guards) | Stage switches, tool commands, `fail_under`, `destructive`, outside-repo writes, protected reads |

## 8. Using other agents

- **Rules:** most agents read `AGENTS.md` at the repo root. Claude Code reads `CLAUDE.md`, which imports it.
- **Git hooks:** `--git-hooks` gates any agent (or a human) with the secret scan and the commit-message lint; they need no Claude Code.
- **Skills:** `AGENTS.md` refers to scripts by path under `.agentkit/skills/`, so any agent that can run a terminal command can use them. Agents that support the SKILL.md format natively can also be pointed at `.agentkit/skills/`.
- **Read-only mode:** for a series of questions use your agent's read-only mode on top of the `?` prefix (Claude Code plan mode, Cursor Ask mode).
- **Claude-Code-only:** `/task`, the commit-commands plugin (`/commit`, `/commit-push-pr`), the SessionStart hook (`--hooks`), the guard hooks (`--guards`), `/btw`, `/branch`/`/resume`, `opusplan`. Everything else is agent-neutral.

## 9. Troubleshooting

| Problem | Fix |
|---|---|
| A skill does not trigger | Name it in the request ("use writing-plans") or check `validate_skills.py`. In Claude Code you can also call `/writing-plans` |
| `verify.py` says "tool missing" | Install the tool declared by the project, or set the gate's command in `.agentkit.json` |
| `nt brief` shows no task | You are on a branch without a task note (e.g., `main`); it lists open tasks to switch to |
| Windows: no symlinks | The installer copies instead; re-run it after updating the kit |
| Windows: garbled Vietnamese or `UnicodeEncodeError` | Update the kit: every script switches stdout/stderr to UTF-8; `python tools/selftest.py --cp1252` checks it |
| A commit is rejected by a git hook | Read the `BLOCK:`/`ERROR:` lines, fix the cause (remove the secret, reword the message) and commit again. Never `--no-verify` |
| A commit fails with "run install.py again" | The hook is from 0.4.0 or `.agentkit/scripts/gitkit.py` is missing: run `install.py <project> --git-hooks` |
| `/commit` is not found | Run `claude plugin install commit-commands@claude-plugins-official --scope project` once per machine |
| `vf` says `NOTHING VERIFIED` though tests exist | `vf --detect` shows the reason; set `verify.test` in `.agentkit.json` if detection is wrong |
| `install.py` says "removed in 0.5.0" for `--with` | Drop the flag; every skill is installed |

## 10. Adding your own skill

1. Create `skills/<gerund-name>/SKILL.md` with frontmatter: `name` (equals the folder name, lowercase-hyphen) and `description` (third person, what it does + "Use when …", ≤1024 characters). The description is the only part always loaded, so make every word count.
2. Keep the body under ~150 lines. Move detail to `reference/*.md` (one level deep, linked from SKILL.md).
3. Put rule-based logic in `scripts/` (stdlib only, `--help`, exit codes 0/1/2, error messages that say how to fix). Let the model handle only what needs judgment.
4. Run `python tools/validate_skills.py`, then `python tools/install.py <project>`.

## 11. Git hooks

Git workflow (commit, push, PR) belongs to the **`commit-commands@claude-plugins-official`** plugin: `/commit`, `/commit-push-pr`, `/clean_gone`. The installer enables it in the settings file (`enabledPlugins`; per the Claude Code docs the key only enables a plugin, each person still installs it with `claude plugin install … --scope project`). The kit keeps what the plugin cannot enforce for other agents: two git hooks and the [guards](#13-guards-scripts-that-always-run-v030).

```bash
python agent-kit/tools/install.py path/to/your-project --git-hooks
python agent-kit/tools/install.py path/to/your-project --uninstall-git-hooks
```

| Hook | Runs | Effect |
|---|---|---|
| `pre-commit` | `.agentkit/scripts/gitkit.py scan --staged` | A secret, a `.env`/key file or an oversized file in the staged changes rejects the commit |
| `commit-msg` | `.agentkit/scripts/gitkit.py lint-msg "$1"` | A message that is not a valid Conventional Commit rejects the commit; `Merge`, `Revert`, `fixup!`, `squash!` pass; a body may carry an `Assumptions:` block |

- Only exit code `1` (BLOCK) rejects the commit; a WARN (`2`) passes. If `gitkit.py` is missing the hook **fails loudly** ("run install.py again") instead of skipping the check; the same holds for a 0.4.0 hook once the old skill is gone (the installer leaves a small shim at the old path and WARNs).
- **Never overwrites a hook you already have.** The kit's block sits between `# >>> agent-kit >>>` and `# <<< agent-kit <<<` right after the shebang; re-running `--git-hooks` rewrites that block in place (no duplicate, your lines stay). A hook in another language is left untouched with a WARN.
- Honours `core.hooksPath`, works in Git for Windows' `sh`, and runs the Python that ran the installer (else `python3`, `python`, `py -3`). `--uninstall-git-hooks` removes only the kit's blocks.
- Agents must never use `--no-verify`: a hook BLOCK is fixed, not bypassed (`AGENTS.md`; the guards block it).

## 12. Benchmark: does the kit help?

`tools/bench/bench.py` runs the same tasks headless with Claude Code under three conditions and measures what changes. It only ever works in throwaway `git clone`s under the temp dir (it refuses to run inside the fixture or in this repo, and removes the clone's `origin`).

| Condition | The copy gets |
|---|---|
| `none` | `AGENTS.md`, `CLAUDE.md`, `.claude/`, `.agentkit/`, `.agentkit.json`, `notes/` removed |
| `rules` | `none` plus `templates/AGENTS.rules-only.md` (mode, token and safety rules, no scripts) |
| `kit` | `none` plus `install.py --git-hooks` |

```bash
python tools/bench/bench.py overhead --fixture path/to/repo --model sonnet --trials 3
python tools/bench/bench.py run --fixture path/to/repo --tasks tools/bench/tasks --conditions none,rules,kit \
       --trials 3 --model sonnet --max-turns 60 --max-budget-usd 2 --out bench/results.jsonl --only T1 --yes
python tools/bench/bench.py report bench/results.jsonl --csv bench/summary.csv
```

- **Cost:** `run` prints the worst case (sessions × `--max-budget-usd`) and exits 2 until you add `--yes`. `bench/` is gitignored.
- **Task folder** `tasks/<ID>/`: `task.md` (a line `---new-session---` starts a new `claude -p` session, which tests resuming from notes), `meta.json` (`max_turns_per_session`, `oracle_cmd`), `oracle/` (hidden tests, copied in after the agent finishes), optional `seed/`. A `{{FAKE_KEY}}` in `task.md` is replaced per run by a fresh fake key, counted by `secret_in_history`. `T1`–`T5` target the mini-faq-rag fixture.
- **Isolation:** sessions run with `--setting-sources project,local --strict-mcp-config`, so your own user-level skills and MCP servers do not leak into the comparison (`--no-isolate` to include them).
- **Recorded per run** (one JSON line): success (oracle exit 0), tokens (input / output / cache), cost, turns, duration, tool calls, `kit_calls`, loaded skills, and six violations: `secret_in_history`, `tests_modified`, `commits_on_protected`, `bad_commit_msgs`, `uncommitted_changes`, `no_verify_used`. `overhead` reports the fixed token price of the rules and descriptions; `report` prints per task and condition the success rate, median tokens, cost, turns and violations against `none`.

The selftest runs all of this against a fake `claude` on `PATH`; neither the selftest nor CI starts the real one.

## 13. Guards: scripts that always run (v0.3.0)

Rules in `AGENTS.md` and `SKILL.md` are advice the model can ignore. The guards are scripts in `scripts/` that **always run and can block**. Claude Code hooks and git hooks are thin wrappers that only call them, so the same checks work with any agent, or with none.

**Requirements:** `bash`, `git`, Python 3.9+ and `jq` (on Windows: Git for Windows' bash). `guard-git.sh` and `guard-fs.sh` wrap `guard.py` (standard library) and need no `jq`; `py.sh` finds the interpreter (python3, python, `py -3`; the Microsoft Store stub is skipped) and without Python they fail loudly (exit 2). `verify.sh` and `pre-push` read `.agentkit.json` with `jq`, so `install.py --guards` checks `bash -c 'command -v jq'` first and exits 1 without writing anything when it is missing (`winget install jqlang.jq`, `brew install jq`, `sudo apt install jq`). A stage whose tool (`ruff`, `pytest` + `pytest-cov`, `diff-cover`) is missing **fails with an install-or-disable message**, it never passes silently.

**Real failures these exist for** (details and the control for each: [FAILURES.md](FAILURES.md)):

| Failure | Control |
|---|---|
| The agent fast-forwarded a stage branch into `main`; the repo convention is a `--no-ff` merge commit | `guard-git.sh` blocks the command and prints how to inspect the convention; `pre-push` rejects the push even if the hook was bypassed; `AGENTS.md` asks for `git log --first-parent --merges -5 <target>` before any merge into a protected branch |
| Code drifted from the plan; transformations shipped untested | Plan steps are machine-readable; `verify.sh` fails on files outside the plan and on uncovered changed lines |
| The agent piped `vf` into `tail` and a failing step was committed | `guard-git.sh` blocks `verify.py`, `vf` and `verify.sh` piped into another command; a redirect to a file passes |
| A hook rewrote a file in another project | `ruff-fix.sh` leaves files of other repos alone; `guard-fs.sh` and `guard-git.sh` ask before the agent writes, edits or deletes a file outside the repo |

### What each script does

| Script | Runs from | Behavior |
|---|---|---|
| `guard-git.sh` | Claude Code `PreToolUse` (matcher `Bash`), or by hand: `guard-git.sh git merge --ff-only x` | Judges a shell command with `guard.py bash` ([rules below](#guard-rules)). Follows `git switch/checkout` and `cd` earlier in the line, `&&`/`;`/`\|`, `git -C`, env prefixes, `bash -c` and `eval` (three levels) |
| `guard-fs.sh` | Claude Code `PreToolUse` (matcher `Edit\|Write\|MultiEdit\|NotebookEdit`), or `guard-fs.sh path/to/file` | Judges the file about to change with `guard.py edit`: in the repo (or a worktree of it) passes; outside it `fs.allow_write` passes and the rest follows `fs.outside_repo_write` |
| `guard.py` | the two wrappers | The logic: tokenizer, rules, path resolution. `guard.py bash [COMMAND…]`, `guard.py edit [PATH]`; with no argument both read the Claude Code hook JSON from stdin |
| `gitkit.py` | the `pre-commit` and `commit-msg` git hooks | `scan --staged` and `lint-msg`; see [Git](#11-git-hooks) |
| `py.sh` | sourced by the wrappers; its `ak_find_py` is copied into the git hooks | The one place that finds Python |
| `pre-push` | git | For a push to a protected branch every commit on the pushed range's first-parent line must be a merge commit; then runs `verify.sh` |
| `verify.sh` | Claude Code `Stop` (`--hook`), `pre-push` (`--push`), by hand | The single "is this done?" gate; `--stages`, `guards.hook_stages`, `guards.push_stages` pick the stages per caller |
| `plan-step.sh <field>` | `verify.sh`, the agent | Wrapper around `plan.py field` (no `jq`): `title mode files contract tests out-of-scope verify`; `--cumulative` for steps 1..active |
| `ruff-fix.sh` | Claude Code `PostToolUse` (matcher `Edit\|Write`) | `ruff check --fix` and `ruff format` on the edited `.py` file only, and only when it belongs to this repository |
| `lib.sh` | sourced | Config reading (`jq` on `.agentkit.json`), protected-branch matching, base branch, slug |

<a id="guard-rules"></a>**Rules of `guard-git.sh` and `guard-fs.sh`** (all in `guard.py`; tests: `scripts/tests/test_guard_py.py`, `test_guard_git.sh`). A rule answers allow, **ask** (Claude Code shows its permission prompt) or **BLOCK**; the first BLOCK wins, then the first ask. Set `AGENTKIT_HEADLESS=1` for runs nobody watches (`claude -p`, CI): every ask then becomes a BLOCK that says so.

| Rule | Decision | Setting |
|---|---|---|
| `git merge` on a protected branch without `--no-ff`, with `--ff-only` or `--squash` | BLOCK | `git.protected_branches` |
| commit on, or push to, a protected branch | ask | `git.protected_commit` (`ask`/`allow`/`block`) |
| `--no-verify`; `-n` on `git commit`; `git -c core.hooksPath=…`; `git config … core.hooksPath` (reading or unsetting passes) | BLOCK | |
| force push to a protected branch | BLOCK | |
| force push to another branch | ask | `guards.destructive` |
| `reset --hard\|--merge\|--keep`, `clean -f`, `stash drop\|clear`, `checkout -- <path>`, `restore` without `--staged`, `push --delete`, `filter-branch`, `update-ref -d`, `reflog expire`, `gc --prune=now`, `branch -D` or `-d --force` | ask | `guards.destructive` (`ask`/`block`/`allow`) |
| `branch -D` where every operand is a local branch whose upstream `git branch -vv` shows as `[origin/x: gone]` (what `/clean_gone` deletes); never a protected branch, never `-r`, not under `guards.destructive=block` | allow | |
| `verify.py`, `vf` or `verify.sh` piped (`\|`, `\|&`) into another command; `> log` and `\|\|` pass | BLOCK | |
| Edit/Write of a file outside the repo; in Bash `rm`, `mv`, `cp` (destination), `ln`, `touch`, `mkdir`, `chmod`, `tee`, `sed -i`, … and redirects with a target outside the repo | ask | `fs.outside_repo_write`, `fs.allow_write` |
| `rm`, `mv`, `dd`, `sed -i` or `>` whose target holds a variable the guard cannot resolve, or is relative to a `cd` it lost track of | ask | `guards.destructive` |
| a Bash word that resolves to a file matching `fs.deny_read` (`cat ~/.ssh/id_rsa`, `git add .env`), unless `fs.allow_read` matches | BLOCK | `fs.deny_read`, `fs.allow_read` |

**What "the repo" is:** the git work tree around the hook's working directory plus every worktree of the same repository; paths are resolved first (`~`, `$HOME`, relative paths from `cwd` or the last literal `cd`, `..`, symlinks, drive letters). **Reading protected files:** `fs.deny_read` is enforced by the Bash guard and by `Read(...)` rules `install.py --guards` merges into `permissions.deny` (Claude Code applies a Read deny best effort to Grep and Glob; `fs.allow_read` becomes `Read(!pattern)` for relative patterns only; docs: https://code.claude.com/docs/en/permissions). An `.env` outside the project directory is stopped by the Bash guard but not by the Read rule.

**`verify.sh` stages, in order** (each can be switched off):

| Stage | Runs | Fails when |
|---|---|---|
| `lint` | `ruff check` and `ruff format --check` | any violation or unformatted file |
| `tests` | `pytest --cov --cov-report=xml:<guards.coverage_xml>` | a test fails, or none is collected |
| `diff-cover` | coverage of the **changed lines only** against the base branch | below `fail_under` (default 90); skipped when tests failed |
| `scope` | changed files against the `Files:` of plan steps **1..active** | `Out-of-plan file: X.` A file passes if a decision logged on this branch names its full path (`nt decide --topic scope --text "src/x.py: needed by step 1"`). Skipped without a plan (lite path) |

<a id="verify-hook-mode"></a>**Hook mode (`--hook`, the `Stop` hook) blocks only on what the agent can fix alone.** A plan that is saved but not applied (no task note) is `SKIP  plan not applied yet`; no plan is `SKIP` (lite path); `stop_hook_active: true` in the hook JSON ([hooks docs](https://code.claude.com/docs/en/hooks)) prints the summary and exits 0, so a stop is blocked at most once per chain.

| Script | Block |
|---|---|
| `guard-git.sh`, `guard-fs.sh` from hook JSON on stdin | **exit 2** (stderr goes to the model; exit 1 would not block). "Ask" = JSON on stdout, exit 0. With arguments: exit 1 |
| `verify.sh` | exit 1; **exit 2 with `--hook`** unless `stop_hook_active` is true |
| `ruff-fix.sh` (`PostToolUse`) | exit 2 shows stderr to the agent (the edit already happened) |
| `pre-push`, `plan-step.sh` | any non-zero |

Debug output always goes to stderr; stdout is reserved for the "ask" JSON.

### Install

```bash
python agent-kit/tools/install.py path/to/repo --guards           # personal trial: hooks in .claude/settings.local.json
python agent-kit/tools/install.py path/to/repo --guards --team    # team-wide: hooks in .claude/settings.json (commit it)
```

`.agentkit/scripts/` is copied (commit it with `--team`); `.gitattributes` gets `.agentkit/scripts/** text eol=lf` (bash fails on CR; then `git add --renormalize .agentkit/scripts`). Existing settings are **merged, never overwritten**; an invalid-JSON settings file is left untouched with exit 2. An existing `pre-push` hook is kept (the kit's block goes after its shebang). `.agentkit/coverage.xml` and `.coverage` go into `.gitignore`. The installer prints what `git config merge.ff false` does and asks `[y/N]`; without a terminal it changes nothing. The Claude Code hook JSON follows https://code.claude.com/docs/en/hooks and is checked by tests that feed the scripts hook-shaped JSON; it has not been exercised in a live session.

### Configuration (guards)

`.agentkit.json` → `guards` and `fs` (plus `git.protected_branches`, `git.protected_commit`, `notes.dir`, `notes.base_branch`). Every key is optional.

| Key | Default | Meaning |
|---|---|---|
| `ruff` / `pytest` / `diff_cover` / `scope` | `true` | `false` skips that `verify.sh` stage (and `ruff-fix.sh` for `ruff`) |
| `hook_stages` / `push_stages` | `null` (all) | Stages run from the `Stop` hook / `pre-push`, e.g. `["lint", "scope"]` and `["tests", "diff-cover"]`; names: `lint`, `tests`, `diff-cover`, `scope` |
| `ruff_cmd` / `pytest_cmd` / `diff_cover_cmd`, `pytest_args` | `ruff` / `pytest` / `diff-cover`, `""` | Commands (split on spaces) and extra pytest arguments |
| `coverage_xml`, `fail_under` | `.agentkit/coverage.xml`, `90` | Coverage report path; minimum coverage (%) of the changed lines |
| `scope_ignore` | `null` (built-in) | Globs the scope check never reports (built-in: `.agentkit/*`, `.agentkit.json`, `<notes.dir>/*`, `FAILURES.md`, `.coverage*`, `coverage.xml`) |
| `merge_convention`, `require_merge_commits`, `verify_on_push` | `null`, `true`, `true` | Text shown in merge/push block messages; `pre-push` takes merge commits only; `pre-push` also runs `verify.sh` |
| `guards.destructive` | `ask` | `ask`, `block` or `allow` for the destructive-command rules; force pushes to protected branches are always blocked |
| `fs.outside_repo_write` | `ask` | Writing, editing or deleting a file outside the repo: `ask` or `block` |
| `fs.allow_write` | `/tmp/**`, `~/.cache/**`, `{tmp}/**` | Outside-repo paths that are always writable |
| `fs.deny_read`, `fs.allow_read` | `~/.ssh/**`, `~/.aws/**`, `**/.env`, `**/*.pem`, …; `**/.env.example`, … | Files no tool may read; exceptions to that |
| `notes.base_branch` | auto | The base for `diff-cover` and the scope check (`origin/HEAD`, else the first existing protected branch) |

Opt out per stage, e.g. a repo with no tests yet: `{ "guards": { "pytest": false, "diff_cover": false, "scope": false } }`. To drop a whole guard, delete its entry from the hooks file or run `--uninstall-git-hooks` for `pre-push`.

### Failure log and known limits

`FAILURES.md` records every mistake the agent made that you corrected: date, what happened, root cause, **the control that now prevents it**; `AGENTS.md` asks for a computational control (a script or hook) before a new instruction line.

- The guards read the command text; they are not a sandbox. They do not follow aliases, shell functions, scripts, `git pull` (which can fast-forward), the output of `$(...)`, `xargs` or `find -exec`, programs that write files themselves (`curl -o`, `tar -C`, `python -c`), or `GIT_CONFIG_*` / `--config-env` for `core.hooksPath`. `vf || true` is not blocked. `pre-push` is the backstop for what reaches the remote; for OS-level enforcement use Claude Code's sandbox.
- `verify.sh --hook` runs every enabled stage whenever Claude finishes a turn on a branch that differs from the base; switch stages off for slow suites. The scope check needs a plan; a lite-path change has none and is skipped.

### Testing the kit itself

```bash
bash scripts/test-kit.sh            # all bash tests; or: bash scripts/test-kit.sh guard-git
python -m unittest scripts/tests/test_guard_py.py   # guard.py: tokenizer, rules, paths, wrappers
python tools/fp_sweep.py            # every documented command through the guards: must print BLOCK=0 ASK=0
python tools/selftest.py            # installer, migration from a real 0.4.0 install, plan.py parity, guards
```

CI runs them on Ubuntu, Windows and macOS (the selftest job checks out with full history so the `v0.4.0` tag exists). `tools/gen_shipped_hashes.py` regenerates `tools/shipped_hashes.json` (the hashes the installer compares removed skills against).

## Credits

Patterns adapted from Anthropic's skill authoring best practices (progressive disclosure, degrees of freedom, utility scripts, validation loops), the `anthropics/skills` repository (SKILL.md format), `obra/superpowers` (plan-then-execute, verify before claiming done), Matt Pocock's "grill-me" (the design interview now inside writing-plans) and community commit skills.
