---
name: crafting-prompts
description: Builds prompts for a target LLM (the model inside the user's product — Claude, GPT, small open models like Qwen/Llama, or reasoning models) from a JSON spec via scripts/promptkit.py, which finds missing information, renders a model-appropriate layout, lints wording and runs test cases against an API. Use when the user wants to write, improve, port or test a system prompt, RAG prompt, extraction/classification prompt, or any prompt their code sends to a model.
---

# Crafting prompts

You collect requirements and write content; the script decides structure and layout. The agent writing the prompt does not matter — the **target** model does.
Path: `python <this-skill>/scripts/promptkit.py` (`pk`). Exit: 0 OK · 2 WARN · 1 BLOCK/failing case.

## Workflow

```
- [ ] 1. pk init <name> --target <generic|claude|gpt|open-small|reasoning>
- [ ] 2. Fill prompts/<name>.spec.json from what the user said (never invent facts, schemas or examples)
- [ ] 3. pk check prompts/<name>.spec.json
         → BLOCK lists "ASK THE USER" questions: send them in ONE message, only those
- [ ] 4. pk render prompts/<name>.spec.json            → prompts/<name>.<target>.md
- [ ] 5. pk lint prompts/<name>.<target>.md --target <t> → fix wording in the SPEC, re-render
- [ ] 6. pk test prompts/<name>.spec.json --model <m>   (if the user has an API key or local server)
- [ ] 7. Report: target, ~tokens, test pass rate, file paths
```

Edit the spec, never the rendered `.md` (it is regenerated). Spec fields and check types: [reference/spec.md](reference/spec.md).

## Choosing the target

| Target | When |
|---|---|
| `claude` | Claude models: XML sections, long documents before the question |
| `gpt` | OpenAI GPT-style chat models |
| `open-small` | Qwen, Llama, Gemma, Vistral, other ≤~30B open models: examples required, format restated last |
| `reasoning` | Models that think internally: goal + criteria, no "step by step" |
| `generic` | Unknown or mixed; plain markdown |

Unsure → ask which model their code calls. Porting to another model → same spec, new `--target`, re-run tests.

## Writing the content well

- Goal: one sentence with the user/reader and the use of the output.
- Instructions: positive, specific, each one testable. Give the reason for unusual rules.
- Output: for machine-read output use `json` with a `schema`; add `language` for Vietnamese output.
- Examples: real, varied, including one edge case (e.g., "answer not in documents").
- Cases: at least one happy path and one edge case, each with checks that would fail if the prompt broke.

## Testing

`pk test` uses `prompts.provider/model/base_url` from `.agentkit.json`, or `--provider openai|anthropic --model … --base-url …` (OpenAI-compatible covers OpenRouter, vLLM, Ollama, LM Studio). The key comes from `$OPENAI_API_KEY` / `$ANTHROPIC_API_KEY`. Never print or store keys. Failing case → change the spec, re-render, re-test that case with `--case N`.
