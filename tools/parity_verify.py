#!/usr/bin/env python3
"""parity_verify - the 0.5.0 verify.sh and the current verify engine, run on the same scenarios and compared.

  python tools/parity_verify.py [--old-ref v0.5.0] [-k NAME ...] [--keep]

For each scenario a throwaway repo is built twice, identically; the old engine (verify.sh + lib.sh + plan-step.sh,
extracted with `git archive <old-ref>`) runs in one, scripts/verify.sh of this checkout in the other. Compared:
exit code, stdout, the summary rows of the four 0.5.0 stages (lint tests diff-cover scope), the lines under each
failing `--- stage` heading, the VERDICT line up to " - ", and (CLI and --push) the tools called with their arguments.
New stages (format, typecheck, integrity) are not part of the 0.5.0 summary and are left out of the comparison.

It is not vacuous by construction: it fails when no scenario was compared, when the scenarios never produce both a
passing and a failing run, and when a deliberately broken copy of the new engine is NOT told apart from the old one.
Output ends with `compared=N differing=D`. Exit 0 only when D == 0 and the checks above hold.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(KIT / "scripts" / "tests"))
from test_verify_py import STUBS  # noqa: E402

OLD_ROWS = ("lint", "tests", "diff-cover", "scope")
ROW = re.compile(r"^(lint|tests|diff-cover|scope)\s+(PASS|FAIL|SKIP)\b(.*)$")
PLAN = """# Plan: demo
Constraints: none
Done when: tests pass
Out of scope: everything else

### Step 1: Parse input
- Mode: solo
- Files: src/a.py, tests/test_a.py (new)
- Contract: parse(s) -> int
  invariant: result is positive
- Tests: test_parse_ok, test_parse_zero
- Out of scope: src/b.py
- Verify: pytest tests/test_a.py

### Step 2: Write output
- Mode: pipeline
- Files: src/b.py, src/a.py
- Contract: write(n) -> None
- Tests: test_write
- Out of scope: src/a.py signatures
"""
STOP = '{"hook_event_name":"Stop","stop_hook_active":false}'
STOP_AGAIN = '{"hook_event_name":"Stop","stop_hook_active":true}'


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = root
        root.mkdir(parents=True)
        self.git("init", "-q", "-b", "main")
        for k, v in (("user.name", "tester"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            self.git("config", k, v)
        self.commit("README.md", "hello")

    def git(self, *a: str) -> None:
        subprocess.run(["git", *a], cwd=self.root, check=True, capture_output=True,
                       env={**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull})

    def write(self, path: str, text: str) -> None:
        p = self.root / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")

    def commit(self, path: str, text: str = "x\n") -> None:
        self.write(path, text)
        self.git("add", "--", path)
        self.git("commit", "-q", "-m", f"feat: {path}")

    def cfg(self, data: dict) -> None:
        self.write(".agentkit.json", json.dumps(data))

    def plan(self, checked: tuple[int, ...] = (), applied: bool = True) -> None:
        self.git("switch", "-q", "-c", "feat/x")
        self.write("notes/tasks/feat--x.plan.md", PLAN)
        if applied:
            marks = "\n".join(f"- [{'x' if i in checked else ' '}] {i}. step {i}" for i in (1, 2))
            self.write("notes/tasks/feat--x.md", f"# Task: demo\n- branch: feat/x\n\n## Plan\n{marks}\n\n## Next\nx\n")


# a scenario: setup(repo) -> (args, stdin, extra env)
def s_all_pass(r):
    r.plan(); r.commit("src/a.py"); return [], None, {}
def s_out_of_plan(r):
    r.plan(); r.commit("src/a.py"); r.commit("src/other.py"); return [], None, {}
def s_hook_fail(r):
    r.plan(); r.commit("src/other.py"); r.cfg({"guards": {"hook_stages": ["lint", "scope"]}}); return ["--hook"], STOP, {}
def s_hook_pass(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"hook_stages": ["lint", "scope"]}}); return ["--hook"], STOP, {}
def s_hook_active(r):
    r.plan(); r.commit("src/other.py"); r.cfg({"guards": {"hook_stages": ["lint", "scope"]}}); return ["--hook"], STOP_AGAIN, {}
def s_hook_not_applied(r):
    r.plan(applied=False); r.commit("src/a.py"); r.cfg({"guards": {"hook_stages": ["lint", "scope"]}}); return ["--hook"], STOP, {}
def s_cli_not_applied(r):
    r.plan(applied=False); r.commit("src/a.py"); return [], None, {}
def s_push_not_applied(r):
    r.plan(applied=False); r.commit("src/a.py"); return ["--push"], None, {}
def s_decision_logged(r):
    r.plan(); r.commit("src/a.py"); r.commit("src/other.py")
    r.write("notes/decisions.md", "# Decisions\n\n## D-3f9a · 2026-11-07 · scope · feat/x\nsrc/other.py: shared helper\n")
    return [], None, {}
def s_decision_other_branch(r):
    r.plan(); r.commit("src/a.py"); r.commit("src/other.py")
    r.write("notes/decisions.md", "# Decisions\n\n## D-3f9a · 2026-11-07 · scope · feat/elsewhere\nsrc/other.py: needed\n")
    return [], None, {}
def s_step2_active(r):
    r.plan(checked=(1,)); r.commit("src/a.py"); r.commit("src/b.py"); return [], None, {}
def s_later_step_file(r):
    r.plan(); r.commit("src/b.py"); return [], None, {}
def s_untracked(r):
    r.plan(); r.commit("src/a.py"); r.write("src/a.py", "x\ny\n"); r.write("new_untracked.py", "z\n"); return [], None, {}
def s_dir_and_glob(r):
    r.plan()
    p = r.root / "notes/tasks/feat--x.plan.md"
    p.write_text(p.read_text(encoding="utf-8").replace("- Files: src/a.py, tests/test_a.py (new)", "- Files: src/pkg/, docs/*.md"),
                 encoding="utf-8", newline="\n")
    r.commit("src/pkg/deep/mod.py"); r.commit("docs/guide.md"); return [], None, {}
def s_no_plan(r):
    r.git("switch", "-q", "-c", "feat/y"); r.commit("anything.py"); return [], None, {}
def s_missing_ruff(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"ruff_cmd": "definitely-not-installed-ruff"}}); return [], None, {}
def s_missing_diff_cover(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"diff_cover_cmd": "no-such-diff-cover"}}); return [], None, {}
def s_all_off(r):
    r.plan(); r.commit("src/a.py")
    r.cfg({"guards": {"ruff": False, "pytest": False, "diff_cover": False, "scope": False}}); return [], None, {}
def s_tests_fail(r):
    r.plan(); r.commit("src/a.py"); return [], None, {"STUB_PYTEST_RC": "1"}
def s_diff_cover_fail(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"fail_under": 75, "coverage_xml": "out/cov.xml"}})
    return [], None, {"STUB_DC_RC": "1"}
def s_lint_fail(r):
    r.plan(); r.commit("src/a.py"); return [], None, {"STUB_RUFF_RC": "1"}
def s_stages_flag(r):
    r.plan(); r.commit("src/a.py"); return ["--stages", "lint,scope"], None, {}
def s_unknown_stage(r):
    r.plan(); r.commit("src/a.py"); return ["--stages", "lint,coverage"], None, {}
def s_empty_stages(r):
    r.plan(); r.commit("src/a.py"); return ["--stages", ""], None, {}
def s_push_stages(r):
    r.plan(); r.commit("src/other.py")
    r.cfg({"guards": {"hook_stages": ["lint", "scope"], "push_stages": ["tests", "diff-cover"]}}); return ["--push"], None, {}
def s_hook_typo(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"hook_stages": "lint, typo"}}); return ["--hook"], STOP, {}
def s_hook_typo_active(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"hook_stages": "lint, typo"}}); return ["--hook"], STOP_AGAIN, {}
def s_disabled_selected(r):
    r.plan(); r.commit("src/a.py"); r.cfg({"guards": {"pytest": False}}); return ["--stages", "tests,lint"], None, {}
def s_hook_unchanged(r):
    return ["--hook"], STOP, {}


SCENARIOS = {k[2:]: v for k, v in list(globals().items()) if k.startswith("s_") and callable(v)}


def extract_old(ref: str, dest: Path) -> None:
    raw = subprocess.run(["git", "archive", "--format=tar", ref, "scripts", "skills"], cwd=KIT, check=True,
                         capture_output=True).stdout
    with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
        try:
            tf.extractall(dest, filter="data")  # type: ignore[call-arg]
        except TypeError:
            tf.extractall(dest)


def copy_new(dest: Path) -> None:
    for d in ("scripts", "skills"):
        shutil.copytree(KIT / d, dest / d, ignore=shutil.ignore_patterns("__pycache__", "tests"))


def normalise(rc: int, out: str, err: str, calls: str, mode: str, repo: Path) -> dict:
    err = err.replace(str(repo).replace("\\", "/"), "<repo>").replace(str(repo), "<repo>")
    rows, blocks, cur, verdict = [], {}, None, ""
    for line in err.replace("\r", "").split("\n"):
        m = ROW.match(line)
        if m:
            rows.append(f"{m.group(1)} {m.group(2)} {m.group(3).strip()}")
            cur = None
        elif line.startswith("--- "):
            cur = line[4:].split(" ")[0]
            blocks[cur] = set()
        elif line.startswith("VERDICT:"):
            verdict = line.split(" - ")[0]
            cur = None
        elif cur and line.strip():
            blocks[cur].add(line.strip())
        elif line.startswith(("WARN deprecated", "format ", "typecheck ", "integrity ")):
            cur = None
    blocks = {k: sorted(v) for k, v in blocks.items() if k in OLD_ROWS}
    other = sorted(x for x in err.replace("\r", "").split("\n") if x.startswith(("verify.sh:", "verify:")) and "unknown stage" in x)
    tools = sorted(x.strip() for x in calls.splitlines() if x.strip()) if mode != "hook" else []
    return {"rc": rc, "stdout": out, "rows": rows, "blocks": blocks, "verdict": verdict, "tools": tools,
            "errors": [re.sub(r" \(choose from:.*\)$", "", re.sub(r"^verify(\.sh)?: ", "", x)) for x in other]}


def run_one(name: str, tree: Path, work: Path, bindir: Path, logs: Path) -> dict:
    repo = Repo(work / name)
    args, stdin, extra = SCENARIOS[name](repo)
    log = logs / f"{tree.name}-{name}.log"
    log.write_text("", encoding="utf-8")
    env = {**os.environ, "PATH": str(bindir) + os.pathsep + os.environ["PATH"], "STUB_LOG": str(log),
           "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, **extra}
    for k in ("STUB_RUFF_RC", "STUB_PYTEST_RC", "STUB_DC_RC"):
        if k not in extra:
            env.pop(k, None)
    cwd = repo.root
    p = subprocess.run(["bash", str(tree / "scripts" / "verify.sh"), *args], cwd=cwd, input=stdin or "", capture_output=True,
                       text=True, env=env, encoding="utf-8", errors="replace")
    mode = "hook" if "--hook" in args else "cli"
    return normalise(p.returncode, p.stdout, p.stderr, log.read_text(encoding="utf-8"), mode, repo.root)


def compare(names: list[str], old: Path, new: Path, tmp: Path, bindir: Path, label: str) -> list[tuple[str, dict, dict]]:
    diffs, logs = [], tmp / "logs"
    logs.mkdir(exist_ok=True)
    for n in names:
        a = run_one(n, old, tmp / f"{label}-old", bindir, logs)
        b = run_one(n, new, tmp / f"{label}-new", bindir, logs)
        if a != b:
            diffs.append((n, a, b))
        yield_status = "OK  " if a == b else "DIFF"
        print(f"{yield_status} {label}:{n}  rc={a['rc']}/{b['rc']} rows={len(a['rows'])}")
        compare.seen.append(a)
    return diffs


compare.seen = []  # type: ignore[attr-defined]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old-ref", default="v0.5.0")
    ap.add_argument("-k", nargs="+", metavar="NAME")
    ap.add_argument("--keep", action="store_true")
    ap.add_argument("--skip-broken", action="store_true", help="skip the non-vacuity check (quick iteration only; exits 1)")
    a = ap.parse_args()
    names = a.k or list(SCENARIOS)
    tmp = Path(tempfile.mkdtemp(prefix="ak-parity-"))
    try:
        bindir = tmp / "bin"
        bindir.mkdir()
        for tool, body in STUBS.items():
            f = bindir / tool
            f.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
            f.chmod(0o755)
        old, new, broken = tmp / "old", tmp / "new", tmp / "broken"
        extract_old(a.old_ref, old)
        copy_new(new)
        copy_new(broken)
        vp = broken / "scripts" / "verify.py"
        src = vp.read_text(encoding="utf-8")
        mutated = src.replace('f"{stage:<11} {res.status:<5} {res.note}"', 'f"{stage:<11} {res.status:<5}"')
        mutated = mutated.replace("Out-of-plan file: {f}.", "Out-of-plan FILE: {f}.")
        if mutated == src:
            print("parity: the mutation of the broken copy did not apply; update tools/parity_verify.py", file=sys.stderr)
            return 1
        vp.write_text(mutated, encoding="utf-8", newline="\n")

        compare.seen = []
        diffs = compare(names, old, new, tmp, bindir, "main")
        seen = list(compare.seen)
        for n, x, y in diffs:
            print(f"--- {n}\n old: {json.dumps(x, indent=1)[:1500]}\n new: {json.dumps(y, indent=1)[:1500]}")
        compare.seen = []
        broken_diffs = [] if a.skip_broken else compare(names, old, broken, tmp, bindir, "broken")
        problems = []
        if not seen:
            problems.append("no scenario was compared")
        rcs = {x["rc"] for x in seen}
        if not ({0} <= rcs and rcs - {0}):
            problems.append(f"the scenarios never produce both a passing and a failing run (exit codes seen: {sorted(rcs)})")
        statuses = {row.split()[1] for x in seen for row in x["rows"]}
        if not {"PASS", "FAIL", "SKIP"} <= statuses:
            problems.append(f"row statuses seen: {sorted(statuses)}; need PASS, FAIL and SKIP")
        if a.skip_broken:
            problems.append("--skip-broken: the non-vacuity check did not run")
        elif not broken_diffs:
            problems.append("the deliberately broken copy was NOT told apart from the old engine: the comparison is vacuous")
        print(f"compared={len(seen)} differing={len(diffs)} broken_copy_differing={len(broken_diffs)}")
        for p in problems:
            print(f"parity: {p}", file=sys.stderr)
        return 0 if not diffs and not problems else 1
    finally:
        if a.keep:
            print(f"kept {tmp}")
        else:
            def onerr(fn, path, _exc):
                os.chmod(path, 0o700)
                fn(path)
            shutil.rmtree(tmp, onerror=onerr)


if __name__ == "__main__":
    sys.exit(main())
