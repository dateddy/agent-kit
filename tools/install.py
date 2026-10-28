#!/usr/bin/env python3
"""Install agent-kit into a repo (or your user profile). Stdlib only, idempotent.

  python tools/install.py <repo>          project install (recommended)
  python tools/install.py --user          skills only, into ~/.claude/skills (all projects)
  python tools/install.py <repo> --force  overwrite existing .claude/skills/<name> entries

Project install does:
  <repo>/.agentkit/skills/<skill>   canonical copy, agent-neutral (referenced by AGENTS.md)
  <repo>/.claude/skills/<skill>     symlink to the above (copy if symlinks are unavailable)
  <repo>/AGENTS.md, CLAUDE.md, .agentkit.json, .claude/commands/task.md   created only if missing
  notes/ layout + .gitignore rule   via notes.py init
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

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
                      (".claude/commands/task.md", KIT / "templates" / "commands" / "task.md")):
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
                       cwd=repo, capture_output=True, text=True)
    print(r.stdout.strip() or r.stderr.strip())
    print("\nDone. Commit .agentkit/skills, .claude/, AGENTS.md, CLAUDE.md, .agentkit.json, notes/decisions.md.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
