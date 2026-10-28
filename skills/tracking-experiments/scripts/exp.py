#!/usr/bin/env python3
"""exp — append-only experiment log tied to git commits. Stdlib only (PyYAML optional).

  exp.py run --name N [--config F] [--param k=v]... [--metrics F] [--tag T] [--note TXT] -- <cmd...>
        run a training/eval command, capture duration + exit code + log, then record it
  exp.py log --name N [--config F] [--param k=v]... [--metrics F] [--metric k=v]... [--tag T] [--note TXT]
        record a run that already happened
  exp.py list [--sort METRIC] [--asc] [--top N] [--name SUBSTR] [--tag T] [--where k=v]... [--cols a,b]
  exp.py show ID
  exp.py compare ID ID           params that differ + metric deltas
  exp.py best --metric M [--asc] [--name SUBSTR]

Each record: id, time, git sha/branch/dirty, name, cmd, params (flattened), metrics, tags, note,
status, duration, log path. Stored in experiments/runs.jsonl (commit it; logs are gitignored).
Exit: 0 OK · 1 BLOCK · 2 WARN (e.g. dirty tree → run not reproducible from its sha)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
import time
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2
DEFAULTS = {"dir": "experiments", "default_metrics": []}
TAIL = 15  # lines of command output echoed after a run


def sh(*cmd: str) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def root() -> Path:
    r = sh("git", "rev-parse", "--show-toplevel")
    return Path(r) if r else Path.cwd()


def cfg(r: Path) -> dict:
    c = dict(DEFAULTS)
    try:
        c.update(json.loads((r / ".agentkit.json").read_text(encoding="utf-8")).get("experiments", {}))
    except Exception:
        pass
    return c


def coerce(v):
    if isinstance(v, str):
        s = v.strip()
        if s.lower() in ("true", "false"):
            return s.lower() == "true"
        try:
            return int(s)
        except ValueError:
            try:
                return float(s)
            except ValueError:
                return s
    return v


def flatten(d, prefix="") -> dict:
    out = {}
    for k, v in (d or {}).items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        elif isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v):
            out[key] = v
        elif isinstance(v, list):
            out[key] = json.dumps(v)[:200]
        else:
            out[key] = v
    return out


def load_struct(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"BLOCK: file not found: {path}")
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml  # type: ignore
        except ImportError:
            raise SystemExit("BLOCK: YAML config needs PyYAML (pip install pyyaml) — or pass a JSON file")
        return yaml.safe_load(text) or {}
    if p.suffix.lower() == ".jsonl":  # metrics logs: take the last line
        lines = [l for l in text.splitlines() if l.strip()]
        return json.loads(lines[-1]) if lines else {}
    return json.loads(text)


def kv(pairs) -> dict:
    out = {}
    for p in pairs or []:
        if "=" not in p:
            raise SystemExit(f"BLOCK: expected key=value, got '{p}'")
        k, v = p.split("=", 1)
        out[k.strip()] = coerce(v)
    return out


def store(r: Path, c: dict) -> Path:
    d = r / c["dir"]
    (d / "logs").mkdir(parents=True, exist_ok=True)
    gi = d / "logs" / ".gitignore"
    gi.exists() or gi.write_text("*\n")
    return d / "runs.jsonl"


def records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for i, l in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if l.strip():
            try:
                out.append(json.loads(l))
            except json.JSONDecodeError:
                print(f"WARN: runs.jsonl line {i} is corrupt; skipped")
    return out


def next_id(recs: list[dict]) -> str:
    n = max((int(r["id"][2:]) for r in recs if re.match(r"^E-\d+$", r.get("id", ""))), default=0)
    return f"E-{n + 1:04d}"


def git_state(exp_dir: str) -> dict:
    # the experiment log itself changes every run, so it never counts as "dirty"
    dirty = [l for l in sh("git", "status", "--porcelain", "--untracked-files=no", "--", ".",
                           f":(exclude){exp_dir}").splitlines() if l]
    return {"sha": sh("git", "rev-parse", "--short", "HEAD") or None,
            "branch": sh("git", "symbolic-ref", "--quiet", "--short", "HEAD") or None,
            "dirty": len(dirty)}


def build(a, extra: dict, c: dict) -> dict:
    params = flatten(load_struct(a.config)) if a.config else {}
    params.update(kv(a.param))
    metrics = {}
    if a.metrics:
        if Path(a.metrics).exists():
            metrics.update(flatten(load_struct(a.metrics)))
        else:
            print(f"WARN: metrics file not found: {a.metrics}")
    metrics.update(kv(getattr(a, "metric", None)))
    metrics = {k: coerce(v) for k, v in metrics.items()}
    rec = {"time": dt.datetime.now().isoformat(timespec="seconds"), **git_state(c["dir"]), "name": a.name,
           "config_file": a.config, "params": params, "metrics": metrics, "tags": a.tag or [], "note": a.note or ""}
    rec.update(extra)
    return rec


def append(path: Path, rec: dict) -> None:
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def report(rec: dict) -> int:
    m = ", ".join(f"{k}={fmt(v)}" for k, v in list(rec["metrics"].items())[:8]) or "no metrics"
    print(f"OK: {rec['id']} {rec['name']} @ {rec['sha'] or 'no-git'} [{rec.get('status', 'logged')}] {m}")
    if rec["dirty"]:
        print(f"WARN: {rec['dirty']} uncommitted change(s) — this run cannot be reproduced from {rec['sha']}. "
              "Commit before the next run.")
        return WARN
    return OK


def fmt(v) -> str:
    return f"{v:.4g}" if isinstance(v, float) else str(v)


# ---------------------------------------------------------------- commands
def cmd_log(a, r, c):
    path = store(r, c)
    rec = build(a, {}, c)
    rec = {"id": next_id(records(path)), **rec, "status": "logged"}
    append(path, rec)
    return report(rec)


def cmd_run(a, r, c):
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        print("BLOCK: no command after --")
        return BLOCK
    path = store(r, c)
    rid = next_id(records(path))
    log = path.parent / "logs" / f"{rid}.log"
    shell = len(cmd) == 1 and " " in cmd[0]
    t0 = time.time()
    with log.open("w", encoding="utf-8") as fh:
        p = subprocess.run(cmd[0] if shell else cmd, shell=shell, stdout=fh, stderr=subprocess.STDOUT)
    dur = round(time.time() - t0, 1)
    tail = log.read_text(encoding="utf-8", errors="replace").splitlines()[-TAIL:]
    print(f"$ {' '.join(cmd)}\nexit {p.returncode} · {dur}s · log: {log.relative_to(r)}")
    print("\n".join(tail))
    rec = build(a, {"cmd": " ".join(cmd), "duration_s": dur, "exit": p.returncode,
                    "status": "ok" if p.returncode == 0 else "failed", "log": str(log.relative_to(r))}, c)
    rec = {"id": rid, **rec}
    if p.returncode == 0 and a.metrics and not rec["metrics"]:
        print("WARN: command succeeded but no metrics were read — check --metrics path")
    append(path, rec)
    code = report(rec)
    return BLOCK if p.returncode != 0 else code


def matches(rec: dict, a) -> bool:
    if getattr(a, "name", None) and a.name not in rec["name"]:
        return False
    if getattr(a, "tag", None) and a.tag not in rec.get("tags", []):
        return False
    for k, v in kv(getattr(a, "where", None)).items():
        val = rec["params"].get(k, rec["metrics"].get(k, rec.get(k)))
        if coerce(str(val)) != v:
            return False
    return True


def cmd_list(a, r, c):
    recs = [x for x in records(store(r, c)) if matches(x, a)]
    if not recs:
        print("no runs")
        return OK
    if a.sort:
        recs = [x for x in recs if isinstance(x["metrics"].get(a.sort), (int, float))]
        recs.sort(key=lambda x: x["metrics"][a.sort], reverse=not a.asc)
    else:
        recs = recs[::-1]  # newest first
    recs = recs[:a.top]
    cols = a.cols.split(",") if a.cols else (c["default_metrics"] or list(dict.fromkeys(
        k for x in recs for k in x["metrics"]))[:5])
    head = f"{'id':7} {'date':10} {'name':20} {'sha':9} {'st':6} " + " ".join(f"{k[:10]:>10}" for k in cols)
    print(head)
    for x in recs:
        sha = (x["sha"] or "-") + ("*" if x["dirty"] else "")
        print(f"{x['id']:7} {x['time'][:10]:10} {x['name'][:20]:20} {sha:9} {x.get('status', '')[:6]:6} "
              + " ".join(f"{fmt(x['metrics'].get(k, '-'))[:10]:>10}" for k in cols))
    print("(* = uncommitted changes at run time)")
    return OK


def get(recs, rid):
    rec = next((x for x in recs if x["id"] == rid), None)
    if not rec:
        raise SystemExit(f"BLOCK: {rid} not found")
    return rec


def cmd_show(a, r, c):
    rec = get(records(store(r, c)), a.id)
    print(json.dumps(rec, indent=1, ensure_ascii=False))
    return OK


def cmd_compare(a, r, c):
    recs = records(store(r, c))
    x, y = get(recs, a.a), get(recs, a.b)
    print(f"{x['id']} ({x['name']} @ {x['sha']})  vs  {y['id']} ({y['name']} @ {y['sha']})")
    keys = sorted(set(x["params"]) | set(y["params"]))
    diff = [(k, x["params"].get(k, "∅"), y["params"].get(k, "∅")) for k in keys if x["params"].get(k) != y["params"].get(k)]
    print("PARAMS that differ:" if diff else "PARAMS: identical")
    for k, u, v in diff:
        print(f"  {k}: {u} → {v}")
    print("METRICS:")
    for k in sorted(set(x["metrics"]) | set(y["metrics"])):
        u, v = x["metrics"].get(k), y["metrics"].get(k)
        delta = f"  ({v - u:+.4g})" if isinstance(u, (int, float)) and isinstance(v, (int, float)) else ""
        print(f"  {k}: {fmt(u) if u is not None else '∅'} → {fmt(v) if v is not None else '∅'}{delta}")
    if x["sha"] != y["sha"]:
        print(f"CODE changed between runs: git diff --stat {x['sha']} {y['sha']}")
    return OK


def cmd_best(a, r, c):
    recs = [x for x in records(store(r, c)) if matches(x, a) and isinstance(x["metrics"].get(a.metric), (int, float))
            and x.get("status") != "failed"]
    if not recs:
        print(f"no runs with metric '{a.metric}'")
        return BLOCK
    b = (min if a.asc else max)(recs, key=lambda x: x["metrics"][a.metric])
    print(f"{b['id']} {b['name']} @ {b['sha']}{'*' if b['dirty'] else ''}  {a.metric}={fmt(b['metrics'][a.metric])}")
    print("params: " + ", ".join(f"{k}={v}" for k, v in list(b["params"].items())[:12]))
    if b["sha"]:
        print(f"reproduce: git checkout {b['sha']}" + (f" && {b['cmd']}" if b.get("cmd") else ""))
        if b["dirty"]:
            print("WARN: this run had uncommitted changes — the sha alone does not reproduce it")
            return WARN
    return OK


def main() -> int:
    ap = argparse.ArgumentParser(prog="exp", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd_name", required=True)

    def common(s):
        s.add_argument("--name", required=True); s.add_argument("--config"); s.add_argument("--param", action="append")
        s.add_argument("--metrics", help="JSON/YAML/JSONL file written by your script"); s.add_argument("--tag", action="append")
        s.add_argument("--note")
    s = sp.add_parser("run"); common(s); s.add_argument("cmd", nargs=argparse.REMAINDER)
    s = sp.add_parser("log"); common(s); s.add_argument("--metric", action="append")
    s = sp.add_parser("list"); s.add_argument("--sort"); s.add_argument("--asc", action="store_true")
    s.add_argument("--top", type=int, default=15); s.add_argument("--name"); s.add_argument("--tag")
    s.add_argument("--where", action="append"); s.add_argument("--cols")
    s = sp.add_parser("show"); s.add_argument("id")
    s = sp.add_parser("compare"); s.add_argument("a"); s.add_argument("b")
    s = sp.add_parser("best"); s.add_argument("--metric", required=True); s.add_argument("--asc", action="store_true")
    s.add_argument("--name"); s.add_argument("--tag")
    a = ap.parse_args()
    r = root()
    c = cfg(r)
    return {"run": cmd_run, "log": cmd_log, "list": cmd_list, "show": cmd_show, "compare": cmd_compare,
            "best": cmd_best}[a.cmd_name](a, r, c)


if __name__ == "__main__":
    sys.exit(main())
