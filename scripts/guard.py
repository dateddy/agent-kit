#!/usr/bin/env python3
"""guard.py - the logic behind the agent-kit guards. Stdlib only, Python 3.9+.

  guard.py bash [COMMAND...]     judge a shell command (git rules); no arguments = Claude Code PreToolUse JSON on stdin

Exit-code convention: see scripts/guard-git.sh (hook mode: 0 allow/ask, 2 BLOCK; argument mode: 0 allow/ask, 1 BLOCK).
stdout carries nothing but the "ask" JSON; every other message goes to stderr.
"""
from __future__ import annotations

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
