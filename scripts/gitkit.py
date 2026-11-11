#!/usr/bin/env python3
"""gitkit: the two checks behind the agent-kit git hooks. Stdlib only.

  gitkit.py scan --staged        pre-commit: block secrets, .env/key files and oversized files
  gitkit.py lint-msg FILE|-m MSG commit-msg: validate a Conventional Commit message (body may carry `Assumptions:`)

Exit codes: 0 OK, 1 BLOCK (the hook rejects the commit), 2 WARN (the hook lets it through).
Config: the `git` section of .agentkit.json (commit_types, scopes, strict_scopes, subject_max, body_wrap, max_file_mb).
Installed to .agentkit/scripts/gitkit.py by `install.py --git-hooks`; the hooks call it from there.
"""
from __future__ import annotations

import argparse
import fnmatch
import json
import os
import re
import subprocess
import sys
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2

DEFAULTS = {
    "commit_types": ["feat", "fix", "docs", "style", "refactor", "perf", "test", "build", "ci", "chore", "revert"],
    "scopes": [],
    "strict_scopes": False,
    # 72 keeps `git log --oneline` readable in an 80-col terminal with the hash prefix
    "subject_max": 72,
    "body_wrap": 100,
    # GitHub warns at 50 MB; 5 MB catches accidental datasets/checkpoints early
    "max_file_mb": 5,
}


# ---------------------------------------------------------------- utilities
def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")


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
            cfg.update({k: v for k, v in user.items() if not k.startswith("_") and k in DEFAULTS})
        except json.JSONDecodeError as e:
            print(f"WARN: .agentkit.json is invalid JSON ({e}); using defaults")
    return cfg


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


# ---------------------------------------------------------------- kit-managed files
KIT_MANAGED = ("notes/", ".agentkit/")


def kit_managed(p: str) -> bool:
    """Files the kit writes itself; scan ignores them."""
    return p.startswith(KIT_MANAGED) or p == ".agentkit.json"


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
    findings = []
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


# ---------------------------------------------------------------- main
def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="gitkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("lint-msg")
    s.add_argument("file", nargs="?")
    s.add_argument("-m", "--message")
    s = sp.add_parser("scan")
    s.add_argument("--staged", action="store_true")
    a = ap.parse_args()
    if a.cmd == "lint-msg" and a.file is None and a.message is None:
        ap.error("lint-msg needs FILE or -m MSG")
    cfg = load_cfg(repo_root())
    return {"lint-msg": cmd_lint_msg, "scan": cmd_scan}[a.cmd](a, cfg)


if __name__ == "__main__":
    sys.exit(main())
