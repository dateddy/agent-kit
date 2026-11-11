#!/usr/bin/env python3
"""Install agent-kit into a repo (or your user profile). Stdlib only, idempotent.

  python tools/install.py <repo>          project install (recommended): every skill of the kit (all are core)
  python tools/install.py --user          skills only, into ~/.claude/skills (all projects)
  python tools/install.py <repo> --force  overwrite existing .claude/skills/<name> entries
  python tools/install.py <repo> --hooks  also add a Claude Code SessionStart hook that runs `notes.py brief`
                                          (merged into <repo>/.claude/settings.json; nothing existing is overwritten)
  python tools/install.py <repo> --git-hooks            pre-commit (gitkit scan --staged) and commit-msg (gitkit lint-msg)
                                                        git hooks that call .agentkit/scripts/gitkit.py; any agent is gated,
                                                        honours core.hooksPath. An existing hook is kept; the kit's marked
                                                        block is inserted after its shebang, or rewritten in place.
  python tools/install.py <repo> --uninstall-git-hooks  remove only the marked blocks again
  python tools/install.py <repo> --guards [--team]      computational guards (bash + git + jq + python): copies scripts/ to
                                                        .agentkit/scripts, merges Claude Code hooks (PreToolUse guard-git and
                                                        guard-fs, PostToolUse ruff-fix, Stop verify) and permissions.deny Read
                                                        rules (from fs.deny_read) into .claude/settings.local.json
                                                        (--team: .claude/settings.json), adds a pre-push git hook block, and
                                                        offers `git config merge.ff false` after printing its effect;
                                                        exits 1 before writing anything if bash cannot find jq

Project install does:
  <repo>/.agentkit/skills/<skill>   canonical copy, agent-neutral (referenced by AGENTS.md)
  <repo>/.claude/skills/<skill>     symlink to the above (copy if symlinks are unavailable)
  <repo>/AGENTS.md, CLAUDE.md, .agentkit.json, .claude/commands/*.md   created only if missing (task, grill)
  notes/ layout + .gitignore rule   via notes.py init
  <repo>/notes/knowledge.md         created (header only) if missing; the agent appends repo/env facts
  <repo>/.gitattributes             merge=union for notes/decisions.md, notes/debug.jsonl, FAILURES.md
                                    (append-only logs: branches that both add entries merge without a conflict)
"""
from __future__ import annotations

import argparse
import importlib.util
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
GUARD_SCRIPTS = ("lib.sh", "py.sh", "guard-git.sh", "guard-fs.sh", "guard.py", "verify.sh", "plan-step.sh", "ruff-fix.sh", "pre-push")
GUARD_IGNORE = (".agentkit/coverage.xml", ".coverage")  # written by verify.sh
# bash fails on CR: with core.autocrlf=true git would check the scripts out with CRLF
GUARD_EOL = ".agentkit/scripts/** text eol=lf"
JQ_HINT = ("install jq (https://jqlang.org/download/): Windows `winget install jqlang.jq` (winget may not put it on\n"
           "  PATH; copy jq.exe into ~/bin or add its folder to PATH), macOS `brew install jq`, Debian/Ubuntu\n"
           "  `sudo apt install jq`. Check with: bash -c 'command -v jq'")
ALL_SKILLS = {p.name: p for p in sorted((KIT / "skills").iterdir()) if (p / "SKILL.md").exists()}
# short name, skill, script: the AGENTS.md "Short names" lines
SHORT = (("nt", "keeping-notes", "notes.py"), ("pl", "writing-plans", "plan.py"),
         ("vf", "verifying-changes", "verify.py"), ("run", "economizing-context", "run.py"),
         ("rmap", "economizing-context", "repomap.py"), ("dbg", "debugging-systematically", "dbg.py"))
AGENTS_MARK = "<!-- Project-specific rules below this line -->"


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


# ---------------------------------------------------------------- migration of removed skills and commands
SHIPPED = KIT / "tools" / "shipped_hashes.json"  # generated by tools/gen_shipped_hashes.py
# how a project's own rules mention a removed skill: its name, script, or short name used as a command
IN_USE_RE = {
    "crafting-prompts": r"crafting-prompts|promptkit\.py|`pk\b|\bpk (init|check|render|lint|test|targets)\b",
    "tracking-experiments": r"tracking-experiments|exp\.py|`ex\b|\bex (run|log|list|compare|show)\b",
    "managing-git": r"managing-git|gitkit\.py|`gk\b|\bgk (scan|lint-msg|preflight|branch-name|diff)\b",
    "grilling-designs": r"grilling-designs",
}


def norm_digest(path: Path) -> str:
    import hashlib
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def dir_files(d: Path) -> list[Path]:
    return sorted(p for p in d.rglob("*") if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc")


def is_unmodified(d: Path, shipped: dict) -> bool:
    """True when every file of d is a file the kit once shipped for this skill, byte for byte (CRLF-insensitive)."""
    files = dir_files(d)
    return bool(files) and all(
        norm_digest(p) in shipped.get(p.relative_to(d).as_posix(), ()) for p in files)


def project_rules(repo: Path) -> str:
    """The user's own rules: AGENTS.md below the marker (all of it when the file has no marker)."""
    p = repo / "AGENTS.md"
    if not p.is_file():
        return ""
    text = p.read_text(encoding="utf-8", errors="replace")
    return text.partition(AGENTS_MARK)[2] if AGENTS_MARK in text else text


def migrate_removed(repo: Path, kit_skills, kit_commands) -> int:
    """Delete skills and commands the kit no longer ships when the downstream copy is untouched.

    A modified copy, or one the project's own rules still call, is kept with a WARN. User data
    (experiments/, prompts/, notes/) is never looked at. Returns 2 when something was kept, else 0.
    """
    try:
        shipped = json.loads(SHIPPED.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        print(f"WARN {SHIPPED} missing or unreadable; removed skills are left in place")
        return 2
    code = 0
    rules = project_rules(repo)
    canon = repo / ".agentkit" / "skills"
    for name, files in shipped["skills"].items():
        d = canon / name
        link = repo / ".claude" / "skills" / name
        if name in kit_skills or not (d / "SKILL.md").is_file():
            continue
        if not is_unmodified(d, files):
            print(f"WARN keep {d}: modified since it was shipped; the kit no longer ships it (removed in 0.5.0)")
            code = 2
        elif re.search(IN_USE_RE[name], rules):
            print(f"WARN keep {d}: your project rules in AGENTS.md still use it; the kit no longer ships it "
                  f"(removed in 0.5.0). Delete it yourself once you stop using it")
            code = 2
        else:
            if link.is_symlink() or (link.is_dir() and is_unmodified(link, files)):
                remove_path(link)
            elif link.exists():
                print(f"WARN keep {link}: not a link and not the shipped copy")
                code = 2
            remove_path(d)
            print(f"remove {d} (removed in 0.5.0)")
            if name == "managing-git":  # a 0.4.0 git hook still points here; make it fail loudly, never skip
                write_old_gitkit_shim(repo)
                print(f"create {repo / OLD_GITKIT} (a hook not yet rewritten by --git-hooks fails with a clear message)")
    for name, digests in shipped["commands"].items():
        cmd = repo / ".claude" / "commands" / name
        if name in kit_commands or not cmd.is_file():
            continue
        if norm_digest(cmd) in digests:
            remove_path(cmd)
            print(f"remove {cmd} (removed in 0.5.0)")
        else:
            print(f"WARN keep {cmd}: modified since it was shipped; the kit no longer ships it (removed in 0.5.0)")
            code = 2
    return code


KNOWLEDGE_HEAD = (
    "# Knowledge\n\n"
    "Facts about this repo and its environment that took effort to find out. The agent adds a line here when it needed\n"
    "3 or more tries to find the right way, or when you corrected a wrong claim about the repo.\n"
    "Line format: `- <fact>; <the right way> (verified YYYY-MM-DD: <command or evidence>)`.\n"
    "Not here: bugs (dbg journal), decisions (decisions.md), agent mistakes (FAILURES.md).\n\n"
)


def ensure_knowledge(repo: Path) -> str:
    """Create <notes dir>/knowledge.md (header only) if it does not exist; never touch an existing one."""
    try:
        cfg = json.loads((repo / ".agentkit.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    path = repo / (cfg.get("notes") or {}).get("dir", "notes").strip("/") / "knowledge.md"
    if path.exists():
        return f"keep {path}"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_raw(path, KNOWLEDGE_HEAD)
    return f"create {path}"


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
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return f"hook {path} (SessionStart -> notes.py brief)", 0


# ---------------------------------------------------------------- skill selection and templates
def render(template: str) -> str:
    """Fill {{SHORT_NAMES}} with the short names of the kit's skills."""
    items = [f"`{n}` {s}/scripts/{f}" for n, s, f in SHORT]
    short = "\n".join(" · ".join(items[i:i + 3]) for i in range(0, len(items), 3))
    return template.replace("{{SHORT_NAMES}}", short)


# ---------------------------------------------------------------- git hooks
HOOK_MARK = "# >>> agent-kit >>>"
HOOK_BEGIN = HOOK_MARK + " managed block; remove with: install.py --uninstall-git-hooks"
HOOK_END = "# <<< agent-kit <<<"
HOOK_CALLS = {"pre-commit": "scan --staged", "commit-msg": 'lint-msg "$1"'}
SH_SHEBANG = re.compile(r"^#!\s*(\S*/)?(env\s+)?(ba|da|z|k)?sh\b")
BLOCK_RE = re.compile(re.escape(HOOK_MARK) + r".*?" + re.escape(HOOK_END) + r"\n?", re.S)


PY_FN_BEGIN = "# >>> ak_find_py"
PY_FN_END = "# <<< ak_find_py"


def find_py_function() -> list[str]:
    """Lines of the POSIX function ak_find_py of scripts/py.sh (between its markers): the one interpreter finder."""
    lines = (KIT / "scripts" / "py.sh").read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
    return lines[lines.index(PY_FN_BEGIN) + 1:lines.index(PY_FN_END)]


def hook_block(name: str) -> str:
    """POSIX sh that BLOCKs only on gitkit exit 1 (WARN passes) and never calls `exit 0`."""
    pref = shlex.quote(Path(sys.executable).as_posix())
    return "\n".join([
        HOOK_BEGIN,
        *find_py_function(),
        f"ak_pref={pref}",
        'ak_gk="$(git rev-parse --show-toplevel)/.agentkit/scripts/gitkit.py"',
        "ak_run() {",
        '  if [ -x "$ak_pref" ]; then "$ak_pref" "$@"',
        "  elif ak_find_py; then",
        '    if [ -n "$ak_py_args" ]; then "$ak_py" "$ak_py_args" "$@"; else "$ak_py" "$@"; fi',
        '  else echo "agent-kit: python not found \u2014 secret scan and commit lint SKIPPED" >&2; return 0; fi',
        "}",
        "ak_rc=0",
        'if [ ! -f "$ak_gk" ]; then',
        '  echo "agent-kit: .agentkit/scripts/gitkit.py is missing, so this commit is not checked. Run install.py again:" >&2',
        '  echo "  python <agent-kit>/tools/install.py <this repo> --git-hooks   (or --uninstall-git-hooks to drop the hook)" >&2',
        "  exit 1",
        "fi",
        f'ak_run "$ak_gk" {HOOK_CALLS[name]} || ak_rc=$?',
        'if [ "$ak_rc" -eq 1 ]; then exit 1; fi',
        HOOK_END,
        "",
    ])


def union_paths(repo: Path) -> list[str]:
    """Append-only logs that git may merge by keeping both sides' lines (dirs from .agentkit.json)."""
    try:
        cfg = json.loads((repo / ".agentkit.json").read_text(encoding="utf-8"))
    except Exception:
        cfg = {}
    notes = (cfg.get("notes") or {}).get("dir", "notes").strip("/")
    return [f"{notes}/decisions.md", f"{notes}/debug.jsonl", "FAILURES.md"]


def merge_gitattributes(repo: Path) -> str:
    """Add `<log> merge=union` lines to .gitattributes; idempotent, existing lines are kept."""
    ga = repo / ".gitattributes"
    text = read_raw(ga) if ga.exists() else ""
    have = {l.strip() for l in text.splitlines()}
    add = [f"{p} merge=union" for p in union_paths(repo) if f"{p} merge=union" not in have]
    if not add:
        return f"keep {ga}"
    nl = "\r\n" if "\r\n" in text else "\n"
    sep = "" if not text or text.endswith("\n") else nl
    write_raw(ga, text + sep + nl.join(add) + nl)
    return f"{'update' if text else 'create'} {ga} (merge=union for append-only logs)"


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


def prepush_block() -> str:
    """pre-push block: hands git's ref lines to .agentkit/scripts/pre-push, then restores stdin for any hook after it."""
    return "\n".join([
        HOOK_BEGIN,
        "ak_in=$(mktemp)",
        'cat >"$ak_in"',
        'ak_pp="$(git rev-parse --show-toplevel)/.agentkit/scripts/pre-push"',
        "ak_rc=0",
        'if [ -f "$ak_pp" ]; then',
        '  if command -v bash >/dev/null 2>&1; then bash "$ak_pp" "$@" <"$ak_in" || ak_rc=$?',
        '  else echo "agent-kit: bash not found - pre-push checks SKIPPED" >&2; fi',
        "fi",
        'if [ "$ak_rc" -ne 0 ]; then rm -f "$ak_in"; exit "$ak_rc"; fi',
        'exec <"$ak_in"',
        'rm -f "$ak_in" 2>/dev/null',
        HOOK_END,
        "",
    ])


def install_git_hook(d: Path, name: str, block: str | None = None, manual: str | None = None) -> tuple[str, int]:
    path, block = d / name, block or hook_block(name)
    if not path.exists():
        d.mkdir(parents=True, exist_ok=True)
        write_raw(path, "#!/bin/sh\n" + block)
        msg = f"create {path}"
    else:
        text = read_raw(path)
        if HOOK_MARK in text:
            # rewrite the kit's block in place (0.4.0 blocks call the removed managing-git path); user lines stay put
            seen = []

            def swap(_m):
                seen.append(1)
                return block if len(seen) == 1 else ""  # a duplicated block collapses into one
            new = BLOCK_RE.sub(swap, text)
            if new == text:
                return f"keep {path} (agent-kit block already up to date)", 0
            write_raw(path, new)
            path.chmod(path.stat().st_mode | 0o111)
            return f"update {path} (agent-kit block rewritten for .agentkit/scripts/gitkit.py)", 0
        first, _, rest = text.partition("\n")
        if first.startswith("#!"):
            if not SH_SHEBANG.match(first):
                return (f"WARN {path} is not a sh hook ({first.strip()}) — left untouched; "
                        f"call {manual or 'gitkit.py ' + HOOK_CALLS[name]} from it by hand"), 2
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


def git_hooks(repo: Path, action, names: tuple = tuple(HOOK_CALLS)) -> int:
    d = hooks_dir(repo)
    if d is None:
        print(f"WARN {repo} is not the top level of a git repository — run `git init` there first; hooks not touched")
        return 2
    code = 0
    for name in names:
        msg, c = action(d, name)
        print(msg)
        code = max(code, c)
    return code


# ---------------------------------------------------------------- guards (bash scripts + Claude Code wiring)
MERGE_FF_NOTE = (
    "merge.ff: `git config merge.ff false` changes this clone's .git/config only (nothing is committed), but it affects\n"
    "  everyone who uses this clone: `git merge` and `git pull` then create a merge commit instead of fast-forwarding, on\n"
    "  every branch (including `git pull` on a feature branch). `git merge --ff-only` still works when asked for.")


def git_out(repo: Path, *args: str) -> str | None:
    p = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV)
    return p.stdout.strip() if p.returncode == 0 else None


def copy_gitkit(repo: Path) -> str:
    """scripts/gitkit.py -> <repo>/.agentkit/scripts/gitkit.py: the file the pre-commit and commit-msg hooks call."""
    dst = repo / ".agentkit" / "scripts" / "gitkit.py"
    dst.parent.mkdir(parents=True, exist_ok=True)
    data = (KIT / "scripts" / "gitkit.py").read_bytes().replace(b"\r\n", b"\n")
    if dst.exists() and dst.read_bytes() == data:
        return f"keep {dst}"
    dst.write_bytes(data)
    return f"copy {dst}"


OLD_GITKIT = ".agentkit/skills/managing-git/scripts/gitkit.py"  # where 0.4.0 git hooks look for gitkit
OLD_GITKIT_SHIM = (
    "#!/usr/bin/env python3\n"
    '"""Left by agent-kit 0.5.0 so a git hook written by 0.4.0 fails loudly instead of skipping the check."""\n'
    "import sys\n\n"
    'sys.stderr.write("agent-kit: gitkit.py moved to .agentkit/scripts/gitkit.py in 0.5.0 and this git hook still calls the "\n'
    '                 "old path, so the commit is not checked.\\nRun install.py again: "\n'
    '                 "python <agent-kit>/tools/install.py <this repo> --git-hooks\\n")\n'
    "sys.exit(1)\n"
)


def write_old_gitkit_shim(repo: Path) -> None:
    shim = repo / OLD_GITKIT
    shim.parent.mkdir(parents=True, exist_ok=True)
    write_raw(shim, OLD_GITKIT_SHIM)


def warn_old_hooks(repo: Path) -> int:
    """WARN (exit 2) when a kit git hook of this clone still calls the 0.4.0 gitkit path."""
    d = hooks_dir(repo)
    code = 0
    for name in HOOK_CALLS if d else ():
        p = d / name
        if p.is_file() and HOOK_MARK in read_raw(p) and OLD_GITKIT in read_raw(p):
            print(f"WARN {p} still calls the removed {OLD_GITKIT} and now fails every commit; "
                  f"run: python {KIT / 'tools' / 'install.py'} {repo} --git-hooks")
            code = 2
    return code


PLUGIN_ID = "commit-commands@claude-plugins-official"
PLUGIN_INSTALL = f"claude plugin install {PLUGIN_ID} --scope project"


def merge_plugin(path: Path) -> tuple[str, int]:
    """enabledPlugins[PLUGIN_ID] = true in a Claude Code settings file (docs: settings, plugins/loading); the rest is kept.
    The key only enables it: every person still installs the plugin on their machine (PLUGIN_INSTALL)."""
    manual = f'add "enabledPlugins": {{"{PLUGIN_ID}": true}} to it by hand'
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"WARN {path} is not valid JSON ({e}) — left untouched; {manual}", 2
    enabled = data.get("enabledPlugins", {}) if isinstance(data, dict) else None
    if not isinstance(enabled, dict):
        return f"WARN {path} has an unexpected enabledPlugins layout — left untouched; {manual}", 2
    if enabled.get(PLUGIN_ID) is True:
        return f"keep {path} ({PLUGIN_ID} already enabled)", 0
    if PLUGIN_ID in enabled:
        return f"keep {path} ({PLUGIN_ID} is switched off there; left as you set it)", 0
    data.setdefault("enabledPlugins", {})[PLUGIN_ID] = True
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return f"plugin {path} (enabledPlugins: {PLUGIN_ID})", 0


def copy_guard_scripts(repo: Path) -> str:
    dst = repo / ".agentkit" / "scripts"
    dst.mkdir(parents=True, exist_ok=True)
    for name in GUARD_SCRIPTS:
        data = (KIT / "scripts" / name).read_bytes().replace(b"\r\n", b"\n")  # bash chokes on CR
        (dst / name).write_bytes(data)
        (dst / name).chmod((dst / name).stat().st_mode | 0o111)
    return f"copy {dst} ({len(GUARD_SCRIPTS)} scripts; needs bash, git, jq; guard-git runs on python)"


def merge_settings(path: Path, wanted: dict) -> tuple[str, int]:
    """Add the wanted hook groups (event -> [group]) to a Claude Code settings file; nothing existing is touched."""
    manual = "copy the hooks from templates/claude-hooks.json into it by hand"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"WARN {path} is not valid JSON ({e}) — left untouched; {manual}", 2
    hooks = data.get("hooks", {}) if isinstance(data, dict) else None
    if not isinstance(hooks, dict) or any(not isinstance(hooks.get(ev, []), list) for ev in wanted):
        return f"WARN {path} has an unexpected layout — left untouched; {manual}", 2
    hooks = data.setdefault("hooks", {})
    added = []
    for event, groups in wanted.items():
        cur = hooks.setdefault(event, [])
        have = {h.get("command") for g in cur if isinstance(g, dict)
                for h in g.get("hooks", []) if isinstance(h, dict)}
        for g in groups:
            if not all(h["command"] in have for h in g["hooks"]):
                cur.append(g)
                added.append(event)
    if not added:
        return f"keep {path} (guard hooks already present)", 0
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return f"hook {path} ({', '.join(added)} -> .agentkit/scripts)", 0


def load_guard():
    """scripts/guard.py of this kit as a module (the fs.* defaults and the config reader live there)."""
    spec = importlib.util.spec_from_file_location("agentkit_guard", KIT / "scripts" / "guard.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def perm_pattern(p: str) -> str:
    """An fs.* glob as a Claude Code Read() pattern (gitignore syntax): `//path` is absolute, `/path` would be project-relative,
    `~/` and relative patterns (**/.env) are kept. Windows C:/x is written //c/x (docs: permissions, Read and Edit)."""
    m = re.match(r"^([A-Za-z]):[/\\](.*)$", p)
    if m:
        return f"//{m.group(1).lower()}/{m.group(2)}"
    return "/" + p if p.startswith("/") and not p.startswith("//") else p


def read_deny_rules(repo: Path) -> tuple[list[str], list[str]]:
    """(Read() rules for permissions.deny, notes) from fs.deny_read / fs.allow_read of <repo>/.agentkit.json (else the defaults).

    allow_read becomes `Read(!pattern)`: a deny rule starting with ! carves paths out of the rules listed before it in the
    same file, but only out of relative ones (not `~/` or `//`), so other allow_read patterns stay enforced by guard.py only.
    """
    g = load_guard()
    try:
        data = json.loads((repo / ".agentkit.json").read_text(encoding="utf-8-sig"))
    except (OSError, ValueError):
        data = None
    cfg = g.Config(data if isinstance(data, dict) else None)
    rules = [f"Read({perm_pattern(p)})" for p in cfg.cfg_list("fs.deny_read", g.FS_DENY_READ)]
    notes = []
    for p in cfg.cfg_list("fs.allow_read", g.FS_ALLOW_READ):
        if p.startswith(("~", "/", "{")) or re.match(r"^[A-Za-z]:", p):
            notes.append(f"note: fs.allow_read '{p}' cannot be a Claude Code ! carve-out (only relative patterns can); the Read tool stays blocked there")
        else:
            rules.append(f"Read(!{p})")
    return rules, notes


def merge_permissions(path: Path, rules: list[str]) -> tuple[str, int]:
    """Append the rules that are missing to permissions.deny of a Claude Code settings file; nothing existing is touched."""
    manual = "add to permissions.deny by hand: " + ", ".join(rules)
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except json.JSONDecodeError as e:
        return f"WARN {path} is not valid JSON ({e}) — left untouched; {manual}", 2
    perms = data.get("permissions", {}) if isinstance(data, dict) else None
    if not isinstance(perms, dict) or not isinstance(perms.get("deny", []), list):
        return f"WARN {path} has an unexpected permissions layout — left untouched; {manual}", 2
    deny = data.setdefault("permissions", {}).setdefault("deny", [])
    add = [r for r in rules if r not in deny]
    if not add:
        return f"keep {path} (permissions.deny already has the fs.deny_read rules)", 0
    deny.extend(add)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((json.dumps(data, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    return f"permissions {path} (+{len(add)} Read rules in permissions.deny from fs.deny_read)", 0


def append_lines(path: Path, lines: tuple | list) -> list:
    """Append the lines that are not in the file yet; returns the ones added."""
    text = read_raw(path) if path.exists() else ""
    have = {l.strip() for l in text.splitlines()}
    add = [l for l in lines if l not in have]
    if add:
        nl = "\r\n" if "\r\n" in text else "\n"
        write_raw(path, text + ("" if not text or text.endswith("\n") else nl) + nl.join(add) + nl)
    return add


def set_merge_ff(repo: Path, ask=None) -> tuple[str, int]:
    """Print what `merge.ff false` changes and set it only on an explicit yes (never when not interactive)."""
    if git_out(repo, "config", "--local", "--get", "merge.ff") == "false":
        return "keep merge.ff=false (already set)", 0
    print(MERGE_FF_NOTE)
    not_interactive = f"skip merge.ff (not interactive). To apply: git -C {repo} config merge.ff false"
    if ask is None:
        if not sys.stdin.isatty():
            return not_interactive, 0
        ask = lambda: input("Set merge.ff=false in this clone? [y/N] ")  # noqa: E731
    try:
        answer = ask().strip().lower()
    except EOFError:  # isatty() is True for the NUL device on Windows, so a closed stdin shows up here
        print()
        return not_interactive, 0
    if answer not in ("y", "yes"):
        return f"skip merge.ff (declined). To apply later: git -C {repo} config merge.ff false", 0
    if git_out(repo, "config", "--local", "merge.ff", "false") is None:
        return "WARN could not set merge.ff — run: git config merge.ff false", 2
    return "set merge.ff=false in .git/config", 0


def find_bash() -> str | None:
    """bash the guards will run under: Git's on Windows (System32\\bash.exe is the WSL launcher), else PATH's."""
    found = shutil.which("bash")
    if os.name == "nt" and (not found or "system32" in found.lower()):
        g = shutil.which("git")
        cand = Path(g).resolve().parent.parent / "bin" / "bash.exe" if g else None
        if cand and cand.exists():
            return str(cand)
    return found


def guard_prereqs(jq: str = "jq") -> str | None:
    """None when bash, git and jq are usable the way the hooks call them (non-login bash); else the reason."""
    if shutil.which("git") is None:
        return "git not found: the guards need bash, git and jq"
    bash = find_bash()
    if bash is None:
        return "bash not found: the guards need bash, git and jq (on Windows: Git for Windows' bash)"
    r = subprocess.run([bash, "-c", f"command -v {shlex.quote(jq)}"], capture_output=True, text=True,
                       encoding="utf-8", errors="replace", env=CHILD_ENV)
    if r.returncode != 0 or not r.stdout.strip():
        return (f"{jq} not found by {bash}: every guard script needs it, and without it the PreToolUse hook would "
                f"block every Bash call.\n  {JQ_HINT}")
    return None


def exclude_local_settings(repo: Path) -> None:
    """Personal trial: keep .claude/settings.local.json out of git without touching the shared .gitignore."""
    excl = git_out(repo, "rev-parse", "--git-path", "info/exclude")
    if excl:
        path = Path(excl) if Path(excl).is_absolute() else repo / excl
        path.parent.mkdir(parents=True, exist_ok=True)
        if append_lines(path, [".claude/settings.local.json"]):
            print(f"exclude .claude/settings.local.json in {path} (personal, not committed)")


def install_guards(repo: Path, team: bool, ask=None) -> int:
    code = 0
    print(copy_guard_scripts(repo))
    wanted = json.loads((KIT / "templates" / "claude-hooks.json").read_text(encoding="utf-8"))["hooks"]
    settings = repo / ".claude" / ("settings.json" if team else "settings.local.json")
    msg, c = merge_settings(settings, wanted)
    print(msg)
    code = max(code, c)
    rules, notes = read_deny_rules(repo)
    msg, c = merge_permissions(settings, rules)
    print(msg)
    code = max(code, c)
    for note in notes:
        print(note)
    if not team:
        exclude_local_settings(repo)
    if append_lines(repo / ".gitattributes", [GUARD_EOL]):
        print(f"update {repo / '.gitattributes'} ({GUARD_EOL}; then: git add --renormalize .agentkit/scripts)")
    if append_lines(repo / ".gitignore", GUARD_IGNORE):
        print(f"update {repo / '.gitignore'} ({', '.join(GUARD_IGNORE)})")
    d = hooks_dir(repo)
    if d is None:
        print(f"WARN {repo} is not the top level of a git repository — pre-push hook and merge.ff not set up")
        return max(code, 2)
    msg, c = install_git_hook(d, "pre-push", prepush_block(), 'bash .agentkit/scripts/pre-push "$@"')
    print(msg)
    code = max(code, c)
    msg, c = set_merge_ff(repo, ask)
    print(msg)
    if not (repo / ".agentkit.json").exists() or "guards" not in (repo / ".agentkit.json").read_text(encoding="utf-8"):
        print("note: .agentkit.json has no \"guards\" section, so every default applies (see .agentkit.example.json)")
    return max(code, c)


def failures_header() -> str:
    """FAILURES.md without the kit's own entries: the heading and the entry format."""
    text = (KIT / "FAILURES.md").read_text(encoding="utf-8")
    return text.partition("\n## 20")[0].rstrip() + "\n"


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
    ap.add_argument("--with", dest="with_", metavar="NAMES", help=argparse.SUPPRESS)  # removed in 0.5.0
    ap.add_argument("--git-hooks", action="store_true", help="install pre-commit (scan) and commit-msg (lint-msg) git hooks")
    ap.add_argument("--uninstall-git-hooks", action="store_true", help="remove only the agent-kit blocks from git hooks")
    ap.add_argument("--guards", action="store_true",
                    help="install the computational guards: scripts, Claude Code hooks, pre-push (needs bash, git, jq, python)")
    ap.add_argument("--team", action="store_true",
                    help="write the Claude Code settings (commit-commands plugin; with --guards also the hooks) to "
                         ".claude/settings.json (shared) instead of the uncommitted .claude/settings.local.json")
    a = ap.parse_args()

    if a.user and (a.git_hooks or a.uninstall_git_hooks or a.guards):
        ap.error("git hooks and guards belong to a repo; give a repo path instead of --user")
    if a.team and a.user:
        ap.error("--team belongs to a repo; give a repo path instead of --user")
    if a.with_ is not None:
        print("BLOCK: --with was removed in 0.5.0 (every skill is core now), see CHANGELOG. Nothing was installed.")
        return 1
    wanted = list(ALL_SKILLS)
    if a.user:
        dest = Path.home() / ".claude" / "skills"
        dest.mkdir(parents=True, exist_ok=True)
        for name in LEGACY_SKILLS:
            old = dest / name
            md = old / "SKILL.md"
            if md.is_file() and f"name: {name}" in md.read_text(encoding="utf-8"):
                remove_path(old)
                print(f"remove {old} (merged into writing-plans)")
        for name in wanted:
            copy_skill(ALL_SKILLS[name], dest / name)
            print(f"copy {dest / name}")
        print("NOTE: AGENTS.md references .agentkit/skills/ — for project use run a project install too.")
        return 0

    if not a.repo:
        ap.error("give a repo path or --user")
    repo = Path(a.repo).resolve()
    if not repo.is_dir():
        sys.exit(f"not a directory: {repo}")
    if a.guards:  # before anything is written: half-installed guards without jq would block every Bash call
        why = guard_prereqs()
        if why:
            print(f"BLOCK: {why}\nNothing was installed. Re-run with --guards once it is fixed, or drop --guards.")
            return 1
    if a.uninstall_git_hooks:
        return git_hooks(repo, uninstall_git_hook, (*HOOK_CALLS, "pre-push"))

    canon = repo / ".agentkit" / "skills"
    canon.mkdir(parents=True, exist_ok=True)
    remove_legacy(repo)
    mig = migrate_removed(repo, set(ALL_SKILLS), {c.name for c in (KIT / "templates" / "commands").glob("*.md")})
    for name in wanted:
        copy_skill(ALL_SKILLS[name], canon / name)
        print(f"copy {canon / name}")
        print(link_or_copy(canon / name, repo / ".claude" / "skills" / name, a.force))

    for name, src in (("AGENTS.md", KIT / "templates" / "AGENTS.md"),
                      ("CLAUDE.md", KIT / "templates" / "CLAUDE.md"),
                      (".agentkit.json", KIT / ".agentkit.example.json"),
                      *((f".claude/commands/{c.name}", c) for c in sorted((KIT / "templates" / "commands").glob("*.md"))
                        )):
        dst = repo / name
        rendered = render(src.read_text(encoding="utf-8")) if name in ("AGENTS.md", "CLAUDE.md") else None
        if not dst.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            if rendered is None:
                shutil.copy(src, dst)
            else:
                write_raw(dst, rendered)
            print(f"create {dst}")
        elif name == ".claude/commands/task.md" and "starting-tasks" in dst.read_text(encoding="utf-8"):
            shutil.copy(src, dst)
            print(f"update {dst} (now points to writing-plans)")
        elif name == "AGENTS.md" and AGENTS_MARK in dst.read_text(encoding="utf-8"):
            text = dst.read_text(encoding="utf-8").replace("\r\n", "\n")
            head, _, tail = text.partition(AGENTS_MARK)
            new_head = rendered.partition(AGENTS_MARK)[0]
            if head == new_head:
                print(f"keep {dst}")
            else:
                write_raw(dst, new_head + AGENTS_MARK + tail)
                print(f"update {dst} (rules above the marker regenerated for the installed skills)")
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
    print(merge_gitattributes(repo))
    print(ensure_knowledge(repo))
    if not (repo / "FAILURES.md").exists():  # AGENTS.md asks for an entry per corrected mistake
        write_raw(repo / "FAILURES.md", failures_header())
        print(f"create {repo / 'FAILURES.md'}")
    code = max(mig, warn_old_hooks(repo) if not a.git_hooks else 0)
    settings = repo / ".claude" / ("settings.json" if a.team else "settings.local.json")
    msg, c = merge_plugin(settings)
    print(msg)
    code = max(code, c)
    if not a.team:
        exclude_local_settings(repo)
    print(f"NOTE the commit/push/PR workflow is the commit-commands plugin; each person installs it once: {PLUGIN_INSTALL}")
    if a.hooks:
        msg, c = merge_hook(repo)
        print(msg)
        code = max(code, c)
    if a.git_hooks:
        print(copy_gitkit(repo))
        code = max(code, git_hooks(repo, install_git_hook))
    if a.guards:
        code = max(code, install_guards(repo, a.team))
    print("\nDone. Commit .agentkit/skills, .claude/, AGENTS.md, CLAUDE.md, .agentkit.json, .gitattributes, "
          "notes/decisions.md, FAILURES.md" + (", .agentkit/scripts." if a.guards else "."))
    return code


if __name__ == "__main__":
    sys.exit(main())
