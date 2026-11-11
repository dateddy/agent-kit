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

    def protected_patterns(self) -> list[str]:
        lst = self.cfg("git.protected_branches", "") or DEFAULT_PROTECTED
        return [p for p in lst.split("\n") if p]

    def is_protected(self, branch: str) -> bool:
        return any(glob_match(p, branch) for p in self.protected_patterns())


def load_config(cwd: Optional[str] = None) -> Config:
    """Config of the repo around cwd (else cwd itself), like AK_ROOT in lib.sh. The WARN goes to stderr here."""
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

    # rules (functions below); tests replace these three to record how they are called
    def rule_merge(self, b: str, args: list[str]) -> None:
        self.record(guard_merge(self.cfg, b, args))

    def rule_commit(self, b: str) -> None:
        self.record(guard_commit(self.cfg, b))

    def rule_push(self, b: str, args: list[str]) -> None:
        self.record(guard_push(self.cfg, b, args))

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


def guard_push(cfg: Config, b: str, args: list[str]) -> Decision:
    """`git push` that targets a protected branch (b is the branch the push is judged on)."""
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
    if not hit:
        return ALLOW
    policy = protected_policy(cfg)
    if policy == "allow":
        return ALLOW
    if policy == "block":
        return _block(f"BLOCK: push to protected branch '{hit}' (git.protected_commit=block). Push a task branch and merge with --no-ff.")
    return Decision("ask", f"Push to protected branch '{hit}'. Approve only if the user asked for it; prefer pushing a task branch.")


# ---------------------------------------------------------------- command line
USAGE = "usage: guard.py bash [COMMAND...]\n  no COMMAND: read the Claude Code PreToolUse JSON from stdin\n"
NOT_HOOK_JSON = "guard-git: stdin is not Claude Code hook JSON. Pass the command as arguments instead."


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


def cmd_bash(args: list[str]) -> int:
    """Judge a shell command given as arguments (BLOCK = exit 1) or as hook JSON on stdin (BLOCK = exit 2)."""
    block_rc = 1 if args else 2
    cfg = load_config()  # may warn on stderr, before anything is read (as sourcing lib.sh did)
    if args:
        cmd = " ".join(args)
    else:
        parsed = hook_command(sys.stdin.buffer.read())
        if parsed is None:
            _out(sys.stderr, NOT_HOOK_JSON + "\n")
            return block_rc
        cmd = parsed
    if "git" not in cmd:  # nothing to judge: skip the git calls
        return 0
    judge = Judge(cfg, current_branch())
    judge.scan(cmd, 0)
    d = judge.decision()
    if d.kind == "block":
        _out(sys.stderr, d.message + "\n")
        return block_rc
    if d.kind == "ask":
        _out(sys.stdout, ask_json(d.message))
    return 0


SUBCOMMANDS = {
    "bash": cmd_bash,
    # "edit": cmd_edit,  # added in step 1b (file-edit rules)
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
