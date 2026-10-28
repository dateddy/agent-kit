#!/usr/bin/env python3
"""Validate every skills/*/SKILL.md against the authoring rules this kit follows.

Checks: frontmatter name/description limits, name == folder, body length, relative links
exist and stay one level deep, scripts answer --help. Prints a token estimate per skill.
Exit 1 if any ERROR.  Usage: python tools/validate_skills.py [skills_dir]
"""
from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
from pathlib import Path

CHILD_ENV = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}

NAME_RE = re.compile(r"^[a-z0-9-]{1,64}$")
UTF8_RE = re.compile(r"reconfigure\(encoding=[\"']utf-8")
LINK_RE = re.compile(r"\]\(([^)#\s]+)\)")
BODY_MAX = 500          # Anthropic hard guidance
BODY_TARGET = 150       # this kit's own budget: SKILL.md should stay lean
TOK = 4                 # rough chars per token for English markdown


def frontmatter(text: str) -> tuple[dict, str]:
    m = re.match(r"^---\n(.*?)\n---\n(.*)$", text, re.S)
    if not m:
        return {}, text
    meta = {}
    for line in m[1].splitlines():
        if ":" in line:
            k, v = line.split(":", 1)
            meta[k.strip()] = v.strip()
    return meta, m[2]


def check(skill: Path) -> tuple[list[str], list[str], str]:
    errs, warns = [], []
    text = (skill / "SKILL.md").read_text(encoding="utf-8")
    meta, body = frontmatter(text)
    name, desc = meta.get("name", ""), meta.get("description", "")
    if not NAME_RE.match(name):
        errs.append(f"name '{name}' must be lowercase/digits/hyphens, ≤64")
    if name != skill.name:
        errs.append(f"name '{name}' != folder '{skill.name}'")
    if re.search(r"anthropic|claude", name):
        errs.append("name contains a reserved word")
    if not desc or len(desc) > 1024:
        errs.append(f"description length {len(desc)} (must be 1..1024)")
    if re.match(r"(?i)^(i|you|we)\b", desc):
        errs.append("description must be third person")
    if not re.search(r"\buse (when|whenever|after|before|in|at|for|on)\b", desc, re.I):
        warns.append("description should say when to use it ('Use when …')")
    if re.search(r"<[a-z]", desc):
        errs.append("description contains XML-like tags")
    n = len(body.splitlines())
    if n > BODY_MAX:
        errs.append(f"body {n} lines > {BODY_MAX}")
    elif n > BODY_TARGET:
        warns.append(f"body {n} lines > kit target {BODY_TARGET} — move detail to reference/")
    for link in LINK_RE.findall(body):
        if link.startswith(("http://", "https://")):
            continue
        tgt = skill / link
        if not tgt.exists():
            errs.append(f"broken link: {link}")
        elif tgt.suffix == ".md":
            nested = [l for l in LINK_RE.findall(tgt.read_text(encoding="utf-8"))
                      if l.endswith(".md") and not l.startswith("http")]
            if nested:
                warns.append(f"{link} links to {nested} — keep references one level deep")
    for py in sorted((skill / "scripts").glob("*.py")) if (skill / "scripts").exists() else []:
        if not UTF8_RE.search(py.read_text(encoding="utf-8")):
            errs.append(f"{py.name} lacks UTF-8 stdio reconfigure (see A1)")
        r = subprocess.run([sys.executable, str(py), "--help"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30, env=CHILD_ENV)
        if r.returncode != 0:
            errs.append(f"{py.name} --help failed: {r.stderr.strip()[:120]}")
    est = f"desc≈{len(desc) // TOK} tok (always loaded) · body≈{len(body) // TOK} tok (on trigger) · {n} lines"
    return errs, warns, est


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("skills_dir", nargs="?", help="default: <kit>/skills")
    a = ap.parse_args()
    root = Path(a.skills_dir) if a.skills_dir else Path(__file__).resolve().parent.parent / "skills"
    bad = 0
    for skill in sorted(p for p in root.iterdir() if (p / "SKILL.md").exists()):
        errs, warns, est = check(skill)
        print(f"{'FAIL' if errs else 'ok  '} {skill.name:22} {est}")
        for e in errs:
            print("     ERROR:", e)
        for w in warns:
            print("     WARN: ", w)
        bad += bool(errs)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
