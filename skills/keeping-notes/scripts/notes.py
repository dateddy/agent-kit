#!/usr/bin/env python3
"""notes — task state per git branch + append-only decision log. Stdlib only.

Layout (under notes.dir, default "notes/"):
  tasks/<branch>.md   one note per task branch (gitignored by default)
  decisions.md        append-only log shared by the whole project (committed)

Exit codes: 0 OK · 1 BLOCK · 2 WARN

Subcommands:
  init                               create layout, add tasks/ to .gitignore
  task [--goal G] [--plan S]... [--check N]... [--done TXT] [--next TXT]
       [--files F...] [--status S]   create/update the current branch's note
  brief [--topic T]... [--log N]     compact context pack for a new session
  decide --topic T --text TXT [--why W] [--supersedes D-007]
  show [--topic T] [--id D-007] [--branch B] [--all] [--limit N]
  tasks [--all]                      list task notes
  close [--branch B] [--status S]    mark a task done/abandoned
  sync                               mark notes of merged/deleted branches done
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import subprocess
import sys
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2
DEFAULTS = {
    "dir": "notes",
    "commit_tasks": False,
    # ~600 chars ≈ 150 tokens: forces a decision to be a conclusion, not a transcript
    "max_decision_chars": 600,
    "max_decision_lines": 10,
    "base_branch": None,
}
STATUSES = ("active", "blocked", "done", "abandoned")
DEC_HEADER = re.compile(r"^## (D-\d{3,}) · (\d{4}-\d{2}-\d{2}) · ([a-z0-9._-]+) · (\S+)\s*$")


# ---------------------------------------------------------------- env
def run(*cmd: str) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def root() -> Path:
    r = run("git", "rev-parse", "--show-toplevel")
    return Path(r) if r else Path.cwd()


def load_cfg(r: Path) -> dict:
    cfg = dict(DEFAULTS)
    f = r / ".agentkit.json"
    if f.exists():
        try:
            cfg.update({k: v for k, v in json.loads(f.read_text(encoding="utf-8")).get("notes", {}).items()
                        if not k.startswith("_")})
        except json.JSONDecodeError as e:
            print(f"WARN: .agentkit.json invalid ({e}); using defaults")
    return cfg


def current_branch() -> str | None:
    return run("git", "symbolic-ref", "--quiet", "--short", "HEAD") or None


def now() -> str:
    return dt.datetime.now().strftime("%Y-%m-%d %H:%M")


def today() -> str:
    return dt.date.today().isoformat()


def slug(branch: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "-", branch.replace("/", "--")).strip("-")


# ---------------------------------------------------------------- task note model
class Note:
    def __init__(self, branch: str):
        self.branch, self.goal, self.status = branch, "", "active"
        self.created = self.updated = now()
        self.plan: list[list] = []      # [done: bool, text]
        self.next = ""
        self.files: list[str] = []
        self.log: list[str] = []

    @classmethod
    def parse(cls, text: str) -> "Note":
        n, sec = cls(""), None
        for line in text.splitlines():
            if line.startswith("# Task: "):
                n.goal = line[8:].strip()
            elif line.startswith("## "):
                sec = line[3:].strip().lower()
            elif sec is None and (m := re.match(r"^- (branch|status|created|updated): (.*)$", line)):
                setattr(n, m[1], m[2].strip())
            elif sec == "plan" and (m := re.match(r"^- \[( |x)\] \d+\. (.*)$", line)):
                n.plan.append([m[1] == "x", m[2]])
            elif sec == "next" and line.strip():
                n.next = line.strip()
            elif sec == "files" and line.startswith("- "):
                n.files.append(line[2:].strip())
            elif sec == "log" and line.startswith("- "):
                n.log.append(line[2:].strip())
        return n

    def render(self) -> str:
        out = [f"# Task: {self.goal}", f"- branch: {self.branch}", f"- status: {self.status}",
               f"- created: {self.created}", f"- updated: {self.updated}", "", "## Plan"]
        out += [f"- [{'x' if d else ' '}] {i}. {t}" for i, (d, t) in enumerate(self.plan, 1)] or ["(none)"]
        out += ["", "## Next", self.next or "(not set)", "", "## Files"]
        out += [f"- {f}" for f in self.files] or ["(none)"]
        out += ["", "## Log"] + [f"- {l}" for l in self.log]
        return "\n".join(out) + "\n"

    def progress(self) -> str:
        return f"{sum(d for d, _ in self.plan)}/{len(self.plan)}" if self.plan else "-"


def task_path(base: Path, branch: str) -> Path:
    return base / "tasks" / f"{slug(branch)}.md"


def load_note(base: Path, branch: str) -> Note | None:
    p = task_path(base, branch)
    return Note.parse(p.read_text(encoding="utf-8")) if p.exists() else None


def save_note(base: Path, n: Note) -> None:
    n.updated = now()
    p = task_path(base, n.branch)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(n.render(), encoding="utf-8")


def resolve_branch(arg: str | None) -> str | None:
    b = arg or current_branch()
    if not b:
        print("BLOCK: no branch (detached HEAD or not a git repo) — pass --branch NAME")
    return b


# ---------------------------------------------------------------- decisions model
def parse_decisions(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries, cur = [], None
    for line in path.read_text(encoding="utf-8").splitlines():
        m = DEC_HEADER.match(line)
        if m:
            cur = {"id": m[1], "date": m[2], "topic": m[3], "branch": m[4], "body": [], "supersedes": None}
            entries.append(cur)
        elif cur is not None:
            if line.startswith("Supersedes: "):
                cur["supersedes"] = line[12:].strip()
            elif line.strip():
                cur["body"].append(line)
    superseded = {e["supersedes"]: e["id"] for e in entries if e["supersedes"]}
    for e in entries:
        e["superseded_by"] = superseded.get(e["id"])
    return entries


def fmt_decision(e: dict) -> str:
    tag = f"  [superseded by {e['superseded_by']}]" if e["superseded_by"] else ""
    sup = f" (replaces {e['supersedes']})" if e["supersedes"] else ""
    return f"{e['id']} {e['date']} [{e['topic']}]{sup}{tag}\n" + "\n".join("  " + l for l in e["body"])


DEC_HEAD = ("# Decisions\n\nAppend-only. Never edit or delete an entry; to change a decision, add a new one\n"
            "with `Supersedes: D-xxx`. Written by notes.py decide.\n")


# ---------------------------------------------------------------- commands
def cmd_init(a, r, cfg, base):
    (base / "tasks").mkdir(parents=True, exist_ok=True)
    dec = base / "decisions.md"
    if not dec.exists():
        dec.write_text(DEC_HEAD, encoding="utf-8")
    msg = [f"OK: {base.relative_to(r)}/ ready"]
    if not cfg["commit_tasks"]:
        gi, rule = r / ".gitignore", f"/{base.relative_to(r).as_posix()}/tasks/"
        lines = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
        if rule not in lines:
            gi.write_text("\n".join(lines + ["# agent task notes (personal)", rule]) + "\n", encoding="utf-8")
            msg.append(f"added `{rule}` to .gitignore (decisions.md stays committed)")
    print("\n".join(msg))
    return OK


def cmd_task(a, r, cfg, base):
    b = resolve_branch(a.branch)
    if not b:
        return BLOCK
    n = load_note(base, b)
    changed = any([a.goal, a.plan, a.check, a.done, a.next, a.files, a.status, a.reset_plan])
    if n is None:
        if not a.goal:
            print(f"BLOCK: no task note for '{b}'. Create one: notes.py task --goal \"...\" --plan \"step\" ...")
            return BLOCK
        n = Note(b)
    if not changed:
        print(n.render().rstrip())
        return OK
    warns = []
    if a.goal:
        n.goal = a.goal
    if a.reset_plan:
        if n.plan:
            n.log.append(f"{now()} · plan replaced ({n.progress()} done of old plan)")
        n.plan = []
    for s in a.plan or []:
        n.plan.append([False, s])
    for i in a.check or []:
        if 1 <= i <= len(n.plan):
            n.plan[i - 1][0] = True
        else:
            warns.append(f"plan step {i} does not exist (1..{len(n.plan)})")
    if a.done:
        n.log.append(f"{now()} · {a.done}")
    if a.next:
        n.next = a.next
    if a.files:
        n.files = list(dict.fromkeys(n.files + a.files))
    if a.status:
        n.status = a.status
    save_note(base, n)
    for w in warns:
        print("WARN:", w)
    print(f"OK: {task_path(base, b).relative_to(r)}  plan {n.progress()}  next: {n.next or '-'}")
    return WARN if warns else OK


def cmd_brief(a, r, cfg, base):
    b = current_branch()
    lines = []
    n = load_note(base, b) if b else None
    if n:
        lines += [f"TASK {n.goal}", f"branch {b} · {n.status} · plan {n.progress()} · updated {n.updated}"]
        lines += [f"  [{'x' if d else ' '}] {i}. {t}" for i, (d, t) in enumerate(n.plan, 1)][:12]
        lines.append(f"NEXT {n.next or '(not set)'}")
        if n.log:
            lines.append("RECENT")
            lines += ["  " + l for l in n.log[-a.log:]]
        if n.files:
            lines.append("FILES " + ", ".join(n.files[:10]) + (" …" if len(n.files) > 10 else ""))
    else:
        lines.append(f"TASK none for branch '{b or '(detached)'}' — create: notes.py task --goal \"...\"")
        act = [Note.parse(p.read_text(encoding="utf-8")) for p in sorted((base / "tasks").glob("*.md"))]
        act = [x for x in act if x.status in ("active", "blocked")]
        if act:
            lines.append("OPEN TASKS (git switch <branch> to resume)")
            lines += [f"  {x.branch} · {x.status} · {x.progress()} · {x.goal[:60]}" for x in act[:5]]
    st = subprocess.run(["git", "status", "--porcelain=v1"], capture_output=True, text=True, encoding="utf-8", errors="replace").stdout
    ch = [l[3:] for l in st.splitlines() if len(l) > 3]  # keep leading status columns intact
    if ch:
        lines.append(f"UNCOMMITTED {len(ch)}: " + ", ".join(ch[:8]) + (" …" if len(ch) > 8 else ""))
    decs = [e for e in parse_decisions(base / "decisions.md") if not e["superseded_by"]]
    topics = set(a.topic or [])
    rel = [e for e in decs if e["branch"] == b or e["topic"] in topics]
    if rel:
        lines.append("DECISIONS")
        lines += [fmt_decision(e) for e in rel[-5:]]
    elif decs:
        tops = sorted({e["topic"] for e in decs})
        lines.append(f"DECISIONS none for this branch; topics available: {', '.join(tops[:15])}"
                     " — notes.py show --topic T")
    print("\n".join(lines))
    return OK


def cmd_decide(a, r, cfg, base):
    dec = base / "decisions.md"
    entries = parse_decisions(dec)
    topic = re.sub(r"[^a-z0-9._-]+", "-", a.topic.lower()).strip("-")
    body = [l.rstrip() for l in a.text.strip().splitlines() if l.strip()]
    if a.why:
        body.append("Why: " + " ".join(a.why.split()))
    text = "\n".join(body)
    errs = []
    if not topic:
        errs.append("topic is empty")
    if len(text) > cfg["max_decision_chars"]:
        errs.append(f"{len(text)} chars > {cfg['max_decision_chars']} — state the conclusion, not the discussion")
    if len(body) > cfg["max_decision_lines"]:
        errs.append(f"{len(body)} lines > {cfg['max_decision_lines']}")
    warns = []
    if a.supersedes:
        tgt = next((e for e in entries if e["id"] == a.supersedes), None)
        if not tgt:
            errs.append(f"{a.supersedes} does not exist")
        elif tgt["superseded_by"]:
            warns.append(f"{a.supersedes} was already superseded by {tgt['superseded_by']}")
    for e in errs:
        print("BLOCK:", e)
    if errs:
        return BLOCK
    nid = f"D-{(max((int(e['id'][2:]) for e in entries), default=0) + 1):03d}"
    entry = [f"## {nid} · {today()} · {topic} · {current_branch() or '-'}"] + body
    if a.supersedes:
        entry.append(f"Supersedes: {a.supersedes}")
    if not dec.exists():
        dec.parent.mkdir(parents=True, exist_ok=True)
        dec.write_text(DEC_HEAD, encoding="utf-8")
    with dec.open("a", encoding="utf-8") as f:
        f.write("\n" + "\n".join(entry) + "\n")
    for w in warns:
        print("WARN:", w)
    print(f"OK: {nid} [{topic}] recorded")
    return WARN if warns else OK


def cmd_show(a, r, cfg, base):
    es = parse_decisions(base / "decisions.md")
    if a.id:
        es = [e for e in es if e["id"] == a.id]
    else:
        if not a.all:
            es = [e for e in es if not e["superseded_by"]]
        if a.topic:
            es = [e for e in es if e["topic"] == a.topic]
        if a.branch:
            es = [e for e in es if e["branch"] == a.branch]
        es = es[-a.limit:]
    if not es:
        print("no matching decisions")
        return OK
    print("\n".join(fmt_decision(e) for e in es))
    return OK


def cmd_tasks(a, r, cfg, base):
    rows = []
    for p in sorted((base / "tasks").glob("*.md")):
        n = Note.parse(p.read_text(encoding="utf-8"))
        if a.all or n.status in ("active", "blocked"):
            rows.append(f"{n.status:9} {n.updated:16} {n.progress():5} {n.branch:40} {n.goal[:50]}")
    cur = current_branch()
    print("\n".join(rows) if rows else "no task notes" + ("" if a.all else " (active) — --all for done"))
    if cur:
        print(f"current branch: {cur}")
    return OK


def cmd_close(a, r, cfg, base):
    b = resolve_branch(a.branch)
    if not b:
        return BLOCK
    n = load_note(base, b)
    if not n:
        print(f"BLOCK: no task note for '{b}'")
        return BLOCK
    n.status = a.status
    n.log.append(f"{now()} · closed as {a.status}")
    save_note(base, n)
    print(f"OK: '{b}' marked {a.status} (note kept)")
    return OK


def base_branch(cfg) -> str | None:
    if cfg["base_branch"]:
        return cfg["base_branch"]
    head = run("git", "symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head
    for c in ("main", "master"):
        if run("git", "rev-parse", "--verify", "--quiet", c):
            return c
    return None


def cmd_sync(a, r, cfg, base):
    bb = base_branch(cfg)
    if not bb:
        print("BLOCK: cannot find base branch — set notes.base_branch in .agentkit.json")
        return BLOCK
    merged = {l.strip().lstrip("* ").strip() for l in run("git", "branch", "--merged", bb).splitlines()}
    existing = {l.strip().lstrip("* ").strip() for l in run("git", "branch", "--format=%(refname:short)").splitlines()}
    changed = 0
    for p in (base / "tasks").glob("*.md"):
        n = Note.parse(p.read_text(encoding="utf-8"))
        if n.status not in ("active", "blocked") or n.branch == bb.split("/")[-1]:
            continue
        reason = "merged" if n.branch in merged and n.branch in existing else ("branch deleted" if n.branch not in existing else None)
        if reason:
            n.status = "done"
            n.log.append(f"{now()} · auto-closed ({reason} into {bb})" if reason == "merged" else f"{now()} · auto-closed ({reason})")
            save_note(base, n)
            print(f"done: {n.branch} ({reason})")
            changed += 1
    print(f"OK: {changed} note(s) closed")
    return OK


# ---------------------------------------------------------------- main
def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="notes", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    sp.add_parser("init")
    s = sp.add_parser("task")
    s.add_argument("--goal"); s.add_argument("--plan", action="append"); s.add_argument("--check", type=int, action="append")
    s.add_argument("--done"); s.add_argument("--next"); s.add_argument("--files", nargs="+")
    s.add_argument("--status", choices=STATUSES); s.add_argument("--branch")
    s.add_argument("--reset-plan", action="store_true", help="replace the plan steps (used by plan.py apply)")
    s = sp.add_parser("brief"); s.add_argument("--topic", action="append"); s.add_argument("--log", type=int, default=3)
    s = sp.add_parser("decide"); s.add_argument("--topic", required=True); s.add_argument("--text", required=True)
    s.add_argument("--why"); s.add_argument("--supersedes")
    s = sp.add_parser("show"); s.add_argument("--topic"); s.add_argument("--id"); s.add_argument("--branch")
    s.add_argument("--all", action="store_true"); s.add_argument("--limit", type=int, default=10)
    s = sp.add_parser("tasks"); s.add_argument("--all", action="store_true")
    s = sp.add_parser("close"); s.add_argument("--branch"); s.add_argument("--status", choices=("done", "abandoned"), default="done")
    sp.add_parser("sync")
    a = ap.parse_args()
    r = root()
    cfg = load_cfg(r)
    base = r / cfg["dir"]
    fn = {"init": cmd_init, "task": cmd_task, "brief": cmd_brief, "decide": cmd_decide, "show": cmd_show,
          "tasks": cmd_tasks, "close": cmd_close, "sync": cmd_sync}[a.cmd]
    return fn(a, r, cfg, base)


if __name__ == "__main__":
    sys.exit(main())
