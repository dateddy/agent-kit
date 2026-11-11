#!/usr/bin/env python3
"""guard.py - the logic behind the agent-kit guards. Stdlib only, Python 3.9+.

  guard.py bash [COMMAND...]     judge a shell command (git rules); no arguments = Claude Code PreToolUse JSON on stdin
  guard.py edit [PATH]           judge a file edit (writes outside the repo); no argument = PreToolUse JSON on stdin

Exit-code convention: see scripts/guard-git.sh (hook mode: 0 allow/ask, 2 BLOCK; argument mode: 0 allow/ask, 1 BLOCK).
stdout carries nothing but the "ask" JSON; every other message goes to stderr.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from typing import Any, Optional, Tuple

SEP = "\x1f"  # command separator inside a token list; same character as the bash version


Tok = Tuple[str, str]  # (kind, text); kind: word | op | redir | redir_in | dup


def lex(s: str) -> list[Tok]:
    """Quote-aware tokens of s. Kinds: word; op (&& || ; | |& & newline ( ) backtick, text kept);
    redir (> >> >| >& 2> 2>> &> &>>: its target is the next word token); redir_in (< << <<<);
    dup (2>&1, >&2: fd duplication, no file target, not a separator)."""
    toks: list[Tok] = []
    cur: list[str] = []
    q = ""
    have = False
    i, n = 0, len(s)

    def flush() -> None:
        nonlocal cur, have
        if have:
            toks.append(("word", "".join(cur)))
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
            toks.append(("op", c))
        elif c == ">" or (c == "&" and s[i + 1:i + 2] == ">"):
            fd = "".join(cur) if have and "".join(cur).isdigit() and c == ">" else ""
            if fd:
                cur, have = [], False
            else:
                flush()
            op = fd + c
            if c == "&":  # &> and &>>
                i += 1
                op += ">"
            if s[i + 1:i + 2] == ">":
                i += 1
                op += ">"
            elif s[i + 1:i + 2] == "|" and not c == "&":
                i += 1
                op += "|"
            elif s[i + 1:i + 2] == "&" and c == ">":
                m = re.match(r"&(\d+|-)", s[i + 1:])
                if m:  # >&2, 2>&1, >&-: duplicate or close an fd
                    i += len(m.group(0))
                    toks.append(("dup", op + m.group(0)))
                    i += 1
                    continue
                i += 1
                op += "&"
            toks.append(("redir", op))
        elif c == "<":
            flush()
            op = "<"
            while s[i + 1:i + 2] == "<" and len(op) < 3:
                i += 1
                op += "<"
            toks.append(("redir_in", op))
        elif c in "&|":
            flush()
            nxt = s[i + 1:i + 2]
            if nxt == c:
                i += 1
                toks.append(("op", c + c))
            elif c == "|" and nxt == "&":
                i += 1
                toks.append(("op", "|&"))
            else:
                toks.append(("op", c))
        else:
            cur.append(c)
            have = True
        i += 1
    flush()
    return toks


def tokenize(s: str) -> list[str]:
    """Quote-aware words of s, with SEP between commands (&& || ; | & newline ( ) and backtick).

    A projection of lex(): operators become SEP (|& is two), redirect operators stay as words, fd duplications are dropped.
    """
    out: list[str] = []
    for kind, text in lex(s):
        if kind == "op":
            out.extend([SEP, SEP] if text == "|&" else [SEP])
        elif kind != "dup":
            out.append(text)
    return out


def seg_ops(s: str) -> list[tuple[list[str], list[tuple[str, str]], str]]:
    """Segments of s as (words, redirects [(op, target)], operator that ends the segment, "" for the last).

    Output redirects leave `words` and become (op, target); an input redirect's target stays a word. Empty segments are dropped.
    """
    out: list[tuple[list[str], list[tuple[str, str]], str]] = []
    words: list[str] = []
    redirs: list[tuple[str, str]] = []
    pending = ""  # output redirect operator waiting for its target

    def close(op: str) -> None:
        nonlocal words, redirs, pending
        if pending:
            redirs.append((pending, ""))
            pending = ""
        if words or redirs:
            out.append((words, redirs, op))
        words, redirs = [], []

    for kind, text in lex(s):
        if kind == "op":
            close(text)
        elif kind == "redir":
            if pending:
                redirs.append((pending, ""))
            pending = text
        elif kind == "word":
            if pending:
                redirs.append((pending, text))
                pending = ""
            else:
                words.append(text)
    close("")
    return out


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

    def cfg_list(self, path: str, default: list[str]) -> list[str]:
        """The JSON array at path as a list of strings (an explicit empty list stays empty); the default when it is not an array."""
        v: Any = self.data
        for key in path.split("."):
            if not isinstance(v, dict):
                return list(default)
            v = v.get(key)
        return [_jq_tostring(x) for x in v] if isinstance(v, list) else list(default)

    def protected_patterns(self) -> list[str]:
        lst = self.cfg("git.protected_branches", "") or DEFAULT_PROTECTED
        return [p for p in lst.split("\n") if p]

    def is_protected(self, branch: str) -> bool:
        return any(glob_match(p, branch) for p in self.protected_patterns())


def load_config(cwd: Optional[str] = None, root: Optional[str] = None) -> Config:
    """Config of the repo around cwd (else cwd itself), like AK_ROOT in lib.sh; root skips the lookup. The WARN goes to stderr here."""
    if root is None:
        rc, top = git(["rev-parse", "--show-toplevel"], cwd=cwd)
        root = top if rc == 0 and top else (cwd or os.getcwd())
    c = Config.from_file(root.rstrip("/\\") + "/.agentkit.json")  # "/" like the bash version; Windows accepts it
    if c.warning:
        _out(sys.stderr, c.warning + "\n")
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

    def __init__(self, cfg: Config, branch: str, cwd: Optional[str] = None) -> None:
        self.cfg = cfg
        self.branch = branch
        self.cwd0 = cwd or os.getcwd()  # where the command starts; `cd <literal>` moves self.cwd
        self.cwd = self.cwd0
        self.cwd_known = True
        self._repo: Optional[RepoInfo] = None
        self._rules: Optional[FsRules] = None
        self.first_block: Optional[Decision] = None
        self.first_ask: Optional[Decision] = None

    def record(self, d: Decision) -> None:
        if d.kind == "block" and self.first_block is None:
            self.first_block = d
        elif d.kind == "ask" and self.first_ask is None:
            self.first_ask = d

    def decision(self) -> Decision:
        return self.first_block or self.first_ask or ALLOW

    # rules (functions below); tests replace these three to record how they are called
    def rule_merge(self, b: str, args: list[str]) -> None:
        self.record(guard_merge(self.cfg, b, args))

    def rule_commit(self, b: str) -> None:
        self.record(guard_commit(self.cfg, b))

    def rule_push(self, b: str, args: list[str]) -> None:
        self.record(guard_push(self.cfg, b, args))

    def scan(self, cmd: str, depth: int) -> None:
        """Judge every git command in cmd, in order."""
        for words, redirs, op in seg_ops(cmd):
            self.check_segment(depth, words, op, redirs)

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

    # ---- fs rules (1b): writes outside the repo, protected files, cd tracking
    def repo(self) -> "RepoInfo":
        if self._repo is None:
            self._repo = RepoInfo(self.cwd0)
        return self._repo

    def rules(self) -> "FsRules":
        if self._rules is None:
            self._rules = FsRules(self.cfg, self.repo())
        return self._rules

    def do_cd(self, args: list[str]) -> None:
        """Follow `cd <literal>`; a variable, `cd -` or a bare `cd` makes the cwd unknown until the next absolute cd."""
        target = next((a for a in args if not a.startswith("-") or a == "-"), "")
        if not target or target == "-" or _has_variable(target):
            self.cwd_known = False
        elif self.cwd_known or _anchored(target):
            self.cwd = resolve_path(target, self.cwd)
            self.cwd_known = True

    def check_target(self, target: str, label: str, destructive: bool) -> None:
        """A path a command writes or deletes. Unresolvable targets matter only for destructive commands (ask)."""
        if not target or target in _DEVICE_OK:
            return
        if _has_variable(target):
            if destructive:
                self.record(_destructive(self.cfg, label, f"its target has a variable the guard cannot resolve: {target}"))
            return
        if not self.cwd_known and not _anchored(target):
            if destructive:
                self.record(_destructive(self.cfg, label, f"its target '{target}' is relative to a directory the guard lost track of (cd with a variable or -)"))
            return
        self.record(write_decision(resolve_path(target, self.cwd), self.repo(), self.cfg, self.rules()))

    def check_reads(self, words: list[str]) -> None:
        """Any word that resolves to a path matching fs.deny_read (and not fs.allow_read) is blocked."""
        for word in words:
            if word.startswith("-") and "=" not in word:
                continue
            cands = [word, word.split("=", 1)[1]] if word.startswith("-") else [word]
            for c in cands:
                if not c or not _pathlike(c) or _has_variable(c) or (not self.cwd_known and not _anchored(c)):
                    continue
                path = resolve_path(c, self.cwd)
                if self.rules().denies_read(path):
                    self.record(_block(f"BLOCK: reads a protected file: {path} (fs.deny_read). The user has to read it themselves, "
                                       "or take it out of fs.deny_read / add it to fs.allow_read."))
                    return

    def check_fs(self, w: list[str], i: int, redirs: list[tuple[str, str]], op: str = "") -> None:
        """The fs rules for the command that starts at w[i]; redirs are its output redirects; op ends the segment."""
        prog = _base(w[i])
        if prog == "cd":
            self.do_cd(w[i + 1:])
            return
        if prog in ("pushd", "popd"):
            self.cwd_known = False
            return
        for t, destructive in write_targets(prog, w[i + 1:]):
            self.check_target(t, prog, destructive)
        if op == "`" and prog in _DESTRUCTIVE_PROGS:  # the lexer cuts a command at a backtick: what follows is not seen
            self.record(_destructive(self.cfg, prog, "a command substitution (`...`) may supply its target, which the guard cannot resolve"))
        self.check_reads(w[i:] + [t for _, t in redirs])

    def check_redirects(self, redirs: list[tuple[str, str]]) -> None:
        for op, target in redirs:
            self.check_target(target, f"redirect {op}", destructive=op not in (">>", "2>>", "&>>"))

    def check_segment(self, depth: int, words: list[str], op: str = "", redirs: Optional[list[tuple[str, str]]] = None) -> None:
        """Judge one command; op is the operator that ends it (| |& && ; ...), "" for the last one."""
        w = words
        i = 0
        while i < len(w) and (_ENV_ASSIGN.match(w[i]) or w[i] in _SKIP_WORDS):
            i += 1
        if redirs:
            self.check_redirects(redirs)
        if i >= len(w):
            return
        self.check_fs(w, i, redirs or [], op)
        prog = w[i].rsplit("/", 1)[-1]
        if op in ("|", "|&") and is_verify_command(w[i:]):
            self.record(guard_verify_pipe())
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
        hooks_override = ""
        while i < len(w):
            if w[i] == "-C":
                git_dir = w[i + 1] if i + 1 < len(w) else ""
                i += 2
            elif w[i] in _GIT_SKIP2:
                if w[i] == "-c" and i + 1 < len(w) and w[i + 1].lower().split("=", 1)[0] == "core.hookspath":
                    hooks_override = w[i + 1]
                i += 2
            elif w[i].startswith("-"):
                i += 1
            else:
                break
        if i >= len(w):
            return
        sub, args = w[i], w[i + 1:]
        b = self.branch if not git_dir else current_branch(git_dir)
        if hooks_override:
            self.record(_block(f"BLOCK: 'git -c {hooks_override}' redirects the git hooks. Do not bypass the hooks."))
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
        # 1b rules run last: where an older rule already asks, its message is the one shown
        self.record(guard_hooks_bypass(sub, args))
        where = os.path.join(self.cwd, git_dir) if self.cwd and git_dir else (git_dir or self.cwd or None)
        self.record(guard_destructive(self.cfg, sub, args, where))
        if sub == "push":
            self.record(guard_force_push(self.cfg, b, args))


# ---------------------------------------------------------------- rules (messages are verbatim from guard-git.sh)
def _block(msg: str) -> Decision:
    return Decision("block", msg)


def protected_policy(cfg: Config) -> str:
    """git.protected_commit: "allow" | "block" | anything else means "ask"."""
    v = cfg.cfg("git.protected_commit", "ask")
    return v if v in ("allow", "block") else "ask"


def merge_hint(cfg: Config, branch: str) -> str:
    conv = cfg.cfg("guards.merge_convention", "")
    text = (f"Convention: {conv}" if conv else "") + f"\nInspect the convention first: git log --first-parent --merges -5 {branch} --oneline"
    return "\n".join(line for line in text.split("\n") if line)


def guard_merge(cfg: Config, b: str, args: list[str]) -> Decision:
    """`git merge` on protected branch b: needs --no-ff; --ff-only and --squash are always blocked."""
    if any(a in ("--abort", "--continue", "--quit") for a in args):
        return ALLOW
    why = ""
    for a in args:
        if a == "--squash":
            why = "--squash is not allowed"
        elif a == "--ff-only" and not why:
            why = "--ff-only is not allowed"
    if not why:
        if "--no-ff" in args:
            return ALLOW
        why = "a plain 'git merge' can fast-forward"
    return _block(f"BLOCK: {why} on protected branch '{b}'. Use: git merge --no-ff <branch>\n{merge_hint(cfg, b)}")


def guard_commit(cfg: Config, b: str) -> Decision:
    policy = protected_policy(cfg)
    if policy == "allow":
        return ALLOW
    if policy == "block":
        return _block(f"BLOCK: commit on protected branch '{b}' (git.protected_commit=block). Create a task branch: git switch -c <type>/<name>")
    return Decision("ask", f"Commit on protected branch '{b}'. Approve only if the user asked for it; otherwise create a task branch first.")


def _push_parse(args: list[str]) -> tuple[bool, list[str]]:
    """(--all/--mirror given, refspecs) of `git push` arguments; the first operand is the remote."""
    remote = ""
    push_all = False
    skip = False
    specs: list[str] = []
    for a in args:
        if skip:
            skip = False
        elif a in ("-o", "--push-option", "--repo", "--receive-pack", "--exec"):
            skip = True
        elif a in ("--all", "--mirror"):
            push_all = True
        elif a.startswith("-"):
            pass
        elif not remote:
            remote = a
        else:
            specs.append(a)
    return push_all, specs


def _push_hit(cfg: Config, b: str, args: list[str]) -> str:
    """The protected branch a `git push` targets ("" when none); b is the branch the push is judged on."""
    push_all, specs = _push_parse(args)
    hit = "every branch (--all/--mirror)" if push_all else ""
    if not specs:
        if b and cfg.is_protected(b):
            hit = b
    else:
        for s in specs:
            if s.startswith("+"):
                s = s[1:]
            dst = s.rsplit(":", 1)[-1]
            if dst == "HEAD":
                dst = b
            if dst.startswith("refs/heads/"):
                dst = dst[len("refs/heads/"):]
            if dst and cfg.is_protected(dst):
                hit = dst
    return hit


def guard_push(cfg: Config, b: str, args: list[str]) -> Decision:
    """`git push` that targets a protected branch (b is the branch the push is judged on)."""
    hit = _push_hit(cfg, b, args)
    if not hit:
        return ALLOW
    policy = protected_policy(cfg)
    if policy == "allow":
        return ALLOW
    if policy == "block":
        return _block(f"BLOCK: push to protected branch '{hit}' (git.protected_commit=block). Push a task branch and merge with --no-ff.")
    return Decision("ask", f"Push to protected branch '{hit}'. Approve only if the user asked for it; prefer pushing a task branch.")


# ---------------------------------------------------------------- 1b rules: hooks, destructive git, force push, verify pipe
_VALUE_NEXT = frozenset("mFcCto")  # short options whose value is the next argument unless it is attached (-m msg, -mmsg)
_VALUE_STOP = frozenset("Su")  # short options with an optional attached value: the rest of the bundle belongs to it
_LONG_VALUE = frozenset(("--message", "--file", "--author", "--date", "--template", "--reuse-message", "--reedit-message",
                         "--fixup", "--squash", "--cleanup", "--push-option", "--repo", "--receive-pack", "--exec"))
_CONFIG_READ = frozenset(("--get", "--get-all", "--get-regexp", "--list", "-l", "--get-urlmatch", "--unset", "--unset-all"))


def parse_opts(args: list[str]) -> tuple[list[str], list[str], Optional[list[str]]]:
    """(flags, operands, words after "--" or None) of git arguments.

    Short bundles are split (-nm -> -n -m) and the value of an option that takes one is skipped, so
    `commit -m -n` has no -n flag. Long flags keep their "=value".
    """
    flags: list[str] = []
    operands: list[str] = []
    after: Optional[list[str]] = None
    k = 0
    while k < len(args):
        a = args[k]
        k += 1
        if a == "--":
            after = list(args[k:])
            break
        if a.startswith("--"):
            flags.append(a)
            if "=" not in a and a in _LONG_VALUE and k < len(args):
                k += 1
        elif a.startswith("-") and len(a) > 1:
            j = 1
            while j < len(a):
                ch = a[j]
                flags.append("-" + ch)
                j += 1
                if ch in _VALUE_STOP:
                    break
                if ch in _VALUE_NEXT:
                    if j >= len(a) and k < len(args):
                        k += 1
                    break
        else:
            operands.append(a)
    return flags, operands, after


def guard_hooks_bypass(sub: str, args: list[str]) -> Decision:
    """--no-verify (commit -n too) and core.hooksPath changes turn the git hooks off."""
    flags, operands, _ = parse_opts(args)
    if "--no-verify" in flags or (sub == "commit" and "-n" in flags):
        return _block(f"BLOCK: 'git {sub}' skips the git hooks (--no-verify / -n). Fix what the hook reports instead of bypassing it.")
    if sub == "config" and not set(flags) & _CONFIG_READ and any(a.lower().split("=", 1)[0] == "core.hookspath" for a in operands):
        return _block("BLOCK: 'git config core.hooksPath' redirects the git hooks. Do not bypass the hooks.")
    return ALLOW


def destructive_policy(cfg: Config) -> str:
    """guards.destructive: "allow" | "block" | anything else means "ask"."""
    v = cfg.cfg("guards.destructive", "ask")
    return v if v in ("allow", "block") else "ask"


def _destructive(cfg: Config, what: str, why: str) -> Decision:
    policy = destructive_policy(cfg)
    if policy == "allow":
        return ALLOW
    if policy == "block":
        return _block(f"BLOCK: destructive command '{what}' ({why}) (guards.destructive=block).")
    return Decision("ask", f"Destructive command '{what}': {why}. Approve only if the user asked for it.")


_GONE_RE = re.compile(r"^[*+ ]\s+(\S+)\s+\S+\s+\[[^\]]*: gone\]")


def branches_gone(names: list[str], cwd: Optional[str] = None) -> bool:
    """True when every name is a local branch whose upstream is gone, per `git branch -vv` (`[origin/x: gone]`).

    That is what /clean_gone deletes: the remote branch was merged and removed. False for no names, an unknown
    branch, or when git fails, so every doubt keeps the question.
    """
    rc, out = git(["-c", "color.branch=never", "branch", "-vv"], cwd)
    if rc != 0:
        return False
    gone = {m.group(1) for m in map(_GONE_RE.match, out.splitlines()) if m}
    return bool(names) and all(n in gone for n in names)


def guard_destructive(cfg: Config, sub: str, args: list[str], cwd: Optional[str] = None) -> Decision:
    """Git commands that throw work away. Which ones: see the table in README (Guards).

    `git branch -D` of local branches whose upstream is gone is allowed (never a protected one, never under
    guards.destructive=block); `cwd` is where `git branch -vv` is asked.
    """
    flags, operands, after = parse_opts(args)
    fl = set(flags)
    why = ""
    if sub == "reset" and fl & {"--hard", "--merge", "--keep"}:
        why = "discards changes in the working tree"
    elif sub == "branch" and ("-D" in fl or (fl & {"-d", "--delete"} and fl & {"-f", "--force"})):
        if (destructive_policy(cfg) != "block" and not fl & {"-r", "--remotes"}
                and not any(cfg.is_protected(n) for n in operands) and branches_gone(operands, cwd)):
            return ALLOW
        why = "deletes a branch even if it is not merged"
    elif sub == "clean" and fl & {"-f", "--force"} and not fl & {"-n", "--dry-run"}:
        why = "deletes untracked files"
    elif sub == "stash" and operands[:1] in (["drop"], ["clear"]):
        why = "drops stashed work"
    elif sub == "checkout" and (after or "." in operands):
        why = "overwrites files in the working tree"
    elif sub == "restore" and (not fl & {"--staged", "-S"} or fl & {"--worktree", "-W"}):
        why = "overwrites files in the working tree"
    elif sub == "push" and (fl & {"--delete", "-d"} or any(s.startswith(":") for s in _push_parse(args)[1])):
        why = "deletes a remote branch or tag"
    elif sub in ("filter-branch", "filter-repo"):
        why = "rewrites history"
    elif sub == "update-ref" and "-d" in fl:
        why = "deletes a ref"
    elif sub == "reflog" and operands[:1] == ["expire"]:
        why = "drops reflog entries"
    elif sub == "gc" and "--prune=now" in fl:
        why = "deletes unreachable objects at once"
    return _destructive(cfg, f"git {sub}", why) if why else ALLOW


def guard_force_push(cfg: Config, b: str, args: list[str]) -> Decision:
    """A force push to a protected branch is always blocked; to any other branch it follows guards.destructive."""
    flags, _, _ = parse_opts(args)
    forced = any(f in ("-f", "--force", "--force-if-includes") or f.startswith("--force-with-lease") for f in flags)
    forced = forced or any(s.startswith("+") for s in _push_parse(args)[1])
    if not forced:
        return ALLOW
    hit = _push_hit(cfg, b, args)
    if hit:
        return _block(f"BLOCK: force push to protected branch '{hit}'. Push a task branch and merge with --no-ff.")
    return _destructive(cfg, "git push --force", "rewrites the history of a remote branch")


_VERIFY_NAMES = frozenset(("verify.py", "verify.sh", "vf"))


def _base(word: str) -> str:
    name = word.replace("\\", "/").rsplit("/", 1)[-1].lower()
    return name[:-4] if name.endswith(".exe") else name


def is_verify_command(words: list[str]) -> bool:
    """words (starting at the program) run verify: vf, verify.py, verify.sh, or an interpreter running one of them."""
    if not words:
        return False
    prog = _base(words[0])
    if prog in _VERIFY_NAMES:
        return True
    if prog in _SHELLS or prog == "py" or prog.startswith("python"):
        for a in words[1:]:
            if not a.startswith("-"):
                return _base(a) in _VERIFY_NAMES
    return False


def guard_verify_pipe() -> Decision:
    return _block("BLOCK: piping verify into another command hides its exit code (a FAIL looks like success). "
                  "Run it bare, or redirect to a file and read that: vf > log")


def finalize(d: Decision) -> Decision:
    """AGENTKIT_HEADLESS=1: nobody can answer a question, so every ask becomes a block."""
    if d.kind == "ask" and os.environ.get("AGENTKIT_HEADLESS") == "1":
        return Decision("block", "BLOCK (AGENTKIT_HEADLESS=1: no user to ask): " + d.message)
    return d


_DESTRUCTIVE_PROGS = frozenset(("rm", "rmdir", "shred", "unlink", "mv", "truncate", "dd"))
_DEVICE_OK = frozenset(("/dev/null", "/dev/stdout", "/dev/stderr"))
_SECRET_HINT = re.compile(r"\.env|\.pem|id_rsa", re.I)
_DRIVE = re.compile(r"^[A-Za-z]:")


def _has_variable(t: str) -> bool:
    """t holds a shell variable or command substitution the guard cannot expand (a leading $HOME is fine)."""
    for pre in ("${HOME}", "$HOME"):
        if t.startswith(pre) and (len(t) == len(pre) or t[len(pre)] in "/\\"):
            t = t[len(pre):]
            break
    return "$" in t or "`" in t


def _anchored(t: str) -> bool:
    """t does not depend on the current directory."""
    return t.startswith(("/", "~", "$HOME", "${HOME}")) or bool(_DRIVE.match(t))


def _pathlike(c: str) -> bool:
    return "/" in c or "\\" in c or "." in c or c.startswith("~") or bool(_SECRET_HINT.search(c))


def _operands(args: list[str], value_flags: frozenset = frozenset()) -> list[str]:
    """Non-option arguments. Options in value_flags take the next argument (also at the end of a short bundle: -ne script)."""
    ops: list[str] = []
    k = 0
    dd = False
    while k < len(args):
        a = args[k]
        k += 1
        if dd:
            ops.append(a)
        elif a == "--":
            dd = True
        elif a.startswith("-") and len(a) > 1:
            if a in value_flags or (not a.startswith("--") and len(a) > 2 and "-" + a[-1] in value_flags):
                k += 1
        else:
            ops.append(a)
    return ops


def _target_dir(args: list[str]) -> str:
    """The directory given with -t / --target-directory (cp, mv, ln, install), "" if none."""
    for k, a in enumerate(args):
        if a in ("-t", "--target-directory"):
            return args[k + 1] if k + 1 < len(args) else ""
        if a.startswith("--target-directory="):
            return a.split("=", 1)[1]
        if a.startswith("-t") and not a.startswith("--") and len(a) > 2:
            return a[2:]
        if a.startswith("-") and not a.startswith("--") and a.endswith("t") and len(a) > 2:  # -rt DIR
            return args[k + 1] if k + 1 < len(args) else ""
    return ""


def _inplace(args: list[str], stop: str, long_name: str = "--in-place") -> bool:
    """sed/perl in-place switch: -i, -i.bak, -ni ...; a short bundle ends at a switch that takes the rest as its value."""
    for a in args:
        if a == long_name or a.startswith(long_name + "="):
            return True
        if a.startswith("-") and not a.startswith("--"):
            for ch in a[1:]:
                if ch == "i":
                    return True
                if ch in stop:
                    break
    return False


_CP_VALUE = frozenset(("-t", "--target-directory", "-S", "--suffix", "-m", "--mode", "-o", "--owner", "-g", "--group"))


def write_targets(prog: str, args: list[str]) -> list[tuple[str, bool]]:
    """(path, destructive) for each path that program `prog` writes or deletes. destructive: an unresolved path is asked about."""
    if prog in ("rm", "rmdir", "shred", "unlink"):
        return [(t, True) for t in _operands(args)]
    if prog == "touch":
        return [(t, False) for t in _operands(args, frozenset(("-d", "-t", "-r", "--date", "--reference")))]
    if prog == "mkdir":
        return [(t, False) for t in _operands(args, frozenset(("-m", "--mode")))]
    if prog in ("chmod", "chown", "chgrp"):
        ops = _operands(args)
        return [(t, False) for t in (ops if any(a.startswith("--reference") for a in args) else ops[1:])]
    if prog == "truncate":
        return [(t, True) for t in _operands(args, frozenset(("-s", "-r", "--size", "--reference")))]
    if prog == "tee":
        return [(t, False) for t in _operands(args)]
    if prog == "mv":
        out = [(t, True) for t in _operands(args, _CP_VALUE)]
        d = _target_dir(args)
        return out + ([(d, True)] if d else [])
    if prog in ("cp", "ln", "install"):
        d = _target_dir(args)
        ops = _operands(args, _CP_VALUE)
        if d:
            return [(d, False)]
        return [(ops[-1], False)] if len(ops) >= 2 else []
    if prog == "dd":
        return [(a[3:], True) for a in args if a.startswith("of=")]
    if prog == "sed" and _inplace(args, "efl"):
        ops = _operands(args, frozenset(("-e", "-f", "-l", "--expression", "--file", "--line-length")))
        scripted = any(a in ("-e", "-f", "--expression", "--file") or a.startswith(("--expression=", "--file=")) or
                       (a.startswith("-") and not a.startswith("--") and a[-1] in "ef") for a in args)
        return [(t, True) for t in (ops if scripted else ops[1:])]
    if prog == "perl" and _inplace(args, "eEMmIdDVCxF0123456789"):
        ops = _operands(args, frozenset(("-e", "-E")))
        scripted = any(a in ("-e", "-E") or (a.startswith("-") and not a.startswith("--") and a[-1] in "eE") for a in args)
        return [(t, True) for t in (ops if scripted else ops[1:])]
    return []


# ---------------------------------------------------------------- paths and the fs rules (1b)
IS_NT = os.name == "nt"
_MSYS_DRIVE = re.compile(r"^/([A-Za-z])(?=/|$)")
_HOME_VAR = re.compile(r"^\$(\{HOME\}|HOME)(?=[/\\]|$)")
_ABS_PATTERN = re.compile(r"^(/|[A-Za-z]:)")
_GLOB_CHARS = "*?["
FS_ALLOW_WRITE = ["/tmp/**", "~/.cache/**", "{tmp}/**"]  # {tmp} = the OS scratch directory
FS_DENY_READ = ["~/.ssh/**", "~/.aws/**", "~/.config/gh/**", "~/.gnupg/**", "**/.env", "**/.env.*", "**/*.pem", "**/id_rsa*"]
FS_ALLOW_READ = ["**/.env.example", "**/.env.sample", "**/.env.template"]  # exceptions to FS_DENY_READ


def home_dir() -> str:
    return os.environ.get("HOME") or os.path.expanduser("~")


def _expand(p: str) -> str:
    """~ and $HOME at the start of p; on Windows also the MSYS form /c/x -> C:/x. No cwd, no symlinks."""
    if p == "~" or p.startswith("~/") or p.startswith("~\\"):
        p = home_dir() + p[1:]
    else:
        m = _HOME_VAR.match(p)
        if m:
            p = home_dir() + p[m.end():]
    if IS_NT:
        m = _MSYS_DRIVE.match(p)
        if m:
            p = m.group(1).upper() + ":" + p[2:]
            if len(p) == 2:
                p += "/"
    return p


def _real(path: str) -> str:
    """realpath that also works when the end of the path does not exist: the nearest existing ancestor is resolved."""
    if len(path) > 3:
        path = path.rstrip("/\\")
    head, tail = path, []
    while not os.path.lexists(head):
        parent, name = os.path.split(head)
        if parent == head or not name:
            break
        tail.append(name)
        head = parent
    real = os.path.realpath(head)
    return os.path.normpath(os.path.join(real, *reversed(tail))) if tail else real


def resolve_path(p: str, cwd: str) -> str:
    """Absolute, symlink-free, "/"-separated form of p: ~ $HOME and /c/x are expanded, a relative p is taken from cwd,
    `..` is followed, and a path that does not exist yet is resolved through its nearest existing directory."""
    return _real(os.path.join(cwd, _expand(p))).replace("\\", "/")


def _cmp(p: str) -> str:
    p = p.replace("\\", "/").rstrip("/")
    return p.lower() if IS_NT else p


def same_or_under(path: str, root: str) -> bool:
    a, r = _cmp(path), _cmp(root)
    return a == r or a.startswith(r + "/")


class RepoInfo:
    """The repository around cwd: `top` (work tree root, "/"-separated) and `common` (its shared .git directory).

    Outside any repository the cwd is the project root and `common` is "".
    """

    def __init__(self, cwd: str) -> None:
        self.cwd = cwd
        rc, out = git(["rev-parse", "--show-toplevel", "--git-common-dir"], cwd=cwd)
        parts = out.split("\n") if rc == 0 else []
        if len(parts) >= 2 and parts[0]:
            self.top = resolve_path(parts[0], cwd)
            self.common = resolve_path(parts[1], cwd)
        else:
            self.top = resolve_path(".", cwd)
            self.common = ""

    def in_repo(self, path: str) -> bool:
        """path (from resolve_path) is under the work tree, or in a worktree that shares this repository's .git."""
        if same_or_under(path, self.top):
            return True
        if not self.common:
            return False
        d = path
        while not os.path.isdir(d):
            parent = os.path.dirname(d)
            if parent == d:
                return False
            d = parent
        rc, out = git(["rev-parse", "--git-common-dir"], cwd=d)
        return rc == 0 and bool(out) and _cmp(resolve_path(out, d)) == _cmp(self.common)


def resolve_pattern(pat: str, base: str) -> str:
    """A fs.* glob with {tmp}, ~ and $HOME expanded; a relative one is taken from base; the part before the
    first wildcard goes through realpath, so /tmp/** still matches where /tmp is a symlink (macOS)."""
    pat = _expand(pat.replace("{tmp}", tempfile.gettempdir())).replace("\\", "/")
    if pat.startswith("*"):
        return pat
    if IS_NT and pat.startswith("/") and not pat.startswith("//") and base[1:2] == ":":
        pat = base[:2] + pat  # a pattern with no drive letter (/tmp/**) means the drive of the repo
    if not _ABS_PATTERN.match(pat):
        pat = base.rstrip("/") + "/" + pat
    cuts = [pat.find(c) for c in _GLOB_CHARS if c in pat]
    if not cuts:
        return _real(pat).replace("\\", "/")
    prefix = pat[:pat.rfind("/", 0, min(cuts)) + 1]
    return _real(prefix).replace("\\", "/").rstrip("/") + "/" + pat[len(prefix):]


class FsRules:
    """The fs.* settings of a config, resolved against the repository `repo`."""

    def __init__(self, cfg: Config, repo: RepoInfo) -> None:
        self.cfg = cfg
        self.repo = repo
        self._cache: dict[str, list[str]] = {}

    def patterns(self, key: str, default: list[str]) -> list[str]:
        if key not in self._cache:
            self._cache[key] = [resolve_pattern(p, self.repo.top) for p in self.cfg.cfg_list("fs." + key, default)]
        return self._cache[key]

    def matches(self, key: str, default: list[str], path: str) -> bool:
        p = path.lower() if IS_NT else path
        return any(glob_match(pat.lower() if IS_NT else pat, p) for pat in self.patterns(key, default))

    def allows_write(self, path: str) -> bool:
        return self.matches("allow_write", FS_ALLOW_WRITE, path)

    def denies_read(self, path: str) -> bool:
        return self.matches("deny_read", FS_DENY_READ, path) and not self.matches("allow_read", FS_ALLOW_READ, path)

    def outside_policy(self) -> str:
        """fs.outside_repo_write: "block" | anything else means "ask"."""
        return "block" if self.cfg.cfg("fs.outside_repo_write", "ask") == "block" else "ask"


def write_decision(path: str, repo: RepoInfo, cfg: Config, rules: Optional[FsRules] = None) -> Decision:
    """A write or delete of path (absolute, from resolve_path): free in the repo and in fs.allow_write, else fs.outside_repo_write."""
    if repo.in_repo(path):
        return ALLOW
    rules = rules or FsRules(cfg, repo)
    if rules.allows_write(path):
        return ALLOW
    if rules.outside_policy() == "block":
        return _block(f"BLOCK: write outside the repo: {path} (fs.outside_repo_write=block). "
                      "Work inside the repo, or ask the user to add the path to fs.allow_write.")
    return Decision("ask", f"Write outside the repo: {path} (fs.outside_repo_write=ask). Approve only if the user asked for it.")


# ---------------------------------------------------------------- command line
USAGE = ("usage: guard.py bash [COMMAND...]\n  no COMMAND: read the Claude Code PreToolUse JSON from stdin\n"
         "       guard.py edit [PATH]\n  no PATH: read the Claude Code PreToolUse JSON (file_path or notebook_path, cwd) from stdin\n")
NOT_HOOK_JSON = "guard-git: stdin is not Claude Code hook JSON. Pass the command as arguments instead."
NOT_HOOK_JSON_EDIT = "guard-fs: stdin is not Claude Code hook JSON. Pass the path as an argument instead."


def _out(stream: Any, text: str) -> None:
    """Write text to a stream as UTF-8 bytes whatever the console encoding is."""
    stream.buffer.write(text.encode("utf-8"))
    stream.flush()


def hook_command(raw: bytes) -> Optional[str]:
    """The Bash command of a PreToolUse payload (.tool_input.command), "" when there is none; None when raw is not JSON.

    Follows `jq -e .` and `jq -r '.tool_input.command // empty'`: null/false (or empty) input is not valid,
    a missing, null or false command is "", other values are printed the way jq's tostring does.
    """
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return None
    if data is None or data is False:
        return None
    tool_input = data.get("tool_input") if isinstance(data, dict) else None
    cmd = tool_input.get("command") if isinstance(tool_input, dict) else None
    if cmd is None or cmd is False:
        return ""
    return _jq_tostring(cmd).replace("\r", "").replace("\x00", "").rstrip("\n")


def ask_json(reason: str) -> str:
    payload = {"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "ask", "permissionDecisionReason": reason}}
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False) + "\n"


# a command without any of these has nothing for the rules to judge: git, verify, a redirect, a path-like word, a writing program
_RELEVANT = re.compile(r"git|verify|\bvf\b|>|[/\\~.]|\b(rm|rmdir|mv|cp|ln|touch|mkdir|chmod|chown|chgrp|truncate|tee|dd|install|sed|perl|cd|shred|unlink)\b")


def cmd_bash(args: list[str]) -> int:
    """Judge a shell command given as arguments (BLOCK = exit 1) or as hook JSON on stdin (BLOCK = exit 2)."""
    block_rc = 1 if args else 2
    cfg = load_config()  # may warn on stderr, before anything is read (as sourcing lib.sh did)
    cwd: Optional[str] = None
    if args:
        cmd = " ".join(args)
    else:
        raw = sys.stdin.buffer.read()
        parsed = hook_command(raw)
        if parsed is None:
            _out(sys.stderr, NOT_HOOK_JSON + "\n")
            return block_rc
        cmd = parsed
        cwd = _jq_tostring((hook_payload(raw) or {}).get("cwd") or "") or None
    if not _RELEVANT.search(cmd):  # nothing to judge: skip the git calls
        return 0
    judge = Judge(cfg, current_branch() if "git" in cmd else "", cwd)
    judge.scan(cmd, 0)
    d = finalize(judge.decision())
    if d.kind == "block":
        _out(sys.stderr, d.message + "\n")
        return block_rc
    if d.kind == "ask":
        _out(sys.stdout, ask_json(d.message))
    return 0


def hook_payload(raw: bytes) -> Optional[dict]:
    """The PreToolUse JSON object; {} when it is valid JSON but not an object, None when it is not JSON (or null/false)."""
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except ValueError:
        return None
    if data is None or data is False:
        return None
    return data if isinstance(data, dict) else {}


def cmd_edit(args: list[str]) -> int:
    """Judge the file an Edit/Write/MultiEdit/NotebookEdit is about to change: given as an argument or as hook JSON on stdin."""
    block_rc = 1 if args else 2
    if args:
        path, cwd = args[0], os.getcwd()
    else:
        data = hook_payload(sys.stdin.buffer.read())
        if data is None:
            _out(sys.stderr, NOT_HOOK_JSON_EDIT + "\n")
            return block_rc
        tool_input = data.get("tool_input")
        tool_input = tool_input if isinstance(tool_input, dict) else {}
        path = _jq_tostring(tool_input.get("file_path") or tool_input.get("notebook_path") or "")
        cwd = _jq_tostring(data.get("cwd") or "") or os.getcwd()
    if not path:
        return 0
    repo = RepoInfo(cwd)
    cfg = load_config(root=repo.top)
    d = finalize(write_decision(resolve_path(path, cwd), repo, cfg))
    if d.kind == "block":
        _out(sys.stderr, d.message + "\n")
        return block_rc
    if d.kind == "ask":
        _out(sys.stdout, ask_json(d.message))
    return 0


SUBCOMMANDS = {
    "bash": cmd_bash,
    "edit": cmd_edit,
}


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    handler = SUBCOMMANDS.get(argv[0]) if argv else None
    if handler is None:
        _out(sys.stderr, USAGE)
        return 1
    return handler(argv[1:])


if __name__ == "__main__":
    sys.exit(main())
