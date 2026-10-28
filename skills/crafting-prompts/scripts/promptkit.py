#!/usr/bin/env python3
"""promptkit — build prompts for a target model from a spec, by rules, and test them. Stdlib only.

  promptkit.py init NAME [--target T]        create prompts/NAME.spec.json skeleton
  promptkit.py check SPEC                    missing fields → exact questions to ask the user
  promptkit.py render SPEC [--target T]      write prompts/NAME.<target>.md (SYSTEM / USER parts)
  promptkit.py lint FILE [--target T]        static checks on any prompt text
  promptkit.py test SPEC [--target T] [--case N] [--provider openai|anthropic] [--model M] [--base-url URL]
                                             run spec cases against a model, check outputs
  promptkit.py targets                       list render profiles

Targets: generic · claude · gpt · open-small · reasoning (see profiles.json; add your own in prompts/profiles.json).
Exit: 0 OK · 1 BLOCK/failing case · 2 WARN
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
from pathlib import Path

OK, BLOCK, WARN = 0, 1, 2
SKILL_DIR = Path(__file__).resolve().parent.parent
DEFAULTS = {"dir": "prompts", "provider": "openai", "model": None, "base_url": None, "key_env": None,
            # ~3k tokens: beyond this, check that every section earns its place
            "max_tokens_warn": 3000, "timeout_s": 120}
QUESTIONS = {
    "goal": "What should the model accomplish, in one sentence? Who reads the output?",
    "instructions": "Which rules or steps must the model follow? List them.",
    "inputs": "What data is inserted at runtime (e.g. question, retrieved documents)? Name each variable.",
    "output.format": "Which output format: text, markdown, or json?",
    "output.description": "What must the answer contain — sections/fields, length, language?",
    "output.schema": "Which JSON keys are required, and what type is each?",
    "examples": "Can you give 1–3 example inputs with the ideal outputs?",
    "cases": "Which test inputs should we run, and what must each output contain or avoid?",
}
VAGUE = re.compile(r"\b(appropriate(ly)?|relevant|good|nice|properly|etc\.?|various|some|as needed|"
                   r"if necessary|reasonable|phù hợp|hợp lý|v\.v\.?|một số)\b", re.I)
SHOUT = re.compile(r"\b(MUST|NEVER|ALWAYS|IMPORTANT|CRITICAL|ONLY|DO NOT|DON'T)\b")
NEG = re.compile(r"\b(don't|do not|never|avoid|không được|đừng|tránh)\b", re.I)
PLACE = re.compile(r"\{\{\s*([A-Za-z_][\w-]*)\s*\}\}")


def sh(*cmd: str) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    return p.stdout.strip() if p.returncode == 0 else ""


def root() -> Path:
    r = sh("git", "rev-parse", "--show-toplevel")
    return Path(r) if r else Path.cwd()


def cfg(r: Path) -> dict:
    c = dict(DEFAULTS)
    try:
        c.update({k: v for k, v in json.loads((r / ".agentkit.json").read_text(encoding="utf-8"))
                  .get("prompts", {}).items() if not k.startswith("_")})
    except Exception:
        pass
    return c


def profiles(r: Path, c: dict) -> dict:
    p = json.loads((SKILL_DIR / "profiles.json").read_text(encoding="utf-8"))
    local = r / c["dir"] / "profiles.json"
    if local.exists():
        p.update(json.loads(local.read_text(encoding="utf-8")))
    return {k: v for k, v in p.items() if not k.startswith("_")}


def est_tokens(text: str) -> int:
    non_ascii = sum(1 for ch in text if ord(ch) > 127)
    ratio = 2.5 if text and non_ascii / len(text) > 0.15 else 4  # Vietnamese/CJK tokenize denser
    return int(len(text) / ratio)


def todo(v) -> bool:
    return v is None or (isinstance(v, str) and (not v.strip() or "TODO" in v)) or (isinstance(v, list) and not [
        x for x in v if not todo(x)]) or (isinstance(v, dict) and not v)


def load_spec(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"BLOCK: spec not found: {path}")
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"BLOCK: spec is invalid JSON: {e}")


# ---------------------------------------------------------------- init / check
def cmd_init(a, r, c):
    d = r / c["dir"]
    d.mkdir(parents=True, exist_ok=True)
    f = d / f"{a.name}.spec.json"
    if f.exists():
        print(f"BLOCK: {f.relative_to(r)} exists")
        return BLOCK
    spec = {
        "name": a.name, "target": a.target, "role": "", "goal": "TODO",
        "context": "",
        "inputs": [{"name": "TODO", "description": "TODO", "long": False}],
        "instructions": ["TODO"], "constraints": [],
        "output": {"format": "TODO: text | markdown | json", "description": "TODO", "schema": {}, "language": ""},
        "examples": [], "cases": [],
    }
    f.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"OK: created {f.relative_to(r)} — fill it, then run: promptkit.py check {f.relative_to(r)}")
    return OK


def check_spec(spec: dict, prof: dict, target: str) -> tuple[list[str], list[str], list[str]]:
    errs, warns, asks = [], [], []

    def miss(key: str, msg: str | None = None, hard: bool = True):
        (errs if hard else warns).append(msg or f"missing {key}")
        asks.append(f"[{key}] {QUESTIONS[key]}")

    if todo(spec.get("goal")):
        miss("goal")
    if todo(spec.get("instructions")):
        miss("instructions")
    out = spec.get("output", {})
    fmt = str(out.get("format", "")).lower()
    if todo(fmt) or fmt not in ("text", "markdown", "json"):
        miss("output.format", f"output.format must be text|markdown|json (got '{out.get('format')}')")
    if fmt == "json" and todo(out.get("schema")):
        miss("output.schema")
    elif todo(out.get("description")) and fmt != "json":
        miss("output.description")
    inputs = [i for i in spec.get("inputs", []) if not todo(i.get("name"))]
    if not inputs:
        miss("inputs", "no runtime inputs declared — fine for static prompts, otherwise declare them", hard=False)
    names = [i["name"] for i in inputs]
    for n in names:
        if not re.match(r"^[A-Za-z_][\w-]*$", n):
            errs.append(f"input name '{n}' must be an identifier")
    exs = [e for e in spec.get("examples", []) if e.get("output")]
    if prof.get("examples") == "required" and not exs:
        miss("examples", f"target '{target}' requires ≥1 example")
    for i, e in enumerate(exs, 1):
        unknown = set(e.get("input", {})) - set(names)
        if unknown:
            errs.append(f"example {i} uses undeclared inputs {sorted(unknown)}")
    if len([x for x in spec.get("instructions", []) if not todo(x)]) > prof.get("max_instructions", 99):
        warns.append(f"{len(spec['instructions'])} instructions > {prof['max_instructions']} for '{target}' — merge or move to constraints")
    text = json.dumps(spec, ensure_ascii=False).lower()
    for ph in prof.get("banned_phrases", []):
        if ph.lower() in text:
            warns.append(f"'{ph}' is counterproductive for target '{target}' — state the goal and criteria instead")
    cases = spec.get("cases", [])
    if not cases:
        miss("cases", "no test cases — the prompt cannot be verified", hard=False)
    for i, cs in enumerate(cases, 1):
        if not cs.get("checks"):
            errs.append(f"case {i} has no checks")
        missing = set(names) - set(cs.get("vars", {}))
        if missing:
            errs.append(f"case {i} lacks vars {sorted(missing)}")
    return errs, warns, asks


def cmd_check(a, r, c):
    spec = load_spec(a.spec)
    target = a.target or spec.get("target", "generic")
    profs = profiles(r, c)
    if target not in profs:
        print(f"BLOCK: unknown target '{target}' — choose from {sorted(profs)}")
        return BLOCK
    errs, warns, asks = check_spec(spec, profs[target], target)
    for e in errs:
        print("BLOCK:", e)
    for w in warns:
        print("WARN:", w)
    if asks:
        print("ASK THE USER (one message, only these):")
        print("\n".join("  " + q for q in dict.fromkeys(asks)))
    if errs:
        return BLOCK
    print(f"OK: spec ready for target '{target}'" + (" (with warnings)" if warns else ""))
    return WARN if warns else OK


# ---------------------------------------------------------------- render
def section(style: str, title: str, body: str) -> str:
    tag = re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_")
    return f"<{tag}>\n{body}\n</{tag}>" if style == "xml" else f"## {title}\n{body}"


def render(spec: dict, prof: dict) -> tuple[str, str]:
    st = prof.get("style", "markdown")
    out = spec.get("output", {})
    sys_parts = []
    if spec.get("role"):
        sys_parts.append(spec["role"].strip())
    sys_parts.append(section(st, "Goal", spec["goal"].strip()))
    if spec.get("context", "").strip():
        sys_parts.append(section(st, "Context", spec["context"].strip()))
    ins = [x for x in spec.get("instructions", []) if not todo(x)]
    sys_parts.append(section(st, "Instructions", "\n".join(f"{i}. {x}" for i, x in enumerate(ins, 1))))
    cons = [x for x in spec.get("constraints", []) if not todo(x)]
    if cons:
        sys_parts.append(section(st, "Constraints", "\n".join(f"- {x}" for x in cons)))
    fmt_lines = [f"Format: {out.get('format')}"]
    if out.get("description") and not todo(out["description"]):
        fmt_lines.append(out["description"].strip())
    if out.get("language"):
        fmt_lines.append(f"Language: {out['language']}")
    if out.get("schema"):
        fmt_lines.append("JSON schema (return only this JSON, no prose):\n" + json.dumps(out["schema"], indent=2, ensure_ascii=False))
    sys_parts.append(section(st, "Output format", "\n".join(fmt_lines)))
    exs = [e for e in spec.get("examples", []) if e.get("output")]
    if exs:
        blocks = []
        for i, e in enumerate(exs, 1):
            inp = "\n".join(f"{k}: {v}" for k, v in e.get("input", {}).items())
            o = e["output"] if isinstance(e["output"], str) else json.dumps(e["output"], ensure_ascii=False)
            blocks.append(f"<example>\n<input>\n{inp}\n</input>\n<output>\n{o}\n</output>\n</example>" if st == "xml"
                          else f"### Example {i}\nInput:\n{inp}\nOutput:\n{o}")
        sys_parts.append(section(st, "Examples", "\n".join(blocks)))

    inputs = [i for i in spec.get("inputs", []) if not todo(i.get("name"))]
    if prof.get("long_inputs_first"):
        inputs = sorted(inputs, key=lambda i: not i.get("long", False))  # long documents before the question
    user_parts = []
    for i in inputs:
        n = i["name"]
        user_parts.append(f"<{n}>\n{{{{{n}}}}}\n</{n}>" if st == "xml" else f"## {n.replace('_', ' ').title()}\n{{{{{n}}}}}")
    if prof.get("repeat_format_at_end") and str(out.get("format")).lower() == "json":
        user_parts.append("Return only the JSON object defined in Output format.")
    elif prof.get("repeat_format_at_end"):
        user_parts.append(f"Answer in {out.get('format')}" + (f": {out['description'].strip()}" if out.get("description") and not todo(out["description"]) else "."))
    return "\n\n".join(sys_parts), "\n\n".join(user_parts)


def cmd_render(a, r, c):
    spec = load_spec(a.spec)
    target = a.target or spec.get("target", "generic")
    profs = profiles(r, c)
    if target not in profs:
        print(f"BLOCK: unknown target '{target}' — choose from {sorted(profs)}")
        return BLOCK
    errs, warns, asks = check_spec(spec, profs[target], target)
    if errs:
        print("BLOCK: spec incomplete — run check first:\n  " + "\n  ".join(errs))
        return BLOCK
    system, user = render(spec, profs[target])
    out = Path(a.spec).resolve().parent / f"{spec['name']}.{target}.md"
    out.write_text(f"<!-- generated by promptkit from {Path(a.spec).name} (target: {target}). "
                   f"Edit the spec, not this file. -->\n=== SYSTEM ===\n{system}\n\n=== USER ===\n{user}\n", encoding="utf-8")
    t = est_tokens(system + user)
    print(f"OK: wrote {out.relative_to(r) if out.is_relative_to(r) else out}  (~{t} tokens before inputs)")
    for w in warns:
        print("WARN:", w)
    return WARN if warns else OK


def split_rendered(text: str) -> tuple[str, str]:
    m = re.search(r"=== SYSTEM ===\n(.*?)\n=== USER ===\n(.*)$", text, re.S)
    return (m[1].strip(), m[2].strip()) if m else ("", text)


# ---------------------------------------------------------------- lint
def cmd_lint(a, r, c):
    p = Path(a.file)
    if not p.exists():
        print(f"BLOCK: not found: {a.file}")
        return BLOCK
    text = p.read_text(encoding="utf-8")
    body = re.sub(r"<!--.*?-->", "", text, flags=re.S)
    lines = [l for l in body.splitlines() if l.strip()]
    warns = []
    t = est_tokens(body)
    if t > c["max_tokens_warn"]:
        warns.append(f"~{t} tokens > {c['max_tokens_warn']} — cut sections that do not change the output")
    vague = [(i, m[0]) for i, l in enumerate(lines, 1) for m in VAGUE.finditer(l)]
    if vague:
        warns.append("vague terms (replace with concrete criteria): " + ", ".join(f"'{w}' (l{i})" for i, w in vague[:8]))
    shouts = SHOUT.findall(body)
    if len(shouts) > 3:
        warns.append(f"{len(shouts)} ALL-CAPS emphasis words — modern models over-apply them; use plain wording + a reason")
    rule_lines = [l for l in lines if re.match(r"^\s*(\d+\.|-|\*)\s", l)]
    neg = [l for l in rule_lines if NEG.search(l)]
    if rule_lines and len(neg) / len(rule_lines) > 0.5:
        warns.append(f"{len(neg)}/{len(rule_lines)} rules are prohibitions — say what to do instead")
    if not re.search(r"(?i)\b(format|json|markdown|output|định dạng|trả về)\b", body):
        warns.append("no output format stated")
    dup = {l.strip() for l in lines if lines.count(l) > 1 and len(l.strip()) > 20}
    if dup:
        warns.append(f"{len(dup)} duplicated line(s): {next(iter(dup))[:60]}")
    if a.target:
        prof = profiles(r, c).get(a.target, {})
        for ph in prof.get("banned_phrases", []):
            if ph.lower() in body.lower():
                warns.append(f"'{ph}' is counterproductive for target '{a.target}'")
    ph = sorted(set(PLACE.findall(body)))
    print(f"~{t} tokens · {len(lines)} lines · placeholders: {', '.join(ph) or 'none'}")
    for w in warns:
        print("WARN:", w)
    print("OK" if not warns else f"{len(warns)} warning(s)")
    return WARN if warns else OK


# ---------------------------------------------------------------- test
def call_model(provider: str, model: str, base_url: str | None, key: str, system: str, user: str, timeout: int) -> str:
    if provider == "anthropic":
        url = (base_url or "https://api.anthropic.com").rstrip("/") + "/v1/messages"
        body = {"model": model, "max_tokens": 2048, "system": system, "messages": [{"role": "user", "content": user}],
                "temperature": 0}
        headers = {"x-api-key": key, "anthropic-version": "2023-06-01"}
    else:  # any OpenAI-compatible server: OpenAI, OpenRouter, vLLM, Ollama, LM Studio…
        url = (base_url or "https://api.openai.com/v1").rstrip("/") + "/chat/completions"
        body = {"model": model, "temperature": 0,
                "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": user}]}
        headers = {"Authorization": f"Bearer {key}"} if key else {}
    req = urllib.request.Request(url, data=json.dumps(body).encode(), method="POST",
                                 headers={"Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode())
    if provider == "anthropic":
        return "".join(b.get("text", "") for b in data.get("content", []))
    return data["choices"][0]["message"]["content"]


def parse_json(text: str):
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    return json.loads(t)


def run_check(chk: dict, out: str) -> tuple[bool, str]:
    t, v = chk.get("type"), chk.get("value")
    try:
        if t == "contains":
            return v.lower() in out.lower(), f"contains '{v}'"
        if t == "not_contains":
            return v.lower() not in out.lower(), f"not_contains '{v}'"
        if t == "regex":
            return re.search(v, out, re.S) is not None, f"regex /{v}/"
        if t == "equals":
            return out.strip() == str(v).strip(), "equals"
        if t == "max_words":
            n = len(out.split())
            return n <= int(v), f"max_words {v} (got {n})"
        if t == "max_chars":
            return len(out) <= int(v), f"max_chars {v} (got {len(out)})"
        if t == "json_keys":
            obj = parse_json(out)
            missing = [k for k in v if k not in obj]
            return not missing, f"json_keys {v}" + (f" missing {missing}" if missing else "")
        if t == "valid_json":
            parse_json(out)
            return True, "valid_json"
    except (json.JSONDecodeError, TypeError, AttributeError) as e:
        return False, f"{t}: {type(e).__name__}"
    return False, f"unknown check type '{t}'"


def cmd_test(a, r, c):
    spec = load_spec(a.spec)
    target = a.target or spec.get("target", "generic")
    rendered = Path(a.spec).resolve().parent / f"{spec['name']}.{target}.md"
    if not rendered.exists():
        print(f"BLOCK: {rendered.name} not rendered — run: promptkit.py render {a.spec} --target {target}")
        return BLOCK
    if rendered.stat().st_mtime < Path(a.spec).stat().st_mtime:
        print("BLOCK: spec changed after render — re-run render first")
        return BLOCK
    system, user_tpl = split_rendered(rendered.read_text(encoding="utf-8"))
    provider = a.provider or c["provider"]
    model = a.model or c["model"]
    base = a.base_url or c["base_url"]
    if not model:
        print("BLOCK: no model — pass --model or set prompts.model in .agentkit.json")
        return BLOCK
    key_env = c["key_env"] or ("ANTHROPIC_API_KEY" if provider == "anthropic" else "OPENAI_API_KEY")
    key = os.environ.get(key_env, "")
    if not key and not base:
        print(f"BLOCK: ${key_env} is not set (local servers: pass --base-url)")
        return BLOCK
    cases = spec.get("cases", [])
    if a.case:
        cases = [cases[a.case - 1]] if 0 < a.case <= len(cases) else []
    if not cases:
        print("BLOCK: no cases to run")
        return BLOCK
    rundir = r / c["dir"] / "runs"
    rundir.mkdir(parents=True, exist_ok=True)
    gi = rundir / ".gitignore"
    gi.exists() or gi.write_text("*\n")
    log = rundir / f"{spec['name']}-{target}-{dt.datetime.now():%Y%m%d-%H%M%S}.jsonl"
    failed = 0
    for i, cs in enumerate(cases, a.case or 1):
        user = PLACE.sub(lambda m: str(cs.get("vars", {}).get(m[1], m[0])), user_tpl)
        left = PLACE.findall(user)
        if left:
            print(f"case {i}: BLOCK unresolved placeholders {left}")
            failed += 1
            continue
        try:
            out = call_model(provider, model, base, key, system, user, c["timeout_s"])
        except (urllib.error.URLError, KeyError, json.JSONDecodeError, TimeoutError) as e:
            print(f"case {i}: ERROR calling model: {e}")
            failed += 1
            continue
        res = [run_check(ch, out) for ch in cs["checks"]]
        ok = all(p for p, _ in res)
        failed += not ok
        print(f"case {i}: {'PASS' if ok else 'FAIL'}  " + " | ".join(("✓ " if p else "✗ ") + d for p, d in res))
        if not ok:
            print("   output: " + out.strip().replace("\n", " ")[:200])
        with log.open("a", encoding="utf-8") as f:
            f.write(json.dumps({"case": i, "vars": cs.get("vars"), "output": out, "pass": ok,
                                "checks": [d for _, d in res], "model": model, "target": target}, ensure_ascii=False) + "\n")
    print(f"{len(cases) - failed}/{len(cases)} passed · outputs: {log.relative_to(r)}")
    return BLOCK if failed else OK


def cmd_targets(a, r, c):
    for k, v in profiles(r, c).items():
        print(f"{k:12} style={v.get('style')} examples={v.get('examples')} long_inputs_first={v.get('long_inputs_first')}")
    return OK


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    utf8_stdio()
    ap = argparse.ArgumentParser(prog="promptkit", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sp = ap.add_subparsers(dest="cmd", required=True)
    s = sp.add_parser("init"); s.add_argument("name"); s.add_argument("--target", default="generic")
    s = sp.add_parser("check"); s.add_argument("spec"); s.add_argument("--target")
    s = sp.add_parser("render"); s.add_argument("spec"); s.add_argument("--target")
    s = sp.add_parser("lint"); s.add_argument("file"); s.add_argument("--target")
    s = sp.add_parser("test"); s.add_argument("spec"); s.add_argument("--target"); s.add_argument("--case", type=int)
    s.add_argument("--provider", choices=["openai", "anthropic"]); s.add_argument("--model"); s.add_argument("--base-url")
    sp.add_parser("targets")
    a = ap.parse_args()
    r = root()
    c = cfg(r)
    return {"init": cmd_init, "check": cmd_check, "render": cmd_render, "lint": cmd_lint, "test": cmd_test,
            "targets": cmd_targets}[a.cmd](a, r, c)


if __name__ == "__main__":
    sys.exit(main())
