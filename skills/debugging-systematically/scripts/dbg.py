#!/usr/bin/env python3
"""dbg — a bug journal that makes debugging systematic and remembers past bugs. Stdlib only.

  dbg.py open --layer code|data|llm --symptom "…" [--repro "<cmd>"]   start a bug -> B-001 (checks for repeats)
  dbg.py hypo B-001 "…" [--result confirmed|rejected]                  record a hypothesis and its outcome
  dbg.py close B-001 --cause "…" [--fix <sha>]                         close with the root cause (required)
  dbg.py list [--open] [--layer L]                                     show bugs
  dbg.py similar "<text>"                                              past bugs with overlapping keywords
  dbg.py llm-case B-001 --spec prompts/x.spec.json --vars '{"q": "…"}' --check '{"type": "not_contains", "value": "…"}'
                                                                       turn an LLM failure into a prompt test case

The journal is <notes.dir>/debug.jsonl (append-only, committed). Config: .agentkit.json "debug".min_similarity.
Exit: 0 OK · 2 WARN (e.g. a recurring bug) · 1 BLOCK
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2
LAYERS = ("code", "data", "llm")
CHECK_TYPES = ("contains", "not_contains", "regex", "equals", "max_words", "max_chars", "json_keys", "valid_json")
DEFAULTS = {"dir": "notes", "min_similarity": 0.4}
STOP = set("""the and for with that this from have has was are but not you your when then than into out its
into onto over under after before while about would should could can will does did got get let all any one two
bug error loi bị không khi và của cho với là các những này đó trong một được có""".split())


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def sh(*cmd: str) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def root() -> Path:
    r = sh("git", "rev-parse", "--show-toplevel")
    return Path(r) if r else Path.cwd()


def cfg(r: Path) -> dict:
    out = dict(DEFAULTS)
    try:
        data = json.loads((r / ".agentkit.json").read_text(encoding="utf-8"))
        out["dir"] = data.get("notes", {}).get("dir", out["dir"])
        out["min_similarity"] = float(data.get("debug", {}).get("min_similarity", out["min_similarity"]))
    except Exception:
        pass
    return out


def journal(r: Path, c: dict) -> Path:
    return r / c["dir"] / "debug.jsonl"


def events(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # a damaged line must not take the journal down
    return out


def append(path: Path, ev: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ev = {"ts": datetime.now().isoformat(timespec="seconds"), **ev}
    with path.open("a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(ev, ensure_ascii=False) + "\n")


def fold(evs: list[dict]) -> dict[str, dict]:
    bugs: dict[str, dict] = {}
    for e in evs:
        b = bugs.get(e.get("id"))
        kind = e.get("event")
        if kind == "open":
            bugs[e["id"]] = {"id": e["id"], "layer": e["layer"], "symptom": e["symptom"], "repro": e.get("repro", ""),
                             "branch": e.get("branch", ""), "ts": e["ts"], "status": "open", "hypotheses": [],
                             "cause": "", "fix": "", "cases": []}
        elif b is None:
            continue
        elif kind == "hypo":
            b["hypotheses"].append({"text": e["text"], "result": e.get("result")})
        elif kind == "close":
            b.update(status="closed", cause=e["cause"], fix=e.get("fix", ""))
        elif kind == "llm-case":
            b["cases"].append(e.get("spec", ""))
    return bugs


def words(text: str) -> set[str]:
    return {w for w in re.findall(r"\w+", text.lower()) if len(w) >= 3 and w not in STOP and not w.isdigit()}


def find_similar(bugs: dict[str, dict], text: str, threshold: float) -> list[tuple[float, dict]]:
    a = words(text)
    hits = []
    for b in bugs.values():
        bw = words(f"{b['symptom']} {b['cause']}")
        shared = a & bw
        if len(shared) >= 2 and a and bw:
            score = len(shared) / min(len(a), len(bw))
            if score >= threshold:
                hits.append((score, b))
    return sorted(hits, key=lambda h: -h[0])


def fmt_hit(score: float, b: dict) -> str:
    tail = f" — cause: {b['cause']}" if b["cause"] else ""
    fix = f" (fix {b['fix']})" if b["fix"] else ""
    return f"  {b['id']} [{b['layer']}, {b['status']}] {score:.0%}: {b['symptom'][:80]}{tail[:100]}{fix}"


# ---------------------------------------------------------------- commands
def cmd_open(a, r, c):
    path = journal(r, c)
    if not a.symptom.strip():
        print("BLOCK: --symptom is empty — describe what you observed (exact error text or wrong value)")
        return BLOCK
    bugs = fold(events(path))
    bid = f"B-{len(bugs) + 1:03d}"
    hits = find_similar(bugs, a.symptom, c["min_similarity"])
    append(path, {"id": bid, "event": "open", "layer": a.layer, "symptom": a.symptom.strip(),
                  "repro": a.repro or "", "branch": sh("git", "symbolic-ref", "--quiet", "--short", "HEAD")})
    print(f"OK: {bid} [{a.layer}] opened. Next: reproduce it, then `dbg.py hypo {bid} \"…\"`")
    if hits:
        print("WARN: recurring issue — similar past bugs (read their cause before guessing):")
        print("\n".join(fmt_hit(s, b) for s, b in hits[:5]))
        return WARN
    return OK


def need_bug(bugs: dict, bid: str) -> dict | None:
    if bid not in bugs:
        print(f"BLOCK: unknown bug {bid} (dbg.py list)")
        return None
    return bugs[bid]


def cmd_hypo(a, r, c):
    path = journal(r, c)
    b = need_bug(fold(events(path)), a.id)
    if not b:
        return BLOCK
    if not a.text.strip():
        print("BLOCK: hypothesis text is empty")
        return BLOCK
    append(path, {"id": a.id, "event": "hypo", "text": a.text.strip(), "result": a.result})
    n = len(b["hypotheses"]) + 1
    print(f"OK: {a.id} hypothesis {n} recorded" + (f" ({a.result})" if a.result else " (untested)"))
    return OK


def cmd_close(a, r, c):
    path = journal(r, c)
    b = need_bug(fold(events(path)), a.id)
    if not b:
        return BLOCK
    if not (a.cause or "").strip():
        print("BLOCK: --cause is required — state the root cause, not the symptom")
        return BLOCK
    if b["status"] == "closed":
        print(f"WARN: {a.id} is already closed (cause: {b['cause']}); nothing written")
        return WARN
    warn = None
    if a.fix and not sh("git", "rev-parse", "--verify", "--quiet", f"{a.fix}^{{commit}}"):
        warn = f"--fix '{a.fix}' is not a commit in this repo"
    append(path, {"id": a.id, "event": "close", "cause": a.cause.strip(), "fix": a.fix or ""})
    print(f"OK: {a.id} closed — {a.cause.strip()[:100]}")
    if warn:
        print("WARN:", warn)
        return WARN
    return OK


def cmd_list(a, r, c):
    bugs = [b for b in fold(events(journal(r, c))).values()
            if (not a.open or b["status"] == "open") and (not a.layer or b["layer"] == a.layer)]
    if not bugs:
        print("no bugs" + (" open" if a.open else ""))
        return OK
    for b in bugs:
        line = f"{b['id']} {b['status']:6} {b['layer']:4} {b['symptom'][:70]}"
        if b["cause"]:
            line += f" | cause: {b['cause'][:90]}"
        if b["fix"]:
            line += f" | fix: {b['fix']}"
        extra = f"{len(b['hypotheses'])} hypo" + (f", {len(b['cases'])} llm-case" if b["cases"] else "")
        print(f"{line}  [{extra}]")
    return OK


def cmd_similar(a, r, c):
    hits = find_similar(fold(events(journal(r, c))), a.text, c["min_similarity"])
    if not hits:
        print("no similar past bugs")
    else:
        print("similar past bugs:")
        print("\n".join(fmt_hit(s, b) for s, b in hits[:5]))
    return OK


def cmd_llm_case(a, r, c):
    bugs = fold(events(journal(r, c)))
    b = need_bug(bugs, a.id)
    if not b:
        return BLOCK
    spec_path = Path(a.spec)
    if not spec_path.is_file() and (r / a.spec).is_file():
        spec_path = r / a.spec
    if not spec_path.is_file():
        print(f"BLOCK: spec not found: {a.spec}")
        return BLOCK
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        print(f"BLOCK: spec is invalid JSON: {e}")
        return BLOCK
    if not isinstance(spec, dict):
        print("BLOCK: spec must be a JSON object")
        return BLOCK
    errs = []
    try:
        vars_ = json.loads(a.vars)
        assert isinstance(vars_, dict)
    except (json.JSONDecodeError, AssertionError):
        print("BLOCK: --vars must be a JSON object, e.g. '{\"question\": \"…\"}'")
        return BLOCK
    declared = {i["name"] for i in spec.get("inputs", []) if isinstance(i, dict) and i.get("name") and "TODO" not in i["name"]}
    if set(vars_) != declared:
        missing, unknown = sorted(declared - set(vars_)), sorted(set(vars_) - declared)
        errs.append(f"--vars must match the spec's inputs {sorted(declared)}"
                    + (f"; missing {missing}" if missing else "") + (f"; unknown {unknown}" if unknown else ""))
    checks = []
    for raw in a.check or []:
        try:
            chk = json.loads(raw)
        except json.JSONDecodeError:
            errs.append(f"--check is not valid JSON: {raw[:60]}")
            continue
        if not isinstance(chk, dict) or chk.get("type") not in CHECK_TYPES:
            errs.append(f"--check type must be one of {list(CHECK_TYPES)}: {raw[:60]}")
        elif chk["type"] != "valid_json" and "value" not in chk:
            errs.append(f"--check {chk['type']} needs a \"value\"")
        else:
            checks.append(chk)
    if not a.check:
        errs.append("give at least one --check (what must be true of a correct output)")
    if errs:
        print("\n".join("BLOCK: " + e for e in errs))
        return BLOCK
    spec.setdefault("cases", []).append({"vars": vars_, "checks": checks, "source": a.id})
    spec_path.write_bytes((json.dumps(spec, indent=2, ensure_ascii=False) + "\n").encode("utf-8"))
    append(journal(r, c), {"id": a.id, "event": "llm-case", "spec": spec_path.as_posix()})
    msg = f"OK: case {len(spec['cases'])} added to {a.spec} from {a.id}. Run: promptkit.py test {a.spec} --case {len(spec['cases'])}"
    print(msg)
    if b["layer"] != "llm":
        print(f"WARN: {a.id} is a {b['layer']} bug, not an llm bug")
        return WARN
    return OK


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="dbg", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("open")
    s.add_argument("--layer", required=True, choices=LAYERS)
    s.add_argument("--symptom", required=True)
    s.add_argument("--repro")
    s = sp.add_parser("hypo")
    s.add_argument("id")
    s.add_argument("text")
    s.add_argument("--result", choices=("confirmed", "rejected"))
    s = sp.add_parser("close")
    s.add_argument("id")
    s.add_argument("--cause")  # optional for argparse so a missing cause is a BLOCK (1), not a usage error (2)
    s.add_argument("--fix")
    s = sp.add_parser("list")
    s.add_argument("--open", action="store_true")
    s.add_argument("--layer", choices=LAYERS)
    s = sp.add_parser("similar")
    s.add_argument("text")
    s = sp.add_parser("llm-case")
    s.add_argument("id")
    s.add_argument("--spec", required=True)
    s.add_argument("--vars", required=True)
    s.add_argument("--check", action="append")
    a = ap.parse_args()
    r = root()
    fn = {"open": cmd_open, "hypo": cmd_hypo, "close": cmd_close, "list": cmd_list, "similar": cmd_similar,
          "llm-case": cmd_llm_case}[a.cmd]
    return fn(a, r, cfg(r))


if __name__ == "__main__":
    sys.exit(main())
