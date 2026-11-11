#!/usr/bin/env python3
"""False-positive sweep for the agent-kit guards. Stdlib only.

  python tools/fp_sweep.py            run every shell command in the docs through guard.py (on an unprotected branch)
  python tools/fp_sweep.py --perf     time guard.py: median of 20 runs of `edit` and of `bash` with a long command
  python tools/fp_sweep.py -v         also list the commands that were allowed

Where the commands come from: fenced code blocks of README.md, templates/**/*.md and skills/*/SKILL.md whose language is
bash, sh, shell, zsh, console or empty (continuation lines joined, a leading "$ " dropped, comment lines skipped). Docs that
show a command the guard is meant to stop go in a ```text block, or carry `# fp-sweep: skip` at the end of the line.

Exit codes: 0 no command was blocked (ASK lines are listed, each one has to be explainable) · 1 a command was blocked.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import subprocess
import sys
import tempfile
import time
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "scripts"))
import guard  # noqa: E402

SHELL_LANGS = frozenset(("", "bash", "sh", "shell", "zsh", "console"))
SKIP_MARK = "# fp-sweep: skip"
FENCE = re.compile(r"^(\s*)(`{3,}|~{3,})\s*([A-Za-z0-9_+-]*)")


def extract_commands(text: str) -> list[tuple[int, str]]:
    """(line number, command) for every command line in the shell code blocks of a Markdown text."""
    out: list[tuple[int, str]] = []
    lang = None  # None: outside a block
    fence = ""
    pending: list[str] = []
    start = 0
    for n, raw in enumerate(text.splitlines(), 1):
        m = FENCE.match(raw)
        if lang is None:
            if m:
                lang, fence = m.group(3).lower(), m.group(2)[0] * 3
            continue
        if m and raw.strip().startswith(fence) and not m.group(3):
            lang = None
            pending = []
            continue
        if lang not in SHELL_LANGS:
            continue
        line = raw.strip()
        if not pending:
            start = n
            if line.startswith("$ "):
                line = line[2:]
        if line.endswith("\\"):
            pending.append(line[:-1].strip())
            continue
        line = " ".join(pending + [line]).strip()
        pending = []
        if line and not line.startswith("#") and SKIP_MARK not in line:
            out.append((start, line))
    return out


def doc_files(root: Path) -> list[Path]:
    files = [root / "README.md"]
    files += sorted((root / "templates").rglob("*.md"))
    files += sorted((root / "skills").glob("*/SKILL.md"))
    return [f for f in files if f.is_file()]


def make_repo(base: str) -> str:
    """A throwaway git repo on an unprotected branch; returns its path."""
    d = os.path.join(base, "repo")
    os.makedirs(d)
    env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
    subprocess.run(["git", "init", "-q"], cwd=d, env=env, check=True, capture_output=True)
    return d


def sweep(root: Path = KIT, files: list[Path] | None = None) -> list[tuple[str, int, str, guard.Decision]]:
    """(file, line, command, decision) for every command of the docs."""
    results = []
    with tempfile.TemporaryDirectory() as base:
        repo_dir = os.path.realpath(make_repo(base))
        cfg = guard.Config()
        repo = guard.RepoInfo(repo_dir)
        rules = guard.FsRules(cfg, repo)
        for f in files if files is not None else doc_files(root):
            rel = f.relative_to(root).as_posix() if f.is_relative_to(root) else str(f)
            for line, cmd in extract_commands(f.read_text(encoding="utf-8")):
                j = guard.Judge(cfg, "feat/sweep", repo_dir)
                j._repo, j._rules = repo, rules
                j.scan(cmd, 0)
                results.append((rel, line, cmd, j.decision()))
    return results


def report(results: list, verbose: bool = False) -> int:
    blocks = [r for r in results if r[3].kind == "block"]
    asks = [r for r in results if r[3].kind == "ask"]
    for rel, line, cmd, d in blocks + asks if not verbose else results:
        if d.kind == "allow" and not verbose:
            continue
        print(f"{d.kind.upper():5} {rel}:{line}  {cmd}")
        if d.kind != "allow":
            print(f"        {d.message.splitlines()[0]}")
    print(f"{len(results)} commands, BLOCK={len(blocks)}, ASK={len(asks)}")
    return 1 if blocks else 0


def perf(runs: int = 20) -> None:
    """Median wall time of guard.py as a hook calls it, on this machine (process start included)."""
    guard_py = str(KIT / "scripts" / "guard.py")
    target = str(KIT / "README.md")
    long_cmd = " && ".join(["cd scripts", "git status --short", "git log --oneline -5", "rm -rf build dist .cache",
                            "cp -r a b && sed -i s/a/b/ notes.txt", "echo done > out.log", "ls -la | head -20",
                            "git diff --stat HEAD~1", "mkdir -p out/a out/b", "tee -a run.log < in.txt"] * 3)
    payload = {"edit": {"tool_input": {"file_path": target}, "cwd": str(KIT)},
               "bash": {"tool_input": {"command": long_cmd}, "cwd": str(KIT)}}
    env = {**os.environ, "PYTHONUTF8": "1"}
    for name in ("edit", "bash"):
        times = []
        for _ in range(runs):
            t0 = time.perf_counter()
            subprocess.run([sys.executable, guard_py, name], input=json.dumps(payload[name]).encode(), capture_output=True, cwd=KIT, env=env)
            times.append((time.perf_counter() - t0) * 1000)
        label = "edit" if name == "edit" else f"bash-long ({len(long_cmd)} chars)"
        print(f"{label}: median {statistics.median(times):.0f} ms, min {min(times):.0f}, max {max(times):.0f} over {runs} runs ({sys.platform})")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--perf", action="store_true", help="time guard.py edit and guard.py bash instead of sweeping the docs")
    ap.add_argument("-v", "--verbose", action="store_true", help="list allowed commands too")
    args = ap.parse_args(argv)
    if args.perf:
        perf()
        return 0
    return report(sweep(), args.verbose)


if __name__ == "__main__":
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")
    sys.exit(main())
