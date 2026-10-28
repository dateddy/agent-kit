#!/usr/bin/env python3
"""Install agent-kit into a repo (or your user profile). Stdlib only, idempotent.

  python tools/install.py <repo>          project install (recommended)
  python tools/install.py --user          skills only, into ~/.claude/skills (all projects)
  python tools/install.py <repo> --force  overwrite existing .claude/skills/<name> entries
  python tools/install.py <repo> --hooks  also add a Claude Code SessionStart hook that runs `notes.py brief`
                                          (merged into <repo>/.claude/settings.json; nothing existing is overwritten)

Project install does:
  <repo>/.agentkit/skills/<skill>   canonical copy, agent-neutral (referenced by AGENTS.md)
  <repo>/.claude/skills/<skill>     symlink to the above (copy if symlinks are unavailable)
  <repo>/AGENTS.md, CLAUDE.md, .agentkit.json, .claude/commands/*.md   created only if missing (task, grill)
  notes/ layout + .gitignore rule   via notes.py init
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
HOOK_CMD = "python .agentkit/skills/keeping-notes/scripts/notes.py brief"
KIT = Path(__file__).resolve().parent.parent
SKILLS = sorted(p for p in (KIT / "skills").iterdir() if (p / "SKILL.md").exists())


def copy_skill(src: Path, dst: Path) -> None:
    if dst.is_symlink() or dst.is_file():
        dst.unlink()
    elif dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))


def link_or_copy(target: Path, link: Path, force: bool) -> str:
    if link.is_symlink():
        link.unlink()
    elif link.exists():
        if not force:
            return f"SKIP {link} exists and is not a link (use --force)"
        shutil.rmtree(link) if link.is_dir() else link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    try:
        link.symlink_to(os.path.relpath(target, link.parent), target_is_directory=True)
        return f"link {link} -> {target.name}"
    except OSError:  # Windows without developer mode
        copy_skill(target, link)
        return f"copy {link} (symlink unavailable; re-run install after updating the kit)"


def merge_hook(repo: Path) -> tuple[str, int]:
    """Add the SessionStart hook to <repo>/.claude/settings.json without touching anything else."""
    path = repo / ".claude" / "settings.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"WARN {path} is not valid JSON ({e}) — left untouched; add the hook by hand: {HOOK_CMD}", 2
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    if not isinstance(hooks, dict) or not isinstance(hooks.get("SessionStart", []), list):
        return f"WARN {path} has an unexpected layout — left untouched; add the hook by hand: {HOOK_CMD}", 2
    groups = data.setdefault("hooks", {}).setdefault("SessionStart", [])
    for g in groups:
        for h in g.get("hooks", []) if isinstance(g, dict) else []:
            if isinstance(h, dict) and h.get("command") == HOOK_CMD:
                return f"keep {path} (SessionStart hook already present)", 0
    groups.append({"hooks": [{"type": "command", "command": HOOK_CMD}]})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    return f"hook {path} (SessionStart -> notes.py brief)", 0


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("repo", nargs="?")
    ap.add_argument("--user", action="store_true")
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--hooks", action="store_true", help="add a Claude Code SessionStart hook (notes.py brief)")
    a = ap.parse_args()

    if a.user:
        dest = Path.home() / ".claude" / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        for s in SKILLS:
            copy_skill(s, dest / s.name)
            print(f"copy {dest / s.name}")
        print("NOTE: AGENTS.md references .agentkit/skills/ — for project use run a project install too.")
        return 0

    if not a.repo:
        ap.error("give a repo path or --user")
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a directory: {repo}")

    canon = repo / ".agentkit" / "skills"
    canon.mkdir(parents=True, exist_ok=True)
    for s in SKILLS:
        copy_skill(s, canon / s.name)
        print(f"copy {canon / s.name}")
        print(link_or_copy(canon / s.name, repo / ".claude" / "skills" / s.name, a.force))

    for name, src in (("AGENTS.md", KIT / "templates" / "AGENTS.md"),
                      ("CLAUDE.md", KIT / "templates" / "CLAUDE.md"),
                      (".agentkit.json", KIT / ".agentkit.example.json"),
                      *((f".claude/commands/{c.name}", c) for c in sorted((KIT / "templates" / "commands").glob("*.md")))):
        dst = repo / name
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy(src, dst)
            print(f"create {dst}")
        elif name == "AGENTS.md" and "keeping-notes" not in dst.read_text(encoding="utf-8"):
            print(f"WARN {dst} exists — merge the rules from {src} into it by hand")
        elif name == "CLAUDE.md" and "@AGENTS.md" not in dst.read_text(encoding="utf-8"):
            dst.write_text("@AGENTS.md\n\n" + dst.read_text(encoding="utf-8"), encoding="utf-8")
            print(f"update {dst} (prepended @AGENTS.md)")
        else:
            print(f"keep {dst}")

    r = subprocess.run([sys.executable, str(canon / "keeping-notes" / "scripts" / "notes.py"), "init"],
                       cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace", env=CHILD_ENV)
    print(r.stdout.strip() or r.stderr.strip())
    code = 0
    if a.hooks:
        msg, code = merge_hook(repo)
        print(msg)
    print("\nDone. Commit .agentkit/skills, .claude/, AGENTS.md, CLAUDE.md, .agentkit.json, notes/decisions.md.")
    return code


if __name__ == "__main__":
    sys.exit(main())
