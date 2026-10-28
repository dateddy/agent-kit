# Agent rules

Skills live in `.agentkit/skills/<name>/` — read a skill's SKILL.md before the work it covers.
Short names used below (run with `python`; paths relative to `.agentkit/skills/`):
{{SHORT_NAMES}}

## Mode: question or task
- Message starts with `?` → **question mode**: read-only. Read, search and run read-only commands; do not edit files, commit, or touch task notes. Answer concisely, then stop.
- Starts with `/task` or `task:` → follow **writing-plans** (Frame first).
- No prefix and the message asks for a change (add, fix, implement, refactor, remove, rename, commit…) → **task mode**.
- No prefix and unclear → treat as a question; end with one line offering to do it.

## Task lifecycle
1. Start of session: `nt brief`. Trust it over re-exploring; read only what it points to.
2. Non-trivial task → plan first (writing-plans). ≤2 files and no design decision → lite path: no plan file.<!--if:grilling-designs--> New public interface/schema, ≥3 modules or an open design choice → suggest `/grill` (grilling-designs) first; never auto-run it.<!--/if--> After the plan is accepted: branch (`gk branch-name`), save plan, `pl check`, `pl apply`.
3. Per step: `pl step N` → implement → `vf` → `nt task --check N --done "…" --next "…"` → commit (managing-git). Before editing a module: `rmap --zone <file>`. Inner loop: `vf --affected`; the full `vf` is still required before commit.
4. Done only when `vf` exits 0 after the last edit (never pipe it; the VERDICT line is authoritative). Never weaken tests to pass.
5. Decision settled by the user → `nt decide --topic … --text … --why …`.
<!--if:tracking-experiments-->6. Training/eval runs → `ex run … -- <cmd>`, never bare.<!--/if-->
<!--if:debugging-systematically-->7. Something fails → debugging-systematically: `dbg open --layer code|data|llm …`, no guess-and-edit.<!--/if-->

## Scripts decide, you write
- Exit codes: 0 OK · 2 WARN → show the user and wait for yes · 1 BLOCK → fix and re-run. Never bypass a BLOCK.
- Never use `--no-verify` (or any other way around a git hook). A hook BLOCK is fixed, not bypassed.
- Destructive or irreversible commands (reset --hard, force push, deleting branches/files, dropping stashes): quote the exact command and wait for an explicit yes.

## Token discipline
- Locate before reading: `rmap`, search (rg/grep), `rmap --symbols`, then read the needed line range.
- Wrap commands that may print >60 lines in `run`. Grep saved logs instead of re-running.
- Never print lockfiles, datasets, notebook outputs, generated files or checkpoints.
- Replies: lead with the result. No recap of steps, no restating code you just wrote, no filler.
- Reply in the user's language.

<!-- Project-specific rules below this line -->
