#!/usr/bin/env python3
"""plan — executor-ready implementation plans, validated by rules instead of judgment.

  plan.py template [--goal G] [--steps N]   print a plan skeleton
  plan.py path                              where the current branch's plan lives
  plan.py check [FILE]                      validate (default FILE = current branch's plan)
  plan.py apply [FILE]                      check, then load steps into the keeping-notes task note
  plan.py step N [FILE]                     print only step N (+ constraints) for the executor
  plan.py field NAME [--cumulative] [--step N] [--plan FILE] [--note FILE]
                                            one field of the active step (what scripts/plan-step.sh calls)

Plan file format (markdown):
  # Plan: <goal>
  Constraints: <...>          Done when: <...>          Out of scope: <...>
  Decisions: D-012, D-013     (optional; ids must exist in notes/decisions.md)
  ### Step 1: <step title>
  - Files: src/a.py, tests/test_a.py (new)
  - Contract: <signatures, input/output, invariants; may span lines>
  - Tests: test_a_ok, test_a_empty      (written before the code: the definition of done)
  - Out of scope: <what must not be touched>
  - Verify: <command that proves the step>      (optional)

The Files / Tests / Out of scope fields are read by scripts/plan-step.sh and scripts/verify.sh (scope check).
The old format (`## 1. title` with Files/Do/Verify/Done lines) is still read; `check` WARNs on it.

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
STEP_FIELDS = ("Files", "Do", "Verify", "Done")  # old format: all required
NEW_FIELDS = ("Mode", "Files", "Contract", "Tests", "Out of scope", "Verify")  # Verify is optional
NEW_REQUIRED = ("Files", "Contract", "Tests", "Out of scope")  # Mode is still parsed, never required (it is ignored)
ALL_STEP_FIELDS = tuple(dict.fromkeys(STEP_FIELDS + NEW_FIELDS))
HEAD_FIELDS = ("Constraints", "Done when", "Out of scope", "Decisions")
STEP_NEW = re.compile(r"^###\s+Step\s+(\d+)\s*:\s*(.*)$")
STEP_OLD = re.compile(r"^##\s+(\d+)\.\s*(.*)$")
FIELD_LINE = re.compile(r"^(?:-\s+)?(Files|Do|Verify|Done when|Done|Constraints|Out of scope|Decisions|"
                        r"Mode|Contract|Tests):\s*(.*)$")
NO_TESTS = re.compile(r"(none|n/?a|-|—|không)", re.I)
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


# must match keeping-notes/scripts/notes.py: D-<4 hex>, or an old sequential D-001
DEC_HEADER = re.compile(r"^## (D-(?:[0-9a-f]{4}|\d{3,})) · ")
DEC_ID = re.compile(r"\bD-(?:[0-9a-f]{4}|\d{3,})\b")


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
        m = STEP_NEW.match(line) or STEP_OLD.match(line)
        if m:
            fmt = "new" if line.startswith("###") else "old"
            cur = {"n": int(m[1]), "title": m[2].strip(), "fmt": fmt, **{f: "" for f in ALL_STEP_FIELDS}}
            plan["steps"].append(cur)
            field = None
            continue
        if line.startswith("#"):  # any other heading ends the current step
            cur, field = None, None
            continue
        m = FIELD_LINE.match(line)
        if m:
            key, val = m[1], m[2].strip()
            allowed = None if cur is None else (NEW_FIELDS if cur["fmt"] == "new" else STEP_FIELDS)
            if cur is None and key in HEAD_FIELDS:
                plan["head"][key] = val
                field = ("head", key)
            elif cur is not None and key in allowed:
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
        old = s["fmt"] == "old"
        if not s["title"]:
            errs.append(f"{tag}: missing title")
        if old:
            warns.append(f"{tag}: old format (Files/Do/Verify/Done) — rewrite as '### Step N: <title>' with "
                         "Mode, Files, Contract, Tests, Out of scope (see `pl template`)")
        for f in (STEP_FIELDS if old else NEW_REQUIRED):
            if not s[f].strip():
                errs.append(f"{tag}: missing '{f}:'")
        if not old:
            if s["Mode"].strip():
                warns.append(f"{tag}: Mode is ignored (nothing reads it) — delete the 'Mode:' line")
            if s["Tests"].strip() and NO_TESTS.fullmatch(s["Tests"].strip()):
                warns.append(f"{tag}: no tests — name the tests that define done, or say in Contract why none apply")
        for fp, new in split_files(s["Files"]):
            if any(c in fp for c in "*?"):
                warns.append(f"{tag}: glob '{fp}' — list concrete files so the executor does not search")
            elif not new and not (r / fp).exists():
                errs.append(f"{tag}: '{fp}' does not exist — fix the path or mark it '(new)'")
        body_field = "Do" if old else "Contract"
        if len(s[body_field]) > MAX_DO_CHARS:
            warns.append(f"{tag}: '{body_field}' is {len(s[body_field])} chars — split the step")
        if s["Verify"] and MANUAL.search(s["Verify"]):
            warns.append(f"{tag}: manual verification — prefer a command (test, script, curl)")
        if old and s["Done"] and VAGUE.search(s["Done"]) and not re.search(r"\d|`|==|returns|raises|exit", s["Done"]):
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
        out += [f"### Step {i}: <step title>", "- Files: <path>, <path> (new)",
                "- Contract: <signatures, input/output, invariants>",
                "- Tests: <test_name>, <test_name>",
                "- Out of scope: <what must not be touched>",
                "- Verify: <command, e.g. python -m pytest tests/test_x.py -q>", ""]
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
    for f in (STEP_FIELDS if s["fmt"] == "old" else NEW_FIELDS):
        if f == "Mode":
            continue
        if s[f] or f in STEP_FIELDS or f in NEW_REQUIRED:
            print(f"{f}: {s[f]}")
    return OK


# ---------------------------------------------------------------- field (what scripts/plan-step.sh prints)
# A line-for-line port of the awk in plan-step.sh of 0.4.0, so the output and the exit codes stay identical:
# it reads "### Step N: title" + "- Field: value" bullets and the old "## N. title" + "Field: value" form; a
# continuation line extends the current field; a repeated field replaces the earlier value.
FIELD_NAMES = ("title", "mode", "files", "contract", "tests", "out-of-scope", "verify", "do", "done")
H_STEP_NEW = re.compile(r"^###[ \t]+Step[ \t]+[0-9]+[ \t]*:")
H_STEP_OLD = re.compile(r"^##[ \t]+[0-9]+\.")
H_FIELD = re.compile(r"^(- )?(Mode|Files|Contract|Tests|Out of scope|Verify|Do|Done):")
SPACE = " \t\n\r\f\v"


def field_values(text: str, want: list[str], fld: str) -> tuple[list[str], list[str]]:
    """(values, problems): the field `fld` of each wanted step in order; problems are the stderr lines."""
    ok = set(want)
    seen: set[int] = set()
    has: set[int] = set()
    val: dict[int, str] = {}
    step, field = 0, ""
    for line in text.replace("\r", "").split("\n"):
        if H_STEP_NEW.match(line):
            s = re.sub(r"^###[ \t]+Step[ \t]+", "", line)
            step = int(re.sub(r"[ \t]*:.*", "", s, count=1))
            seen.add(step)
            field = ""
            if str(step) in ok and fld == "title":
                val[step] = re.sub(r"^[0-9]+[ \t]*:[ \t]*", "", s, count=1)
                has.add(step)
        elif H_STEP_OLD.match(line):
            s = re.sub(r"^##[ \t]+", "", line)
            step = int(re.sub(r"\..*", "", s, count=1))
            seen.add(step)
            field = ""
            if str(step) in ok and fld == "title":
                val[step] = re.sub(r"^[0-9]+\.[ \t]*", "", s, count=1)
                has.add(step)
        elif line.startswith("#"):
            step, field = 0, ""
        elif step == 0:
            continue
        elif H_FIELD.match(line):
            s = re.sub(r"^- ", "", line)
            field = re.sub(r":.*", "", s, count=1).lower().replace(" ", "-")
            if str(step) in ok and field == fld:
                val[step] = re.sub(r"[ \t]+$", "", re.sub(r"^[^:]*:[ \t]*", "", s, count=1))
                has.add(step)
        elif not line.strip(" \t"):
            continue
        elif field and str(step) in ok and field == fld:
            val[step] = val.get(step, "") + "\n" + line.strip(" \t")
    values: list[str] = []
    problems: list[str] = []
    for w in want:
        n = int(w)
        if n not in seen:
            problems.append(f"step {n} not found in the plan")
        elif n not in has:
            problems.append(f'step {n} has no "{fld}" field')
        else:
            values.append(val[n])
    return values, problems


def active_step(note_text: str) -> str:
    """First unchecked item of the note's `## Plan` checklist, else the last item, else ""."""
    sec, first, last = "", "", ""
    for line in note_text.split("\n"):
        line = line.rstrip("\r")  # notes written on Windows are CRLF
        if line.startswith("## "):
            sec = line[3:].lower()
        elif sec == "plan" and re.match(r"^- \[[ x]\] [0-9]+\.", line):
            n = re.sub(r"\..*", "", re.sub(r"^- \[[ x]\] ", "", line), count=1)
            last = n
            if line[3:4] != "x" and not first:
                first = n
    return first or last


def files_lines(raw: str) -> list[str]:
    """`files` output: one path per line, backticks and a trailing (new) removed, no blanks, no duplicates."""
    out: list[str] = []
    for line in raw.replace(",", "\n").split("\n"):
        line = line.replace("`", "")
        line = re.sub(r"[ \t\r\f\v]*\((?:new|NEW|New|mới)\)[ \t\r\f\v]*$", "", line)
        line = line.strip(SPACE)
        if line and line not in out:
            out.append(line)
    return out


def cmd_field(a, r) -> int:
    """Exit 0 value printed, 1 error; the value goes to stdout, every diagnostic to stderr."""
    def fail(msg: str) -> int:
        print(msg, file=sys.stderr)
        return BLOCK

    fld = a.name
    if fld not in FIELD_NAMES:
        return fail(f"unknown field '{fld}'. Use: title mode files contract tests out-of-scope verify")
    step = a.step or ""
    if step and not (step.isascii() and step.isdigit()):
        return fail(f"--step needs a number, got '{step}'")
    plan, note = a.plan, a.note
    if plan is None or note is None:
        branch = sh("git", "symbolic-ref", "--quiet", "--short", "HEAD")
        if not branch:
            return fail("detached HEAD: pass --plan and --note, or switch to the task branch")
        base = notes_dir(r) / "tasks" / slug(branch)
        plan = plan if plan is not None else base.as_posix() + ".plan.md"
        note = note if note is not None else base.as_posix() + ".md"
    if not Path(plan).is_file():
        return fail(f"no plan at {plan}. Save the accepted plan there (pl path), then run pl apply.")
    if not step:
        if not Path(note).is_file():
            return fail(f"no task note at {note}, so the active step is unknown. Run pl apply, or pass --step N.")
        with open(note, encoding="utf-8", errors="surrogateescape", newline="") as f:
            step = active_step(f.read())
        if not step:
            return fail(f"task note {note} has no plan checklist. Run pl apply, or pass --step N.")
    # steps 1..N; for N < 1 there is no such range, so it is "step 0 not found" (0.4.0 asked the platform's seq: GNU printed nothing, BSD counted down)
    want = [str(i) for i in range(1, int(step) + 1)] if a.cumulative and int(step) >= 1 else [step]
    text = Path(plan).read_bytes().decode("utf-8", "surrogateescape")
    values, problems = field_values(text, want, fld)
    if problems:
        for p in problems:
            print(p, file=sys.stderr)
        return BLOCK
    raw = "".join(v + "\n" for v in values).rstrip("\n")  # what the shell's $(...) kept
    out = "".join(line + "\n" for line in files_lines(raw)) if fld == "files" else raw + "\n"
    sys.stdout.flush()
    sys.stdout.buffer.write(out.encode("utf-8", "surrogateescape"))
    sys.stdout.buffer.flush()
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
    s = sp.add_parser("field", description="print one field of the active step (stdout), diagnostics to stderr; exit 0 or 1")
    s.add_argument("name"); s.add_argument("--step"); s.add_argument("--cumulative", action="store_true")
    s.add_argument("--plan"); s.add_argument("--note")
    field_parser = s

    def field_usage_error(msg: str):  # argparse would exit 2; `field` has one failure code, 1 (like plan-step.sh)
        print(f"plan.py field: {msg}\n{field_parser.format_usage()}", file=sys.stderr)
        sys.exit(BLOCK)
    field_parser.error = field_usage_error
    a = ap.parse_args()
    r = root()
    return {"template": cmd_template, "path": cmd_path, "check": cmd_check, "apply": cmd_apply, "step": cmd_step,
            "field": cmd_field}[a.cmd](a, r)


if __name__ == "__main__":
    sys.exit(main())
