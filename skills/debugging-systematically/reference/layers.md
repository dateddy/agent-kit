# Layer checklists

Common causes to check, in order of how often they turn out to be the answer. Test each by observation.

## code

- The traceback's **last repo frame**, not the last frame: the bug is usually where your code calls a library with a bad value.
- Off-by-one, empty input, `None` reaching a function that assumes a value.
- Mutable default argument; state kept between calls; module-level caches.
- Wrong path or working directory (relative paths, `__file__`, tests run from another dir).
- Import problems: shadowed module name, circular import, wrong package root (`rmap --zone` shows who imports whom).
- Environment: Python version, missing optional dependency, Windows vs POSIX paths and line endings, console encoding.
- Async/concurrency: ordering, unawaited coroutine, shared state.
- Recent change: `git log -5 --stat -- <file>`; `git bisect` when the last good commit is known.

## data

- Schema drift: renamed/missing/extra columns, different types (`"1"` vs `1`), different date formats.
- Counts: rows dropped by a join or filter, duplicates, unexpected nulls, empty files.
- Text: encoding (UTF-8 vs cp1252), Unicode normalisation (NFC vs NFD, Vietnamese diacritics), trailing whitespace, case.
- Ids and keys: type mismatch (str vs int), leading zeros, collisions.
- Ranges and units: negative values, seconds vs milliseconds, percentages as 0–1 vs 0–100.
- Ordering and leakage: unsorted input assumed sorted; train/test overlap.
- What to print: shape and dtypes, `value_counts` of a suspicious column, the failing row, 3 random rows. Never the whole dataset.

## llm

- Prompt missing the rule: the model did what the prompt allowed. Add the rule or an example of the failure.
- Output format drift: extra prose around JSON, code fences, wrong key names. Fix the prompt (schema, example); keep the parser strict.
- Context problems: the answer was not in the retrieved text, the relevant passage was cut or buried, instructions far from the question.
- Wrong language: state the output language in the prompt.
- Temperature or model change between runs; compare the same input on both.
- Target mismatch: a prompt written for one model family used on another (see crafting-prompts targets).
- Evidence to keep: the exact prompt, the raw output, the input variables, the model name and parameters.
- A fixed failure becomes a permanent test: `dbg llm-case`, then `pk test`.
