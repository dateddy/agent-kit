#!/usr/bin/env python3
"""repomap — see a repo's shape and a file's outline without reading file contents.

  repomap.py [PATH] [--depth N]       tree with file counts and ~token cost per dir/file
  repomap.py --big [N]                the N most expensive text files (read these by range only)
  repomap.py --symbols FILE [FILE…]   outline: classes/functions with line numbers (py, js/ts, md)

  repomap.py --zone SEED [SEED…] [--hops 2] [--direction both|imports|importers] [--json]
                                      blast radius of a file/module via the Python import graph
  repomap.py --zone-from-diff [--hops 2] [--json]     seeds = changed + untracked .py files
  repomap.py --zone-from-log LOGFILE [--hops 2]       seeds = repo files in Python traceback frames
  (zone prints paths, line counts and ~tokens, never file contents)

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
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd, encoding="utf-8", errors="replace")
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


# ---------------------------------------------------------------- zone: blast radius via the import graph
TEST_RE = re.compile(r"(^|/)(tests?|__tests__)/|(^|/)test_[^/]*\.py$|_test\.py$")
ZONE_MAX_FILES = 40


def zone_max(root: Path) -> int:
    try:
        return int(json.loads((root / ".agentkit.json").read_text(encoding="utf-8")).get("context", {})
                   .get("zone_max_files", ZONE_MAX_FILES))
    except Exception:
        return ZONE_MAX_FILES


def rel_of(root: Path, p: Path) -> str:
    return p.relative_to(root).as_posix()


def module_roots(root: Path, files: list[Path]) -> list[Path]:
    """Dirs that top-level packages/modules are imported from: repo root, src/, parents of top-most packages."""
    roots = [root]
    if (root / "src").is_dir():
        roots.append(root / "src")
    for f in files:
        d = f.parent
        if d == root or not (d / "__init__.py").exists():
            continue
        while d.parent != root and (d.parent / "__init__.py").exists():
            d = d.parent
        if d.parent not in roots:
            roots.append(d.parent)
    return roots


def module_name(f: Path, roots: list[Path]) -> str | None:
    best = max((r for r in roots if r in f.parents), key=lambda r: len(r.parts), default=None)
    if best is None:
        return None
    parts = list(f.relative_to(best).with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    if not parts or not all(p.isidentifier() for p in parts):
        return None
    return ".".join(parts)


def parse_imports(f: Path, mod: str) -> tuple[set[str], list[tuple[int, str]]]:
    """Candidate dotted names a file imports + dynamic imports we cannot trace. Never executes code."""
    try:
        tree = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError as e:
        return set(), [(e.lineno or 0, "syntax error")]
    pkg = mod if f.name == "__init__.py" else mod.rpartition(".")[0]
    cands: set[str] = set()
    notes: list[tuple[int, str]] = []
    for nd in ast.walk(tree):
        if isinstance(nd, ast.Import):
            cands.update(a.name for a in nd.names)
        elif isinstance(nd, ast.ImportFrom):
            base = nd.module or ""
            if nd.level:
                parts = pkg.split(".") if pkg else []
                if nd.level - 1 > len(parts):
                    continue
                parts = parts[:len(parts) - (nd.level - 1)]
                base = ".".join(parts + ([base] if base else []))
            if base:
                cands.add(base)
            cands.update(f"{base}.{a.name}" if base else a.name for a in nd.names if a.name != "*")
        elif isinstance(nd, ast.Call):
            fn = nd.func
            name = fn.attr if isinstance(fn, ast.Attribute) else fn.id if isinstance(fn, ast.Name) else ""
            if name in ("import_module", "__import__"):
                arg = nd.args[0] if nd.args else None
                if isinstance(arg, ast.Constant) and isinstance(arg.value, str) and not arg.value.startswith("."):
                    cands.add(arg.value)
                else:
                    notes.append((nd.lineno, f"dynamic import ({name})"))
    return cands, notes


class Graph:
    def __init__(self, root: Path, ignore: set[str]):
        self.root = root
        self.files = sorted(f.resolve() for f in list_files(root)
                            if f.suffix == ".py" and not (set(f.relative_to(root).parts[:-1]) & ignore))
        roots = module_roots(root, self.files)
        self.mod = {f: module_name(f, roots) for f in self.files}
        index = {m: f for f, m in self.mod.items() if m}
        self.imports: dict[Path, set[Path]] = {f: set() for f in self.files}
        self.importers: dict[Path, set[Path]] = {f: set() for f in self.files}
        self.notes: dict[Path, list[tuple[int, str]]] = {}
        for f in self.files:
            if not self.mod[f]:
                continue
            cands, self.notes[f] = parse_imports(f, self.mod[f])
            for c in cands:
                parts = c.split(".")
                for i in range(1, len(parts) + 1):  # importing a.b.c also runs a/__init__ and a/b/__init__
                    tgt = index.get(".".join(parts[:i]))
                    if tgt and tgt != f:
                        self.imports[f].add(tgt)
                        self.importers[tgt].add(f)

    def resolve_seed(self, s: str) -> Path | None:
        for cand in (Path.cwd() / s, self.root / s):
            if cand.is_file():
                cand = cand.resolve()
                return cand if cand in self.imports else None
        return next((f for f, m in self.mod.items() if m == s), None)


def bfs(starts: list[Path], adj: dict[Path, set[Path]], hops: int, root: Path) -> dict[Path, int]:
    """Breadth-first distances; non-seed test files are neither listed nor expanded."""
    dist = {s: 0 for s in starts}
    frontier = list(starts)
    for d in range(1, hops + 1):
        nxt = []
        for f in frontier:
            for g in sorted(adj[f]):
                if g not in dist and not TEST_RE.search(rel_of(root, g)):
                    dist[g] = d
                    nxt.append(g)
        frontier = nxt
    return dist


def seeds_from_diff(g: Graph) -> list[Path]:
    root = g.root
    names = run("git", "diff", "--name-only", "HEAD", cwd=root) or run("git", "diff", "--name-only", "--cached", cwd=root)
    names += "\n" + run("git", "ls-files", "--others", "--exclude-standard", cwd=root)
    out = []
    for n in names.splitlines():
        p = (root / n.strip()).resolve() if n.strip().endswith(".py") else None
        if p and p in g.imports and p not in out:
            out.append(p)
    return out


def seeds_from_log(g: Graph, log: Path) -> list[Path]:
    out = []
    for m in re.finditer(r'File "([^"]+)", line \d+', log.read_text(encoding="utf-8", errors="replace")):
        p = Path(m[1])
        try:
            p = (p if p.is_absolute() else g.root / p).resolve()
        except OSError:
            continue
        if p in g.imports and p not in out:
            out.append(p)
    return out


def cmd_zone(a, mode: str) -> int:
    base = Path.cwd()
    root = Path(run("git", "rev-parse", "--show-toplevel", cwd=base).strip() or base).resolve()
    g = Graph(root, cfg_ignore(root))
    if not g.files:
        print("zone supports Python only — no .py files in this repo")
        return 2
    if mode == "zone":
        seeds = []
        for s in a.zone:
            f = g.resolve_seed(s)
            if f is None:
                print(f"seed not found in the Python graph: {s}")
                return 1
            seeds.append(f)
    elif mode == "diff":
        seeds = seeds_from_diff(g)
    else:
        lp = Path(a.zone_from_log)
        if not lp.is_file():
            print(f"log not found: {lp}")
            return 1
        seeds = seeds_from_log(g, lp)

    d_up = bfs(seeds, g.importers, a.hops, root) if a.direction in ("both", "importers") else {}
    d_down = bfs(seeds, g.imports, a.hops, root) if a.direction in ("both", "imports") else {}
    zone: dict[Path, tuple[int, str]] = {}
    for f in set(d_up) | set(d_down):
        if f in seeds:
            continue
        du, dd = d_up.get(f), d_down.get(f)
        if dd is None or (du is not None and du < dd):
            zone[f] = (du, "importers")
        elif du is None or dd < du:
            zone[f] = (dd, "imports")
        else:
            zone[f] = (du, "both")
    members = set(seeds) | set(zone)
    tests = sorted(t for t in g.files if TEST_RE.search(rel_of(root, t)) and (
        t in members or g.imports[t] & members
        or any(t.name == f"test_{m.stem}.py" for m in members if m.name != "__init__.py")))
    notes = [f"{'syntax error' if txt == 'syntax error' else 'dynamic import'} in {rel_of(root, f)}:{ln} — not traced"
             for f in sorted(members) for ln, txt in g.notes.get(f, [])]
    ordered = sorted(zone.items(), key=lambda kv: (kv[1][0], rel_of(root, kv[0])))

    if a.json:
        print(json.dumps({"seeds": [rel_of(root, s) for s in seeds],
                          "files": [{"path": rel_of(root, f), "distance": d, "direction": dr} for f, (d, dr) in ordered],
                          "tests": [rel_of(root, t) for t in tests], "notes": notes}, ensure_ascii=False))
        return 0
    if not seeds:
        print("no seeds found (nothing changed, or no repo .py file in the log)")
        return 0

    def size(f: Path) -> tuple[int, int]:
        b = f.read_bytes()
        return b.count(b"\n") + (not b.endswith(b"\n") and bool(b)), len(b)

    arrow = {"importers": "↑", "imports": "↓", "both": "↑↓"}
    cap = zone_max(root)
    total = sum(size(f)[1] for f in list(seeds) + [f for f, _ in ordered])
    print("SEEDS  " + ", ".join(rel_of(root, s) for s in seeds))
    print("dist  dir  path  lines  ~tok   (↑ imported-by · ↓ imports)")
    for f, (d, dr) in ordered[:cap]:
        n, b = size(f)
        print(f"{d:>4}  {arrow[dr]:<3}  {rel_of(root, f)}  {n}  ~{tok(b)}")
    if len(ordered) > cap:
        print(f"… +{len(ordered) - cap} more (raise --hops / narrow seeds)")
    print("TESTS" + ("" if tests else "  (none found)"))
    for t in tests[:cap]:
        print("  " + rel_of(root, t))
    for n in notes:
        print("NOTE: " + n)
    print(f"zone {len(members)} of {len(g.files)} .py files · ~{tok(total)} tok")
    return 0


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path", nargs="?", default=".")
    ap.add_argument("--depth", type=int, default=2)
    ap.add_argument("--big", type=int, nargs="?", const=10)
    ap.add_argument("--symbols", nargs="+", metavar="FILE")
    ap.add_argument("--zone", nargs="+", metavar="SEED", help="file or dotted module")
    ap.add_argument("--zone-from-diff", action="store_true")
    ap.add_argument("--zone-from-log", metavar="LOGFILE")
    ap.add_argument("--hops", type=int, default=2)
    ap.add_argument("--direction", choices=("both", "imports", "importers"), default="both")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.zone:
        return cmd_zone(a, "zone")
    if a.zone_from_diff:
        return cmd_zone(a, "diff")
    if a.zone_from_log:
        return cmd_zone(a, "log")
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
