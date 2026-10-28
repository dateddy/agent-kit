#!/usr/bin/env python3
"""plan — executor-ready implementation plans, validated by rules instead of judgment.

  plan.py template [--goal G] [--steps N]   print a plan skeleton
  plan.py path                              where the current branch's plan lives
  plan.py check [FILE]                      validate (default FILE = current branch's plan)
  plan.py apply [FILE]                      check, then load steps into the keeping-notes task note
  plan.py step N [FILE]                     print only step N (+ constraints) for the executor

Plan file format (markdown):
  # Plan: <goal>
  Constraints: <...>          Done when: <...>          Out of scope: <...>
  Decisions: D-012, D-013     (optional; ids must exist in notes/decisions.md)
  ## 1. <step title>
  Files: src/a.py, tests/test_a.py (new)
  Do: <what to change; may span lines>
  Verify: <command that proves the step>
  Done: <observable criterion>

Exit: 0 OK · 1 BLOCK · 2 WARN
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2
STEP_FIELDS = ("Files", "Do", "Verify", "Done")
HEAD_FIELDS = ("Constraints", "Done when", "Out of scope", "Decisions")
# A step longer than ~800 chars of instructions is usually 2+ steps in disguise
MAX_DO_CHARS = 800
# Beyond ~8 steps a plan is better split into separate tasks/branches
MAX_STEPS_WARN = 8
VAGUE = re.compile(r"\b(works?|working|correctly|properly|as expected|good|better|clean(er)?|nice|"
                   r"fine|ok|hoạt động tốt|ổn|đúng|chạy được)\b", re.I)
# template placeholders look like "<step title>" (contain a space); generics like List<int> do not
PLACEHOLDER = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b|\?\?\?|<[a-z][^<>\n]*\s[^<>\n]*>")
MANUAL = re.compile(r"\b(manual(ly)?|visually|by hand|eyeball|thủ công|xem bằng mắt)\b", re.I)


CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

def sh(*cmd: str) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def root() -> Path:
    r = sh("git", "rev-parse", "--show-toplevel")
    return Path(r) if r else Path.cwd()


def notes_dir(r: Path) -> Path:
    try:
        d = json.loads((r / ".agentkit.json").read_text(encoding="utf-8")).get("notes", {}).get("dir", "notes")
    except Exception:
        d = "notes"
    return r / d


def slug(branch: str) -> str:  # must match keeping-notes/scripts/notes.py
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch.replace("/", "--")).strip("-")


def default_path(r: Path) -> Path | None:
    b = sh("git", "symbolic-ref", "--quiet", "--short", "HEAD")
    return notes_dir(r) / "tasks" / f"{slug(b)}.plan.md" if b else None


DEC_HEADER = re.compile(r"^## (D-\d{3,}) · ")
DEC_ID = re.compile(r"\bD-\d{3,}\b")


def load_decisions(path: Path) -> dict[str, str | None]:
    """decision id -> id of the entry that supersedes it (None if still active)."""
    out: dict[str, str | None] = {}
    sup: dict[str, str] = {}
    cur = None
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = DEC_HEADER.match(line)
            if m:
                cur = m[1]
                out[cur] = None
            elif cur and line.startswith("Supersedes: "):
                sup[line[12:].strip()] = cur
    for old, new in sup.items():
        if old in out:
            while new in sup:  # follow the chain to the live replacement
                new = sup[new]
            out[old] = new
    return out


# ---------------------------------------------------------------- parsing
def parse(text: str) -> dict:
    plan = {"goal": "", "head": {}, "steps": []}
    cur, field = None, None
    for raw in text.splitlines():
        line = raw.rstrip()
        if line.startswith("# Plan:"):
            plan["goal"] = line[7:].strip()
            continue
        m = re.match(r"^##\s+(\d+)\.\s*(.*)$", line)
        if m:
            cur = {"n": int(m[1]), "title": m[2].strip(), **{f: "" for f in STEP_FIELDS}}
            plan["steps"].append(cur)
            field = None
            continue
        m = re.match(r"^(Files|Do|Verify|Done when|Done|Constraints|Out of scope|Decisions):\s*(.*)$", line)
        if m:
            key, val = m[1], m[2].strip()
            if cur is None and key in HEAD_FIELDS:
                plan["head"][key] = val
                field = ("head", key)
            elif cur is not None and key in STEP_FIELDS:
                cur[key] = val
                field = ("step", key)
            continue
        if field and line.strip():  # continuation line
            tgt = plan["head"] if field[0] == "head" else cur
            tgt[field[1]] = (tgt[field[1]] + "\n" + line.strip()).strip()
    return plan


def split_files(s: str) -> list[tuple[str, bool]]:
    out = []
    for part in re.split(r"[,\n]", s):
        part = part.strip().strip("`").strip()
        if not part:
            continue
        new = bool(re.search(r"\((new|mới)\)\s*$", part, re.I))
        out.append((re.sub(r"\s*\((new|mới)\)\s*$", "", part, flags=re.I).strip("`"), new))
    return out


# ---------------------------------------------------------------- commands
def check_plan(path: Path, r: Path) -> tuple[int, dict | None]:
    if not path or not path.exists():
        print(f"BLOCK: plan file not found: {path}. Write it there (plan.py path), then re-run.")
        return BLOCK, None
    text = path.read_text(encoding="utf-8")
    plan = parse(text)
    errs, warns = [], []
    if not plan["goal"]:
        errs.append("missing '# Plan: <goal>' header")
    if "Done when" not in plan["head"]:
        warns.append("no 'Done when:' for the whole task — how will the user know it is finished?")
    ids = DEC_ID.findall(plan["head"].get("Decisions", ""))
    if ids:
        dec_file = notes_dir(r) / "decisions.md"
        known = load_decisions(dec_file)
        for i in dict.fromkeys(ids):
            if i not in known:
                errs.append(f"decision {i} not found in {dec_file.relative_to(r).as_posix()}")
            elif known[i]:
                warns.append(f"{i} is superseded by {known[i]} — reference {known[i]} instead")
    steps = plan["steps"]
    if not steps:
        errs.append("no steps ('## 1. <title>')")
    if len(steps) > MAX_STEPS_WARN:
        warns.append(f"{len(steps)} steps — consider splitting into smaller tasks/branches")
    for i, s in enumerate(steps, 1):
        tag = f"step {s['n']}"
        if s["n"] != i:
            errs.append(f"{tag}: numbering must be 1..N in order (expected {i})")
        if not s["title"]:
            errs.append(f"{tag}: missing title")
        for f in STEP_FIELDS:
            if not s[f].strip():
                errs.append(f"{tag}: missing '{f}:'")
        for fp, new in split_files(s["Files"]):
            if any(c in fp for c in "*?"):
                warns.append(f"{tag}: glob '{fp}' — list concrete files so the executor does not search")
            elif not new and not (r / fp).exists():
                errs.append(f"{tag}: '{fp}' does not exist — fix the path or mark it '(new)'")
        if len(s["Do"]) > MAX_DO_CHARS:
            warns.append(f"{tag}: 'Do' is {len(s['Do'])} chars — split the step")
        if s["Verify"] and MANUAL.search(s["Verify"]):
            warns.append(f"{tag}: manual verification — prefer a command (test, script, curl)")
        if s["Done"] and VAGUE.search(s["Done"]) and not re.search(r"\d|`|==|returns|raises|exit", s["Done"]):
            warns.append(f"{tag}: 'Done' is vague ('{VAGUE.search(s['Done'])[0]}') — state an observable result")
    for ln, line in enumerate(text.splitlines(), 1):
        if PLACEHOLDER.search(line):
            errs.append(f"line {ln}: unresolved placeholder: {line.strip()[:70]}")
    for e in errs:
        print("BLOCK:", e)
    for w in warns:
        print("WARN:", w)
    if errs:
        return BLOCK, plan
    print(f"OK: {len(steps)} step(s), goal: {plan['goal'][:70]}" if not warns else "OK with warnings")
    return (WARN if warns else OK), plan


def cmd_template(a, r):
    n = max(1, a.steps)
    out = [f"# Plan: {a.goal or '<one-line outcome>'}", "Constraints: <what must not change / required tech>",
           "Done when: <observable result for the whole task>", "Out of scope: <deferred work>", ""]
    for i in range(1, n + 1):
        out += [f"## {i}. <step title>", "Files: <path>, <path> (new)", "Do: <exact change: functions, signatures, behavior>",
                "Verify: <command, e.g. python -m pytest tests/test_x.py -q>", "Done: <observable criterion>", ""]
    print("\n".join(out).rstrip())
    return OK


def cmd_path(a, r):
    p = default_path(r)
    if not p:
        print("BLOCK: detached HEAD — create the task branch first")
        return BLOCK
    print(p.relative_to(r))
    return OK


def resolve(a, r) -> Path | None:
    return Path(a.file).resolve() if a.file else default_path(r)


def cmd_check(a, r):
    return check_plan(resolve(a, r), r)[0]


def cmd_apply(a, r):
    path = resolve(a, r)
    code, plan = check_plan(path, r)
    if code == BLOCK:
        return BLOCK
    notes = Path(__file__).resolve().parents[2] / "keeping-notes" / "scripts" / "notes.py"
    args = ["task", "--goal", plan["goal"], "--reset-plan"]
    for s in plan["steps"]:
        args += ["--plan", s["title"]]
    args += ["--next", f"step 1: {plan['steps'][0]['title']} (plan.py step 1)"]
    if not notes.exists():
        print("WARN: keeping-notes not installed; record the plan with:")
        print("  notes.py " + " ".join(f'"{x}"' if " " in x else x for x in args))
        return WARN
    p = subprocess.run([sys.executable, str(notes), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV)
    print(p.stdout.strip() or p.stderr.strip())
    return code if p.returncode == 0 else p.returncode


def cmd_step(a, r):
    path = resolve(a, r)
    if not path or not path.exists():
        print(f"BLOCK: plan file not found: {path}")
        return BLOCK
    plan = parse(path.read_text(encoding="utf-8"))
    s = next((x for x in plan["steps"] if x["n"] == a.n), None)
    if not s:
        print(f"BLOCK: step {a.n} not found (1..{len(plan['steps'])})")
        return BLOCK
    print(f"STEP {s['n']}/{len(plan['steps'])}: {s['title']}   (task: {plan['goal']})")
    for k in ("Constraints", "Out of scope", "Decisions"):
        if plan["head"].get(k):
            print(f"{k}: {plan['head'][k]}")
    for f in STEP_FIELDS:
        print(f"{f}: {s[f]}")
    return OK


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="plan", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("template"); s.add_argument("--goal"); s.add_argument("--steps", type=int, default=3)
    sp.add_parser("path")
    s = sp.add_parser("check"); s.add_argument("file", nargs="?")
    s = sp.add_parser("apply"); s.add_argument("file", nargs="?")
    s = sp.add_parser("step"); s.add_argument("n", type=int); s.add_argument("file", nargs="?")
    a = ap.parse_args()
    r = root()
    return {"template": cmd_template, "path": cmd_path, "check": cmd_check, "apply": cmd_apply, "step": cmd_step}[a.cmd](a, r)


if __name__ == "__main__":
    sys.exit(main())
