#!/usr/bin/env python3
"""run — execute a command, keep the full log on disk, print only what an agent needs.

  run.py [--tail N] [--name LABEL] [--timeout SEC] -- <cmd> [args...]
  run.py [opts] "<shell pipeline>"          (single string → run through the shell)

Prints: exit code, duration, line count, log path, error lines (deduped), the last Python
traceback, and the tail. Short outputs (≤ context.max_output_lines) are printed whole.
Exits with the command's own exit code, so it can replace the bare command anywhere.
Read more of a log later with:  grep -n "<pattern>" <log>   or   sed -n '120,160p' <log>
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

# 60 lines ≈ 1–1.5k tokens: enough to diagnose, small enough to stay in history cheaply
DEFAULT_MAX_LINES = 60
DEFAULT_TAIL = 20
MAX_ERROR_LINES = 25
MAX_TRACEBACK_LINES = 30
KEEP_LOGS = 50  # oldest logs beyond this are pruned

ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ERROR_RE = re.compile(
    r"(\berror\b|\w+Error\b|\w+Exception\b|\bexception\b|\bfatal\b|\bpanic\b|\bfailed\b|\bfailure\b|assertionerror|"
    r"^E\s{2,}|^FAILED |^ERROR |\bnot found\b|\bno such file\b|\bpermission denied\b|"
    r"\bcuda out of memory\b|\bnan\b loss|\bkilled\b|segmentation fault)", re.I)
NOISE_RE = re.compile(r"(\b0 errors?\b|\berror[s]?: 0\b|errors=0|0 failed|warnings? summary)", re.I)


def repo_root() -> Path:
    p = subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True)
    return Path(p.stdout.strip()) if p.returncode == 0 else Path.cwd()


def max_lines(root: Path) -> int:
    f = root / ".agentkit.json"
    try:
        return int(json.loads(f.read_text(encoding="utf-8")).get("context", {}).get("max_output_lines", DEFAULT_MAX_LINES))
    except Exception:
        return DEFAULT_MAX_LINES


def clean(raw: str) -> list[str]:
    lines = []
    for l in ANSI.sub("", raw).split("\n"):
        if "\r" in l:  # progress bars: keep only the final redraw
            l = l.split("\r")[-1] or l.rstrip("\r").split("\r")[-1]
        lines.append(l.rstrip())
    while lines and not lines[-1]:
        lines.pop()
    return lines


def last_traceback(lines: list[str]) -> list[str]:
    start = max((i for i, l in enumerate(lines) if l.startswith("Traceback (most recent call last)")), default=None)
    if start is None:
        return []
    block = [lines[start]]
    for l in lines[start + 1:]:
        block.append(l)
        if l and not l.startswith((" ", "\t")):  # the exception line ends the block
            break
    if len(block) > MAX_TRACEBACK_LINES:  # keep head (entry) and tail (where it broke)
        block = block[:5] + [f"  … {len(block) - 25} frames …"] + block[-20:]
    return block


def prune(logdir: Path) -> None:
    logs = sorted(logdir.glob("*.log"), key=lambda p: p.stat().st_mtime)
    for p in logs[:-KEEP_LOGS]:
        p.unlink(missing_ok=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--tail", type=int, default=DEFAULT_TAIL)
    ap.add_argument("--name", help="log file label (default: first word of the command)")
    ap.add_argument("--timeout", type=float, default=None, help="seconds; process is killed after")
    ap.add_argument("cmd", nargs=argparse.REMAINDER)
    a = ap.parse_args()
    cmd = a.cmd[1:] if a.cmd and a.cmd[0] == "--" else a.cmd
    if not cmd:
        ap.error("no command given")
    shell = len(cmd) == 1 and " " in cmd[0]
    root = repo_root()
    logdir = root / ".agentkit" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / ".gitignore").exists() or (logdir / ".gitignore").write_text("*\n")  # logs never get committed
    label = re.sub(r"[^A-Za-z0-9_-]+", "-", a.name or Path(cmd[0].split()[0]).name)[:30]
    log = logdir / f"{label}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"

    t0 = time.time()
    try:
        p = subprocess.run(cmd[0] if shell else cmd, shell=shell, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, timeout=a.timeout)
        raw, code = p.stdout.decode("utf-8", "replace"), p.returncode
    except subprocess.TimeoutExpired as e:
        raw, code = (e.stdout or b"").decode("utf-8", "replace") + f"\n[killed after {a.timeout}s timeout]", 124
    except FileNotFoundError:
        print(f"exit 127 · command not found: {cmd[0]}")
        return 127
    dur = time.time() - t0
    log.write_text(raw, encoding="utf-8")
    prune(logdir)
    lines = clean(raw)
    rel = log.relative_to(root) if log.is_relative_to(root) else log

    status = "OK" if code == 0 else "FAILED"
    print(f"$ {' '.join(cmd)}")
    print(f"{status} exit {code} · {dur:.1f}s · {len(lines)} lines · log: {rel}")
    limit = max_lines(root)
    if len(lines) <= limit:
        print("\n".join(lines))
        return code

    errs, seen = [], set()
    for i, l in enumerate(lines, 1):
        if ERROR_RE.search(l) and not NOISE_RE.search(l):
            key = re.sub(r"\d+", "#", l.strip())[:160]
            if key not in seen:
                seen.add(key)
                errs.append(f"{i:>6}: {l[:240]}")
    if errs:
        print(f"ERRORS ({len(errs)} unique{', first ' + str(MAX_ERROR_LINES) if len(errs) > MAX_ERROR_LINES else ''}):")
        print("\n".join(errs[:MAX_ERROR_LINES]))
    tb = last_traceback(lines)
    tb_start = max((i for i, l in enumerate(lines) if l.startswith("Traceback (most recent call last)")), default=-1)
    if tb and code != 0 and tb_start < len(lines) - a.tail:  # skip if the tail already shows it
        print("LAST TRACEBACK:")
        print("\n".join(tb))
    print(f"TAIL ({a.tail}):")
    print("\n".join(lines[-a.tail:]))
    return code


if __name__ == "__main__":
    sys.exit(main())
