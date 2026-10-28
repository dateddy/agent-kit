#!/usr/bin/env python3
"""bench — does the kit help? Run the same tasks headless with Claude Code under three conditions. Stdlib only.

  bench.py overhead --fixture REPO --model sonnet [--trials 3]
  bench.py run --fixture REPO --tasks tools/bench/tasks --conditions none,rules,kit --trials 3 \\
               --model sonnet --max-turns 60 --max-budget-usd 2 --out bench/results.jsonl [--only T1,T3] --yes
  bench.py report bench/results.jsonl [--csv bench/summary.csv]

Conditions (applied to a fresh `git clone` of the fixture in a temp dir; the fixture itself is never touched):
  none   AGENTS.md, CLAUDE.md, .claude/, .agentkit/, .agentkit.json and notes/ removed
  rules  like none, plus templates/AGENTS.rules-only.md (mode, token and safety rules, no scripts) as AGENTS.md
  kit    like none, plus `install.py <copy> --git-hooks` (core skills, plus `with` from the task's meta.json)

Task folder tasks/<ID>/:  task.md (prompt; a line `---new-session---` starts a NEW `claude -p` session),
meta.json {"max_turns_per_session": [..], "with": [], "oracle_cmd": "..."}, oracle/ (hidden tests, copied in
only after the agent finishes), optional seed/ (files copied over the clone before the setup commit, so they are
part of the starting state, e.g. an eval set or a pre-seeded failing test).

Real runs cost money: `run` prints the worst-case spend and exits 2 until you pass --yes. By default each session
runs with --setting-sources project,local --strict-mcp-config, so your user-level skills/MCP servers do not leak
into the comparison (--no-isolate turns that off).
Exit: 0 ok · 1 error · 2 `run` needs --yes.
"""
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import os
import re
import shlex
import shutil
import statistics
import stat
import subprocess
import sys
import tempfile
import time
from collections import Counter
from pathlib import Path

KIT = Path(__file__).resolve().parents[2]
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
CONDITIONS = ("none", "rules", "kit")
SPLIT_RE = re.compile(r"^[ \t]*---new-session---[ \t]*$", re.M)
STRIP = ("AGENTS.md", "CLAUDE.md", ".claude", ".agentkit", ".agentkit.json", "notes")
KIT_SCRIPT_RE = re.compile(r"/scripts/(notes|gitkit|plan|verify|run|repomap|exp|promptkit|dbg)\.py\b")
VIOLATIONS = ("secret_in_history", "tests_modified", "commits_on_protected", "bad_commit_msgs",
              "uncommitted_changes", "no_verify_used")
NOISE = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.agentkit/logs)(/|$)|\.pyc$")
OVERHEAD_PROMPT = "Reply with exactly: ok"


class Unsafe(SystemExit):
    """Raised before a session would run outside a throwaway copy."""


# ---------------------------------------------------------------- helpers
def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def sh(cmd: list, cwd: Path | None = None, timeout: int = 120, env: dict | None = None) -> tuple[int, str]:
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout, env=env or CHILD_ENV)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def git(cwd: Path, *args: str) -> str:
    return sh(["git", *args], cwd)[1]


def rmtree(p: Path) -> None:
    def onerr(fn, path, _exc):  # git objects are read-only on Windows
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(p, onerror=onerr)


def inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def assert_safe(workdir: Path, fixture: Path) -> None:
    """Sessions may only run in a throwaway copy under the temp dir, never in the fixture or in this kit repo."""
    wd = workdir.resolve()
    if inside(wd, fixture) or inside(wd, KIT) or inside(KIT, wd):
        raise Unsafe(f"refusing to run in {wd}: it is, or contains/is inside, the fixture or the kit repo")
    if not inside(wd, Path(tempfile.gettempdir())):
        raise Unsafe(f"refusing to run in {wd}: not under the temp dir {tempfile.gettempdir()}")


_MODS: dict = {}


def kit_module(rel: str, name: str):
    """Load a kit script as a module to reuse its constants (secret patterns, test-path regexes)."""
    if name not in _MODS:
        spec = importlib.util.spec_from_file_location(name, KIT / rel)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _MODS[name] = mod
    return _MODS[name]


def gitkit():
    return kit_module("skills/managing-git/scripts/gitkit.py", "bench_gitkit")


def verifymod():
    return kit_module("skills/verifying-changes/scripts/verify.py", "bench_verify")


def find_claude() -> str:
    # walk PATH in order (first hit wins); on Windows only launchable names count, not the extensionless sh shim
    names = ("claude.exe", "claude.cmd") if os.name == "nt" else ("claude",)
    for d in os.environ.get("PATH", "").split(os.pathsep):
        for n in names:
            if d and (Path(d) / n).is_file():
                return str(Path(d) / n)
    raise SystemExit("BLOCK: `claude` not found on PATH")


# ---------------------------------------------------------------- workdirs
def prepare_workdir(fixture: Path, cond: str, meta: dict, base: Path, label: str,
                    seed: Path | None = None) -> tuple[Path, str, str]:
    """Clone the fixture, apply the condition, commit it. Returns (workdir, base_sha, fixture_sha)."""
    fixture_sha = git(fixture, "rev-parse", "HEAD").strip()
    wd = base / label
    code, out = sh(["git", "clone", "-q", "--no-hardlinks", str(fixture), str(wd)], timeout=300)
    if code != 0:
        raise RuntimeError(f"git clone failed: {out.strip()[:200]}")
    assert_safe(wd, fixture)
    git(wd, "remote", "remove", "origin")  # nothing an agent does can reach the fixture
    for k, v in (("user.name", "bench"), ("user.email", "bench@example.com"), ("core.autocrlf", "false")):
        git(wd, "config", k, v)
    for name in STRIP:
        p = wd / name
        if p.is_symlink() or p.is_file():
            p.unlink()
        elif p.is_dir():
            rmtree(p)
    if seed is not None and seed.is_dir():
        shutil.copytree(seed, wd, dirs_exist_ok=True)
    if cond == "rules":
        shutil.copy(KIT / "templates" / "AGENTS.rules-only.md", wd / "AGENTS.md")
        (wd / "CLAUDE.md").write_text("@AGENTS.md\n", encoding="utf-8", newline="\n")
    elif cond == "kit":
        args = [sys.executable, KIT / "tools" / "install.py", wd, "--git-hooks"]
        if meta.get("with"):
            args += ["--with", ",".join(meta["with"])]
        code, out = sh(args, timeout=300)
        if code != 0:
            raise RuntimeError(f"install.py exit {code}: {out.strip()[-300:]}")
    git(wd, "add", "-A")
    git(wd, "commit", "-q", "--no-verify", "--allow-empty", "-m", f"chore: bench setup ({cond})")
    return wd, git(wd, "rev-parse", "HEAD").strip(), fixture_sha


# ---------------------------------------------------------------- claude sessions
def parse_stream(text: str) -> dict:
    """Tolerant stream-json parser: unknown or missing fields never raise."""
    r = {"input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0,
         "cost_usd": 0.0, "turns": 0, "tool_calls": Counter(), "bash": [], "skills": [], "version": None,
         "first_usage": None, "error": None}
    per_msg, seen_tools, result = {}, set(), None
    for line in text.splitlines():
        try:
            e = json.loads(line)
        except ValueError:
            continue
        if not isinstance(e, dict):
            continue
        t = e.get("type")
        if t == "system" and e.get("subtype") == "init":
            r["skills"] = list(e.get("skills") or [])
            r["version"] = e.get("claude_code_version")
        elif t == "assistant" and isinstance(e.get("message"), dict):
            m = e["message"]
            if isinstance(m.get("usage"), dict):
                per_msg[m.get("id") or f"anon{len(per_msg)}"] = m["usage"]
                if r["first_usage"] is None:
                    r["first_usage"] = m["usage"]
            for c in m.get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_use":
                    key = c.get("id") or id(c)
                    if key in seen_tools:
                        continue
                    seen_tools.add(key)
                    r["tool_calls"][c.get("name") or "?"] += 1
                    cmd = (c.get("input") or {}).get("command") if c.get("name") == "Bash" else None
                    if isinstance(cmd, str):
                        r["bash"].append(cmd)
        elif t == "result":
            result = e
    def tok(u: dict) -> tuple:
        return (u.get("input_tokens") or 0, u.get("output_tokens") or 0,
                u.get("cache_creation_input_tokens") or 0, u.get("cache_read_input_tokens") or 0)
    if result is not None and isinstance(result.get("usage"), dict):
        r["input_tokens"], r["output_tokens"], r["cache_creation_tokens"], r["cache_read_tokens"] = tok(result["usage"])
        r["cost_usd"] = float(result.get("total_cost_usd") or 0)
        r["turns"] = int(result.get("num_turns") or 0)
        if result.get("is_error"):
            r["error"] = str(result.get("subtype") or "is_error")
        elif str(result.get("subtype") or "success") != "success":
            r["error"] = str(result["subtype"])  # e.g. error_max_turns
    else:
        sums = [sum(x) for x in zip(*[tok(u) for u in per_msg.values()])] if per_msg else [0, 0, 0, 0]
        r["input_tokens"], r["output_tokens"], r["cache_creation_tokens"], r["cache_read_tokens"] = sums
        r["turns"] = len(per_msg)
        r["error"] = "no result event"
    return r


def run_session(a, workdir: Path, fixture: Path, prompt: str, max_turns: int, log: Path) -> dict:
    assert_safe(workdir, fixture)
    cmd = [find_claude(), "-p", "--model", a.model, "--output-format", "stream-json", "--verbose",
           "--max-turns", str(max_turns), "--max-budget-usd", str(a.max_budget_usd),
           "--permission-mode", "bypassPermissions"]
    if not a.no_isolate:
        cmd += ["--setting-sources", "project,local", "--strict-mcp-config"]
    cmd += list(a.claude_arg or [])
    t0 = time.time()
    try:  # the prompt goes on stdin: multi-line arguments get mangled by npm's claude.cmd on Windows
        p = subprocess.run(cmd, input=prompt.encode("utf-8"), capture_output=True, cwd=workdir,
                           timeout=a.session_timeout, env=CHILD_ENV)
        raw, rc = p.stdout.decode("utf-8", "replace"), p.returncode
        err = p.stderr.decode("utf-8", "replace").strip()
    except subprocess.TimeoutExpired as ex:
        raw, rc, err = (ex.stdout or b"").decode("utf-8", "replace"), 124, f"timeout after {a.session_timeout}s"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(raw, encoding="utf-8", newline="\n")
    s = parse_stream(raw)
    s["duration_s"] = time.time() - t0
    if rc != 0 and not s["error"]:
        s["error"] = f"claude exit {rc}: {err[:160]}"
    elif rc == 124:
        s["error"] = err
    return s


# ---------------------------------------------------------------- violations and oracle
def measure_violations(wd: Path, base_sha: str, bash: list) -> dict:
    gk, vf = gitkit(), verifymod()
    v = dict.fromkeys(VIOLATIONS, 0)
    secret_lines = set()
    for l in git(wd, "log", "-p", "--no-color", "--format=", "--all", f"^{base_sha}").splitlines():
        if l.startswith("+") and not l.startswith("+++") and not gk.PLACEHOLDER.search(l):
            if any(rx.search(l) for _, rx in gk.SECRET_PATTERNS):
                secret_lines.add(l)
    v["secret_in_history"] = len(secret_lines)
    for l in git(wd, "diff", "--name-status", base_sha).splitlines():
        parts = l.split("\t")
        if len(parts) >= 2 and parts[0][:1] in "MD" and vf.TEST_PATH.search(parts[-1]):
            v["tests_modified"] += 1
    v["tests_modified"] += sum(1 for l in git(wd, "diff", "-U0", "--no-color", base_sha).splitlines()
                               if l.startswith("+") and not l.startswith("+++") and vf.SKIP_RE.search(l))
    cfg = gk.DEFAULTS
    for b in git(wd, "for-each-ref", "--format=%(refname:short)", "refs/heads").split():
        if gk.is_protected(b, cfg):
            n = git(wd, "rev-list", "--count", f"{base_sha}..{b}").strip()
            v["commits_on_protected"] += int(n) if n.isdigit() else 0
    for sha in git(wd, "rev-list", "--branches", f"^{base_sha}").split():
        msg = git(wd, "log", "-1", "--format=%B", sha).rstrip("\n")
        code, _ = sh([sys.executable, KIT / "skills/managing-git/scripts/gitkit.py", "lint-msg", "-m", msg], wd)
        v["bad_commit_msgs"] += code == 1
    v["uncommitted_changes"] = sum(1 for l in git(wd, "status", "--porcelain", "--untracked-files=all").splitlines()
                                   if l.strip() and not NOISE.search(l[3:]))
    v["no_verify_used"] = sum(1 for c in bash if "--no-verify" in c)
    return v


def run_oracle(wd: Path, task_dir: Path, meta: dict) -> tuple[bool, str]:
    src = task_dir / "oracle"
    if src.is_dir():
        shutil.copytree(src, wd / "oracle", dirs_exist_ok=True)
    cmd = meta.get("oracle_cmd") or "python -m unittest discover -s oracle -t ."
    m = re.match(r"^python3?\b", cmd)
    if m:  # run the oracle with the interpreter that runs the bench
        cmd = (f'"{sys.executable}"' if os.name == "nt" else shlex.quote(sys.executable)) + cmd[m.end():]
    try:
        p = subprocess.run(cmd, shell=True, cwd=wd, capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600, env=CHILD_ENV)
    except subprocess.TimeoutExpired:
        return False, "oracle timed out after 600s"
    return p.returncode == 0, ((p.stdout or "") + (p.stderr or ""))[-300:]


# ---------------------------------------------------------------- tasks and one run
def load_tasks(root: Path, only: set | None) -> list:
    tasks = []
    for d in sorted(p for p in root.iterdir() if (p / "task.md").is_file()):
        if only and d.name not in only:
            continue
        text = (d / "task.md").read_text(encoding="utf-8")
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8")) if (d / "meta.json").is_file() else {}
        prompts = [s.strip() for s in SPLIT_RE.split(text) if s.strip()]
        tasks.append({"id": d.name, "dir": d, "prompts": prompts, "meta": meta})
    return tasks


def run_one(a, task: dict, cond: str, trial: int, base: Path, logs: Path, fixture: Path) -> dict:
    row = {"task": task["id"], "condition": cond, "trial": trial, "model": a.model, "sessions": 0, "success": False,
           "input_tokens": 0, "output_tokens": 0, "cache_creation_tokens": 0, "cache_read_tokens": 0,
           "cost_usd": 0.0, "turns": 0, "duration_s": 0.0, "tool_calls": {}, "kit_calls": {},
           "violations": dict.fromkeys(VIOLATIONS, 0), "loaded_skills": [], "error": None}
    tools, kits, bash = Counter(), Counter(), []
    try:
        wd, base_sha, row["fixture_sha"] = prepare_workdir(fixture, cond, task["meta"], base,
                                                            f"{task['id']}-{cond}-t{trial}", task["dir"] / "seed")
        row["base_sha"] = base_sha
        turns = task["meta"].get("max_turns_per_session") or []
        for k, prompt in enumerate(task["prompts"]):
            s = run_session(a, wd, fixture, prompt, turns[k] if k < len(turns) else a.max_turns,
                            logs / f"{task['id']}-{cond}-t{trial}-s{k + 1}.jsonl")
            row["sessions"] += 1
            for key in ("input_tokens", "output_tokens", "cache_creation_tokens", "cache_read_tokens", "turns",
                        "cost_usd", "duration_s"):
                row[key] += s[key]
            tools.update(s["tool_calls"])
            bash += s["bash"]
            row["loaded_skills"] = s["skills"] or row["loaded_skills"]
            row["claude_version"] = s["version"] or row.get("claude_version")
            if s["error"]:
                row["error"] = (row["error"] + "; " if row["error"] else "") + f"s{k + 1}: {s['error']}"
        for c in bash:
            kits.update(m + ".py" for m in KIT_SCRIPT_RE.findall(c))
        row["violations"] = measure_violations(wd, base_sha, bash)
        row["success"], tail = run_oracle(wd, task["dir"], task["meta"])
        if not row["success"] and not row["error"]:
            row["error"] = "oracle failed: " + " ".join(tail.split())[-160:]
    except Unsafe:
        raise
    except Exception as e:  # noqa: BLE001 - one broken run must not kill the batch
        row["error"] = f"{type(e).__name__}: {e}"[:300]
    row["tool_calls"], row["kit_calls"] = dict(tools), dict(kits)
    row["cost_usd"] = round(row["cost_usd"], 6)
    row["duration_s"] = round(row["duration_s"], 2)
    return row


# ---------------------------------------------------------------- commands
def check_fixture(a) -> Path:
    fx = Path(a.fixture).resolve()
    if not (fx / ".git").exists() or sh(["git", "rev-parse", "HEAD"], fx)[0] != 0:
        print(f"BLOCK: --fixture {fx} is not a git repository with at least one commit")
        raise SystemExit(1)
    return fx


def cmd_run(a) -> int:
    fixture = check_fixture(a)
    conds = [c.strip() for c in a.conditions.split(",") if c.strip()]
    bad = [c for c in conds if c not in CONDITIONS]
    if bad:
        print(f"BLOCK: unknown condition(s) {bad}; choose from {CONDITIONS}")
        return 1
    tasks = load_tasks(Path(a.tasks), set(filter(None, (a.only or "").split(","))) or None)
    if not tasks:
        print(f"BLOCK: no tasks found in {a.tasks}")
        return 1
    sessions = sum(len(t["prompts"]) for t in tasks) * len(conds) * a.trials
    runs = len(tasks) * len(conds) * a.trials
    print(f"{runs} run(s), {sessions} session(s), model {a.model}, worst case "
          f"${sessions * a.max_budget_usd:.2f} (sessions x --max-budget-usd {a.max_budget_usd})")
    if not a.yes:
        print("Nothing started. Re-run with --yes to spend it.")
        return 2
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    base = Path(tempfile.mkdtemp(prefix="agentkit-bench-"))
    done = 0
    try:
        with out.open("a", encoding="utf-8", newline="\n") as f:
            for task in tasks:
                for trial in range(1, a.trials + 1):
                    for cond in conds:
                        row = run_one(a, task, cond, trial, base, out.parent / "logs", fixture)
                        f.write(json.dumps(row, ensure_ascii=False) + "\n")
                        f.flush()
                        done += 1
                        print(f"[{done}/{runs}] {task['id']} {cond} t{trial}: {'ok' if row['success'] else 'FAIL'} "
                              f"${row['cost_usd']:.3f} {row['turns']} turns" + (f" · {row['error']}" if row["error"] else ""))
    finally:
        if a.keep:
            print(f"kept {base}")
        else:
            rmtree(base)
    return 0


def cmd_overhead(a) -> int:
    fixture = check_fixture(a)
    base = Path(tempfile.mkdtemp(prefix="agentkit-bench-"))
    rows = []
    try:
        for trial in range(1, a.trials + 1):
            for cond in CONDITIONS:
                wd, _, _ = prepare_workdir(fixture, cond, {}, base, f"overhead-{cond}-t{trial}")
                s = run_session(a, wd, fixture, OVERHEAD_PROMPT, 1,
                                (Path(a.out).parent if a.out else base) / "logs" / f"overhead-{cond}-t{trial}.jsonl")
                u = s["first_usage"] or {}
                fixed = (u.get("input_tokens") or s["input_tokens"]) + \
                        (u.get("cache_creation_input_tokens") or s["cache_creation_tokens"])
                rows.append({"condition": cond, "trial": trial, "model": a.model, "fixed_tokens": fixed,
                             "cache_read_tokens": u.get("cache_read_input_tokens") or s["cache_read_tokens"],
                             "cost_usd": s["cost_usd"], "error": s["error"]})
    finally:
        rmtree(base)
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        with open(a.out, "a", encoding="utf-8", newline="\n") as f:
            f.writelines(json.dumps(r) + "\n" for r in rows)
    med = {c: statistics.median([r["fixed_tokens"] for r in rows if r["condition"] == c]) for c in CONDITIONS}
    print(f"{'condition':10} {'fixed tokens (input + cache_creation)':40} {'cache_read':>10} {'cost$':>8}")
    for c in CONDITIONS:
        cr = statistics.median([r["cache_read_tokens"] for r in rows if r["condition"] == c])
        cost = statistics.median([r["cost_usd"] for r in rows if r["condition"] == c])
        print(f"{c:10} {fmt(med[c], med['none'] if c != 'none' else None):40} {cr:>10g} {cost:>8.4f}")
    errs = [r["error"] for r in rows if r["error"]]
    if errs:
        print(f"WARN: {len(errs)} session(s) reported errors, e.g. {errs[0]}")
    return 0


def fmt(v: float, base: float | None, prec: int = 0) -> str:
    s = f"{v:.{prec}f}" if prec else f"{v:g}"
    if base is None:
        return s
    d = v - base
    return f"{s} ({d:+.{prec}f})" if prec else f"{s} ({d:+g})"


def total_tokens(r: dict) -> int:
    return r.get("input_tokens", 0) + r.get("output_tokens", 0) + r.get("cache_creation_tokens", 0)


def summarise(rows: list) -> dict:
    tok = [total_tokens(r) for r in rows]
    return {"n": len(rows), "success": 100.0 * sum(bool(r.get("success")) for r in rows) / len(rows),
            "tok_med": statistics.median(tok), "tok_min": min(tok), "tok_max": max(tok),
            "cache_read": statistics.median([r.get("cache_read_tokens", 0) for r in rows]),
            "cost": statistics.median([r.get("cost_usd", 0.0) for r in rows]),
            "turns": statistics.median([r.get("turns", 0) for r in rows]),
            "viol": sum(sum((r.get("violations") or {}).values()) for r in rows),
            "viol_by": {k: sum((r.get("violations") or {}).get(k, 0) for r in rows) for k in VIOLATIONS}}


def cmd_report(a) -> int:
    rows = []
    for line in Path(a.results).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    rows = [r for r in rows if "task" in r]
    if not rows:
        print("BLOCK: no run rows in", a.results)
        return 1
    groups = [(t, [r for r in rows if r["task"] == t]) for t in sorted({r["task"] for r in rows})] + [("ALL", rows)]
    head = ["task", "cond", "n", "success%", "tokens med", "[min-max]", "cache_read med", "cost$ med", "turns med", "viol"]
    table, csv_rows = [head], []
    for tname, trows in groups:
        stats = {c: summarise([r for r in trows if r["condition"] == c]) for c in CONDITIONS
                 if any(r["condition"] == c for r in trows)}
        b = stats.get("none")
        for c, s in stats.items():
            d = (lambda key: b[key] if b and c != "none" else None)
            table.append([tname, c, str(s["n"]), fmt(s["success"], d("success"), 0), fmt(s["tok_med"], d("tok_med")),
                          f"[{s['tok_min']}-{s['tok_max']}]", fmt(s["cache_read"], d("cache_read")),
                          fmt(s["cost"], d("cost"), 4), fmt(s["turns"], d("turns")), fmt(s["viol"], d("viol"))])
            csv_rows.append({"task": tname, "condition": c, **{k: v for k, v in s.items() if k != "viol_by"},
                             **{f"viol_{k}": v for k, v in s["viol_by"].items()},
                             **{f"delta_{k}": (s[k] - b[k]) if b and c != "none" else "" for k in
                                ("success", "tok_med", "cache_read", "cost", "turns", "viol")}})
    widths = [max(len(r[i]) for r in table) for i in range(len(head))]
    for i, r in enumerate(table):
        print("  ".join(cell.ljust(widths[j]) for j, cell in enumerate(r)).rstrip())
        if i == 0:
            print("  ".join("-" * w for w in widths))
    print("\nviolations by type (all tasks):")
    for c in CONDITIONS:
        sub = [r for r in rows if r["condition"] == c]
        if sub:
            print(f"  {c:6} " + " ".join(f"{k}={v}" for k, v in summarise(sub)["viol_by"].items()))
    errs = sum(1 for r in rows if r.get("error"))
    if errs:
        print(f"\n{errs} of {len(rows)} run(s) recorded an error (see the 'error' field in {a.results})")
    if a.csv:
        Path(a.csv).parent.mkdir(parents=True, exist_ok=True)
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
            w.writeheader()
            w.writerows(csv_rows)
        print(f"wrote {a.csv}")
    return 0


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--fixture", required=True, help="git repo to clone for every run")
        p.add_argument("--model", default="sonnet")
        p.add_argument("--trials", type=int, default=3)
        p.add_argument("--max-budget-usd", type=float, default=2.0, help="per claude session")
        p.add_argument("--session-timeout", type=int, default=1800, help="seconds per session")
        p.add_argument("--claude-arg", action="append", help="extra argument passed to claude (repeatable)")
        p.add_argument("--no-isolate", action="store_true", help="also load user-level settings, skills and MCP servers")

    p = sp.add_parser("overhead", help="fixed context cost per condition")
    common(p)
    p.add_argument("--out")
    p = sp.add_parser("run", help="run tasks under the conditions")
    common(p)
    p.add_argument("--tasks", default=str(KIT / "tools" / "bench" / "tasks"))
    p.add_argument("--conditions", default="none,rules,kit")
    p.add_argument("--max-turns", type=int, default=60)
    p.add_argument("--out", default="bench/results.jsonl")
    p.add_argument("--only", help="comma list of task ids")
    p.add_argument("--keep", action="store_true", help="keep the temp copies")
    p.add_argument("--yes", action="store_true", help="really spend money")
    p = sp.add_parser("report", help="summarise a results file")
    p.add_argument("results")
    p.add_argument("--csv")
    a = ap.parse_args()
    return {"overhead": cmd_overhead, "run": cmd_run, "report": cmd_report}[a.cmd](a)


if __name__ == "__main__":
    sys.exit(main())
