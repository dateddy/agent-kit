---
name: debugging-systematically
description: Debugs by classifying the failure first (code, data or LLM output), then following that layer's procedure, and keeps an append-only bug journal (scripts/dbg.py) that flags recurring bugs. Use when something fails, raises an exception, returns wrong results, or a model's output is wrong or invalid, and before changing code to "try a fix".
---

# Debugging systematically

Guessing and editing burns tokens and hides the real cause. Classify, reproduce, find the cause, then fix.
Path: `python <this-skill>/scripts/dbg.py` (`dbg`). Exit: 0 OK · 2 WARN (recurring bug, show the user) · 1 BLOCK (fix and re-run).
Other tools: `run` and `rmap` (economizing-context), `vf` (verifying-changes), `nt` (keeping-notes), `pk` (crafting-prompts).

## Step 0 — open the bug

```
dbg open --layer code|data|llm --symptom "<exact error text or wrong value>" [--repro "<cmd>"]
```

Pick the layer from the symptom:

| Layer | Looks like | First move |
|---|---|---|
| **code** | exception, traceback, wrong logic with correct inputs | reproduce → zone → failing test |
| **data** | right code, wrong result on some inputs; schema/count/encoding drift | look at the real inputs |
| **llm** | model output is wrong, off-format, hallucinated or fails validation | capture prompt + raw output |

Unsure → start with **data** (cheapest to rule out), then code.
`open` prints past bugs with overlapping keywords. A WARN means this has happened before: read the earlier **cause** and check it first before forming new theories.

## code

```
- [ ] 1. run -- <repro>                         exit code + traceback, full log saved
- [ ] 2. rmap --zone-from-log .agentkit/logs/<log>  the files that can matter (paths, never contents)
- [ ] 3. rmap --symbols <file> + a line range      read only the failing function
- [ ] 4. Write a FAILING test that reproduces it; run it and see it fail for the right reason
- [ ] 5. dbg hypo B-NNN "<cause guess>"          one at a time; check it with a print/assert/test, then
         dbg hypo B-NNN "…" --result confirmed|rejected
- [ ] 6. Fix the cause (not the symptom); the new test passes; vf (full) exits 0
- [ ] 7. dbg close B-NNN --cause "<root cause>" --fix <sha>      (commit first, then close)
```

Never loosen or delete a test to get green (see verifying-changes).

## data

```
- [ ] 1. Inspect the real inputs BEFORE touching code: schema/columns, row counts, null and
         duplicate counts, 3–5 samples including the failing one, encoding. Use a tiny script
         that prints a summary; never print a whole dataset.
- [ ] 2. Compare with what the code assumes (types, ranges, languages, id formats)
- [ ] 3. dbg hypo … for each mismatch you find; confirm with the failing row
- [ ] 4. Fix where the assumption lives: validate/clean at the loader and add a test with the bad row;
         fix the data only if it is wrong at the source
- [ ] 5. vf, then dbg close B-NNN --cause "…"
```

## llm

```
- [ ] 1. Capture verbatim: the exact prompt sent, the raw model output, the validation that failed
         (field or rule). Save to a file; do not paraphrase.
- [ ] 2. dbg llm-case B-NNN --spec prompts/<name>.spec.json \
           --vars '{"question": "…"}' --check '{"type":"not_contains","value":"…"}'
         → adds the failure as a test case to the prompt spec (vars must match the spec's inputs)
- [ ] 3. Change the PROMPT SPEC (crafting-prompts: clearer instruction, an example of the failure,
         output schema) and re-render; never "fix" by loosening the parser or validator
- [ ] 4. pk test prompts/<name>.spec.json --case N      the new case passes, the old ones still pass
- [ ] 5. dbg close B-NNN --cause "<what the prompt failed to say>"
```

## Other commands

| Need | Command |
|---|---|
| Past bugs about X | `dbg similar "<text>"` |
| What is open | `dbg list --open` (`--layer data`) |
| Record a theory and its outcome | `dbg hypo B-NNN "…" [--result confirmed\|rejected]` |
| Finish | `dbg close B-NNN --cause "…" [--fix <sha>]` — BLOCKs without a cause |

Checklists with the common causes per layer: [reference/layers.md](reference/layers.md).

## Rules

- Reproduce before theorising; if you cannot reproduce, say so and ask for the input.
- One hypothesis at a time, each tested by an observation, not by editing code.
- After 3 rejected hypotheses, stop and re-read the evidence (log, sample, raw output) or ask the user.
- Close with the **root cause**. "Fixed" or "works now" is not a cause.
- `notes/debug.jsonl` is committed with the fix; it is the project's memory of what broke and why.
