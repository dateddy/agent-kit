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
    "rm": SK / "economizing-context/scripts/repomap.py",
    "run": SK / "economizing-context/scripts/run.py",
    "dbg": SK / "debugging-systematically/scripts/dbg.py",
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
            f.write_text(text, encoding="utf-8", newline="\n")

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


def staged(t: T, files: dict, name: str) -> tuple[int, str]:
    d = t.repo(name)
    t.write(d, files)
    git(d, "add", "-A")
    return kit("gk", "suggest", "--staged", cwd=d)


@scenario("A4a", "gitkit suggest: source + its test is one concern")
def a4a(t: T):
    code, out = staged(t, {"faq/data.py": "x = 1\n", "tests/test_data.py": UT_TEST}, "a")
    check(code == 0, f"exit {code}: {out[:300]}")


@scenario("A4b", "gitkit suggest: source + test + README + requirements warns")
def a4b(t: T):
    code, out = staged(t, {"faq/data.py": "x = 1\n", "tests/test_data.py": UT_TEST,
                           "README.md": "# demo\nmore\n", "requirements.txt": "requests\n"}, "b")
    check(code == 2 and "WARN" in out, f"exit {code}: {out[:300]}")


@scenario("A4c", "gitkit suggest: notes/decisions.md is ignored for categorising")
def a4c(t: T):
    code, out = staged(t, {"notes/decisions.md": "# Decisions\n", "faq/data.py": "x = 1\n"}, "c")
    check(code == 0, f"exit {code}: {out[:300]}")


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


# ================================================================ B1 zone
def zone_json(d: Path, *args) -> tuple[int, dict, str]:
    code, out = kit("rm", *args, "--json", cwd=d)
    try:
        return code, json.loads(out), out
    except json.JSONDecodeError:
        return code, {}, out


@scenario("B1a", "zone: a->b->c chain, hops 2 lists b(1), a(2) as importers + their tests")
def b1a(t: T):
    d = t.repo(files={"a.py": "import b\n", "b.py": "import c\n", "c.py": "X = 1\n",
                      "tests/test_a.py": "import a\n", "tests/test_c.py": "import c\n"})
    code, j, out = zone_json(d, "--zone", "c.py", "--hops", "2")
    check(code == 0 and j, f"exit {code}: {out[:300]}")
    files = {f["path"]: (f["distance"], f["direction"]) for f in j["files"]}
    check(files.get("b.py") == (1, "importers") and files.get("a.py") == (2, "importers"), f"files: {files}")
    check({"tests/test_a.py", "tests/test_c.py"} <= set(j["tests"]), f"tests: {j['tests']}")
    code, out = kit("rm", "--zone", "c", "--hops", "1", cwd=d)
    check(code == 0 and "b.py" in out and "a.py" not in out.split("TESTS")[0].replace("SEEDS", ""),
          f"text output (dotted seed, hops 1): {out[:300]}")


@scenario("B1b", "zone: relative imports inside packages under src/ are resolved")
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


@scenario("B1c", "zone-from-log: seeds are repo frames only")
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


@scenario("B1d", "zone --json has the documented keys and reports dynamic imports")
def b1d(t: T):
    d = t.repo(files={"y.py": "V = 1\n",
                      "x.py": "import importlib\nimport y\n\n\ndef f(n):\n    return importlib.import_module(n)\n"})
    code, j, out = zone_json(d, "--zone", "y.py")
    check(code == 0 and set(j) == {"seeds", "files", "tests", "notes"}, f"keys: {sorted(j)} / {out[:200]}")
    check(any("dynamic import in x.py:6" in n for n in j["notes"]), f"notes: {j['notes']}")
    code, out = kit("rm", "--zone", "y.py", cwd=d)
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


@scenario("B2a", "vf --affected runs only the tests in the changed file's zone")
def b2a(t: T):
    d = affected_repo(t, "r")
    code, out = kit("vf", cwd=d)
    check(code == 1 and "VERDICT: FAIL (exit 1)" in out, f"full run should FAIL on test_d: exit {code}: {out[:300]}")
    code, out = kit("vf", "--affected", cwd=d)
    check(code == 0 and "VERDICT: PASS (exit 0) (affected only)" in out, f"exit {code}: {out[:400]}")


@scenario("B2b", "vf --affected without repomap falls back to the full test gate and says why")
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


def decided_repo(t: T, supersede: bool = False) -> Path:
    d = t.repo()
    kit("nt", "init", cwd=d)
    code, out = kit("nt", "decide", "--topic", "storage", "--text", "Use JSON files", "--why", "small data", cwd=d)
    check(code == 0 and "D-001" in out, f"decide: {out[:200]}")
    if supersede:
        code, out = kit("nt", "decide", "--topic", "storage", "--text", "Use SQLite", "--why", "grew",
                        "--supersedes", "D-001", cwd=d)
        check(code == 0 and "D-002" in out, f"supersede: {out[:200]}")
    return d


@scenario("B3a", "plan check: an existing Decisions id passes")
def b3a(t: T):
    d = decided_repo(t)
    t.write(d, {"plan.md": plan_with("D-001")})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 0, f"exit {code}: {out[:300]}")


@scenario("B3b", "plan check: a missing Decisions id BLOCKs")
def b3b(t: T):
    d = decided_repo(t)
    t.write(d, {"plan.md": plan_with("D-001, D-099")})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 1 and "D-099" in out, f"exit {code}: {out[:300]}")


@scenario("B3c", "plan check: a superseded Decisions id WARNs and names the replacement")
def b3c(t: T):
    d = decided_repo(t, supersede=True)
    t.write(d, {"plan.md": plan_with("D-001")})
    code, out = kit("pl", "check", "plan.md", cwd=d)
    check(code == 2 and "D-002" in out, f"exit {code}: {out[:300]}")


@scenario("B3d", "install copies the grilling-designs skill and /grill command")
def b3d(t: T):
    d = t.repo()
    code, out = kit("install", str(d), cwd=t.base)
    check(code == 0, f"install exit {code}: {out[:300]}")
    check((d / ".agentkit/skills/grilling-designs/SKILL.md").is_file(), "skill not installed")
    check((d / ".claude/commands/grill.md").is_file() and (d / ".claude/commands/task.md").is_file(),
          "commands not installed")
    text = (d / ".agentkit/skills/grilling-designs/SKILL.md").read_text(encoding="utf-8")
    check("disable-model-invocation: true" in text, "frontmatter lost")


# ================================================================ B4 dbg
@scenario("B4a", "dbg open/hypo/close round trip; list shows the closed bug with its cause")
def b4a(t: T):
    d = t.repo()
    sha = git(d, "rev-parse", "HEAD").strip()
    code, out = kit("dbg", "open", "--layer", "code", "--symptom", "KeyError answer parsing model output",
                    "--repro", "python app.py", cwd=d)
    check(code == 0 and "B-001" in out, f"open: exit {code}: {out[:200]}")
    code, out = kit("dbg", "hypo", "B-001", "dict is missing the key", "--result", "rejected", cwd=d)
    check(code == 0, f"hypo: exit {code}: {out[:200]}")
    code, out = kit("dbg", "close", "B-001", "--cause", f"loader skipped {VI}", "--fix", sha, cwd=d, force_cp1252=True)
    check(code == 0, f"close: exit {code}: {out[:200]}")
    code, out = kit("dbg", "list", cwd=d, force_cp1252=True)
    check(code == 0 and "B-001" in out and "closed" in out and f"loader skipped {VI}" in out, f"list: {out[:300]}")
    code, out = kit("dbg", "list", "--open", cwd=d)
    check("no bugs open" in out, f"list --open: {out[:200]}")


@scenario("B4b", "dbg close without --cause BLOCKs")
def b4b(t: T):
    d = t.repo()
    kit("dbg", "open", "--layer", "data", "--symptom", "row count drops after join", cwd=d)
    code, out = kit("dbg", "close", "B-001", cwd=d)
    check(code == 1 and "--cause" in out, f"exit {code}: {out[:200]}")
    code, out = kit("dbg", "list", "--open", cwd=d)
    check("B-001" in out, "bug must stay open")


@scenario("B4c", "a second open with a similar symptom WARNs and names the earlier bug")
def b4c(t: T):
    d = t.repo()
    kit("dbg", "open", "--layer", "llm", "--symptom", "KeyError answer when parsing model output json", cwd=d)
    code, out = kit("dbg", "open", "--layer", "llm", "--symptom", "KeyError answer parsing model output", cwd=d)
    check(code == 2 and "B-001" in out and "B-002" in out, f"exit {code}: {out[:300]}")
    code, out = kit("dbg", "open", "--layer", "data", "--symptom", "unrelated disk quota exceeded", cwd=d)
    check(code == 0, f"unrelated bug should not warn: exit {code}: {out[:200]}")


@scenario("B4d", "dbg llm-case appends a valid case; promptkit check still passes; bad vars BLOCK")
def b4d(t: T):
    spec = {"name": "qa", "target": "generic", "goal": "Answer the question from the documents.",
            "inputs": [{"name": "question", "description": "user question"}],
            "instructions": ["Answer only from the documents."],
            "output": {"format": "text", "description": "A short answer."}, "cases": []}
    d = t.repo(files={"prompts/qa.spec.json": json.dumps(spec, indent=2) + "\n"})
    kit("dbg", "open", "--layer", "llm", "--symptom", "model invents a price", cwd=d)
    code, out = kit("dbg", "llm-case", "B-001", "--spec", "prompts/qa.spec.json", "--vars", '{"nope": "x"}',
                    "--check", '{"type":"not_contains","value":"$"}', cwd=d)
    check(code == 1, f"mismatched vars should BLOCK: exit {code}: {out[:200]}")
    code, out = kit("dbg", "llm-case", "B-001", "--spec", "prompts/qa.spec.json",
                    "--vars", '{"question": "How much is it?"}', "--check", '{"type":"not_contains","value":"$"}', cwd=d)
    check(code == 0, f"llm-case: exit {code}: {out[:200]}")
    got = json.loads((d / "prompts/qa.spec.json").read_text(encoding="utf-8"))
    check(got["cases"] == [{"vars": {"question": "How much is it?"},
                            "checks": [{"type": "not_contains", "value": "$"}], "source": "B-001"}], f"cases: {got['cases']}")
    code, out = kit("pk", "check", "prompts/qa.spec.json", cwd=d)
    check(code == 0, f"promptkit check exit {code}: {out[:300]}")


# ================================================================ B5 SessionStart hook
HOOK = "python .agentkit/skills/keeping-notes/scripts/notes.py brief"


@scenario("B5a", "install --hooks twice: hook present once, existing settings preserved")
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


@scenario("B5b", "install without --hooks writes no settings.json; invalid settings.json is left untouched (exit 2)")
def b5b(t: T):
    d = t.repo(name="plain")
    code, out = kit("install", str(d), cwd=t.base)
    check(code == 0 and not (d / ".claude/settings.json").exists(), f"exit {code}; settings.json must not exist")
    bad = t.repo(name="bad", files={".claude/settings.json": "{ not json\n"})
    code, out = kit("install", str(bad), "--hooks", cwd=t.base)
    check(code == 2 and "WARN" in out, f"exit {code}: {out[-300:]}")
    check((bad / ".claude/settings.json").read_text(encoding="utf-8") == "{ not json\n", "invalid file was modified")


# ================================================================ V validate the real kit
@scenario("V", "validate_skills on the real kit: exit 0, all 10 skills ok")
def v(t: T):
    code, out = kit("validate", cwd=t.base)
    oks = [l for l in out.splitlines() if l.startswith("ok ")]
    check(code == 0 and len(oks) == 10 and "FAIL" not in out and "ERROR" not in out,
          f"exit {code}, {len(oks)} ok lines: {out[:400]}")


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
            rows.append(f"PASS · {sid:4} · {title}")
        except Exception as e:  # noqa: BLE001 - a scenario must never take the runner down
            failed += 1
            detail = str(e) if isinstance(e, Fail) else traceback.format_exc(limit=3)
            rows.append(f"FAIL · {sid:4} · {title}")
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
