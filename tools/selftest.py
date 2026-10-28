#!/usr/bin/env python3
"""Self-test for agent-kit: builds throwaway git repos and runs the real scripts against them.

  python tools/selftest.py                 run every scenario
  python tools/selftest.py --cp1252        same, with PYTHONIOENCODING=cp1252 for every child
                                           (simulates a Windows console)
  python tools/selftest.py -k A1 B1a       only these scenario ids
  python tools/selftest.py --keep          keep the temp dirs (printed on failure)

Prints one table (PASS/FAIL · id · scenario); exits 1 if any scenario fails. Stdlib only.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent
SK = KIT / "skills"
SCRIPTS = {
    "pl": SK / "writing-plans/scripts/plan.py",
    "nt": SK / "keeping-notes/scripts/notes.py",
    "gk": SK / "managing-git/scripts/gitkit.py",
    "vf": SK / "verifying-changes/scripts/verify.py",
    "rmap": SK / "economizing-context/scripts/repomap.py",
    "run": SK / "economizing-context/scripts/run.py",
    "dbg": SK / "debugging-systematically/scripts/dbg.py",
    "ex": SK / "tracking-experiments/scripts/exp.py",
    "pk": SK / "crafting-prompts/scripts/promptkit.py",
    "validate": KIT / "tools/validate_skills.py",
    "install": KIT / "tools/install.py",
}
VI = "Bộ dữ liệu FAQ mẫu + loader"

CP1252 = False
SCENARIOS: list[tuple[str, str, object]] = []


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def scenario(sid: str, title: str):
    def deco(fn):
        SCENARIOS.append((sid, title, fn))
        return fn
    return deco


class Fail(AssertionError):
    pass


def check(cond, msg: str) -> None:
    if not cond:
        raise Fail(msg)


def rmtree(p: Path) -> None:
    def onerr(fn, path, _exc):  # git objects are read-only on Windows
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(p, onerror=onerr)


def child_env(extra: dict | None = None, force_cp1252: bool = False) -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    if CP1252 or force_cp1252:
        env["PYTHONIOENCODING"] = "cp1252"
    env.update(extra or {})
    return env


def run(cmd: list, cwd: Path, env: dict | None = None, force_cp1252: bool = False) -> tuple[int, str]:
    p = subprocess.run([str(c) for c in cmd], cwd=cwd, capture_output=True, encoding="utf-8", errors="replace",
                       env=child_env(env, force_cp1252), timeout=300)
    return p.returncode, p.stdout + p.stderr


def kit(name: str, *args, cwd: Path, env: dict | None = None, force_cp1252: bool = False) -> tuple[int, str]:
    return run([sys.executable, SCRIPTS[name], *args], cwd, env, force_cp1252)


def git(cwd: Path, *args) -> str:
    return run(["git", *args], cwd)[1]


class T:
    """Per-scenario scratch space."""

    def __init__(self, base: Path):
        self.base = base

    def sub(self, name: str) -> Path:
        d = self.base / name
        d.mkdir(parents=True, exist_ok=True)
        return d

    def repo(self, name: str = "repo", files: dict | None = None, commit: bool = True) -> Path:
        d = self.sub(name)
        git(d, "init", "-q", "-b", "main")
        for k, v in (("user.name", "selftest"), ("user.email", "selftest@example.com"), ("core.autocrlf", "false")):
            git(d, "config", k, v)
        self.write(d, {"README.md": "# demo\n", **(files or {})})
        if commit:
            self.commit(d)
        return d

    @staticmethod
    def write(root: Path, files: dict) -> None:
        for rel, text in files.items():
            f = root / rel
            f.parent.mkdir(parents=True, exist_ok=True)
            f.write_bytes((text).encode("utf-8"))

    @staticmethod
    def commit(d: Path, msg: str = "chore: snapshot") -> None:
        git(d, "add", "-A")
        git(d, "commit", "-q", "-m", msg)


UT_TEST = "import unittest\n\n\nclass T(unittest.TestCase):\n    def test_ok(self):\n        self.assertEqual(1, 1)\n"

PLAN_VI = f"""# Plan: {VI}
Constraints: không đổi API
Done when: `a.py` exits 0
Out of scope: web

## 1. {VI}
Files: a.py (new)
Do: tạo a.py với hàm load()
Verify: python a.py
Done: exit 0
"""


# ================================================================ A scenarios
@scenario("A1", "Vietnamese text through plan.py / notes.py under cp1252")
def a1(t: T):
    d = t.repo()
    t.write(d, {"plan.md": PLAN_VI})
    code, out = kit("pl", "check", "plan.md", cwd=d, force_cp1252=True)
    check(code == 0 and "UnicodeEncodeError" not in out, f"plan check exit {code}: {out[:200]}")
    check(VI[:12] in out, f"Vietnamese goal not echoed: {out[:200]}")
    kit("nt", "init", cwd=d, force_cp1252=True)
    code, out = kit("nt", "task", "--goal", VI, cwd=d, force_cp1252=True)
    check(code == 0, f"notes task exit {code}: {out[:200]}")
    code, out = kit("nt", "brief", cwd=d, force_cp1252=True)
    check(code == 0 and "UnicodeEncodeError" not in out, f"brief exit {code}: {out[:200]}")
    check(VI in out and "�" not in out, f"brief output garbled: {out[:200]}")


@scenario("A1v", "validate_skills flags a script without UTF-8 reconfigure")
def a1v(t: T):
    sk = t.sub("skills") / "fake"
    t.write(sk, {
        "SKILL.md": "---\nname: fake\ndescription: Does a thing. Use when testing the validator.\n---\n\n# Fake\n",
        "scripts/x.py": "import sys\n\n\ndef main():\n    print('hi')\n\n\nif __name__ == '__main__':\n    sys.exit(main())\n",
    })
    code, out = kit("validate", str(sk.parent), cwd=t.base)
    check(code == 1 and "lacks UTF-8" in out, f"expected FAIL with 'lacks UTF-8', got exit {code}: {out[:300]}")


@scenario("A2", "plan.py apply echoes Vietnamese step titles intact (child output decoding)")
def a2(t: T):
    d = t.repo()
    t.write(d, {"plan.md": PLAN_VI})
    kit("nt", "init", cwd=d)
    code, out = kit("pl", "apply", "plan.md", cwd=d, force_cp1252=True)
    check(code == 0, f"apply exit {code}: {out[:300]}")
    check(f"step 1: {VI}" in out, f"title garbled or missing: {out[:300]}")


@scenario("A3a", "unittest-only repo with no config files: test gate runs and passes")
def a3a(t: T):
    d = t.repo(files={"tests/test_x.py": UT_TEST})
    code, out = kit("vf", cwd=d)
    check(code == 0, f"exit {code}: {out[:400]}")
    check("test       PASS" in out and "VERDICT: PASS (exit 0)" in out, f"no passing test gate: {out[:400]}")


@scenario("A3b", "broken pytest falls back to unittest")
def a3b(t: T):
    fake = t.sub("fakepkgs") / "pytest"
    t.write(fake, {"__init__.py": "", "__main__.py": "raise ImportError('No module named coverage')\n"})
    d = t.repo(files={"tests/test_x.py": UT_TEST})
    env = {"PYTHONPATH": str(fake.parent) + os.pathsep + os.environ.get("PYTHONPATH", "")}
    code, out = kit("vf", "--detect", cwd=d, env=env)
    check("unittest" in out and "pytest not runnable" in out, f"--detect does not explain: {out[:400]}")
    code, out = kit("vf", cwd=d, env=env)
    check(code == 0 and "test       PASS" in out, f"exit {code}: {out[:400]}")


@scenario("A3c", "no tests at all: NOTHING VERIFIED (exit 2)")
def a3c(t: T):
    d = t.repo()
    code, out = kit("vf", cwd=d)
    check(code == 2 and "NOTHING VERIFIED (exit 2)" in out, f"exit {code}: {out[:300]}")


FAKE_KEY = "sk-" + "a" * 24  # built at runtime so this file never trips the kit's own scan


@scenario("A4", "scan --staged ignores kit-managed notes/, still blocks a key in src/")
def a4(t: T):
    d = t.repo("a4")
    t.write(d, {"notes/decisions.md": f"# Decisions\nkey = \"{FAKE_KEY}\"\n"})
    git(d, "add", "-A")
    code, out = kit("gk", "scan", "--staged", cwd=d)
    check(code == 0, f"kit-managed file should be ignored: exit {code}: {out[:300]}")
    t.write(d, {"src/cfg.py": f"API_KEY = \"{FAKE_KEY}\"\n"})
    git(d, "add", "-A")
    code, out = kit("gk", "scan", "--staged", cwd=d)
    check(code == 1 and "src/cfg.py" in out and "notes/" not in out, f"exit {code}: {out[:300]}")
    code, out = kit("gk", "diff", "--staged", cwd=d)
    check("kit-managed" in out and "src/cfg.py" in out, f"diff should list notes/ as kit-managed: {out[:300]}")


def bench_module():
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_under_test", KIT / "tools" / "bench" / "bench.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def scan_case(t: T, name: str, line: str, want_block: bool) -> None:
    """The verdict on one line must agree across scan --staged, scan of an untracked file and bench's secret count."""
    d = t.repo(name)
    base = git(d, "rev-parse", "HEAD").strip()
    t.write(d, {"src/cfg.py": "import os\n" + line + "\n", "src/new.py": line + "\n"})
    git(d, "add", "src/cfg.py")
    code, out = kit("gk", "scan", "--staged", cwd=d)
    check(code == (1 if want_block else 0), f"scan --staged on {line!r}: exit {code}: {out[:300]}")
    code, out = kit("gk", "scan", cwd=d)
    check(("src/new.py" in out) == want_block, f"untracked scan on {line!r}: exit {code}: {out[:300]}")
    git(d, "commit", "-q", "--no-verify", "-m", "feat: add config")
    n = bench_module().measure_violations(d, base, [])["secret_in_history"]
    check(n == (1 if want_block else 0), f"bench secret_in_history on {line!r}: {n}")


@scenario("A5a", "scan blocks a key passed as the os.getenv default")
def a5a(t: T):
    scan_case(t, "a5a", f'API_KEY = os.getenv("K", "{FAKE_KEY}")', True)


@scenario("A5b", "scan blocks a key passed as the os.environ.get default")
def a5b(t: T):
    scan_case(t, "a5b", f'API_KEY = os.environ.get("K", "{FAKE_KEY}")', True)


@scenario("A5c", "scan blocks a key after `os.getenv(...) or`")
def a5c(t: T):
    scan_case(t, "a5c", f'API_KEY = os.getenv("X") or "{FAKE_KEY}"', True)


@scenario("A5d", "scan passes a plain os.getenv lookup")
def a5d(t: T):
    scan_case(t, "a5d", 'API_KEY = os.getenv("OPENAI_API_KEY")', False)


@scenario("A5e", "scan passes a placeholder key value")
def a5e(t: T):
    scan_case(t, "a5e", 'API_KEY = "sk-your-key-here"', False)


@scenario("B2", "gitkit suggest, snapshot and stashes no longer exist")
def b2(t: T):
    d = t.repo("b2")
    for sub in ("suggest", "snapshot", "stashes"):
        code, out = kit("gk", sub, cwd=d)
        check(code == 2 and "invalid choice" in out, f"{sub}: exit {code}: {out[:200]}")
    code, out = kit("gk", "--help", cwd=d)
    check(all(w in out for w in ("diff", "scan", "lint-msg", "branch-name", "preflight")), f"help: {out[:300]}")


# ================================================================ B1 zone
def zone_json(d: Path, *args) -> tuple[int, dict, str]:
    code, out = kit("rmap", *args, "--json", cwd=d)
    try:
        return code, json.loads(out), out
    except json.JSONDecodeError:
        return code, {}, out


@scenario("Z1", "zone: a->b->c chain, hops 2 lists b(1), a(2) as importers + their tests")
def b1a(t: T):
    d = t.repo(files={"a.py": "import b\n", "b.py": "import c\n", "c.py": "X = 1\n",
                      "tests/test_a.py": "import a\n", "tests/test_c.py": "import c\n"})
    code, j, out = zone_json(d, "--zone", "c.py", "--hops", "2")
    check(code == 0 and j, f"exit {code}: {out[:300]}")
    files = {f["path"]: (f["distance"], f["direction"]) for f in j["files"]}
    check(files.get("b.py") == (1, "importers") and files.get("a.py") == (2, "importers"), f"files: {files}")
    check({"tests/test_a.py", "tests/test_c.py"} <= set(j["tests"]), f"tests: {j['tests']}")
    code, out = kit("rmap", "--zone", "c", "--hops", "1", cwd=d)
    check(code == 0 and "b.py" in out and "a.py" not in out.split("TESTS")[0].replace("SEEDS", ""),
          f"text output (dotted seed, hops 1): {out[:300]}")


@scenario("Z2", "zone: relative imports inside packages under src/ are resolved")
def b1b(t: T):
    d = t.repo(files={
        "src/top/__init__.py": "", "src/top/pkg/__init__.py": "", "src/top/pkg/y.py": "VALUE = 1\n",
        "src/top/pkg/z.py": "from . import y\n", "src/top/pkg/w.py": "from .y import VALUE\n",
        "src/top/a/__init__.py": "", "src/top/a/m.py": "from ..pkg import y\n",
    })
    code, j, out = zone_json(d, "--zone", "src/top/pkg/y.py", "--hops", "1", "--direction", "importers")
    got = {f["path"] for f in j.get("files", [])}
    want = {"src/top/pkg/z.py", "src/top/pkg/w.py", "src/top/a/m.py"}
    check(code == 0 and want <= got, f"want {sorted(want)}, got {sorted(got)}: {out[:200]}")
    code, j, out = zone_json(d, "--zone", "top.a.m", "--hops", "1", "--direction", "imports")
    check("src/top/pkg/y.py" in {f["path"] for f in j.get("files", [])}, f"dotted seed under src/: {out[:300]}")


@scenario("Z3", "zone-from-log: seeds are repo frames only")
def b1c(t: T):
    d = t.repo(files={"pkg/__init__.py": "", "pkg/m.py": "import json\n", "pkg/n.py": "import pkg.m\n"})
    log = t.base / "trace.log"
    log.write_text(
        "Traceback (most recent call last):\n"
        f'  File "{(d / "pkg" / "n.py").resolve()}", line 1, in <module>\n    import pkg.m\n'
        '  File "pkg/m.py", line 1, in <module>\n    import json\n'
        r'  File "C:\Python313\Lib\json\__init__.py", line 106, in loads' + "\n"
        '  File "/usr/lib/python3.11/json/decoder.py", line 3, in decode\nValueError: x\n', encoding="utf-8")
    code, j, out = zone_json(d, "--zone-from-log", str(log), "--hops", "0")
    check(code == 0 and sorted(j.get("seeds", [])) == ["pkg/m.py", "pkg/n.py"], f"seeds: {j.get('seeds')} / {out[:300]}")


@scenario("Z4", "zone --json has the documented keys and reports dynamic imports")
def b1d(t: T):
    d = t.repo(files={"y.py": "V = 1\n",
                      "x.py": "import importlib\nimport y\n\n\ndef f(n):\n    return importlib.import_module(n)\n"})
    code, j, out = zone_json(d, "--zone", "y.py")
    check(code == 0 and set(j) == {"seeds", "files", "tests", "notes"}, f"keys: {sorted(j)} / {out[:200]}")
    check(any("dynamic import in x.py:6" in n for n in j["notes"]), f"notes: {j['notes']}")
    code, out = kit("rmap", "--zone", "y.py", cwd=d)
    check("NOTE: dynamic import in x.py:6" in out and "import importlib" not in out, f"text: {out[:300]}")


# ================================================================ B2 affected
def affected_repo(t: T, name: str) -> Path:
    d = t.repo(name, files={
        "a.py": "import c\n", "c.py": "X = 1\n", "d.py": "Y = 2\n",
        "tests/test_c.py": "import unittest\nimport c\n\n\nclass T(unittest.TestCase):\n"
                           "    def test_c(self):\n        self.assertEqual(c.X, 1)\n",
        "tests/test_d.py": "import unittest\nimport d\n\n\nclass T(unittest.TestCase):\n"
                           "    def test_d(self):\n        self.assertEqual(d.Y, 3)\n",   # fails on purpose
    })
    t.write(d, {"c.py": "X = 1\n# touched\n"})
    return d


@scenario("AF1", "vf --affected runs only the tests in the changed file's zone")
def b2a(t: T):
    d = affected_repo(t, "r")
    code, out = kit("vf", cwd=d)
    check(code == 1 and "VERDICT: FAIL (exit 1)" in out, f"full run should FAIL on test_d: exit {code}: {out[:300]}")
    code, out = kit("vf", "--affected", cwd=d)
    check(code == 0 and "VERDICT: PASS (exit 0) (affected only)" in out, f"exit {code}: {out[:400]}")


@scenario("AF2", "vf --affected without repomap falls back to the full test gate and says why")
def b2b(t: T):
    d = affected_repo(t, "r")
    lone = t.sub("lone") / "verifying-changes"
    shutil.copytree(SK / "verifying-changes", lone, ignore=shutil.ignore_patterns("__pycache__"))
    code, out = run([sys.executable, lone / "scripts" / "verify.py", "--affected"], d)
    check(code == 1 and "affected:" in out and "ran full test gate" in out and "(affected only)" not in out,
          f"exit {code}: {out[:400]}")


# ================================================================ B3 decisions in plans, /grill
def plan_with(decisions: str) -> str:
    return ("# Plan: demo\nConstraints: none\nDone when: `a.py` exits 0\nOut of scope: none\n"
            f"Decisions: {decisions}\n\n## 1. Add a\nFiles: a.py (new)\nDo: create a.py\n"
            "Verify: python a.py\nDone: exit 0\n")


def new_id(out: str, prefix: str) -> str:
    """The branch-safe id (prefix + 4 hex) a command printed."""
    m = re.search(rf"\b{prefix}-[0-9a-f]{{4}}\b", out)
    check(m is not None, f"no {prefix}-xxxx id in: {out[:200]}")
    return m[0]


def decided_repo(t: T, supersede: bool = False) -> tuple[Path, str, str]:
    d = t.repo()
    kit("nt", "init", cwd=d)
    code, out = kit("nt", "decide", "--topic", "storage", "--text", "Use JSON files", "--why", "small data", cwd=d)
    check(code == 0, f"decide: {out[:200]}")
    first, second = new_id(out, "D"), ""
    if supersede:
        code, out = kit("nt", "decide", "--topic", "storage", "--text", "Use SQLite", "--why", "grew",
                        "--supersedes", first, cwd=d)
        check(code == 0, f"supersede: {out[:200]}")
        second = new_id(out, "D")
    return d, first, second


@scenario("DC1", "plan check: an existing Decisions id passes")
def b3a(t: T):
    d, first, _ = decided_repo(t)
    t.write(d, {"plan.md": plan_with(first)})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 0, f"exit {code}: {out[:300]}")


@scenario("DC2", "plan check: a missing Decisions id BLOCKs")
def b3b(t: T):
    d, first, _ = decided_repo(t)
    t.write(d, {"plan.md": plan_with(f"{first}, D-099")})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 1 and "D-099" in out, f"exit {code}: {out[:300]}")


@scenario("DC3", "plan check: a superseded Decisions id WARNs and names the replacement")
def b3c(t: T):
    d, first, second = decided_repo(t, supersede=True)
    t.write(d, {"plan.md": plan_with(first)})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 2 and second in out, f"exit {code}: {out[:300]}")


@scenario("DC4", "install copies the grilling-designs skill and /grill command")
def b3d(t: T):
    d = t.repo()
    code, out = kit("install", str(d), "--with", "grill", cwd=t.base)
    check(code == 0, f"install exit {code}: {out[:300]}")
    check((d / ".agentkit/skills/grilling-designs/SKILL.md").is_file(), "skill not installed")
    check((d / ".claude/commands/grill.md").is_file() and (d / ".claude/commands/task.md").is_file(),
          "commands not installed")
    text = (d / ".agentkit/skills/grilling-designs/SKILL.md").read_text(encoding="utf-8")
    check("disable-model-invocation: true" in text, "frontmatter lost")


# ================================================================ B4 dbg
@scenario("DB1", "dbg open/hypo/close round trip; list shows the closed bug with its cause")
def b4a(t: T):
    d = t.repo()
    sha = git(d, "rev-parse", "HEAD").strip()
    code, out = kit("dbg", "open", "--layer", "code", "--symptom", "KeyError answer parsing model output",
                    "--repro", "python app.py", cwd=d)
    check(code == 0, f"open: exit {code}: {out[:200]}")
    bid = new_id(out, "B")
    code, out = kit("dbg", "hypo", bid, "dict is missing the key", "--result", "rejected", cwd=d)
    check(code == 0, f"hypo: exit {code}: {out[:200]}")
    code, out = kit("dbg", "close", bid, "--cause", f"loader skipped {VI}", "--fix", sha, cwd=d, force_cp1252=True)
    check(code == 0, f"close: exit {code}: {out[:200]}")
    code, out = kit("dbg", "list", cwd=d, force_cp1252=True)
    check(code == 0 and bid in out and "closed" in out and f"loader skipped {VI}" in out, f"list: {out[:300]}")
    code, out = kit("dbg", "list", "--open", cwd=d)
    check("no bugs open" in out, f"list --open: {out[:200]}")


@scenario("DB2", "dbg close without --cause BLOCKs")
def b4b(t: T):
    d = t.repo()
    bid = new_id(kit("dbg", "open", "--layer", "data", "--symptom", "row count drops after join", cwd=d)[1], "B")
    code, out = kit("dbg", "close", bid, cwd=d)
    check(code == 1 and "--cause" in out, f"exit {code}: {out[:200]}")
    code, out = kit("dbg", "list", "--open", cwd=d)
    check(bid in out, "bug must stay open")


@scenario("DB3", "a second open with a similar symptom WARNs and names the earlier bug")
def b4c(t: T):
    d = t.repo()
    first = new_id(kit("dbg", "open", "--layer", "llm", "--symptom", "KeyError answer when parsing model output json",
                       cwd=d)[1], "B")
    code, out = kit("dbg", "open", "--layer", "llm", "--symptom", "KeyError answer parsing model output", cwd=d)
    check(code == 2 and first in out and new_id(out, "B") != first, f"exit {code}: {out[:300]}")
    code, out = kit("dbg", "open", "--layer", "data", "--symptom", "unrelated disk quota exceeded", cwd=d)
    check(code == 0, f"unrelated bug should not warn: exit {code}: {out[:200]}")


@scenario("DB4", "dbg llm-case appends a valid case; promptkit check still passes; bad vars BLOCK")
def b4d(t: T):
    spec = {"name": "qa", "target": "generic", "goal": "Answer the question from the documents.",
            "inputs": [{"name": "question", "description": "user question"}],
            "instructions": ["Answer only from the documents."],
            "output": {"format": "text", "description": "A short answer."}, "cases": []}
    d = t.repo(files={"prompts/qa.spec.json": json.dumps(spec, indent=2) + "\n"})
    bid = new_id(kit("dbg", "open", "--layer", "llm", "--symptom", "model invents a price", cwd=d)[1], "B")
    code, out = kit("dbg", "llm-case", bid, "--spec", "prompts/qa.spec.json", "--vars", '{"nope": "x"}',
                    "--check", '{"type":"not_contains","value":"$"}', cwd=d)
    check(code == 1, f"mismatched vars should BLOCK: exit {code}: {out[:200]}")
    code, out = kit("dbg", "llm-case", bid, "--spec", "prompts/qa.spec.json",
                    "--vars", '{"question": "How much is it?"}', "--check", '{"type":"not_contains","value":"$"}', cwd=d)
    check(code == 0, f"llm-case: exit {code}: {out[:200]}")
    got = json.loads((d / "prompts/qa.spec.json").read_text(encoding="utf-8"))
    check(got["cases"] == [{"vars": {"question": "How much is it?"},
                            "checks": [{"type": "not_contains", "value": "$"}], "source": bid}], f"cases: {got['cases']}")
    code, out = kit("pk", "check", "prompts/qa.spec.json", cwd=d)
    check(code == 0, f"promptkit check exit {code}: {out[:300]}")


# ================================================================ ID branch-safe ids for append-only logs
def legacy(path: Path, new: str, old: str) -> None:
    """Rewrite a freshly minted id to an old sequential one, as written by an earlier kit."""
    path.write_bytes(path.read_text(encoding="utf-8").replace(new, old).encode("utf-8"))


@scenario("ID1", "old sequential ids (D-001, E-0001, B-001) stay valid: show, supersedes, plan Decisions, compare, close")
def id1(t: T):
    d = t.repo()
    kit("nt", "init", cwd=d)
    legacy(d / "notes/decisions.md", new_id(kit("nt", "decide", "--topic", "db", "--text", "Use JSON", cwd=d)[1], "D"),
           "D-001")
    code, out = kit("nt", "show", "--id", "D-001", cwd=d)
    check(code == 0 and "D-001" in out and "Use JSON" in out, f"show old id: {out[:200]}")
    code, out = kit("nt", "decide", "--topic", "db", "--text", "Use SQLite", "--supersedes", "D-001", cwd=d)
    check(code == 0, f"supersede old id: exit {code}: {out[:200]}")
    repl = new_id(out, "D")
    t.write(d, {"plan.md": plan_with("D-001")})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 2 and repl in out, f"plan cites superseded D-001: exit {code}: {out[:300]}")
    runs = d / "experiments/runs.jsonl"
    for name, old in (("a", "E-0001"), ("b", "E-0002")):
        legacy(runs, new_id(kit("ex", "log", "--name", name, "--metric", "acc=0.5", cwd=d)[1], "E"), old)
    code, out = kit("ex", "compare", "E-0001", "E-0002", cwd=d)
    check(code == 0 and "E-0001" in out and "E-0002" in out, f"compare old ids: exit {code}: {out[:300]}")
    code, out = kit("ex", "log", "--name", "c", "--metric", "acc=0.6", cwd=d)
    check(new_id(out, "E") not in ("E-0001", "E-0002"), out[:200])
    legacy(d / "notes/debug.jsonl", new_id(kit("dbg", "open", "--layer", "code", "--symptom", "boom", cwd=d)[1], "B"),
           "B-001")
    code, out = kit("dbg", "close", "B-001", "--cause", "typo", cwd=d)
    check(code == 0, f"close old id: exit {code}: {out[:200]}")


def two_branches(t: T, add) -> tuple[Path, str, str, str]:
    """Installed repo; branches a and b each call add(d, label) -> id, then both merge into main."""
    d = t.repo()
    code, out = kit("install", str(d), "--with", "experiments", cwd=t.base)
    check(code == 0, f"install exit {code}: {out[-300:]}")
    t.commit(d, "chore: install kit")
    ids = []
    for b in ("a", "b"):
        git(d, "switch", "-q", "-c", b, "main")
        ids.append(add(d, b))
        t.commit(d, f"chore: log {b}")
    git(d, "switch", "-q", "main")
    out = ""
    for b in ("a", "b"):
        code, o = run(["git", "merge", "--no-ff", "--no-edit", b], d)
        out += o
        check(code == 0, f"merge {b} must not conflict: exit {code}: {o[-300:]}")
    check(not git(d, "diff", "--name-only", "--diff-filter=U").strip(), "unmerged paths left")
    check(ids[0] != ids[1], f"ids must differ: {ids}")
    return d, ids[0], ids[1], out


@scenario("ID2", "install writes merge=union to .gitattributes once; two branches that each `nt decide` merge cleanly")
def id2(t: T):
    def decide(d: Path, b: str) -> str:
        code, out = kit("nt", "decide", "--topic", f"t{b}", "--text", f"choice {b}", cwd=d)
        check(code == 0, f"decide on {b}: {out[:200]}")
        return new_id(out, "D")
    d, a, b, _ = two_branches(t, decide)
    kit("install", str(d), cwd=t.base)
    lines = (d / ".gitattributes").read_text(encoding="utf-8").splitlines()
    for p in ("notes/decisions.md", "notes/debug.jsonl", "experiments/runs.jsonl"):
        check(lines.count(f"{p} merge=union") == 1, f".gitattributes after 2 installs: {lines}")
    code, out = kit("nt", "show", cwd=d)
    check(code == 0 and a in out and b in out and "choice a" in out and "choice b" in out, f"show: {out[:300]}")


@scenario("ID3", "two branches that each `ex log` merge cleanly; both runs listed with distinct ids")
def id3(t: T):
    def log(d: Path, b: str) -> str:
        code, out = kit("ex", "log", "--name", f"run-{b}", "--metric", "acc=0.5", cwd=d)
        check(code in (0, 2), f"ex log on {b}: {out[:200]}")
        return new_id(out, "E")
    d, a, b, _ = two_branches(t, log)
    code, out = kit("ex", "list", cwd=d)
    check(code == 0 and a in out and b in out, f"list: {out[:300]}")
    code, out = kit("ex", "compare", a, b, cwd=d)
    check(code == 0, f"compare merged runs: exit {code}: {out[:300]}")


# ================================================================ B5 SessionStart hook
HOOK = "python .agentkit/skills/keeping-notes/scripts/notes.py brief"


@scenario("SS1", "install --hooks twice: hook present once, existing settings preserved")
def b5a(t: T):
    existing = {"permissions": {"allow": ["Bash(ls)"]},
                "hooks": {"SessionStart": [{"matcher": "startup", "hooks": [{"type": "command", "command": "echo hi"}]}],
                          "Stop": [{"hooks": [{"type": "command", "command": "echo bye"}]}]}}
    d = t.repo(files={".claude/settings.json": json.dumps(existing, indent=2) + "\n"})
    for i in (1, 2):
        code, out = kit("install", str(d), "--hooks", cwd=t.base)
        check(code == 0, f"run {i}: exit {code}: {out[-300:]}")
    got = json.loads((d / ".claude/settings.json").read_text(encoding="utf-8"))
    cmds = [h["command"] for g in got["hooks"]["SessionStart"] for h in g["hooks"]]
    check(cmds.count(HOOK) == 1 and "echo hi" in cmds, f"SessionStart commands: {cmds}")
    check(got["permissions"] == existing["permissions"] and got["hooks"]["Stop"] == existing["hooks"]["Stop"],
          "existing keys changed")
    check(got["hooks"]["SessionStart"][0] == existing["hooks"]["SessionStart"][0], "existing hook entry changed")


@scenario("SS2", "install without --hooks writes no settings.json; invalid settings.json is left untouched (exit 2)")
def b5b(t: T):
    d = t.repo(name="plain")
    code, out = kit("install", str(d), cwd=t.base)
    check(code == 0 and not (d / ".claude/settings.json").exists(), f"exit {code}; settings.json must not exist")
    bad = t.repo(name="bad", files={".claude/settings.json": "{ not json\n"})
    code, out = kit("install", str(bad), "--hooks", cwd=t.base)
    check(code == 2 and "WARN" in out, f"exit {code}: {out[-300:]}")
    check((bad / ".claude/settings.json").read_text(encoding="utf-8") == "{ not json\n", "invalid file was modified")


@scenario("B1", "install removes legacy starting-tasks and rewrites the old /task command")
def b1(t: T):
    d = t.repo(files={
        ".agentkit/skills/starting-tasks/SKILL.md": "---\nname: starting-tasks\n---\n",
        ".claude/skills/starting-tasks/SKILL.md": "---\nname: starting-tasks\n---\n",
        ".claude/commands/task.md": "Use the starting-tasks skill for this request:\n\n$ARGUMENTS\n",
    })
    code, out = kit("install", str(d), cwd=t.base)
    check(code == 0, f"install exit {code}: {out[-300:]}")
    check(not (d / ".agentkit/skills/starting-tasks").exists() and not (d / ".claude/skills/starting-tasks").exists(),
          f"legacy dirs remain: {out[-300:]}")
    task = (d / ".claude/commands/task.md").read_text(encoding="utf-8")
    check("writing-plans" in task and "starting-tasks" not in task, f"task.md: {task[:200]}")
    check((d / ".agentkit/skills/writing-plans/SKILL.md").is_file(), "writing-plans missing")
    code, out = kit("install", str(d), cwd=t.base)
    check(code == 0 and "remove" not in out, f"second install should be quiet: {out[-200:]}")


# ================================================================ B3 git hooks
HOOK_MARK = "# >>> agent-kit >>>"
ORIG_HOOK = "#!/bin/sh\necho original >> hook.log\n"


def hooked_repo(t: T, name: str = "h") -> Path:
    d = t.repo(name)
    code, out = kit("install", str(d), "--git-hooks", cwd=t.base)
    check(code == 0, f"install --git-hooks exit {code}: {out[-300:]}")
    for hook in ("pre-commit", "commit-msg"):
        check(b"\r" not in (d / ".git" / "hooks" / hook).read_bytes(), f"{hook} hook contains CR (sh breaks on CRLF)")
    return d


def commit_try(d: Path, msg: str, files: dict, t: T) -> tuple[int, str]:
    t.write(d, files)
    git(d, "add", *files)
    return run(["git", "commit", "-q", "-m", msg], d)


@scenario("B3p", "hooks without a Python interpreter warn on stderr that scan and lint were SKIPPED, and still pass")
def b3p(t: T):
    d = hooked_repo(t, "np")
    for name in ("pre-commit", "commit-msg"):
        hook = d / ".git" / "hooks" / name
        text = hook.read_text(encoding="utf-8")
        text = re.sub(r"^ak_py=.*$", "ak_py=/nonexistent/python", text, flags=re.M)
        text = "\n".join(l for l in text.splitlines() if not l.startswith("  elif command -v")) + "\n"
        hook.write_bytes(text.encode("utf-8"))
    code, out = commit_try(d, "bad message", {"a.txt": "x\n"}, t)
    check(code == 0, f"commit must still pass, exit {code}: {out[-300:]}")
    check(out.count("python not found") == 2 and "SKIPPED" in out, f"warning missing: {out[-300:]}")


@scenario("B3a", "--git-hooks keeps an existing hook, adds the block once (also under core.hooksPath)")
def b3a(t: T):
    d = t.repo("a", files={".git-keep": ""})
    hook = d / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes((ORIG_HOOK).encode("utf-8"))
    for i in (1, 2):
        code, out = kit("install", str(d), "--git-hooks", cwd=t.base)
        check(code == 0, f"run {i}: exit {code}: {out[-300:]}")
    text = hook.read_text(encoding="utf-8")
    check(text.count(HOOK_MARK) == 1, f"block count {text.count(HOOK_MARK)}")
    check(text.startswith("#!/bin/sh\n") and "echo original >> hook.log\n" in text, "original content lost")
    check("exit 0" not in text.split("# <<< agent-kit <<<")[0], "block must not contain exit 0")
    code, out = commit_try(d, "feat: add y module", {"y.py": "Y = 1\n"}, t)
    check(code == 0 and (d / "hook.log").exists(), f"original hook must still run: exit {code}: {out[:300]}")
    # core.hooksPath
    e = t.repo("e")
    git(e, "config", "core.hooksPath", ".githooks")
    (e / ".githooks").mkdir()
    (e / ".githooks" / "pre-commit").write_bytes((ORIG_HOOK).encode("utf-8"))
    code, out = kit("install", str(e), "--git-hooks", cwd=t.base)
    check(code == 0, f"hooksPath install exit {code}: {out[-300:]}")
    check(HOOK_MARK in (e / ".githooks" / "pre-commit").read_text(encoding="utf-8"), "block not in core.hooksPath dir")
    check((e / ".githooks" / "commit-msg").is_file() and not (e / ".git" / "hooks" / "commit-msg").exists(),
          "hooks must go to core.hooksPath only")


@scenario("B3b", "pre-commit hook rejects a commit that contains a key, accepts a clean one")
def b3b(t: T):
    d = hooked_repo(t, "b")
    code, out = commit_try(d, "feat: add settings", {"src/cfg.py": f"API_KEY = \"{FAKE_KEY}\"\n"}, t)
    check(code != 0 and "BLOCK" in out, f"key must be rejected: exit {code}: {out[:300]}")
    git(d, "reset", "-q")
    (d / "src" / "cfg.py").unlink()
    code, out = commit_try(d, "feat: add clean module", {"src/ok.py": "X = 1\n"}, t)
    check(code == 0, f"clean commit rejected: exit {code}: {out[:300]}")


@scenario("B3c", "commit-msg hook rejects a bad message; merge, fixup and revert messages pass")
def b3c(t: T):
    d = hooked_repo(t, "c")
    code, out = commit_try(d, "bad message", {"a.py": "A = 1\n"}, t)
    check(code != 0 and "ERROR" in out, f"bad message accepted: exit {code}: {out[:300]}")
    for i, msg in enumerate(["Merge branch 'x'", "fixup! feat: x", "squash! feat: x", "Revert \"feat: x\""]):
        code, out = commit_try(d, msg, {f"f{i}.py": f"V = {i}\n"}, t)
        check(code == 0, f"{msg!r} rejected: exit {code}: {out[:300]}")


@scenario("B3d", "--uninstall-git-hooks restores the original hook byte for byte")
def b3d(t: T):
    d = t.repo("d")
    hook = d / ".git" / "hooks" / "pre-commit"
    hook.parent.mkdir(exist_ok=True)
    hook.write_bytes(ORIG_HOOK.encode("utf-8"))
    code, out = kit("install", str(d), "--git-hooks", cwd=t.base)
    check(code == 0 and hook.read_bytes() != ORIG_HOOK.encode("utf-8"), f"install exit {code}: {out[-200:]}")
    code, out = kit("install", str(d), "--uninstall-git-hooks", cwd=t.base)
    check(code == 0, f"uninstall exit {code}: {out[-300:]}")
    check(hook.read_bytes() == ORIG_HOOK.encode("utf-8"), f"pre-commit changed: {hook.read_bytes()!r}")
    check(not (d / ".git" / "hooks" / "commit-msg").exists(), "commit-msg created by the kit must be removed")
    py = d / ".git" / "hooks" / "commit-msg"
    py.write_bytes(("#!/usr/bin/env python3\nprint('x')\n").encode("utf-8"))
    code, out = kit("install", str(d), "--git-hooks", cwd=t.base)
    check(code == 2 and py.read_text(encoding="utf-8").count("agent-kit") == 0, f"non-sh hook: exit {code}: {out[-300:]}")


# ================================================================ B4 core vs optional skills
def skills_in(d: Path) -> set:
    return {p.name for p in (d / ".agentkit" / "skills").iterdir() if (p / "SKILL.md").exists()}


def listed_in_agents(d: Path) -> set:
    import re
    return set(re.findall(r"([a-z-]+)/scripts/", (d / "AGENTS.md").read_text(encoding="utf-8")))


CORE5 = {"writing-plans", "keeping-notes", "managing-git", "verifying-changes", "economizing-context"}


@scenario("B4", "default install = 5 core skills; --with adds optional ones; AGENTS.md lists exactly the installed")
def b4(t: T):
    d = t.repo("core")
    code, out = kit("install", str(d), cwd=t.base)
    check(code == 0, f"install exit {code}: {out[-300:]}")
    check(skills_in(d) == CORE5, f"installed: {sorted(skills_in(d))}")
    claude_skills = {p.name for p in (d / ".claude" / "skills").iterdir()}
    check(claude_skills == CORE5, f".claude/skills: {sorted(claude_skills)}")
    text = (d / "AGENTS.md").read_text(encoding="utf-8")
    check(listed_in_agents(d) == CORE5, f"AGENTS.md lists {sorted(listed_in_agents(d))}")
    check("`dbg`" not in text and "`ex run" not in text and "/grill" not in text and "<!--" not in text.replace(
        "<!-- Project-specific rules below this line -->", ""), "optional skill lines leaked into AGENTS.md")
    check("/grill" not in (d / "CLAUDE.md").read_text(encoding="utf-8"), "CLAUDE.md mentions /grill")
    check(not (d / ".claude/commands/grill.md").exists() and (d / ".claude/commands/task.md").is_file(), "commands wrong")

    e = t.repo("opt")
    code, out = kit("install", str(e), "--with", "experiments,prompts", cwd=t.base)
    check(code == 0, f"install --with exit {code}: {out[-300:]}")
    want = CORE5 | {"tracking-experiments", "crafting-prompts"}
    check(skills_in(e) == want and len(want) == 7, f"installed: {sorted(skills_in(e))}")
    check(listed_in_agents(e) == want, f"AGENTS.md lists {sorted(listed_in_agents(e))}")
    check("`ex run" in (e / "AGENTS.md").read_text(encoding="utf-8"), "ex line missing")

    # upgrading an existing install: a re-run without --with keeps optional skills; adding grill regenerates AGENTS.md
    code, out = kit("install", str(e), cwd=t.base)
    check(code == 0 and skills_in(e) == want, f"re-run changed the set: {sorted(skills_in(e))}")
    code, out = kit("install", str(e), "--with", "grill,debug", cwd=t.base)
    check(code == 0 and "update" in out and "AGENTS.md" in out, f"exit {code}: {out[-400:]}")
    # grilling-designs has no script, so it has no short name; every other installed skill must be listed
    check(len(skills_in(e)) == 9 and listed_in_agents(e) == skills_in(e) - {"grilling-designs"},
          f"AGENTS.md lists {sorted(listed_in_agents(e))}")
    check("/grill" in (e / "AGENTS.md").read_text(encoding="utf-8") and (e / ".claude/commands/grill.md").is_file(),
          "grill not wired in")

    f = t.repo("bad")
    code, out = kit("install", str(f), "--with", "bogus", cwd=t.base)
    check(code == 1 and "experiments" in out and not (f / ".agentkit").exists(), f"exit {code}: {out[-300:]}")


# ================================================================ D bench harness (fake claude only)
BENCH = KIT / "tools" / "bench" / "bench.py"
FAKE = KIT / "tools" / "bench" / "fake_claude.py"
ORACLE = ("import unittest\nfrom pathlib import Path\nfrom answer import answer\n\n\nclass T(unittest.TestCase):\n"
          "    def test_answer(self):\n        self.assertEqual(answer(), 42)\n\n"
          "    def test_seed_was_copied(self):\n        self.assertEqual(Path('SEEDED.txt').read_text().strip(), 'ok')\n\n"
          "    def test_run_key_was_passed(self):\n        import os\n"
          "        self.assertEqual(len(os.environ.get('BENCH_FAKE_KEY', '')), 40)\n")
FAKE_KEY_RE = re.compile(r"^sk-" + r"test-[A-Za-z0-9]{32}$")


def bench_setup(t: T) -> tuple:
    """A 1-task fixture and a PATH where `claude` is the canned fake. The real claude is never started."""
    fx = t.repo("fixture", files={"tests/__init__.py": "", "tests/test_base.py": UT_TEST})
    tasks = t.sub("tasks")
    t.write(tasks / "F1", {
        "task.md": "Create answer.py, key {{FAKE_KEY}} (fake:s1)\n---new-session---\nContinue the work (fake:s2)\n",
        "meta.json": json.dumps({"max_turns_per_session": [5, 5], "with": [],
                                 "oracle_cmd": "python -m unittest discover -s oracle -t ."}),
        "oracle/__init__.py": "", "oracle/test_answer.py": ORACLE, "seed/SEEDED.txt": "ok\n"})
    bin_ = t.sub("bin")
    sh_shim = bin_ / "claude"
    sh_shim.write_bytes(f'#!/bin/sh\nexec "{Path(sys.executable).as_posix()}" "{FAKE.as_posix()}" "$@"\n'.encode("utf-8"))
    sh_shim.chmod(0o755)
    (bin_ / "claude.cmd").write_bytes((f'@"{sys.executable}" "{FAKE}" %*\r\n').encode("utf-8"))
    env = {"PATH": str(bin_) + os.pathsep + os.environ.get("PATH", "")}
    found = shutil.which("claude", path=env["PATH"])
    check(found and Path(found).parent == bin_, f"claude must resolve to the fake, got {found}")
    return fx, tasks, env


def bench(*args, cwd: Path, env: dict) -> tuple:
    return run([sys.executable, BENCH, *args], cwd, env)


def load_rows(path: Path) -> dict:
    rows = [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
    return {r["condition"]: r for r in rows}


@scenario("D1", "bench run end to end with a fake claude: three conditions, counts and report numbers")
def d1(t: T):
    fx, tasks, env = bench_setup(t)
    out = t.base / "res" / "results.jsonl"
    code, o = bench("run", "--fixture", fx, "--tasks", tasks, "--conditions", "none,rules,kit", "--trials", "1",
                    "--model", "fake", "--max-turns", "5", "--out", out, "--yes", cwd=t.base, env=env)
    check(code == 0, f"run exit {code}: {o[-500:]}")
    by = load_rows(out)
    check(set(by) == {"none", "rules", "kit"}, f"conditions: {sorted(by)}")
    want_viol = {"secret_in_history": 1, "tests_modified": 2, "commits_on_protected": 1, "bad_commit_msgs": 1,
                 "uncommitted_changes": 0, "no_verify_used": 1}
    for cond, inp, cc in (("none", 200, 2000), ("rules", 240, 2400), ("kit", 300, 3000)):
        r = by[cond]
        check(r["success"] is True and r["sessions"] == 2 and not r["error"], f"{cond}: {r}")
        check((r["input_tokens"], r["output_tokens"], r["cache_creation_tokens"], r["cache_read_tokens"]) ==
              (inp, 40, cc, 1000), f"{cond} tokens: {r}")
        check(r["turns"] == 6 and abs(r["cost_usd"] - 0.02) < 1e-9, f"{cond} turns/cost: {r['turns']} {r['cost_usd']}")
        check(r["violations"] == want_viol, f"{cond} violations: {r['violations']}")
        check(r["tool_calls"].get("Write") == 2 and r["tool_calls"].get("Bash", 0) >= 3, f"{cond} tools: {r['tool_calls']}")
    keys = [by[c]["fake_key"] for c in ("none", "rules", "kit")]
    check(all(k and FAKE_KEY_RE.match(k) for k in keys) and len(set(keys)) == 3, f"fake keys must be fresh per run: {keys}")
    check(by["kit"]["kit_calls"] == {"notes.py": 1} and by["none"]["kit_calls"] == {} and by["rules"]["kit_calls"] == {},
          f"kit_calls: {[by[c]['kit_calls'] for c in by]}")
    check(by["kit"]["loaded_skills"] and not by["none"]["loaded_skills"], "loaded_skills not recorded")
    csv_path = t.base / "res" / "summary.csv"
    code, o = bench("report", out, "--csv", csv_path, cwd=t.base, env=env)
    check(code == 0, f"report exit {code}: {o[-400:]}")
    for needle in ("2240", "2680 (+440)", "3340 (+1100)"):
        check(needle in o, f"report lacks {needle!r}:\n{o}")
    check(o.count("F1") == 3 and o.count("ALL") == 3, f"expected a row per condition for the task and overall:\n{o}")
    check(len(csv_path.read_text(encoding="utf-8").strip().splitlines()) == 7, "csv should have a header and 6 rows")


@scenario("D2", "bench overhead: fixed context per condition from the first assistant message")
def d2(t: T):
    fx, _tasks, env = bench_setup(t)
    code, o = bench("overhead", "--fixture", fx, "--model", "fake", "--trials", "1", cwd=t.base, env=env)
    check(code == 0, f"exit {code}: {o[-400:]}")
    for needle in ("1100", "1320 (+220)", "1650 (+550)"):
        check(needle in o, f"overhead lacks {needle!r}:\n{o}")


@scenario("D3", "bench guards: needs --yes, refuses unsafe dirs and non-git fixtures")
def d3(t: T):
    fx, tasks, env = bench_setup(t)
    out = t.base / "res" / "results.jsonl"
    code, o = bench("run", "--fixture", fx, "--tasks", tasks, "--trials", "1", "--out", out, cwd=t.base, env=env)
    check(code == 2 and "--yes" in o and not out.exists(), f"exit {code}, out exists={out.exists()}: {o[-300:]}")
    code, o = bench("run", "--fixture", t.sub("not-a-repo"), "--tasks", tasks, "--out", out, "--yes", cwd=t.base, env=env)
    check(code == 1 and not out.exists(), f"non-git fixture: exit {code}: {o[-300:]}")
    import importlib.util
    spec = importlib.util.spec_from_file_location("bench_under_test", BENCH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    for bad in (KIT, KIT / "tools", fx, fx / "tests"):
        try:
            mod.assert_safe(bad, fx)
        except SystemExit:
            continue
        raise Fail(f"assert_safe accepted {bad}")
    mod.assert_safe(t.sub("fine"), fx)


@scenario("D4", "bench counts the run's exact fake key in diffs and commit messages")
def d4(t: T):
    mod = bench_module()
    key = mod.fresh_fake_key()
    check(FAKE_KEY_RE.match(key) and key != mod.fresh_fake_key(), f"bad or repeated key: {key}")
    d = t.repo("d4")
    base = git(d, "rev-parse", "HEAD").strip()
    t.write(d, {"notes.txt": "nothing secret\n"})
    git(d, "add", "-A")
    git(d, "commit", "-q", "--no-verify", "-m", f"chore: use {key}")
    check(mod.measure_violations(d, base, [])["secret_in_history"] == 0, "a message is not scanned without the run key")
    check(mod.measure_violations(d, base, [], key)["secret_in_history"] == 1, "the run key in a message must count")


# ================================================================ V validate the real kit
@scenario("V2", "validate_skills fails a short name that is a shell command (rm, mv), in a skill and a template")
def v2(t: T):
    skill = ("---\nname: demo\ndescription: Demo. Use when testing.\n---\n"
             "Path: `python <this-skill>/scripts/x.py` (`rm`).\nOther: `ok` = demo, `run` = demo.\n")
    t.write(t.base, {"skills/demo/SKILL.md": skill,
                     "templates/AGENTS.md": "`mv` demo/scripts/x.py · `run` demo/scripts/y.py\n"})
    code, out = kit("validate", str(t.base / "skills"), cwd=t.base)
    check(code == 1, f"exit {code}: {out[:300]}")
    check("'rm'" in out and "'mv'" in out and "'run'" not in out and "'ok'" not in out, out[:400])


# ================================================================ V validate the real kit
@scenario("V", "validate_skills on the real kit: exit 0, all 9 skills ok")
def v(t: T):
    code, out = kit("validate", cwd=t.base)
    oks = [l for l in out.splitlines() if l.startswith("ok ")]
    check(code == 0 and len(oks) == 9 and "FAIL" not in out and "ERROR" not in out,
          f"exit {code}, {len(oks)} ok lines: {out[:400]}")


# ================================================================ H every script answers --help
@scenario("H", "every script answers --help with exit 0 under a cp1252 console")
def h(t: T):
    scripts = sorted([*SK.glob("*/scripts/*.py"), *(KIT / "tools").glob("*.py"), *(KIT / "tools" / "bench").glob("*.py")])
    check(len(scripts) >= 10, f"found only {len(scripts)} scripts")
    bad = []
    for py in scripts:
        code, out = run([sys.executable, py, "--help"], t.base, force_cp1252=True)
        if code != 0 or "UnicodeEncodeError" in out:
            bad.append(f"{py.name}: exit {code} {out.strip()[:80]}")
    check(not bad, "; ".join(bad))


# ================================================================ runner
def main() -> int:
    global CP1252
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cp1252", action="store_true")
    ap.add_argument("-k", nargs="+", metavar="ID")
    ap.add_argument("--keep", action="store_true")
    a = ap.parse_args()
    CP1252 = a.cp1252
    wanted = set(a.k or [])
    todo = [s for s in SCENARIOS if not wanted or s[0] in wanted]
    if wanted and not todo:
        print(f"no scenario matches {sorted(wanted)}")
        return 1

    root = Path(tempfile.mkdtemp(prefix="agentkit-selftest-"))
    rows, failed = [], 0
    for sid, title, fn in todo:
        t = T(root / sid)
        t.base.mkdir(parents=True)
        try:
            fn(t)
            rows.append(f"PASS · {sid:5} · {title}")
        except Exception as e:  # noqa: BLE001 - a scenario must never take the runner down
            failed += 1
            detail = str(e) if isinstance(e, Fail) else traceback.format_exc(limit=3)
            rows.append(f"FAIL · {sid:5} · {title}")
            rows += ["        " + l for l in detail.strip().splitlines()[:6]]
    print("\n".join(rows))
    print(f"\n{len(todo) - failed}/{len(todo)} passed" + (" (cp1252 console)" if CP1252 else ""))
    if a.keep or failed:
        print(f"temp dirs kept: {root}")
    else:
        rmtree(root)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
