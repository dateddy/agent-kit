I'd like to measure how well the FAQ search ranks its answers.

`data/eval.jsonl` has one JSON object per line: `{"query": "...", "expected_id": "faq-..."}`. Write `scripts/eval.py`
that runs every query through the existing search and writes `out/metrics.json` with `recall@1` and `recall@3`: the
fraction of queries whose expected entry is in the top 1 and top 3 results, as numbers between 0 and 1. It must work as
`python scripts/eval.py` from the repository root.

`out/` holds generated files and should not be committed. Commit everything else.
