#!/usr/bin/env python3
"""gitkit — deterministic git helpers for agents. Stdlib only, git >= 2.38 recommended.

Every subcommand prints compact text and uses exit codes as the decision signal:
  0 = OK, 1 = BLOCK (do not proceed), 2 = WARN (proceed only after telling the user)

Subcommands:
  diff [--staged]       size-capped diff; skips lockfiles, generated and kit-managed files
  lint-msg FILE|-m MSG  validate a Conventional Commit message against config
  scan [--staged]       block secrets, .env files and oversized files before commit
  branch-name TYPE DESC [--ticket T]   build a branch name;  --check NAME  validates one
  preflight OP [ARG]    OP in: commit, switch, merge, rebase, push, stash-pop, pull
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
import unicodedata
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2

DEFAULTS = {
    "protected_branches": ["main", "master", "develop", "release/*"],
    # ask = WARN (agent must get user's yes) · allow = OK · block = hard BLOCK
    "protected_commit": "ask",
    "branch_pattern": "{type}/{ticket}{slug}",
    "branch_types": ["feat", "fix", "chore", "docs", "refactor", "test", "perf", "ci", "hotfix", "exp"],
    "commit_types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"],
    "scopes": [],
    "strict_scopes": False,
    # 72 keeps `git log --oneline` readable in an 80-col terminal with the hash prefix
    "subject_max": 72,
    "body_wrap": 100,
    # GitHub warns at 50 MB; 5 MB catches accidental datasets/checkpoints early
    "max_file_mb": 5,
    "diff_ignore": ["*.lock", "package-lock.json", "pnpm-lock.yaml", "poetry.lock", "uv.lock",
                    "*.min.js", "*.map", "*.ipynb", "dist/*", "build/*"],
}
# Diff budget: ~400 lines ≈ 4-6k tokens, enough to describe a focused commit
DIFF_MAX_LINES = 400
DIFF_MAX_PER_FILE = 120


# ---------------------------------------------------------------- utilities
def git(*args: str, check: bool = False) -> subprocess.CompletedProcess:
    p = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    if check and p.returncode != 0:
        sys.exit(f"git {' '.join(args)} failed: {p.stderr.strip()}")
    return p


def out(*args: str) -> str:
    return git(*args).stdout.strip()


def repo_root() -> Path:
    p = git("rev-parse", "--show-toplevel")
    if p.returncode != 0:
        print("BLOCK: not inside a git repository (run `git init` or cd into a repo)")
        sys.exit(BLOCK)
    return Path(p.stdout.strip())


def load_cfg(root: Path) -> dict:
    cfg = dict(DEFAULTS)
    f = root / ".agentkit.json"
    if f.exists():
        try:
            user = json.loads(f.read_text(encoding="utf-8")).get("git", {})
            cfg.update({k: v for k, v in user.items() if not k.startswith("_")})
        except json.JSONDecodeError as e:
            print(f"WARN: .agentkit.json is invalid JSON ({e}); using defaults")
    if cfg["protected_commit"] not in ("ask", "allow", "block"):
        print(f"WARN: git.protected_commit='{cfg['protected_commit']}' invalid; using 'ask'")
        cfg["protected_commit"] = "ask"
    return cfg


def current_branch() -> str:
    b = out("symbolic-ref", "--quiet", "--short", "HEAD")
    return b or "(detached)"


def is_protected(branch: str, cfg: dict) -> bool:
    return any(fnmatch.fnmatch(branch, pat) for pat in cfg["protected_branches"])


def in_progress(gitdir: Path) -> str | None:
    marks = {"MERGE_HEAD": "merge", "rebase-merge": "rebase", "rebase-apply": "rebase/am",
             "CHERRY_PICK_HEAD": "cherry-pick", "REVERT_HEAD": "revert", "BISECT_LOG": "bisect"}
    for name, label in marks.items():
        if (gitdir / name).exists():
            return label
    return None


def git_dir() -> Path:
    return Path(out("rev-parse", "--absolute-git-dir"))


def status_entries() -> list[tuple[str, str]]:
    """Return (XY, path) from porcelain v1 -z."""
    raw = git("status", "--porcelain=v1", "-z", "--untracked-files=all").stdout
    parts, res, i = raw.split("\0"), [], 0
    while i < len(parts):
        e = parts[i]
        if not e:
            i += 1
            continue
        xy, path = e[:2], e[3:]
        res.append((xy, path))
        i += 2 if xy[0] in "RC" else 1  # renames carry an extra source path
    return res


def changed_paths(staged: bool) -> list[str]:
    args = ["diff", "--name-only", "-z"] + (["--cached"] if staged else [])
    return [p for p in git(*args).stdout.split("\0") if p]


def ignored(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(os.path.basename(path), p) for p in patterns)


# ---------------------------------------------------------------- diff
def cmd_diff(a, cfg):
    paths = changed_paths(a.staged)
    if a.paths:
        paths = [p for p in paths if any(p == q or p.startswith(q.rstrip("/") + "/") for q in a.paths)]
    if not paths:
        print("no changes" + (" staged" if a.staged else ""))
        return OK
    skipped, shown, total = [], [], 0
    base = ["diff", "--no-color", "--no-ext-diff", "-U2"] + (["--cached"] if a.staged else [])
    for p in paths:
        if kit_managed(p):
            skipped.append(p + " (kit-managed)")
            continue
        if ignored(p, cfg["diff_ignore"]):
            skipped.append(p)
            continue
        d = git(*base, "--", p).stdout.splitlines()
        if any(l.startswith("Binary files") for l in d[:6]):
            skipped.append(p + " (binary)")
            continue
        body = [l for l in d if not l.startswith(("diff --git", "index "))]
        if len(body) > DIFF_MAX_PER_FILE:
            body = body[:DIFF_MAX_PER_FILE] + [f"… [{len(d) - DIFF_MAX_PER_FILE} lines truncated in {p}]"]
        if total + len(body) > a.max_lines:
            skipped.append(p + " (budget)")
            continue
        shown += body
        total += len(body)
    print("\n".join(shown))
    if skipped:
        print(f"\n# skipped {len(skipped)}: " + ", ".join(skipped[:15]) + (" …" if len(skipped) > 15 else ""))
        print("# view one with: gitkit.py diff" + (" --staged" if a.staged else "") + " <path>")
    return OK


# ---------------------------------------------------------------- kit-managed files
KIT_MANAGED = ("notes/", ".agentkit/", "prompts/runs/")


def kit_managed(p: str) -> bool:
    """Files the kit writes itself; scan and diff ignore them."""
    return p.startswith(KIT_MANAGED) or p in (".agentkit.json", "experiments/runs.jsonl")


# ---------------------------------------------------------------- lint-msg
HEADER_RE = re.compile(r"^(?P<type>[a-z]+)(\((?P<scope>[a-z0-9._/-]+)\))?(?P<bang>!)?: (?P<subject>.+)$")
PAST_TENSE = re.compile(r"^(added|fixed|updated|removed|changed|created|refactored|implemented|adds|fixes|updates|removes)\b", re.I)


GIT_GENERATED = ("Merge ", "Revert \"", "fixup! ", "squash! ", "amend! ")
SCISSORS_RE = re.compile(r"^# -+ >8 -+$")


def cmd_lint_msg(a, cfg):
    msg = a.message if a.message is not None else Path(a.file).read_text(encoding="utf-8")
    lines = []
    for l in msg.splitlines():
        if SCISSORS_RE.match(l):  # `git commit -v` appends the diff after this line
            break
        if not l.startswith("#"):
            lines.append(l)
    while lines and not lines[-1].strip():
        lines.pop()
    errs, warns = [], []
    if not lines:
        print("BLOCK: empty message")
        return BLOCK
    h = lines[0]
    m = HEADER_RE.match(h)
    if h.startswith(GIT_GENERATED):
        print("OK: git-generated merge/revert/fixup/squash header")
        return OK
    if not m:
        errs.append("header must be `type(scope)?: subject` (lowercase type, colon, one space)")
    else:
        if m["type"] not in cfg["commit_types"]:
            errs.append(f"type '{m['type']}' not in {cfg['commit_types']}")
        if m["scope"] and cfg["scopes"] and m["scope"] not in cfg["scopes"]:
            (errs if cfg["strict_scopes"] else warns).append(f"scope '{m['scope']}' not in {cfg['scopes']}")
        s = m["subject"]
        if s.endswith("."):
            errs.append("subject must not end with '.'")
        if s[:1].isupper() and not s[:2].isupper():
            warns.append("subject should start lowercase (unless acronym)")
        if PAST_TENSE.match(s):
            errs.append(f"use imperative mood ('add', not '{s.split()[0]}')")
        if len(s.split()) < 2:
            warns.append("subject is one word — say what changed")
        if re.search(r"\b(stuff|things|misc|wip|update code|fix bug|changes)\b", s, re.I):
            errs.append("subject is vague — name the concrete change")
    if len(h) > cfg["subject_max"]:
        errs.append(f"header is {len(h)} chars (max {cfg['subject_max']})")
    if len(lines) > 1 and lines[1].strip():
        errs.append("line 2 must be blank")
    for i, l in enumerate(lines[2:], start=3):
        if len(l) > cfg["body_wrap"] and not re.match(r"^\s*(https?://|[\w-]+: )", l):
            warns.append(f"line {i} is {len(l)} chars (wrap at {cfg['body_wrap']})")
    if m and m["bang"] and not any(l.startswith("BREAKING CHANGE:") for l in lines):
        warns.append("'!' used — add a `BREAKING CHANGE: …` footer explaining migration")
    for e in errs:
        print("ERROR:", e)
    for w in warns:
        print("WARN:", w)
    if errs:
        return BLOCK
    print("OK" if not warns else "OK with warnings")
    return WARN if warns else OK


# ---------------------------------------------------------------- scan
SECRET_PATTERNS = [
    ("private key", re.compile(r"-----BEGIN (RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY")),
    ("AWS access key", re.compile(r"\b(AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("GitHub token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    ("OpenAI/Anthropic key", re.compile(r"\bsk-(ant-)?[A-Za-z0-9_-]{20,}\b")),
    ("Hugging Face token", re.compile(r"\bhf_[A-Za-z0-9]{30,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
    ("Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}\b")),
    ("JWT", re.compile(r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}")),
    ("hardcoded credential", re.compile(
        r"(?i)\b(api[_-]?key|secret|passw(or)?d|token|access[_-]?key)\b\s*[:=]\s*['\"][^'\"\s]{12,}['\"]")),
    ("connection string w/ password", re.compile(r"\b[a-z+]+://[^/\s:@]+:[^/\s@]{4,}@")),
]
# Judged on the matched value only: a getenv()/os.environ call on the same line exempts nothing, since a key passed as
# its default value is still committed.
PLACEHOLDER = re.compile(r"(?i)(xxx|your[_-]|example|changeme|<[^>]*>|\$\{)")
BAD_FILES = [".env", ".env.*", "*.pem", "*.key", "id_rsa*", "*.p12", "*.pfx", "credentials.json",
             "service-account*.json", "*.sqlite", "*.db"]
ALLOW_FILES = [".env.example", ".env.sample", ".env.template"]


def secret_hits(text: str) -> list[tuple[str, str]]:
    """(label, matched value) for every secret-pattern match whose value is not itself a placeholder."""
    return [(label, mt.group(0)) for label, rx in SECRET_PATTERNS for mt in rx.finditer(text)
            if not PLACEHOLDER.search(mt.group(0))]


def cmd_scan(a, cfg):
    root = repo_root()
    paths = [p for p in changed_paths(a.staged) if not kit_managed(p)]
    if not a.staged:
        paths += [p for xy, p in status_entries() if xy == "??" and not kit_managed(p)]
    findings, warns = [], []
    for p in paths:
        base = os.path.basename(p)
        if any(fnmatch.fnmatch(base, g) for g in BAD_FILES) and base not in ALLOW_FILES:
            findings.append(f"{p}: sensitive file type — add to .gitignore and unstage")
        fp = root / p
        if fp.is_file():
            mb = fp.stat().st_size / 1_048_576
            if mb > cfg["max_file_mb"]:
                findings.append(f"{p}: {mb:.1f} MB > {cfg['max_file_mb']} MB — use Git LFS/DVC or ignore it")
    diff = git("diff", "--no-color", "-U0", *(["--cached"] if a.staged else [])).stdout
    cur = None
    for l in diff.splitlines():
        if l.startswith("+++ b/"):
            cur = l[6:]
        elif l.startswith("+") and not l.startswith("+++") and cur and not kit_managed(cur):
            for label, value in secret_hits(l):
                findings.append(f"{cur}: possible {label}: {value[:12]}…")
    if not a.staged:
        for p in [p for xy, p in status_entries() if xy == "??" and not kit_managed(p)]:
            fp = root / p
            if fp.is_file() and fp.stat().st_size < 1_000_000:
                for label, value in secret_hits(fp.read_text(errors="ignore")):
                    findings.append(f"{p}: possible {label}: {value[:12]}…")
    for f in dict.fromkeys(findings):
        print("BLOCK:", f)
    if findings:
        print("Fix: remove the value, load it from env/secret manager, `git restore --staged <file>`.")
        print("If it is a false positive, tell the user and let THEM decide.")
        return BLOCK
    print(f"OK: scanned {len(paths)} file(s), no secrets/oversized files")
    return OK


# ---------------------------------------------------------------- branch-name
def slugify(s: str, maxlen: int = 40) -> str:
    s = unicodedata.normalize("NFKD", s.replace("đ", "d").replace("Đ", "D"))
    s = "".join(c for c in s if not unicodedata.combining(c)).lower()
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:maxlen].rstrip("-")


def cmd_branch_name(a, cfg):
    if a.check:
        n = a.check
        errs = []
        if git("check-ref-format", "--branch", n).returncode != 0:
            errs.append("invalid git ref name")
        t = n.split("/")[0] if "/" in n else None
        if t not in cfg["branch_types"]:
            errs.append(f"prefix must be one of {cfg['branch_types']}/")
        if n != n.lower() or " " in n:
            errs.append("use lowercase-kebab-case")
        for e in errs:
            print("ERROR:", e)
        if not errs:
            print("OK")
        return BLOCK if errs else OK
    if not a.type or not a.desc:
        print("usage: branch-name TYPE DESC [--ticket T]  |  branch-name --check NAME")
        return BLOCK
    if a.type not in cfg["branch_types"]:
        print(f"ERROR: type must be one of {cfg['branch_types']}")
        return BLOCK
    ticket = (slugify(a.ticket) + "-") if a.ticket else ""
    name = cfg["branch_pattern"].format(type=a.type, ticket=ticket, slug=slugify(" ".join(a.desc)))
    exists = git("show-ref", "--verify", "--quiet", f"refs/heads/{name}").returncode == 0
    print(name)
    if exists:
        print(f"WARN: branch '{name}' already exists")
        return WARN
    return OK


# ---------------------------------------------------------------- preflight
def ref_exists(ref: str) -> bool:
    return git("rev-parse", "--verify", "--quiet", ref + "^{commit}").returncode == 0


def predict_conflicts(target: str) -> list[str] | None:
    """Use merge-tree (no worktree changes). None = cannot predict (old git)."""
    p = git("merge-tree", "--write-tree", "--name-only", "--no-messages", "HEAD", target)
    if p.returncode == 0:
        return []
    if p.returncode == 1:
        return [l for l in p.stdout.splitlines()[1:] if l.strip()]
    return None


def cmd_preflight(a, cfg):
    repo_root()
    gd, br = git_dir(), current_branch()
    ent = status_entries()
    dirty = [p for xy, p in ent if xy != "??"]
    blocks, warns = [], []
    op = in_progress(gd)
    if op and a.op != "commit":
        blocks.append(f"{op} in progress — `git {op.split('/')[0]} --continue` or `--abort` first")
    up = out("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}")

    if a.op == "commit":
        if not changed_paths(True):
            blocks.append("nothing staged — stage explicit paths with `git add <path>`")
        if is_protected(br, cfg):
            mode = cfg["protected_commit"]
            if mode == "block":
                blocks.append(f"'{br}' is protected — create a branch: gitkit.py branch-name <type> <desc>")
            elif mode != "allow":
                warns.append(f"'{br}' is protected — ASK USER: commit directly to '{br}', "
                             "or create a task branch (gitkit.py branch-name <type> <desc>)?")
        if br == "(detached)":
            blocks.append("detached HEAD — commits will be orphaned; create a branch first")
        if any("U" in xy for xy, _ in ent):
            blocks.append("unresolved conflicts present")
    elif a.op == "switch":
        if not a.arg:
            blocks.append("switch needs a target branch")
        if dirty:
            warns.append(f"{len(dirty)} uncommitted change(s) — commit or `git stash push -m \"<why>\"` first")
    elif a.op in ("merge", "rebase"):
        if not a.arg or not ref_exists(a.arg):
            blocks.append(f"target '{a.arg}' does not exist (try `git fetch` first)")
        if dirty:
            blocks.append(f"{len(dirty)} uncommitted change(s) — commit or stash before {a.op}")
        if a.op == "rebase" and up and out("rev-list", "--count", f"{up}..HEAD") != "0" and \
                git("ls-remote", "--exit-code", "--heads", "origin", br).returncode == 0:
            warns.append(f"'{br}' is already pushed — rebase rewrites shared history; needs force-with-lease push")
        if a.op == "rebase" and is_protected(br, cfg):
            blocks.append(f"never rebase protected branch '{br}'")
        if a.arg and ref_exists(a.arg) and not blocks:
            c = predict_conflicts(a.arg)
            if c is None:
                warns.append("cannot predict conflicts (git < 2.38)")
            elif c:
                warns.append(f"{len(c)} file(s) will conflict: " + ", ".join(c[:10]))
            behind = out("rev-list", "--count", f"HEAD..{a.arg}")
            if behind == "0":
                print(f"NOTE: already up to date with {a.arg}")
    elif a.op == "push":
        if br == "(detached)":
            blocks.append("detached HEAD")
        if is_protected(br, cfg):
            warns.append(f"pushing directly to protected '{br}' — confirm with user / prefer PR")
        if up:
            behind = out("rev-list", "--count", f"HEAD..{up}")
            if behind not in ("", "0"):
                blocks.append(f"behind {up} by {behind} — pull/rebase first (never --force on shared branches)")
        else:
            print(f"NOTE: no upstream — use `git push -u origin {br}`")
    elif a.op == "pull":
        if dirty:
            warns.append("uncommitted changes — pull may fail or conflict; stash first")
        if not up:
            blocks.append("no upstream configured")
    elif a.op == "stash-pop":
        if not out("stash", "list"):
            blocks.append("no stashes")
        if dirty:
            warns.append("working tree dirty — pop may conflict; prefer `git stash apply` then drop after success")
    for b in blocks:
        print("BLOCK:", b)
    for w in warns:
        print("WARN:", w)
    if blocks:
        return BLOCK
    if warns:
        return WARN
    print(f"OK: {a.op} is safe on '{br}'")
    return OK


# ---------------------------------------------------------------- main
def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="gitkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("diff"); s.add_argument("--staged", action="store_true"); s.add_argument("--max-lines", type=int, default=DIFF_MAX_LINES); s.add_argument("paths", nargs="*")
    s = sp.add_parser("lint-msg"); s.add_argument("file", nargs="?"); s.add_argument("-m", "--message")
    s = sp.add_parser("scan"); s.add_argument("--staged", action="store_true")
    s = sp.add_parser("branch-name"); s.add_argument("type", nargs="?"); s.add_argument("desc", nargs="*"); s.add_argument("--ticket"); s.add_argument("--check")
    s = sp.add_parser("preflight"); s.add_argument("op", choices=["commit", "switch", "merge", "rebase", "push", "pull", "stash-pop"]); s.add_argument("arg", nargs="?")
    a = ap.parse_args()
    if a.cmd == "lint-msg" and a.file is None and a.message is None:
        ap.error("lint-msg needs FILE or -m MSG")
    cfg = load_cfg(repo_root())
    fn = {"diff": cmd_diff, "lint-msg": cmd_lint_msg, "scan": cmd_scan,
          "branch-name": cmd_branch_name, "preflight": cmd_preflight}[a.cmd]
    return fn(a, cfg)


if __name__ == "__main__":
    sys.exit(main())
