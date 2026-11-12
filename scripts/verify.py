#!/usr/bin/env python3
"""verify - the single "is this done?" gate: format, lint, typecheck, tests, integrity, diff-cover, plan scope.

  verify.py                 full run (plain CLI): every stage, exit 0 PASS / 1 FAIL / 2 PASS WITH TEST CHANGES
  verify.py --push          git pre-push: same exit codes; stages from verify.push_stages (default: all)
  verify.py --hook          Claude Code Stop hook; stages from verify.hook_stages (default: lint tests integrity scope)
  verify.py --edit [FILE]   PostToolUse hook: ruff check --fix + ruff format on that one file (FILE, or JSON on stdin)
  verify.py --stages a,b    only these stages (alias --only; names: format lint typecheck tests integrity diff-cover scope)
  verify.py --affected      tests: only those in the blast radius of the changed files (skips diff-cover)
  verify.py --detect        print the command each stage would run, and why

Exit codes. CLI and --push: 0 pass, 1 fail, 2 pass but test files were deleted, skipped or weakened (ask the user).
--hook: 0 pass, 2 fail (exit 2 stops Claude from finishing and feeds stderr back; exit 1 would not block). It blocks
only on what the agent can fix alone; a plan saved but not applied (no task note) is a SKIP. If the hook JSON on stdin
has "stop_hook_active": true (Claude is already continuing because of a Stop hook) it never exits 2: it prints the
summary and exits 0, so a stop is blocked at most once per chain. A tree with nothing changed against the base branch
exits 0 at once. --edit: 0, or 2 when ruff left problems or is missing. All output goes to stderr; the VERDICT line
is the result. Full logs: .agentkit/logs/verify-<stage>.log.

Config (.agentkit.json, block `verify`): <stage>: "auto" | "<command>" | false, plus ruff_cmd, pytest_cmd, pytest_args,
diff_cover_cmd, fail_under, coverage_xml, scope_ignore, hook_stages, push_stages. The 0.5.0 keys (guards.ruff, guards.pytest,
guards.*_cmd, guards.hook_stages, verify.test, ...) still work and print a deprecation WARN. A missing tool fails its
stage; it never passes silently. The scope stage is skipped when the branch has no plan (lite path).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import akcore as ak  # noqa: E402

HERE = Path(__file__).resolve().parent
ALL_STAGES = ("format", "lint", "typecheck", "tests", "integrity", "diff-cover", "scope")
HOOK_DEFAULT = ("lint", "tests", "integrity", "scope")
QUIET = {"format", "typecheck", "integrity"}  # a SKIP row of these is not printed (the 0.5.0 summary had four rows)
KEY = {"diff-cover": "diff_cover"}
ALIAS = {"test": "tests", "diff_cover": "diff-cover"}
HEAD_FIRST = {"format", "lint", "typecheck"}  # the useful part of these tools' output comes first
MAX_LINES = 15
STOP_RE = re.compile(r'"stop_hook_active"\s*:\s*true')
TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|spec)/|(^|/)test_[^/]*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$")
SKIP_RE = re.compile(r"(pytest\.mark\.(skip|xfail)|@unittest\.skip|\b(it|test|describe)\.(skip|todo)\(|"
                     r"\bxit\(|\bxdescribe\(|t\.Skip\(|#\[ignore\]|pytest\.skip\()")


class Abort(Exception):
    def __init__(self, rc: int) -> None:
        super().__init__(rc)
        self.rc = rc


def say(*lines: str) -> None:
    for line in lines:
        print(line, file=sys.stderr)


class Res:
    """Outcome of one stage: PASS, FAIL, WARN (test changes), SKIP."""

    def __init__(self, status: str, note: str = "", detail: str = "") -> None:
        self.status, self.note, self.detail = status, note, detail


class Ctx:
    def __init__(self, root: Path, cfg: ak.Cfg, mode: str, args: argparse.Namespace) -> None:
        self.root, self.cfg, self.mode, self.args = root, cfg, mode, args
        self.branch = ak.current_branch(root)
        self.base = cfg.base_ref()
        self.xml = str(cfg.verify("coverage_xml", ".agentkit/coverage.xml"))
        self.ignores = ak.default_ignores(cfg, self.xml)
        self.changed: list[str] = []
        self.tests_rc = 0
        self.selected: list[str] = []
        self.narrow = mode == "hook" or args.affected
        self._detected: dict | None = None

    @property
    def detected(self) -> dict:
        if self._detected is None:
            self._detected = detect(self.root)
        return self._detected

    def tool(self, key: str, default: str) -> list[str]:
        return str(self.cfg.verify(key, default)).split() or [default]

    def py_targets(self) -> list[str] | None:
        """Paths handed to ruff: '.' for a full run, the changed python files for the Stop hook (None = nothing to check)."""
        if self.mode != "hook":
            return ["."]
        files = [f for f in self.changed if f.endswith(".py") and (self.root / f).is_file()]
        return files or None


# ---------------------------------------------------------------- stage plumbing
def read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def disabled(ctx: Ctx, stage: str) -> str | None:
    if stage in ("format", "lint", "typecheck", "tests") and ctx.cfg.is_null(stage):
        return f"disabled: {ctx.cfg.switch_name(stage)}=null"
    raw = ctx.cfg.verify(KEY.get(stage, stage), "auto")
    if raw is False or raw == "" or (not isinstance(raw, (dict, list)) and ak.is_off(raw)):
        return f"disabled: {ctx.cfg.switch_name(stage)}={'false' if raw != '' else repr('')}"
    return None


def custom_cmd(ctx: Ctx, stage: str) -> str | None:
    """A command line the user wrote for format / lint / typecheck / tests (verify.<stage>: "<command>")."""
    if stage not in ("format", "lint", "typecheck", "tests"):
        return None
    raw = ctx.cfg.verify(stage, "auto")
    return raw.strip() if isinstance(raw, str) and raw.strip() and raw.strip().lower() != "auto" else None


def missing_tool(ctx: Ctx, tool: str, stage: str) -> Res | None:
    if ak.resolve_exe(tool) or ak.has_tool(tool):
        return None
    return Res("FAIL", detail=f"{tool} not found: install it, or set {ctx.cfg.switch_name(stage)} to false in "
                              ".agentkit.json to skip this stage")


def write_log(ctx: Ctx, stage: str, text: str) -> None:
    d = ctx.root / ".agentkit" / "logs"
    try:
        d.mkdir(parents=True, exist_ok=True)
        gi = d / ".gitignore"
        if not gi.exists():
            gi.write_text("*\n", encoding="utf-8")
        (d / f"verify-{stage}.log").write_text(text, encoding="utf-8")
    except OSError:
        pass


def finish(ctx: Ctx, stage: str, rc: int, out: str) -> Res:
    write_log(ctx, stage, out)
    return Res("PASS" if rc == 0 else "FAIL", detail=out)


def run_argv_stage(ctx: Ctx, stage: str, argv: list[str]) -> Res:
    rc, out = ak.run_argv(argv, ctx.root)
    return finish(ctx, stage, rc, out)


def run_shell_stage(ctx: Ctx, stage: str, cmd: str) -> Res:
    rc, out = ak.run_shell(cmd, ctx.root)
    return finish(ctx, stage, rc, out)


# ---------------------------------------------------------------- detection (tools other than ruff and pytest)
def detect(root: Path) -> dict[str, tuple[str, str]]:
    """stage -> (shell command, why) for projects that do not use ruff / pytest. A stage not listed runs ruff / pytest."""
    found: dict[str, tuple[str, str]] = {}
    pyproject, setup_cfg = read(root / "pyproject.toml"), read(root / "setup.cfg")
    reqs = (" ".join(read(p) for p in root.glob("requirements*.txt")) + pyproject).lower()
    python_proj = bool(pyproject or setup_cfg or list(root.glob("requirements*.txt")) or (root / "setup.py").exists()
                       or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists())
    ruff_cfg = "[tool.ruff" in pyproject or (root / "ruff.toml").exists() or (root / ".ruff.toml").exists() or "ruff" in reqs
    if python_proj:
        if not ruff_cfg and ((root / ".flake8").exists() or "[flake8]" in setup_cfg):
            found["lint"] = ("flake8", "flake8 configured")
        if not ruff_cfg and "[tool.black" in pyproject:
            found["format"] = ("black --check .", "black configured")
        if "[tool.mypy" in pyproject or (root / "mypy.ini").exists() or "[mypy" in setup_cfg:
            found["typecheck"] = ("mypy .", "mypy configured")
        elif (root / "pyrightconfig.json").exists() or "[tool.pyright" in pyproject:
            found["typecheck"] = ("pyright", "pyright configured")
    pkg = root / "package.json"
    if pkg.exists():
        try:
            scripts = json.loads(read(pkg)).get("scripts", {})
        except (ValueError, AttributeError):
            scripts = {}
        pm = "pnpm" if (root / "pnpm-lock.yaml").exists() else "yarn" if (root / "yarn.lock").exists() \
            else "bun" if (root / "bun.lockb").exists() else "npm"
        for stage, names in (("format", ["format:check", "fmt:check"]), ("lint", ["lint"]),
                             ("typecheck", ["typecheck", "type-check", "tsc"]), ("tests", ["test"])):
            name = next((n for n in names if n in scripts), None)
            if name and "no test specified" not in scripts[name] and stage not in found and (stage == "typecheck" or not python_proj):
                found[stage] = (f"{pm} run {name}", f"package.json script '{name}'")
        if "typecheck" not in found and (root / "tsconfig.json").exists():
            found["typecheck"] = ("npx tsc --noEmit", "tsconfig.json present")
    if not python_proj:
        if (root / "go.mod").exists():
            found.setdefault("lint", ("go vet ./...", "go.mod"))
            found.setdefault("tests", ("go test ./...", "go.mod"))
        if (root / "Cargo.toml").exists():
            found.setdefault("lint", ("cargo clippy -q -- -D warnings", "Cargo.toml"))
            found.setdefault("tests", ("cargo test -q", "Cargo.toml"))
    mk = read(root / "Makefile")
    for stage, target in (("typecheck", "typecheck"), ("lint", "lint"), ("tests", "test"), ("format", "format")):
        if stage not in found and re.search(rf"^{target}:", mk, re.M) and (stage == "typecheck" or not python_proj):
            found[stage] = (f"make {target}", "Makefile target")
    return found


# ---------------------------------------------------------------- affected tests
def helper(name: str, *rel: str) -> Path | None:
    for p in (HERE / name, HERE.joinpath("..", *rel).resolve()):
        if p.is_file():
            return p
    return None


def affected_tests(ctx: Ctx) -> tuple[list[str] | None, str]:
    """Test files in the blast radius of the changed files, or (None, why) when unknown."""
    rm = helper("repomap.py", "skills", "economizing-context", "scripts", "repomap.py")
    if rm is None:
        return None, "repomap.py not installed"
    try:
        rc, out = ak.run_argv([sys.executable, str(rm), "--zone-from-diff", "--json"], ctx.root)
    except OSError as e:
        return None, str(e)
    if rc != 0:
        first = next(iter(out.strip().splitlines()), "no output")
        return None, f"repomap exited {rc}: {first}"[:200]
    try:
        return list(json.loads(out)["tests"]), ""
    except (ValueError, KeyError):
        return None, "repomap returned invalid JSON"


def narrow_test_cmd(root: Path, cmd: str, tests: list[str]) -> str | None:
    """The test command restricted to `tests`, or None if that runner is not supported."""
    if "-m pytest" in cmd or cmd.split()[:1] == ["pytest"]:
        return cmd + " " + " ".join(f'"{x}"' for x in tests)
    if "-m unittest" in cmd:
        py = cmd.split(" -m unittest")[0]
        files = [x for x in tests if x.endswith(".py")]
        if all((root / Path(x).parent / "__init__.py").exists() for x in files):
            return f"{py} -m unittest " + " ".join(x[:-3].replace("/", ".") for x in files)
        dirs = ", ".join(repr(d) for d in dict.fromkeys(Path(x).parent.as_posix() for x in files))
        argv = ", ".join(repr(s) for s in ["unittest", *(Path(x).stem for x in files)])
        return f'{py} -c "import sys, unittest; sys.path[:0] = [{dirs}]; unittest.main(module=None, argv=[{argv}])"'
    return None


# ---------------------------------------------------------------- stages
def ruff_argv(ctx: Ctx, stage: str, targets: list[str]) -> list[str]:
    force = ["--force-exclude"] if targets != ["."] else []
    if stage == "lint":
        return [*ctx.tool("ruff_cmd", "ruff"), "check", "--quiet", "--output-format", "concise", *force, *ak.RUFF_EXCLUDE, *targets]
    return [*ctx.tool("ruff_cmd", "ruff"), "format", "--check", *force, *ak.RUFF_EXCLUDE, *targets]


def format_with_lint(ctx: Ctx) -> bool:
    """On the ruff path `lint` also runs `ruff format --check`, as in 0.5.0; a user or detected formatter runs as `format`."""
    return disabled(ctx, "format") is None and not custom_cmd(ctx, "format") and "format" not in ctx.detected


def stage_ruff(ctx: Ctx, stage: str) -> Res:
    """lint = ruff check (+ ruff format --check); format = a formatter other than ruff, or ruff format --check alone."""
    cmd = custom_cmd(ctx, stage) or (ctx.detected.get(stage) or ("",))[0]
    if cmd:
        return run_shell_stage(ctx, stage, cmd)
    if stage == "format" and "lint" in ctx.selected and disabled(ctx, "lint") is None and not custom_cmd(ctx, "lint"):
        return Res("SKIP", "checked with lint")
    targets = ctx.py_targets()
    if targets is None:
        return Res("SKIP", "no changed python files")
    argv = ruff_argv(ctx, stage, targets)
    miss = missing_tool(ctx, argv[0].split()[0], stage)
    if miss:
        return miss
    rc, out = ak.run_argv(argv, ctx.root)
    if stage == "lint" and format_with_lint(ctx):
        rc2, out2 = ak.run_argv(ruff_argv(ctx, "format", targets), ctx.root)
        rc, out = rc or rc2, out + out2
    return finish(ctx, stage, rc, out)


def stage_typecheck(ctx: Ctx) -> Res:
    cmd = custom_cmd(ctx, "typecheck") or (ctx.detected.get("typecheck") or ("",))[0]
    if not cmd:
        return Res("SKIP", "none configured")
    return run_shell_stage(ctx, "typecheck", cmd)


def stage_tests(ctx: Ctx) -> Res:
    cmd = custom_cmd(ctx, "tests") or (ctx.detected.get("tests") or ("",))[0]
    tests: list[str] = []
    if ctx.narrow:
        found, why = affected_tests(ctx)
        if found is None:
            if ctx.mode == "hook":
                return Res("SKIP", f"affected tests unavailable: {why}")
            ctx.args.notes.append(f"--- note: affected: {why} - ran the full test stage")
            ctx.narrow = False
        elif not found:
            return Res("SKIP", "no affected tests")
        else:
            tests = found
    if cmd:
        if ctx.narrow:
            cmd = narrow_test_cmd(ctx.root, cmd, tests)
            if cmd is None:
                return Res("SKIP", "the test command cannot be narrowed to affected tests")
        res = run_shell_stage(ctx, "tests", cmd)
    else:
        pytest = ctx.tool("pytest_cmd", "pytest")
        extra = str(ctx.cfg.verify("pytest_args", "") or "").split()
        miss = missing_tool(ctx, pytest[0], "tests")
        if miss:
            ctx.tests_rc = 1
            return miss
        if ctx.narrow:
            argv = [*pytest, *extra, *tests]
        else:
            xml = ctx.root / ctx.xml
            xml.parent.mkdir(parents=True, exist_ok=True)
            xml.unlink(missing_ok=True)
            argv = [*pytest, "--cov", f"--cov-report=xml:{ctx.xml}", *extra]
        res = run_argv_stage(ctx, "tests", argv)
        if "unrecognized arguments: --cov" in res.detail:
            res.detail += "\npytest-cov is not installed: pip install pytest-cov, or set verify.tests to false"
    if not ctx.narrow:
        ctx.tests_rc = 0 if res.status == "PASS" else 1
    return res


def tamper_check(ctx: Ctx) -> list[str]:
    """Deleted test files, added skip/xfail markers and removed assertions vs HEAD (staged and unstaged)."""
    notes: list[str] = []
    for line in ak.git_lines(["diff", "HEAD", "--name-status"], ctx.root):
        parts = line.split("\t")
        if len(parts) >= 2 and TEST_PATH.search(parts[-1]) and parts[0].startswith("D"):
            notes.append(f"test file DELETED: {parts[-1]}")
    cur = None
    for line in ak.git_lines(["diff", "HEAD", "-U0"], ctx.root):
        if line.startswith("+++ b/"):
            cur = line[6:]
        elif line.startswith("+") and not line.startswith("+++") and SKIP_RE.search(line):
            notes.append(f"skip/xfail ADDED in {cur}: {line[1:].strip()[:90]}")
        elif line.startswith("-") and not line.startswith("---") and cur and TEST_PATH.search(cur) \
                and re.search(r"\bassert\b|expect\(|assertEqual|assert_eq!", line):
            notes.append(f"assertion REMOVED in {cur}: {line[1:].strip()[:90]}")
    return list(dict.fromkeys(notes))


def stage_integrity(ctx: Ctx) -> Res:
    if ctx.args.no_guard:
        return Res("SKIP", "--no-guard")
    notes = tamper_check(ctx)
    if not notes:
        return Res("PASS")
    return Res("WARN", f"{len(notes)} finding(s)", "\n".join("  " + n for n in notes))


def stage_diffcover(ctx: Ctx) -> Res:
    if ctx.args.affected:
        return Res("SKIP", "affected run has no coverage report")
    if disabled(ctx, "tests") is None and ctx.tests_rc != 0:
        return Res("SKIP", "needs passing tests")
    dc = ctx.tool("diff_cover_cmd", "diff-cover")
    under = str(ctx.cfg.verify("fail_under", 90))
    miss = missing_tool(ctx, dc[0], "diff-cover")
    if miss:
        return miss
    if not ctx.base:
        return Res("FAIL", detail="no base branch found: set notes.base_branch in .agentkit.json")
    if not (ctx.root / ctx.xml).is_file():
        return Res("FAIL", detail=f"{ctx.xml} is missing: diff-cover needs the coverage report of the tests stage "
                                  f"({ctx.cfg.switch_name('tests')})")
    return run_argv_stage(ctx, "diff-cover", [*dc, ctx.xml, f"--compare-branch={ctx.base}", f"--fail-under={under}"])


def branch_decisions(ctx: Ctx) -> str:
    f = ctx.cfg.notes_dir() / "decisions.md"
    if not f.is_file() or not ctx.branch:
        return ""
    keep, cur = [], False
    for line in read(f).replace("\r", "").split("\n"):
        if line.startswith("## D-"):
            parts = line.split(" · ")
            cur = len(parts) >= 4 and parts[3].strip() == ctx.branch
            continue
        if cur:
            keep.append(line)
    return "\n".join(keep)


def mentions(text: str, path: str) -> bool:
    rx = re.compile(r"(^|[^A-Za-z0-9_./-])" + re.escape(path) + r"([^A-Za-z0-9_./-]|$)")
    return any(rx.search(line) for line in text.split("\n"))


def in_plan(path: str, allowed: list[str]) -> bool:
    import fnmatch
    for p in allowed:
        if path == p or (p.endswith("/") and path.startswith(p)) or fnmatch.fnmatchcase(path, p):
            return True
    return False


def stage_scope(ctx: Ctx) -> Res:
    tasks = ctx.cfg.notes_dir() / "tasks"
    plan = tasks / f"{ak.slug(ctx.branch or 'detached')}.plan.md"
    if not ctx.branch or not plan.is_file():
        return Res("SKIP", "no plan for this branch (lite path)")
    if ctx.mode == "hook" and not (tasks / f"{ak.slug(ctx.branch)}.md").is_file():
        return Res("SKIP", "plan not applied yet (no task note): run pl apply once the plan is accepted")
    if not ctx.base:
        return Res("FAIL", detail="no base branch found: set notes.base_branch in .agentkit.json")
    plan_py = helper("plan.py", "skills", "writing-plans", "scripts", "plan.py")
    if plan_py is None:
        return Res("FAIL", detail="plan.py not found; run install.py again")
    import subprocess
    p = subprocess.run([sys.executable, str(plan_py), "field", "files", "--cumulative"], cwd=ctx.root, capture_output=True,
                       text=True, encoding="utf-8", errors="replace", env=ak.CHILD_ENV)
    if p.returncode != 0:
        return Res("FAIL", detail=p.stderr)
    allowed = [x.strip() for x in p.stdout.splitlines() if x.strip()]
    logged = branch_decisions(ctx)
    bad = [f for f in ctx.changed if not in_plan(f, allowed) and not (logged and mentions(logged, f))]
    if not bad:
        return Res("PASS")
    lines = [f"Out-of-plan file: {f}. Log the reason in the decision log or revert." for f in bad[:10]]
    if len(bad) > 10:
        lines.append(f"(+{len(bad) - 10} more)")
    lines.append('(log: nt decide --topic scope --text "<path>: <why>")')
    return Res("FAIL", detail="\n".join(lines))


STAGE_FN = {
    "format": lambda c: stage_ruff(c, "format"),
    "lint": lambda c: stage_ruff(c, "lint"),
    "typecheck": stage_typecheck,
    "tests": stage_tests,
    "integrity": stage_integrity,
    "diff-cover": stage_diffcover,
    "scope": stage_scope,
}


# ---------------------------------------------------------------- stage selection
def parse_stages(raw, src: str, fail) -> list[str]:
    out = []
    for name in ak.as_list(raw):
        name = ALIAS.get(name, name)
        if name not in ALL_STAGES:
            fail(f"verify: unknown stage '{name}' in {src} (choose from: {', '.join(ALL_STAGES)})")
        out.append(name)
    return out


def select(ctx: Ctx, fail) -> tuple[list[str], str]:
    a = ctx.args
    if a.stages is not None:
        sel = parse_stages(a.stages, "--stages", fail)
        if not sel:
            fail(f"verify: --stages needs at least one of: {', '.join(ALL_STAGES)}")
        return sel, "--stages"
    key = "hook_stages" if ctx.mode == "hook" else "push_stages" if ctx.mode == "push" else ""
    if key:
        v = ctx.cfg.section("verify")
        src = f"verify.{key}" if v.get(key) is not None else f"guards.{key}"
        sel = parse_stages(ctx.cfg.verify(key), src, fail)
        if sel:
            return sel, src
        if ctx.mode == "hook":
            return list(HOOK_DEFAULT), "default Stop stages"
    return list(ALL_STAGES), ""


# ---------------------------------------------------------------- modes
def edit_mode(arg: str) -> int:
    """PostToolUse: ruff check --fix and ruff format on the one .py file just edited, if it is in this repository."""
    raw = sys.stdin.read() if arg == "-" else ""
    file = arg
    if arg == "-":
        try:
            file = str((json.loads(raw).get("tool_input") or {}).get("file_path") or "")
        except (ValueError, AttributeError):
            file = ""
    file = ak.native_path(file) if file else ""
    if not file.endswith(".py"):
        return 0
    root = ak.repo_root()
    if root is None or not Path(file).is_file() or not ak.in_repo(file, root):
        return 0
    cfg = ak.Cfg(root, warn=False)
    ctx_off = [cfg.verify(k, "auto") for k in ("lint", "format")]
    if all(v is False or ak.is_off(v) or cfg.is_null(k) for v, k in zip(ctx_off, ("lint", "format"))):
        return 0
    if any(isinstance(v, str) and v.strip() and v.strip().lower() != "auto" for v in ctx_off):
        return 0  # a user command replaces ruff: not ours to run on one file
    ruff = str(cfg.verify("ruff_cmd", "ruff")).split() or ["ruff"]
    if not (ak.resolve_exe(ruff[0]) or ak.has_tool(ruff[0])):
        say(f"{ruff[0]} not found: install it, or set {cfg.switch_name('lint')} to false in .agentkit.json to skip this stage")
        return 2
    rc, out = ak.run_argv([*ruff, "check", "--fix", "--quiet", "--force-exclude", *ak.RUFF_EXCLUDE, file], root)
    ak.run_argv([*ruff, "format", "--quiet", "--force-exclude", *ak.RUFF_EXCLUDE, file], root)
    if rc != 0:
        say(f"ruff left problems in {file}:", *out.rstrip("\n").split("\n")[-MAX_LINES:])
        return 2
    return 0


def describe(ctx: Ctx, stage: str) -> tuple[str, str]:
    off = disabled(ctx, stage)
    if off:
        return "-", off
    cmd = custom_cmd(ctx, stage)
    if cmd:
        return cmd, f"set in {ctx.cfg.switch_name(stage)}"
    det = ctx.detected.get(stage)
    if det and stage in ("format", "lint", "typecheck", "tests"):
        return det
    if stage in ("format", "lint"):
        return " ".join(ruff_argv(ctx, stage, ["."])), "default: ruff"
    if stage == "tests":
        return " ".join([*ctx.tool("pytest_cmd", "pytest"), "--cov", f"--cov-report=xml:{ctx.xml}"]), "default: pytest"
    if stage == "diff-cover":
        return " ".join([*ctx.tool("diff_cover_cmd", "diff-cover"), ctx.xml, f"--compare-branch={ctx.base or '<base>'}",
                         f"--fail-under={ctx.cfg.verify('fail_under', 90)}"]), "default: diff-cover"
    if stage == "typecheck":
        return "-", "not detected"
    return "(built in)", "default"


def build_parser() -> argparse.ArgumentParser:
    class Parser(argparse.ArgumentParser):
        def error(self, message: str) -> None:  # a usage error must exit 1, never 2 (2 blocks a Stop hook)
            say(message)
            self.print_usage(sys.stderr)
            sys.exit(1)

    ap = Parser(prog="verify.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--hook", action="store_true")
    ap.add_argument("--push", action="store_true")
    ap.add_argument("--edit", nargs="?", const="-", metavar="FILE")
    ap.add_argument("--stages", "--only", dest="stages", metavar="LIST")
    ap.add_argument("--affected", action="store_true")
    ap.add_argument("--detect", action="store_true")
    ap.add_argument("--no-guard", action="store_true", help="skip the integrity stage")
    return ap


def main(argv: list[str] | None = None) -> int:
    ak.utf8_stdio()
    a = build_parser().parse_args(argv)
    a.notes = []
    if a.edit:
        return edit_mode(a.edit)
    mode = "hook" if a.hook else "push" if a.push else "cli"
    raw_in = sys.stdin.read() if mode == "hook" and not sys.stdin.isatty() else ""
    stop_active = bool(STOP_RE.search(raw_in))
    # exit codes: a hook blocks with 2, except when it already blocked once in this chain (stop_hook_active)
    fail_rc = (0 if stop_active else 2) if mode == "hook" else 1

    def fail(msg: str) -> None:
        say(msg)
        raise Abort(fail_rc)

    try:
        if not ak.in_git_repo():
            if mode == "hook":
                return 0
            fail("verify: not inside a git repository")
        root = ak.repo_root() or Path.cwd()
        cfg = ak.Cfg(root)
        ctx = Ctx(root, cfg, mode, a)
        selected, src = select(ctx, fail)
        ctx.selected = selected
        if a.detect:
            say(*cfg.warnings)
            for st in selected:
                cmd, why = describe(ctx, st)
                bad = "" if cmd in ("-", "(built in)") or ak.has_tool(cmd.split()[0]) else "  [tool not installed]"
                print(f"{st:10} {cmd:40} ({why}){bad}")
            return 0
        ctx.changed = ak.changed_files(root, ctx.base, ctx.ignores)
        if mode == "hook" and not ctx.changed:
            return 0
        say(*cfg.warnings)
        return run_all(ctx, selected, src, stop_active, fail_rc)
    except Abort as e:
        return e.rc


def run_all(ctx: Ctx, selected: list[str], src: str, stop_active: bool, fail_rc: int) -> int:
    rows: list[tuple[str, Res]] = []
    failed = False
    warned = False
    for stage in ALL_STAGES:
        off = disabled(ctx, stage)
        if off:
            res = Res("SKIP", off)
        elif stage not in selected:
            res = Res("SKIP", f"not selected ({src})")
        else:
            res = STAGE_FN[stage](ctx)
            if res.status == "WARN" and ctx.mode == "hook":
                res.status = "FAIL"  # the Stop hook blocks once on test changes; the agent must tell the user
            if res.status == "WARN" and ctx.mode == "push":
                res.status = "PASS"  # pre-push only reports them
        rows.append((stage, res))
        failed |= res.status == "FAIL"
        warned |= res.status == "WARN"
    for stage, res in rows:
        if res.status == "SKIP" and stage in QUIET:
            continue
        say(f"{stage:<11} {res.status:<5} {res.note}".rstrip())
    for stage, res in rows:
        if res.status not in ("FAIL", "WARN") and not (stage == "integrity" and res.detail):
            continue
        lines = [x.rstrip("\r") for x in res.detail.rstrip("\n").split("\n")] if res.detail else []
        title = "integrity (report these to the user; do not hide them)" if stage == "integrity" else stage
        say(f"--- {title}", *(lines[:MAX_LINES] if stage in HEAD_FIRST else lines[-MAX_LINES:]))
    say(*ctx.args.notes)
    if not failed and not warned:
        say("VERDICT: PASS (exit 0)")
        return 0
    if not failed:
        say("VERDICT: PASS WITH TEST CHANGES (exit 2) - get the user's OK on the changes above before reporting done.")
        return 2
    if stop_active:
        say("VERDICT: FAIL (exit 0, not blocking: stop_hook_active is true, this stop was already blocked once) - "
            "report the failing stage to the user")
        return 0
    say(f"VERDICT: FAIL (exit {fail_rc}) - fix the failing stage above and re-run verify.py")
    return fail_rc


if __name__ == "__main__":
    sys.exit(main())
