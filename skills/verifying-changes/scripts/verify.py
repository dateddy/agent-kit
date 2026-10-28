#!/usr/bin/env python3
"""verify — run the project's quality gates and return one verdict. Stdlib only.

  verify.py                 run all gates (format, lint, typecheck, test)
  verify.py --only lint,test
  verify.py --detect        print the commands that would run, and why
  verify.py --no-guard      skip the test-tampering check

Gate commands come from .agentkit.json "verify" (string = command, null/"" = skip,
"auto"/missing = auto-detect). Full logs go to .agentkit/logs/.

Exit: 0 all gates passed · 1 a gate failed · 2 nothing verified, or tests were deleted/skipped/
      weakened (needs the user's OK)
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

GATES = ("format", "lint", "typecheck", "test")
MAX_ERR = 15  # error lines shown per failed gate
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
ERR_RE = re.compile(r"(\berror\b|\w+Error\b|\bfailed\b|^FAILED |^E\s{2,}|^ERROR |\s-->\s|:\d+:\d+: [A-Z]+\d+|"
                    r"would reformat|✗|✘|\bnot ok\b)", re.I)
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")
SKIP_RE = re.compile(r"(pytest\.mark\.(skip|xfail)|@unittest\.skip|\b(it|test|describe)\.(skip|todo)\(|"
                     r"\bxit\(|\bxdescribe\(|t\.Skip\(|#\[ignore\]|pytest\.skip\()")


def sh(*cmd: str, cwd: Path | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, encoding="utf-8", errors="replace")
    return p.stdout if p.returncode == 0 else ""


def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def has_tool(cmd: str) -> bool:
    parts = shlex.split(cmd)
    if not parts:
        return False
    if parts[0] in ("python", "python3") and len(parts) > 2 and parts[1] == "-m":
        return subprocess.run([sys.executable, "-c", f"import {parts[2]}"], capture_output=True,
                              env=CHILD_ENV).returncode == 0
    return shutil.which(parts[0]) is not None


# ---------------------------------------------------------------- detection
def probe_pytest(root: Path) -> tuple[bool, str]:
    """Is `python -m pytest` runnable here? Returns (ok, version line | first error line)."""
    try:
        p = subprocess.run([sys.executable, "-m", "pytest", "--version"], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", cwd=root, timeout=20, env=CHILD_ENV)
    except subprocess.TimeoutExpired:
        return False, "pytest not runnable: probe timed out after 20s"
    lines = [l.strip() for l in (p.stdout + "\n" + p.stderr).splitlines() if l.strip()]
    if p.returncode == 0:
        return True, lines[0] if lines else "pytest"
    err = next((l for l in lines if re.search(r"\w+(Error|Exception)\b", l)), lines[0] if lines else f"exit {p.returncode}")
    return False, f"pytest not runnable: {err[:140]}"


def detect(root: Path) -> dict[str, tuple[str | None, str]]:
    """Return gate -> (command or None, reason)."""
    found: dict[str, tuple[str | None, str]] = {}
    py = Path(sys.executable).as_posix() if " " not in sys.executable else "python"
    pyproject = read(root / "pyproject.toml")
    setup_cfg = read(root / "setup.cfg")
    reqs = " ".join(read(p) for p in root.glob("requirements*.txt")).lower() + pyproject.lower()

    # --- Python
    if pyproject or setup_cfg or list(root.glob("requirements*.txt")) or (root / "setup.py").exists():
        if "[tool.ruff" in pyproject or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or "ruff" in reqs:
            found["lint"] = ("ruff check --output-format concise .", "ruff configured")
            if "[tool.ruff.format" in pyproject or "ruff format" in read(root / ".pre-commit-config.yaml"):
                found["format"] = ("ruff format --check .", "ruff format configured")
        elif (root / ".flake8").exists() or "[flake8]" in setup_cfg:
            found["lint"] = ("flake8", "flake8 configured")
        if "[tool.black" in pyproject and "format" not in found:
            found["format"] = ("black --check .", "black configured")
        if "[tool.mypy" in pyproject or (root / "mypy.ini").exists() or "[mypy" in setup_cfg:
            found["typecheck"] = ("mypy .", "mypy configured")
        elif (root / "pyrightconfig.json").exists() or "[tool.pyright" in pyproject:
            found["typecheck"] = ("pyright", "pyright configured")

    # --- Python tests: independent of config files; the runner is chosen by probing
    pytest_cfg = "[tool.pytest" in pyproject or (root / "pytest.ini").exists() or (root / "conftest.py").exists() \
        or "pytest" in reqs
    test_dirs = [d for d in ("tests", "test") if (root / d).is_dir()]
    test_files = [p for d in test_dirs for p in (root / d).rglob("test*.py")] + list(root.glob("test_*.py"))
    if pytest_cfg or test_files:
        ok, why = probe_pytest(root)
        if ok:
            found["test"] = (f"{py} -m pytest -q", f"pytest runnable: {why}")
        elif any("unittest.TestCase" in read(p) for p in test_files):
            d = next((d for d in test_dirs if list((root / d).rglob("test*.py"))), ".")
            top = " -t ." if d != "." and (root / d / "__init__.py").exists() else ""
            found["test"] = (f"{py} -m unittest discover -s {d}{top}", f"unittest TestCase found; {why}")
        else:
            found["test"] = (None, why)

    # --- JavaScript / TypeScript
    pkg_path = root / "package.json"
    if pkg_path.exists():
        try:
            scripts = json.loads(read(pkg_path)).get("scripts", {})
        except json.JSONDecodeError:
            scripts = {}
        pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() \
            else "bun" if (root / "bun.lockb").exists() else "npm"
        run = f"{pm} run"
        for gate, names in (("format", ["format:check", "fmt:check"]), ("lint", ["lint"]),
                            ("typecheck", ["typecheck", "type-check", "tsc"]), ("test", ["test"])):
            name = next((n for n in names if n in scripts), None)
            if name and "no test specified" not in scripts[name] and gate not in found:
                found[gate] = (f"{run} {name}", f"package.json script '{name}'")
        if "typecheck" not in found and (root / "tsconfig.json").exists():
            found["typecheck"] = ("npx tsc --noEmit", "tsconfig.json present")

    # --- Go / Rust
    if (root / "go.mod").exists():
        found.setdefault("lint", ("go vet ./...", "go.mod"))
        found.setdefault("test", ("go test ./...", "go.mod"))
    if (root / "Cargo.toml").exists():
        found.setdefault("lint", ("cargo clippy -q -- -D warnings", "Cargo.toml"))
        found.setdefault("test", ("cargo test -q", "Cargo.toml"))

    # --- Makefile fallback
    mk = read(root / "Makefile")
    for gate in ("lint", "test", "typecheck", "format"):
        if gate not in found and re.search(rf"^{gate}:", mk, re.M):
            found[gate] = (f"make {gate}", "Makefile target")

    # --- config overrides
    try:
        user = json.loads(read(root / ".agentkit.json") or "{}").get("verify", {})
    except json.JSONDecodeError:
        user = {}
    for gate in GATES:
        if gate in user and user[gate] != "auto":
            found[gate] = (user[gate] or None, "set in .agentkit.json" if user[gate] else "disabled in .agentkit.json")
        found.setdefault(gate, (None, "not detected"))
    return found


# ---------------------------------------------------------------- running
def run_gate(root: Path, gate: str, cmd: str) -> tuple[str, float, list[str], Path]:
    logdir = root / ".agentkit" / "logs"
    logdir.mkdir(parents=True, exist_ok=True)
    (logdir / ".gitignore").exists() or (logdir / ".gitignore").write_text("*\n")  # logs never get committed
    log = logdir / f"verify-{gate}-{dt.datetime.now():%Y%m%d-%H%M%S}.log"
    t0 = time.time()
    p = subprocess.run(cmd, shell=True, cwd=root, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       env=CHILD_ENV)
    out = p.stdout.decode("utf-8", "replace")
    log.write_text(out, encoding="utf-8")
    dur = time.time() - t0
    lines = [ANSI.sub("", l).rstrip() for l in out.splitlines()]
    if p.returncode == 0:
        return "PASS", dur, [], log
    if p.returncode == 5 and "pytest" in cmd:  # pytest: no tests collected
        return "EMPTY", dur, ["pytest collected no tests"], log
    errs, seen = [], set()
    for l in lines:
        if ERR_RE.search(l) and l.strip() not in seen:
            seen.add(l.strip())
            errs.append(l[:200])
    if not errs:
        errs = lines[-MAX_ERR:]
    summary = [l for l in lines[-5:] if re.search(r"\d+ (passed|failed|error)", l)]
    return "FAIL", dur, errs[:MAX_ERR] + [s for s in summary if s not in errs], log


def tamper_check(root: Path) -> list[str]:
    """Flag deleted/modified tests and newly added skip markers vs HEAD (staged + unstaged)."""
    notes = []
    status = sh("git", "diff", "HEAD", "--name-status", cwd=root)
    for line in status.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2 and TEST_PATH.search(parts[-1]):
            if parts[0].startswith("D"):
                notes.append(f"test file DELETED: {parts[-1]}")
    diff = sh("git", "diff", "HEAD", "-U0", cwd=root)
    cur = None
    for l in diff.splitlines():
        if l.startswith("+++ b/"):
            cur = l[6:]
        elif l.startswith("+") and not l.startswith("+++") and SKIP_RE.search(l):
            notes.append(f"skip/xfail ADDED in {cur}: {l[1:].strip()[:90]}")
        elif l.startswith("-") and not l.startswith("---") and cur and TEST_PATH.search(cur) \
                and re.search(r"\bassert\b|expect\(|assertEqual|assert_eq!", l):
            notes.append(f"assertion REMOVED in {cur}: {l[1:].strip()[:90]}")
    return list(dict.fromkeys(notes))


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--only", help="comma list of gates: " + ",".join(GATES))
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--no-guard", action="store_true")
    a = ap.parse_args()
    top = sh("git", "rev-parse", "--show-toplevel").strip()
    root = Path(top) if top else Path.cwd()
    gates = detect(root)
    wanted = [g.strip() for g in a.only.split(",")] if a.only else list(GATES)
    bad = [g for g in wanted if g not in GATES]
    if bad:
        print(f"unknown gate(s): {bad}; choose from {GATES}")
        return 1

    if a.detect:
        for g in wanted:
            cmd, why = gates[g]
            avail = "" if not cmd or has_tool(cmd) else "  [tool not installed]"
            print(f"{g:10} {cmd or '-':40} ({why}){avail}")
        return 0

    rows, details, failed, ran = [], [], False, 0
    for g in wanted:
        cmd, why = gates[g]
        if not cmd:
            rows.append(f"{g:10} SKIP   {why}")
            continue
        if not has_tool(cmd):
            rows.append(f"{g:10} SKIP   tool missing for `{cmd}` — install it or set verify.{g} in .agentkit.json")
            continue
        status, dur, errs, log = run_gate(root, g, cmd)
        ran += status in ("PASS", "FAIL")
        rel = log.relative_to(root)
        rows.append(f"{g:10} {status:6} {cmd:34} {dur:5.1f}s" + (f"  log: {rel}" if status != "PASS" else ""))
        if status == "FAIL":
            failed = True
            details.append(f"--- {g} ({len(errs)} lines; full log {rel})\n" + "\n".join(errs))
        elif status == "EMPTY":
            details.append(f"--- {g}: no tests collected — the change is unverified by tests")

    tamper = [] if a.no_guard else tamper_check(root)
    print("\n".join(rows))
    if details:
        print("\n".join(details))
    if tamper:
        print("--- TEST INTEGRITY (report these to the user; do not hide them)")
        print("\n".join("  " + t for t in tamper))
    if failed:
        print("VERDICT: FAIL (exit 1) — fix the cause, re-run verify. Do not report the task as done.")
        return 1
    if ran == 0:
        print("VERDICT: NOTHING VERIFIED (exit 2) — no gate ran. Tell the user; consider setting commands in .agentkit.json.")
        return 2
    if tamper:
        print("VERDICT: PASS WITH TEST CHANGES (exit 2) — get the user's OK on the changes above before reporting done.")
        return 2
    print("VERDICT: PASS (exit 0)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
