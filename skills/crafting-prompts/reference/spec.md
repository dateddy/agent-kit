# Prompt spec reference

```json
{
  "name": "medical-qa",                       // file stem; renders to medical-qa.<target>.md
  "target": "open-small",                     // default target; --target overrides
  "role": "You are …",                        // optional one-line persona (first line of SYSTEM)
  "goal": "Answer … for …",                   // REQUIRED, one sentence
  "context": "Background that is always true",// optional, static
  "inputs": [                                 // runtime variables → {{name}} placeholders in USER
    {"name": "documents", "description": "retrieved passages", "long": true},  // long → placed first for claude/open-small/reasoning
    {"name": "question",  "description": "user question"}
  ],
  "instructions": ["…", "…"],                 // REQUIRED, numbered in output
  "constraints": ["…"],                       // optional bullets
  "output": {
    "format": "json",                         // REQUIRED: text | markdown | json
    "description": "…",                       // REQUIRED unless format=json with schema
    "schema": {"answer": "string|null"},      // REQUIRED when format=json
    "language": "vi"                          // optional
  },
  "examples": [                               // required for target open-small
    {"input": {"documents": "…", "question": "…"}, "output": {"answer": "…"}}
  ],
  "cases": [                                  // used by `pk test`
    {"vars": {"documents": "…", "question": "…"},
     "checks": [{"type": "json_keys", "value": ["answer"]}, {"type": "contains", "value": "Metformin"}]}
  ]
}
```

Check types: `contains`, `not_contains` (case-insensitive) · `regex` · `equals` · `max_words`, `max_chars` · `json_keys` (list of required keys; strips ``` fences) · `valid_json`.

Rendered file layout:
```
=== SYSTEM ===   role, goal, context, instructions, constraints, output format, examples
=== USER ===     one block per input with {{placeholders}} (+ format reminder for open-small)
```
Your code fills `{{name}}` placeholders and sends SYSTEM as the system message, USER as the user message.

Custom target: add a profile to `<repo>/prompts/profiles.json` with keys `style` (markdown|xml), `long_inputs_first`, `examples` (optional|required), `repeat_format_at_end`, `max_instructions`, `banned_phrases`.
