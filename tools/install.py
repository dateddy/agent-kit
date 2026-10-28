#!/usr/bin/env python3
"""Install agent-kit into a repo (or your user profile). Stdlib only, idempotent.

  python tools/install.py <repo>          project install (recommended)
  python tools/install.py --user          skills only, into ~/.claude/skills (all projects)
  python tools/install.py <repo> --force  overwrite existing .claude/skills/<name> entries
  python tools/install.py <repo> --hooks  also add a Claude Code SessionStart hook that runs `notes.py brief`
                                          (merged into <repo>/.claude/settings.json; nothing existing is overwritten)
  python tools/install.py <repo> --git-hooks            pre-commit (gitkit scan --staged) and commit-msg (gitkit lint-msg)
                                                        git hooks; any agent is gated, honours core.hooksPath. An existing
                                                        hook is kept; a marked block is inserted after its shebang.
  python tools/install.py <repo> --uninstall-git-hooks  remove only the marked blocks again

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
import re
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
LEGACY_SKILLS = ("starting-tasks",)  # merged into writing-plans in v3
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


def remove_path(p: Path) -> None:
    """Remove a symlink, junction, file or directory without following links."""
    if p.is_symlink() or p.is_file():
        p.unlink()
        return
    try:
        os.rmdir(p)  # also removes a Windows junction without touching its target
    except OSError:
        shutil.rmtree(p)


def remove_legacy(repo: Path) -> None:
    for name in LEGACY_SKILLS:
        for p in (repo / ".claude" / "skills" / name, repo / ".agentkit" / "skills" / name):
            if p.is_symlink() or p.exists():
                remove_path(p)
                print(f"remove {p} (merged into writing-plans)")


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


# ---------------------------------------------------------------- git hooks
HOOK_MARK = "# >>> agent-kit >>>"
HOOK_BEGIN = HOOK_MARK + " managed block; remove with: install.py --uninstall-git-hooks"
HOOK_END = "# <<< agent-kit <<<"
HOOK_CALLS = {"pre-commit": "scan --staged", "commit-msg": 'lint-msg "$1"'}
SH_SHEBANG = re.compile(r"^#!\s*(\S*/)?(env\s+)?(ba|da|z|k)?sh\b")
BLOCK_RE = re.compile(re.escape(HOOK_MARK) + r".*?" + re.escape(HOOK_END) + r"\n?", re.S)


def hook_block(name: str) -> str:
    """POSIX sh that BLOCKs only on gitkit exit 1 (WARN passes) and never calls `exit 0`."""
    py = shlex.quote(Path(sys.executable).as_posix())
    return "\n".join([
        HOOK_BEGIN,
        f"ak_py={py}",
        'ak_gk="$(git rev-parse --show-toplevel)/.agentkit/skills/managing-git/scripts/gitkit.py"',
        "ak_run() {",
        '  if [ -x "$ak_py" ]; then "$ak_py" "$@"',
        '  elif command -v python3 >/dev/null 2>&1; then python3 "$@"',
        '  elif command -v python >/dev/null 2>&1; then python "$@"',
        '  elif command -v py >/dev/null 2>&1; then py -3 "$@"',
        "  else return 0; fi",
        "}",
        "ak_rc=0",
        'if [ -f "$ak_gk" ]; then',
        f'  ak_run "$ak_gk" {HOOK_CALLS[name]} || ak_rc=$?',
        "fi",
        'if [ "$ak_rc" -eq 1 ]; then exit 1; fi',
        HOOK_END,
        "",
    ])


def read_raw(path: Path) -> str:
    with open(path, encoding="utf-8", newline="") as f:
        return f.read()


def write_raw(path: Path, text: str) -> None:
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write(text)


def hooks_dir(repo: Path) -> Path | None:
    """The hooks directory git really uses (honours core.hooksPath), or None if repo is not a git toplevel."""
    def g(*args: str) -> str | None:
        p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", env=CHILD_ENV)
        return p.stdout.strip() if p.returncode == 0 else None
    top = g("rev-parse", "--show-toplevel")
    if not top or Path(top).resolve() != repo:
        return None
    d = g("rev-parse", "--git-path", "hooks")
    if not d:
        return None
    return Path(d) if Path(d).is_absolute() else repo / d


def install_git_hook(d: Path, name: str) -> tuple[str, int]:
    path, block = d / name, hook_block(name)
    if not path.exists():
        d.mkdir(parents=True, exist_ok=True)
        write_raw(path, "#!/bin/sh\n" + block)
        msg = f"create {path}"
    else:
        text = read_raw(path)
        if HOOK_MARK in text:
            return f"keep {path} (agent-kit block already present)", 0
        first, _, rest = text.partition("\n")
        if first.startswith("#!"):
            if not SH_SHEBANG.match(first):
                return (f"WARN {path} is not a sh hook ({first.strip()}) — left untouched; "
                        f"call gitkit.py {HOOK_CALLS[name]} from it by hand"), 2
            write_raw(path, first + "\n" + block + rest)
        else:
            write_raw(path, block + text)
        msg = f"hook {path} (block inserted; existing content kept)"
    path.chmod(path.stat().st_mode | 0o111)
    return msg, 0


def uninstall_git_hook(d: Path, name: str) -> tuple[str, int]:
    path = d / name
    if not path.is_file():
        return f"skip {path} (no such hook)", 0
    new, n = BLOCK_RE.subn("", read_raw(path))
    if not n:
        return f"keep {path} (no agent-kit block)", 0
    if all(not l.strip() or l.startswith("#!") for l in new.splitlines()):
        path.unlink()
        return f"remove {path} (only the agent-kit block was in it)", 0
    write_raw(path, new)
    return f"unhook {path} (agent-kit block removed)", 0


def git_hooks(repo: Path, action) -> int:
    d = hooks_dir(repo)
    if d is None:
        print(f"WARN {repo} is not the top level of a git repository — run `git init` there first; hooks not touched")
        return 2
    code = 0
    for name in HOOK_CALLS:
        msg, c = action(d, name)
        print(msg)
        code = max(code, c)
    return code


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
    ap.add_argument("--git-hooks", action="store_true", help="install pre-commit (scan) and commit-msg (lint-msg) git hooks")
    ap.add_argument("--uninstall-git-hooks", action="store_true", help="remove only the agent-kit blocks from git hooks")
    a = ap.parse_args()

    if a.user and (a.git_hooks or a.uninstall_git_hooks):
        ap.error("git hooks belong to a repo; give a repo path instead of --user")
    if a.user:
        dest = Path.home() / ".claude" / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        for name in LEGACY_SKILLS:
            old = dest / name
            md = old / "SKILL.md"
            if md.is_file() and f"name: {name}" in md.read_text(encoding="utf-8"):
                remove_path(old)
                print(f"remove {old} (merged into writing-plans)")
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
    if a.uninstall_git_hooks:
        return git_hooks(repo, uninstall_git_hook)

    canon = repo / ".agentkit" / "skills"
    canon.mkdir(parents=True, exist_ok=True)
    remove_legacy(repo)
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
        elif name == ".claude/commands/task.md" and "starting-tasks" in dst.read_text(encoding="utf-8"):
            shutil.copy(src, dst)
            print(f"update {dst} (now points to writing-plans)")
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
    if a.git_hooks:
        code = max(code, git_hooks(repo, install_git_hook))
    print("\nDone. Commit .agentkit/skills, .claude/, AGENTS.md, CLAUDE.md, .agentkit.json, notes/decisions.md.")
    return code


if __name__ == "__main__":
    sys.exit(main())
