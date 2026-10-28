# Commit message examples

Format: `type(scope)!: subject` · blank line · body (why) · blank line · footers.

**Good**
```
feat(retrieval): add BM25 fallback when dense recall is empty

Dense retriever returns nothing for rare drug names; BM25 keeps
answers grounded instead of letting the model hallucinate.

Refs: #42
```
```
fix(api): return 422 instead of 500 on empty question
```
```
refactor(pipeline): extract chunking into its own module
```
```
perf(embed): batch encode 64 docs per call

Cuts indexing time on the cardiology corpus from 41 to 9 minutes.
```
```
feat(config)!: rename `top_k` to `retrieval.top_k`

BREAKING CHANGE: old configs fail validation; run scripts/migrate_cfg.py.
```

**Bad → why**
- `fix bug` → no type format, vague
- `feat: Added login.` → past tense, capitalized, trailing period
- `chore: update files a.py, b.py, c.py` → file list instead of intent
- `feat(api): add endpoint and fix typo and bump deps` → three commits in one

**Choosing the type (you decide; lint-msg only checks it is allowed)**

| Changed files | Type |
|---|---|
| Only `*.md`, `docs/` | `docs` |
| Only tests | `test` |
| Only `requirements.txt`, `pyproject.toml`, `package.json`, `Dockerfile`, lockfiles | `build` |
| Only `.github/`, CI config | `ci` |
| Only formatter/linter config, whitespace | `style` |
| `.gitignore`, `LICENSE`, `.agentkit.json` | `chore` |
| Source: a user-visible capability that did not exist | `feat` |
| Source: existing behavior was wrong | `fix` |
| Source: behavior identical, code reorganized | `refactor` |
| Source: behavior identical, faster/smaller | `perf` |

Code + its tests + its doc line is **one** commit; the source change decides the type (a new function with its test and a README line is `feat`, not three commits).
AI-project specifics: prompt text changes that alter outputs are `feat`/`fix` (they are behavior); notebook-only exploration is `chore(exp)` or stays off main.
