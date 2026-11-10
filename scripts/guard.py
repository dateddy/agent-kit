#!/usr/bin/env python3
"""guard.py - the logic behind the agent-kit guards. Stdlib only, Python 3.9+.

  guard.py bash [COMMAND...]     judge a shell command (git rules); no arguments = Claude Code PreToolUse JSON on stdin

Exit-code convention: see scripts/guard-git.sh (hook mode: 0 allow/ask, 2 BLOCK; argument mode: 0 allow/ask, 1 BLOCK).
stdout carries nothing but the "ask" JSON; every other message goes to stderr.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from typing import Any, Optional

SEP = "\x1f"  # command separator inside a token list; same character as the bash version


def tokenize(s: str) -> list[str]:
    """Quote-aware words of s, with SEP between commands (&& || ; | & newline ( ) and backtick)."""
    toks: list[str] = []
    cur: list[str] = []
    q = ""
    have = False
    i, n = 0, len(s)

    def flush() -> None:
        nonlocal cur, have
        if have:
            toks.append("".join(cur))
        cur, have = [], False

    while i < n:
        c = s[i]
        if q:
            if c == q:
                q = ""
            elif q == '"' and c == "\\" and i + 1 < n:
                i += 1
                cur.append(s[i])
            else:
                cur.append(c)
        elif c in "'\"":
            q, have = c, True
        elif c == "\\":
            if i + 1 < n:
                i += 1
                cur.append(s[i])
                have = True
        elif c in " \t":
            flush()
        elif c in "\n;()`":
            flush()
            toks.append(SEP)
        elif c in "&|":
            flush()
            toks.append(SEP)
            if s[i + 1:i + 2] == c:
                i += 1
        else:
            cur.append(c)
            have = True
        i += 1
    flush()
    return toks


def segments(toks: list[str]) -> list[list[str]]:
    """Split a token list on SEP; empty segments are dropped."""
    out: list[list[str]] = []
    seg: list[str] = []
    for t in toks + [SEP]:
        if t == SEP:
            if seg:
                out.append(seg)
            seg = []
        else:
            seg.append(t)
    return out


# ---------------------------------------------------------------- git and config (lib.sh parity)
DEFAULT_PROTECTED = "main\nmaster\ndevelop\nrelease/*"  # same default as protected_list() in lib.sh


def git(args: list[str], cwd: Optional[str] = None) -> tuple[int, str]:
    """Run git; (returncode, stdout without trailing newlines). 127 when git is not installed."""
    try:
        p = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return 127, ""
    return p.returncode, p.stdout.rstrip("\n")


def current_branch(dir: Optional[str] = None) -> str:
    """Short branch name of HEAD (of `git -C dir` when given); "" when detached or not a repo."""
    rc, out = git((["-C", dir] if dir else []) + ["symbolic-ref", "--quiet", "--short", "HEAD"])
    return out if rc == 0 else ""


def branch_exists(name: str) -> bool:
    return git(["rev-parse", "--verify", "-q", f"refs/heads/{name}"])[0] == 0


class _Num(float):
    """A JSON float that remembers its literal: jq 1.7 prints 1.0 and 1E+3 the way they were written."""

    def __new__(cls, lit: str) -> "_Num":
        self = super().__new__(cls, lit)
        self.lit = lit
        return self


def _jq_tostring(v: Any) -> str:
    """jq's tostring: strings as is, everything else as compact JSON."""
    if isinstance(v, str):
        return v
    if isinstance(v, _Num):
        return v.lit
    return json.dumps(v, separators=(",", ":"), ensure_ascii=False)


class Config:
    """Values of <repo>/.agentkit.json with the semantics of cfg() in lib.sh.

    `warning` is set when the file exists but jq -e would reject it (not JSON, empty, null or false);
    the defaults are used then.
    """

    def __init__(self, data: Any = None, warning: str = "") -> None:
        self.data = data
        self.warning = warning

    @classmethod
    def from_file(cls, path: str) -> "Config":
        if not os.path.isfile(path):
            return cls()
        try:
            with open(path, encoding="utf-8-sig") as f:
                data = json.load(f, parse_float=_Num)
        except (OSError, ValueError):
            data = None
        if data is None or data is False:
            return cls(warning=f"WARN: {path} is not valid JSON; using defaults")
        return cls(data)

    def cfg(self, path: str, default: str = "") -> str:
        """Scalar as text, array one element per line; the default when the key is absent, null or empty."""
        v: Any = self.data
        for key in path.split("."):
            if not isinstance(v, dict):
                return default
            v = v.get(key)
            if v is None:
                return default
        text = "\n".join(_jq_tostring(x) for x in v) if isinstance(v, list) else _jq_tostring(v)
        text = text.replace("\r", "").rstrip("\n")
        return text or default

    def protected_patterns(self) -> list[str]:
        lst = self.cfg("git.protected_branches", "") or DEFAULT_PROTECTED
        return [p for p in lst.split("\n") if p]

    def is_protected(self, branch: str) -> bool:
        return any(glob_match(p, branch) for p in self.protected_patterns())


def load_config(cwd: Optional[str] = None) -> Config:
    """Config of the repo around cwd (else cwd itself), like AK_ROOT in lib.sh. The WARN goes to stderr here."""
    rc, top = git(["rev-parse", "--show-toplevel"], cwd=cwd)
    root = top if rc == 0 and top else (cwd or os.getcwd())
    c = Config.from_file(os.path.join(root, ".agentkit.json"))
    if c.warning:
        print(c.warning, file=sys.stderr)
    return c


_POSIX_CLASSES = {
    "alpha": "a-zA-Z", "digit": "0-9", "alnum": "a-zA-Z0-9", "upper": "A-Z", "lower": "a-z",
    "space": " \\t\\n\\r\\f\\v", "blank": " \\t", "punct": "!-/:-@\\[-`{-~", "xdigit": "0-9a-fA-F",
}


def _bracket(pattern: str, i: int) -> tuple[Optional[str], int]:
    """Translate the bash bracket expression that starts at pattern[i] == "[". (regex or None if unclosed, next index)."""
    n = len(pattern)
    j = i + 1
    neg = j < n and pattern[j] in "!^"
    if neg:
        j += 1
    items: list[tuple[str, str]] = []  # ("c", char) | ("k", regex class body)
    first = True
    while j < n and (pattern[j] != "]" or first):
        first = False
        if pattern[j] == "[" and pattern[j + 1:j + 2] == ":":
            end = pattern.find(":]", j + 2)
            name = pattern[j + 2:end] if end != -1 else ""
            if end != -1 and name in _POSIX_CLASSES:
                items.append(("k", _POSIX_CLASSES[name]))
                j = end + 2
                continue
        if pattern[j] == "\\" and j + 1 < n:
            j += 1
        items.append(("c", pattern[j]))
        j += 1
    if j >= n:
        return None, i + 1
    parts: list[str] = []
    k = 0
    while k < len(items):
        kind, val = items[k]
        if kind == "c" and k + 2 < len(items) and items[k + 1] == ("c", "-") and items[k + 2][0] == "c":
            hi = items[k + 2][1]
            if val <= hi:  # a reversed range matches nothing
                parts.append(re.escape(val) + "-" + re.escape(hi))
            k += 3
            continue
        parts.append(val if kind == "k" else re.escape(val))
        k += 1
    if not parts:
        return ("." if neg else "(?!)"), j + 1
    return "[" + ("^" if neg else "") + "".join(parts) + "]", j + 1


def glob_match(pattern: str, text: str) -> bool:
    """bash `case` glob: * ? [set] [!set] [^set] and backslash escapes. * also matches "/". Not fnmatch."""
    out: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c == "*":
            out.append(".*")
        elif c == "?":
            out.append(".")
        elif c == "\\" and i + 1 < n:
            out.append(re.escape(pattern[i + 1]))
            i += 1
        elif c == "[":
            rx, nxt = _bracket(pattern, i)
            if rx is None:  # no closing bracket: a literal [
                out.append(re.escape(c))
            else:
                out.append(rx)
                i = nxt - 1
        else:
            out.append(re.escape(c))
        i += 1
    try:
        return re.fullmatch("".join(out), text, re.S) is not None
    except re.error:
        return False


# ---------------------------------------------------------------- decisions and the command walker
MAX_DEPTH = 3  # how many levels of `bash -c` / `eval` are looked into

_ENV_ASSIGN = re.compile(r"[A-Za-z_].*=", re.S)
_SKIP_WORDS = frozenset(("env", "command", "exec", "nohup", "time", "sudo"))
_SHELLS = frozenset(("bash", "sh", "zsh", "dash"))
_SHELL_C_FLAG = re.compile(r"-[A-Za-z].*c", re.S)  # -lc, -ec, ... (plain -c is tested separately)
_GIT_SKIP2 = frozenset(("-c", "--git-dir", "--work-tree", "--namespace", "--super-prefix"))


class Decision:
    """What a rule decided: kind is "allow", "ask" or "block"; message is shown to the model or the user."""

    def __init__(self, kind: str, message: str = "") -> None:
        self.kind = kind
        self.message = message

    def __repr__(self) -> str:
        return f"Decision({self.kind!r}, {self.message!r})"


ALLOW = Decision("allow")


class Judge:
    """Walks a command line and collects the decisions of the rules.

    `branch` is the branch later segments are judged on; `git switch/checkout` earlier in the same
    command line moves it. The first block wins, then the first ask (see decision()).
    """

    def __init__(self, cfg: Config, branch: str) -> None:
        self.cfg = cfg
        self.branch = branch
        self.first_block: Optional[Decision] = None
        self.first_ask: Optional[Decision] = None

    def record(self, d: Decision) -> None:
        if d.kind == "block" and self.first_block is None:
            self.first_block = d
        elif d.kind == "ask" and self.first_ask is None:
            self.first_ask = d

    def decision(self) -> Decision:
        return self.first_block or self.first_ask or ALLOW

    # rules: filled in by the rule functions of guard-git (merge, commit, push)
    def rule_merge(self, b: str, args: list[str]) -> None:
        pass

    def rule_commit(self, b: str) -> None:
        pass

    def rule_push(self, b: str, args: list[str]) -> None:
        pass

    def scan(self, cmd: str, depth: int) -> None:
        """Judge every git command in cmd, in order."""
        for seg in segments(tokenize(cmd)):
            self.check_segment(depth, seg)

    def track_switch(self, sub: str, args: list[str]) -> None:
        """Move self.branch the way `git switch/checkout <args>` would."""
        new = target = ""
        detach = False
        k = 0
        while k < len(args):
            a = args[k]
            k += 1
            if a == "--":
                break
            if a in ("-b", "-B", "-c", "-C", "--create", "--force-create", "--orphan"):
                new = args[k] if k < len(args) else ""
                k += 1
            elif a in ("-d", "--detach"):
                detach = True
            elif a.startswith("-"):
                pass
            elif not target:
                target = a
        if new:
            self.branch = new
        elif detach:
            self.branch = ""
        elif target and (sub == "switch" or branch_exists(target)):
            self.branch = target

    def check_segment(self, depth: int, words: list[str]) -> None:
        w = words
        i = 0
        while i < len(w) and (_ENV_ASSIGN.match(w[i]) or w[i] in _SKIP_WORDS):
            i += 1
        if i >= len(w):
            return
        prog = w[i].rsplit("/", 1)[-1]
        if prog in _SHELLS:
            for j in range(i + 1, len(w)):
                if (w[j] == "-c" or _SHELL_C_FLAG.fullmatch(w[j])) and j + 1 < len(w) and depth < MAX_DEPTH:
                    self.scan(w[j + 1], depth + 1)
            return
        if prog == "eval":
            if depth < MAX_DEPTH:
                self.scan(" ".join(w[i + 1:]), depth + 1)
            return
        if prog not in ("git", "git.exe"):
            return
        i += 1
        git_dir = ""
        while i < len(w):
            if w[i] == "-C":
                git_dir = w[i + 1] if i + 1 < len(w) else ""
                i += 2
            elif w[i] in _GIT_SKIP2:
                i += 2
            elif w[i].startswith("-"):
                i += 1
            else:
                break
        if i >= len(w):
            return
        sub, args = w[i], w[i + 1:]
        b = self.branch if not git_dir else current_branch(git_dir)
        if sub in ("switch", "checkout"):
            if not git_dir:
                self.track_switch(sub, args)
        elif sub == "merge":
            if b and self.cfg.is_protected(b):
                self.rule_merge(b, args)
        elif sub == "commit":
            if b and self.cfg.is_protected(b):
                self.rule_commit(b)
        elif sub == "push":
            self.rule_push(b, args)
