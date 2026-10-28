---
name: tracking-experiments
description: Records every training/eval/benchmark run with its git commit, config, params and metrics in an append-only experiments/runs.jsonl via scripts/exp.py, and answers "which run was best", "what changed between runs" and "how do I reproduce it" from data instead of memory. Use when running or launching training, fine-tuning, evaluation, hyperparameter sweeps, RAG/retrieval benchmarks, or when the user asks to compare, rank or reproduce results.
---

# Tracking experiments

Path: `python <this-skill>/scripts/exp.py` (`ex`). Exit: 0 OK · 2 WARN · 1 BLOCK/failed run.

## Run through `ex run`, not directly

```
ex run --name <short-family-name> --config configs/x.yaml --metrics out/metrics.json [--tag baseline] -- python train.py --config configs/x.yaml
```

- `--name`: stable family name (`dense-bge`, `bm25-only`, `rerank-v2`); variants go in params, not names.
- `--config` (JSON/YAML) is flattened into params (`train.lr`). Add overrides with `--param train.lr=3e-4`.
- `--metrics`: the JSON/YAML file the script writes at the end (a `.jsonl` uses its last line). If the script only prints metrics, ask the user to add a `json.dump(...)` at the end; do not parse logs by hand.
- Long runs: add `--note "why this run"`. The full log goes to `experiments/logs/<id>.log`; only the tail is printed.

Already ran (notebook, Colab, server)? `ex log --name … --param k=v --metric f1=0.81 --note "colab"`.

## Reproducibility gate

WARN "uncommitted change(s)" means the run cannot be rebuilt from its sha. Tell the user, and commit (managing-git) before the next run. Never edit or delete lines in `runs.jsonl`.

## Answering questions from the log

| User asks | Command |
|---|---|
| "best so far?" | `ex best --metric eval.f1` (`--asc` for loss/latency) |
| "show results" | `ex list --sort eval.f1 --cols eval.f1,recall@5` |
| "what changed between A and B?" | `ex compare E-0003 E-0007` |
| "runs with lr 3e-4" | `ex list --where train.lr=0.0003` |
| "details of a run" | `ex show E-0007` |

Report numbers exactly as printed, with the run id. Do not round, average or extrapolate unless asked.

## Setup notes

`runs.jsonl` is committed (small, shared with teammates); `experiments/logs/` is gitignored automatically. Keep large artifacts (checkpoints) out of git; record their path with `--param artifact=…`.
