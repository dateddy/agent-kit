#!/usr/bin/env python3
"""akcore - helpers shared by the agent-kit scripts: config, git, paths, tool lookup. Stdlib only.

Imported by verify.py (and later prepush.py); not a command. It replaces lib.sh and needs no jq.
Config is read from <repo>/.agentkit.json. `verify` is the one block for the gates; the 0.5.0 keys
(`guards.ruff`, `guards.pytest_cmd`, `verify.test`, ...) are still read, and print a deprecation WARN.
"""

from __future__ import annotations

import fnmatch
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

IS_NT = os.name == "nt"
CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
RUFF_EXCLUDE = ["--extend-exclude", ".agentkit", "--extend-exclude", ".claude"]

# new key under `verify` -> the 0.5.0 key under `guards` (a bool switch, or a value)
LEGACY_GUARDS = {
    "lint": "ruff",
    "format": "ruff",
    "tests": "pytest",
    "diff_cover": "diff_cover",
    "scope": "scope",
    "ruff_cmd": "ruff_cmd",
    "pytest_cmd": "pytest_cmd",
    "pytest_args": "pytest_args",
    "diff_cover_cmd": "diff_cover_cmd",
    "fail_under": "fail_under",
    "coverage_xml": "coverage_xml",
    "scope_ignore": "scope_ignore",
    "hook_stages": "hook_stages",
    "push_stages": "push_stages",
}
# the stage names for the switch keys of the 0.5.0 layout, used in messages
GUARDS_SWITCH = {
    "lint": "guards.ruff",
    "format": "guards.ruff",
    "tests": "guards.pytest",
    "diff-cover": "guards.diff_cover",
    "scope": "guards.scope",
}
OFF = {"false", "0", "no", "off"}


def git(args: list[str], cwd: str | Path | None = None) -> tuple[int, str]:
    """(exit code, stdout) of git; paths are never quoted."""
    try:
        p = subprocess.run(["git", "-c", "core.quotepath=false", *args], cwd=cwd, capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
    except OSError:
        return 127, ""
    return p.returncode, p.stdout


def git_lines(args: list[str], cwd: str | Path | None = None) -> list[str]:
    rc, out = git(args, cwd)
    return [x.rstrip("\r") for x in out.splitlines() if x.strip()] if rc == 0 else []


def repo_root(cwd: str | Path | None = None) -> Path | None:
    rc, out = git(["rev-parse", "--show-toplevel"], cwd)
    return Path(out.strip()) if rc == 0 and out.strip() else None


def in_git_repo(cwd: str | Path | None = None) -> bool:
    return git(["rev-parse", "--git-dir"], cwd)[0] == 0


def current_branch(root: Path) -> str:
    rc, out = git(["symbolic-ref", "--quiet", "--short", "HEAD"], root)
    return out.strip() if rc == 0 else ""


def slug(branch: str) -> str:
    """Must match slug() in skills/keeping-notes/scripts/notes.py."""
    s = branch.replace("/", "--")
    s = re.sub(r"[^A-Za-z0-9._-]+", "-", s)
    return s.strip("-")


def as_list(v) -> list[str]:
    """A config list: a JSON array, or a string separated by commas, spaces or newlines."""
    if v is None:
        return []
    if isinstance(v, (list, tuple)):
        return [str(x) for x in v if str(x).strip()]
    return [x for x in re.split(r"[,\s]+", str(v)) if x]


def is_off(v) -> bool:
    return str(v).strip().lower() in OFF


class Cfg:
    """`.agentkit.json` of one repository, with the 0.5.0 keys folded into the `verify` block."""

    def __init__(self, root: Path, warn: bool = True) -> None:
        self.root = root
        self.data: dict = {}
        self.warnings: list[str] = []
        p = root / ".agentkit.json"
        if p.is_file():
            try:
                data = json.loads(p.read_text(encoding="utf-8-sig"))
                if isinstance(data, dict):
                    self.data = data
                else:
                    raise ValueError("not an object")
            except (OSError, ValueError):
                self.warnings.append(f"WARN: {p} is not valid JSON; using defaults")
        v, g = self.section("verify"), self.section("guards")
        seen: set[str] = set()
        for new, old in LEGACY_GUARDS.items():
            if old in g and old not in seen:
                seen.add(old)
                target = "verify.lint / verify.format" if old == "ruff" else f"verify.{new}"
                self.warnings.append(f"WARN deprecated: guards.{old} -> {target}")
        if "test" in v:
            self.warnings.append("WARN deprecated: verify.test -> verify.tests")
        if not warn:
            self.warnings = []

    def section(self, name: str) -> dict:
        v = self.data.get(name)
        return v if isinstance(v, dict) else {}

    def get(self, dotted: str, default=None):
        cur = self.data
        for part in dotted.split("."):
            if not isinstance(cur, dict) or part not in cur:
                return default
            cur = cur[part]
        return default if cur is None else cur

    def has_new_keys(self) -> bool:
        """True when the `verify` block uses the unified key names (so messages name verify.*)."""
        v = self.section("verify")
        return any(k in v for k in LEGACY_GUARDS) or any(k in v for k in ("typecheck", "integrity", "typecheck_cmd"))

    def uses_guards(self) -> bool:
        g = self.section("guards")
        return any(old in g for old in LEGACY_GUARDS.values())

    def verify(self, key: str, default=None):
        """Value of verify.<key>; falls back to the 0.5.0 guards.<old> and (tests) verify.test; then default."""
        v, g = self.section("verify"), self.section("guards")
        if key in v and v[key] is not None:
            return v[key]
        if key == "tests" and v.get("test") is not None:
            return v["test"]
        old = LEGACY_GUARDS.get(key)
        if old and old in g and g[old] is not None:
            return g[old]
        return default

    def explicit(self, key: str) -> bool:
        """The user set verify.<key> (or its 0.5.0 guards key) to a value: they asked for that tool."""
        v, g = self.section("verify"), self.section("guards")
        old = LEGACY_GUARDS.get(key)
        return v.get(key) is not None or bool(old and g.get(old) is not None)

    def is_null(self, key: str) -> bool:
        """verify.<key> is an explicit JSON null: the 0.5.0 skill's way to switch a format/lint/typecheck/tests gate off."""
        v = self.section("verify")
        return (key in v and v[key] is None) or (key == "tests" and "tests" not in v and "test" in v and v["test"] is None)

    def switch_name(self, stage: str) -> str:
        """The key to tell the user about for a stage switch: guards.* when they still use the 0.5.0 layout."""
        if self.uses_guards() and not self.has_new_keys():
            return GUARDS_SWITCH.get(stage, f"verify.{stage}")
        return f"verify.{stage.replace('-', '_')}"

    def notes_dir(self) -> Path:
        d = str(self.get("notes.dir", "notes")).rstrip("/\\") or "notes"
        return self.root / d

    def protected(self) -> list[str]:
        v = as_list(self.get("git.protected_branches"))
        return v or ["main", "master", "develop", "release/*"]

    def is_protected(self, branch: str) -> bool:
        return any(fnmatch.fnmatchcase(branch, p) for p in self.protected())

    def base_ref(self) -> str:
        """A ref for the base branch: notes.base_branch, else origin/HEAD, else the first literal protected branch."""
        root = self.root
        b = str(self.get("notes.base_branch", "") or "")
        if not b:
            rc, out = git(["symbolic-ref", "--quiet", "--short", "refs/remotes/origin/HEAD"], root)
            b = re.sub(r"^origin/", "", out.strip()) if rc == 0 else ""
        if not b:
            for pat in self.protected():
                if re.search(r"[*?\[]", pat):
                    continue
                if git(["rev-parse", "--verify", "-q", f"refs/heads/{pat}"], root)[0] == 0:
                    b = pat
                    break
        if not b:
            return ""
        if git(["rev-parse", "--verify", "-q", b], root)[0] == 0:
            return b
        if git(["rev-parse", "--verify", "-q", f"origin/{b}"], root)[0] == 0:
            return f"origin/{b}"
        return ""


# ---------------------------------------------------------------- changed files
def default_ignores(cfg: Cfg, xml: str) -> list[str]:
    custom = as_list(cfg.verify("scope_ignore"))
    pats = custom or [".agentkit/*", ".agentkit.json", f"{cfg.get('notes.dir', 'notes')}/*", "FAILURES.md", ".coverage",
                      ".coverage.*", "coverage.xml"]
    return [*pats, xml, ".agentkit/logs/*", ".agentkit/state/*", ".agentkit/cache/*"]


def ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatchcase(path, p) for p in patterns if p)


def changed_files(root: Path, base: str, patterns: list[str]) -> list[str]:
    """Paths changed on the branch (base...HEAD), uncommitted and untracked, minus ignored ones, in order, unique."""
    out: list[str] = []
    if base:
        out += git_lines(["diff", "--name-only", f"{base}...HEAD"], root)
    out += git_lines(["diff", "--name-only", "HEAD"], root)
    out += git_lines(["ls-files", "--others", "--exclude-standard"], root)
    seen: set[str] = set()
    res = []
    for f in out:
        if f in seen:
            continue
        seen.add(f)
        if not ignored(f, patterns):
            res.append(f)
    return res


# ---------------------------------------------------------------- tools
def _pathext() -> list[str]:
    return [e.lower() for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e] if IS_NT else [""]


def resolve_exe(name: str) -> str | None:
    """Path of a command. On Windows an extensionless script (a bash stub, `#!` on line 1) is found too."""
    exts = _pathext()
    dirs = [""] if (os.sep in name or "/" in name) else [d for d in os.environ.get("PATH", "").split(os.pathsep) if d]
    for d in dirs:
        base = os.path.join(d, name) if d else name
        cands = [base] if (not IS_NT or Path(base).suffix.lower() in exts) else [base + e for e in exts] + [base]
        for c in cands:
            if os.path.isfile(c) and (IS_NT or os.access(c, os.X_OK)):
                return c
    return None


def find_bash() -> str | None:
    """bash for #! scripts: skips the WSL launcher in System32 and the Microsoft Store stubs."""
    for d in os.environ.get("PATH", "").split(os.pathsep):
        low = d.lower().replace("/", "\\")
        if not d or "\\windows\\system32" in low or "windowsapps" in low:
            continue
        for e in _pathext() or [""]:
            c = os.path.join(d, "bash" + e)
            if os.path.isfile(c):
                return c
    return None


def exec_argv(argv: list[str]) -> list[str]:
    """argv with argv[0] resolved; a `#!` script that Windows cannot start is run through its interpreter."""
    exe = resolve_exe(argv[0])
    if exe is None:
        raise FileNotFoundError(argv[0])
    if IS_NT and Path(exe).suffix.lower() not in (".exe", ".com", ".bat", ".cmd"):
        try:
            with open(exe, "rb") as fh:
                first = fh.readline().decode("utf-8", "replace").strip()
        except OSError:
            first = ""
        if first.startswith("#!"):
            toks = first[2:].split()
            interp = toks[1:] if toks and Path(toks[0]).name == "env" else [Path(toks[0]).name, *toks[1:]] if toks else []
            if interp:
                prog = find_bash() if interp[0] == "bash" else resolve_exe(interp[0])
                if prog:
                    return [prog, *interp[1:], exe, *argv[1:]]
        return [exe, *argv[1:]]
    return [exe, *argv[1:]]


def has_tool(cmd: str) -> bool:
    """Is the first word of a shell command line a command we can start?"""
    parts = cmd.split()
    if not parts:
        return False
    if parts[0] in ("python", "python3") and len(parts) > 2 and parts[1] == "-m":
        return subprocess.run([sys.executable, "-c", f"import {parts[2]}"], capture_output=True,
                              env=CHILD_ENV).returncode == 0
    return resolve_exe(parts[0]) is not None or shutil.which(parts[0]) is not None


def run_argv(argv: list[str], cwd: Path, stdin: str | None = None) -> tuple[int, str]:
    """Run a command, stdout and stderr merged; (exit code, text)."""
    p = subprocess.run(exec_argv(argv), cwd=cwd, input=stdin, text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       encoding="utf-8", errors="replace", env=CHILD_ENV)
    return p.returncode, p.stdout


def run_shell(cmd: str, cwd: Path) -> tuple[int, str]:
    p = subprocess.run(cmd, shell=True, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=CHILD_ENV)
    return p.returncode, p.stdout.decode("utf-8", "replace")


# ---------------------------------------------------------------- paths
def native_path(p: str) -> str:
    """A path from hook JSON: backslashes, /c/x (MSYS) and /tmp/x (via cygpath) made usable by Windows Python."""
    p = p.strip().replace("\\", "/")
    if IS_NT:
        m = re.match(r"^/([A-Za-z])(/.*)?$", p)
        if m:
            p = m.group(1).upper() + ":" + (m.group(2) or "/")
        elif p.startswith("/") and not os.path.exists(p):
            cyg = shutil.which("cygpath")
            if cyg:
                r = subprocess.run([cyg, "-m", p], capture_output=True, text=True)
                if r.returncode == 0 and r.stdout.strip():
                    p = r.stdout.strip()
    return p


def _norm(p: str | Path) -> str:
    s = os.path.realpath(str(p)).replace("\\", "/").rstrip("/")
    return s.lower() if IS_NT else s


def in_repo(path: str | Path, root: Path) -> bool:
    """The file's own git work tree is this repository's work tree (not another repo, not outside any repo)."""
    d = Path(path).parent
    top = repo_root(d)
    return top is not None and _norm(top) == _norm(root)


def find_py() -> str:
    """The interpreter that runs the kit scripts (the shell wrappers pick it with py.sh)."""
    return sys.executable


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace", newline="\n")
