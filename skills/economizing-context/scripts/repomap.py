#!/usr/bin/env python3
"""repomap — see a repo's shape and a file's outline without reading file contents.

  repomap.py [PATH] [--depth N]       tree with file counts and ~token cost per dir/file
  repomap.py --big [N]                the N most expensive text files (read these by range only)
  repomap.py --symbols FILE [FILE…]   outline: classes/functions with line numbers (py, js/ts, md)

Honors .gitignore (via git ls-files) and context.ignore_dirs from .agentkit.json.
Token estimate = bytes / 4 (rough; Vietnamese text runs ~1.5x higher).
"""
from __future__ import annotations

import argparse
import ast
import json
import os
import re
import subprocess
import sys
from collections import defaultdict
from pathlib import Path

IGNORE_DIRS = {".git", "node_modules", ".venv", "venv", "env", "__pycache__", "dist", "build", ".next",
               ".mypy_cache", ".pytest_cache", ".ruff_cache", ".agentkit", "wandb", "mlruns",
               "checkpoints", ".ipynb_checkpoints", ".idea", ".vscode"}
BINARY_EXT = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".gz", ".tar", ".7z",
              ".pt", ".pth", ".bin", ".safetensors", ".onnx", ".ckpt", ".h5", ".npy", ".npz", ".pkl",
              ".parquet", ".feather", ".arrow", ".db", ".sqlite", ".mp3", ".mp4", ".wav", ".so", ".dll",
              ".exe", ".woff", ".woff2", ".ttf", ".jar", ".class", ".pyc"}
MAX_LINES = 80  # tree output budget


def run(*cmd: str, cwd: Path | None = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, errors="replace")
    return p.stdout if p.returncode == 0 else ""


def cfg_ignore(root: Path) -> set[str]:
    try:
        extra = json.loads((root / ".agentkit.json").read_text(encoding="utf-8")).get("context", {}).get("ignore_dirs", [])
        return IGNORE_DIRS | set(extra)
    except Exception:
        return IGNORE_DIRS


def list_files(base: Path) -> list[Path]:
    out = run("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard", cwd=base)
    if out:
        files = [base / f for f in out.split("\0") if f]
    else:
        files = []
        for d, dirs, fs in os.walk(base):
            dirs[:] = [x for x in dirs if x not in IGNORE_DIRS]
            files += [Path(d) / f for f in fs]
    return [f for f in files if f.is_file()]


def is_text(p: Path) -> bool:
    if p.suffix.lower() in BINARY_EXT:
        return False
    try:
        with p.open("rb") as fh:
            return b"\0" not in fh.read(1024)
    except OSError:
        return False


def tok(n: int) -> str:
    t = n // 4
    return f"{t / 1000:.1f}k" if t >= 1000 else str(t)


def cmd_tree(base: Path, depth: int, ignore: set[str]) -> None:
    files = [f for f in list_files(base) if not (set(f.relative_to(base).parts[:-1]) & ignore)]
    dirs = defaultdict(lambda: [0, 0, 0])  # files, text bytes, binary files
    shown_files = []
    for f in files:
        rel = f.relative_to(base)
        size = f.stat().st_size
        text = is_text(f)
        for i in range(1, min(len(rel.parts), depth + 1)):
            key = Path(*rel.parts[:i])
            dirs[key][0] += 1
            dirs[key][1] += size if text else 0
            dirs[key][2] += 0 if text else 1
        if len(rel.parts) <= depth:
            shown_files.append((rel, size, text))
    total_text = sum(f.stat().st_size for f in files if is_text(f))
    print(f"{base.name}/  {len(files)} files · ~{tok(total_text)} tok of text")
    # depth-first order: parents before children, dirs before files at each level
    entries = sorted([(k, "d") for k in dirs] + [(r, "f") for r, _, _ in shown_files],
                     key=lambda x: tuple((0, p) for p in x[0].parts[:-1]) + ((0 if x[1] == "d" else 1, x[0].name),))
    sizes = {r: (s, t) for r, s, t in shown_files}
    lines = []
    for p, kind in entries:
        ind = "  " * (len(p.parts) - 1)
        if kind == "d":
            n, b, binn = dirs[p]
            extra = f", {binn} binary" if binn else ""
            lines.append(f"{ind}{p.name}/  ({n} files, ~{tok(b)} tok{extra})")
        else:
            s, t = sizes[p]
            lines.append(f"{ind}{p.name}  ~{tok(s)} tok" if t else f"{ind}{p.name}  [binary {s // 1024} KB]")
    if len(lines) > MAX_LINES:
        lines = lines[:MAX_LINES] + [f"… +{len(lines) - MAX_LINES} entries — narrow with PATH or --depth {max(1, depth - 1)}"]
    print("\n".join(lines))


def cmd_big(base: Path, n: int, ignore: set[str]) -> None:
    files = [f for f in list_files(base) if is_text(f) and not (set(f.relative_to(base).parts[:-1]) & ignore)]
    files.sort(key=lambda f: f.stat().st_size, reverse=True)
    for f in files[:n]:
        lc = sum(1 for _ in f.open("rb"))
        print(f"~{tok(f.stat().st_size):>6} tok  {lc:>6} lines  {f.relative_to(base)}")


def outline_py(src: str) -> list[str]:
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [f"(syntax error line {e.lineno}: cannot outline)"]
    out = []

    def sig(fn) -> str:
        args = [a.arg for a in fn.args.posonlyargs + fn.args.args]
        if fn.args.vararg:
            args.append("*" + fn.args.vararg.arg)
        args += [a.arg for a in fn.args.kwonlyargs]
        if fn.args.kwarg:
            args.append("**" + fn.args.kwarg.arg)
        return f"({', '.join(args)})"

    def walk(nodes, ind=""):
        for nd in nodes:
            if isinstance(nd, ast.ClassDef):
                end = getattr(nd, "end_lineno", nd.lineno)
                out.append(f"{nd.lineno:>5}-{end:<5} {ind}class {nd.name}")
                walk(nd.body, ind + "  ")
            elif isinstance(nd, (ast.FunctionDef, ast.AsyncFunctionDef)):
                end = getattr(nd, "end_lineno", nd.lineno)
                pre = "async def" if isinstance(nd, ast.AsyncFunctionDef) else "def"
                out.append(f"{nd.lineno:>5}-{end:<5} {ind}{pre} {nd.name}{sig(nd)}")
            elif not ind and isinstance(nd, ast.Assign) and all(isinstance(t, ast.Name) and t.id.isupper() for t in nd.targets):
                out.append(f"{nd.lineno:>5}       " + ", ".join(t.id for t in nd.targets) + " = …")
    walk(tree.body)
    return out


JS_RE = re.compile(r"^\s*(export\s+(default\s+)?)?(async\s+)?(function\*?\s+(\w+)|class\s+(\w+)|"
                   r"(const|let|var)\s+(\w+)\s*=\s*(async\s*)?(\([^)]*\)|\w+)\s*=>|interface\s+(\w+)|type\s+(\w+)\s*=)")


def outline_js(src: str) -> list[str]:
    out = []
    for i, l in enumerate(src.splitlines(), 1):
        m = JS_RE.match(l)
        if m:
            out.append(f"{i:>5}       {l.strip()[:100]}")
    return out


def outline_md(src: str) -> list[str]:
    return [f"{i:>5}       {l.strip()}" for i, l in enumerate(src.splitlines(), 1) if re.match(r"^#{1,4} ", l)]


def cmd_symbols(paths: list[str]) -> int:
    rc = 0
    for p in map(Path, paths):
        if not p.is_file():
            print(f"{p}: not a file")
            rc = 1
            continue
        src = p.read_text(encoding="utf-8", errors="replace")
        ext = p.suffix.lower()
        fn = outline_py if ext == ".py" else outline_js if ext in (".js", ".jsx", ".ts", ".tsx", ".mjs") \
            else outline_md if ext in (".md", ".rst") else None
        print(f"{p}  ({len(src.splitlines())} lines, ~{tok(len(src.encode()))} tok)")
        if fn is None:
            print("  (no outliner for this type — use grep -n)")
            continue
        items = fn(src)
        print("\n".join(items) if items else "  (no top-level symbols)")
        print("  → read a symbol with: sed -n 'START,ENDp' " + str(p))
    return rc


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--big", type=int, nargs="?", const=10)
    ap.add_argument("--symbols", nargs="+", metavar="FILE")
    a = ap.parse_args()
    if a.symbols:
        return cmd_symbols(a.symbols)
    base = Path(a.path).resolve()
    root = Path(run("git", "rev-parse", "--show-toplevel", cwd=base).strip() or base)
    ignore = cfg_ignore(root)
    if a.big:
        cmd_big(base, a.big, ignore)
    else:
        cmd_tree(base, a.depth, ignore)
    return 0


if __name__ == "__main__":
    sys.exit(main())
