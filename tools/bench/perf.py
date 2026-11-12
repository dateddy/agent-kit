#!/usr/bin/env python3
"""perf - wall-clock benchmark of the agent-kit gates on THIS repo. Stdlib only.

  perf.py [--runs 5] [--case NAME ...] [--json] [--root DIR]

--root measures another checkout (e.g. a worktree of the previous tag); default is this repo.

Cases (median of --runs, seconds):
  vf_full         full verify (CLI)
  hook_nochange   Stop hook on an unchanged tree
  hook_1file      Stop hook with one new untracked file (perf_probe_tmp.py, deleted afterwards)
  edit            PostToolUse hook for .agentkit/tmp/perf_edit.py (fixed content, deleted afterwards)
  edit_ruff       the same two ruff calls without the engine; engine overhead = edit - edit_ruff when the
                  repo's config makes the hook run ruff (the kit's own config does not, so there edit IS the overhead)
  rmap_zone_cold  rmap --zone with the repomap cache removed first
  rmap_zone_warm  rmap --zone right after a cold run

The engine is found by what exists: scripts/verify.py (0.6.0+) or the 0.5.0 pair
scripts/verify.sh + skills/verifying-changes/scripts/verify.py. Run it with a clean tree.
Exit: 0 ok, 1 a case failed to run.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PY = sys.executable
BASH = os.environ.get("AK_BASH") or shutil.which("bash") or "bash"
EDIT_FILE = ROOT / ".agentkit" / "tmp" / "perf_edit.py"
STOP = json.dumps({"hook_event_name": "Stop", "stop_hook_active": False})
CACHE = ROOT / ".agentkit" / "cache" / "repomap.json"
RMAP = ROOT / "skills" / "economizing-context" / "scripts" / "repomap.py"


def set_root(root: Path) -> None:
    global ROOT, EDIT_FILE, CACHE, RMAP
    ROOT = root.resolve()
    EDIT_FILE = ROOT / ".agentkit" / "tmp" / "perf_edit.py"
    CACHE = ROOT / ".agentkit" / "cache" / "repomap.json"
    RMAP = ROOT / "skills" / "economizing-context" / "scripts" / "repomap.py"


CASES = ("vf_full", "hook_nochange", "hook_1file", "edit", "edit_ruff", "rmap_zone_cold", "rmap_zone_warm")
PROBE_SRC = "x = 1" + chr(10)
EDIT_SRC = "import   os,sys" + chr(10) + "x = {  'a':1 }" + chr(10)  # unformatted on purpose: ruff has work to do


def run(argv: list[str], stdin: str | None = None) -> int:
    p = subprocess.run(argv, cwd=ROOT, input=stdin, text=True, capture_output=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"})
    return p.returncode


def full_cmd() -> list[str]:
    new = ROOT / "scripts" / "verify.py"
    old = ROOT / "skills" / "verifying-changes" / "scripts" / "verify.py"
    return [PY, str(new if new.exists() else old)]


def hook_cmd() -> list[str]:
    return [BASH, str(ROOT / "scripts" / "verify.sh"), "--hook"]


def timed(fn) -> float:
    t = time.perf_counter()
    fn()
    return time.perf_counter() - t


@contextlib.contextmanager
def scratch(path: Path, text: str):
    """Create a throwaway file for one measurement and always delete it again. The kit's tools never write a tracked
    file: the files used here are untracked (the probe) or under .agentkit/tmp (gitignored)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8", newline="")
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def one(case: str) -> float:
    if case == "vf_full":
        return timed(lambda: run(full_cmd()))
    if case == "hook_nochange":
        return timed(lambda: run(hook_cmd(), STOP))
    if case == "hook_1file":  # one new untracked python file, so the hook has exactly one changed file
        with scratch(ROOT / "perf_probe_tmp.py", PROBE_SRC):
            return timed(lambda: run(hook_cmd(), STOP))
    if case == "edit":  # the PostToolUse hook for one file: engine overhead (+ ruff when the repo's config runs it)
        with scratch(EDIT_FILE, EDIT_SRC) as f:
            payload = json.dumps({"tool_input": {"file_path": str(f)}})
            return timed(lambda: run([BASH, str(ROOT / "scripts" / "ruff-fix.sh")], payload))
    if case == "edit_ruff":  # the same two ruff calls the hook makes, without the engine: its overhead is edit - edit_ruff
        ruff = shutil.which("ruff")
        if ruff is None:
            raise RuntimeError("ruff not found on PATH")
        with scratch(EDIT_FILE, EDIT_SRC) as f:
            args = ["--quiet", "--force-exclude", "--extend-exclude", ".agentkit", "--extend-exclude", ".claude", str(f)]
            return timed(lambda: (run([ruff, "check", "--fix", *args]), run([ruff, "format", *args])))
    if case == "rmap_zone_cold":
        CACHE.unlink(missing_ok=True)
        return timed(lambda: run([PY, str(RMAP), "--zone", "scripts/gitkit.py"]))
    if case == "rmap_zone_warm":
        run([PY, str(RMAP), "--zone", "scripts/gitkit.py"])
        return timed(lambda: run([PY, str(RMAP), "--zone", "scripts/gitkit.py"]))
    raise ValueError(case)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=int, default=5)
    ap.add_argument("--case", nargs="+", choices=CASES)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--root", type=Path)
    a = ap.parse_args()
    if a.root:
        set_root(a.root)
    rows = {}
    for case in a.case or CASES:
        try:
            xs = [one(case) for _ in range(a.runs)]
        except Exception as e:  # noqa: BLE001 - report which case broke
            print(f"perf: {case} failed: {e}", file=sys.stderr)
            return 1
        rows[case] = {"median_s": round(statistics.median(xs), 3), "min": round(min(xs), 3), "max": round(max(xs), 3), "n": len(xs)}
    if a.json:
        print(json.dumps(rows))
    else:
        print(f"{'case':16} {'median_s':>9} {'min':>8} {'max':>8} {'n':>3}")
        for c, r in rows.items():
            print(f"{c:16} {r['median_s']:9.3f} {r['min']:8.3f} {r['max']:8.3f} {r['n']:3d}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
