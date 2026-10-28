Add a machine-readable output mode to the FAQ search command of this repository.

`python -m faq search "<query>" --json` must print one JSON array and nothing else. Each element is an object with
exactly the keys `question`, `answer` and `score` (a number), sorted by score, best first. `-k/--top-k` keeps
working. When nothing matches it prints `[]`. Without `--json` the output stays exactly as it is today.

Add tests for it to the existing test suite, and make sure the whole suite passes. Commit your work.
---new-session---
This repository had a task started earlier in another session: a `--json` flag for `python -m faq search`.
You have no memory of it. Find out what was done and what is missing, then make sure the feature is complete, tested,
verified (the whole suite passes) and committed. Do not start anything new.
