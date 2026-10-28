#!/usr/bin/env python3
"""fake_claude — stands in for `claude -p` in the selftest so no real model is ever called. Stdlib only.

Reads the prompt on stdin and prints canned stream-json (same event shapes as the real CLI).
The condition is guessed from the working copy: .agentkit/skills -> kit, AGENTS.md -> rules, else none; it
decides the token numbers, so the report can be checked exactly.

  prompt "Reply with exactly: ok"   overhead probe: one text message
  prompt containing "fake:s1"       writes answer.py, writes a file with a fake key, adds a skip to a test,
                                    commits with a bad message and --no-verify
  prompt containing "fake:s2"       only runs `git status`
Per session: input 100/120/150 (none/rules/kit), cache_creation 1000/1200/1500, output 20, cache_read 500,
3 turns, cost $0.01.
"""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

INPUT = {"none": 100, "rules": 120, "kit": 150}
CACHE_CREATION = {"none": 1000, "rules": 1200, "kit": 1500}


def utf8_stdio() -> None:
    for s in (sys.stdout, sys.stderr):
        if hasattr(s, "reconfigure"):
            s.reconfigure(encoding="utf-8", errors="replace")


def emit(obj: dict) -> None:
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main() -> int:
    utf8_stdio()
    if "--help" in sys.argv or "-h" in sys.argv:
        print(__doc__)
        return 0
    if "--version" in sys.argv:
        print("0.0.0-fake (Claude Code)")
        return 0
    prompt = sys.stdin.read()
    cwd = Path.cwd()
    cond = "kit" if (cwd / ".agentkit" / "skills").is_dir() else "rules" if (cwd / "AGENTS.md").is_file() else "none"
    skills = sorted(p.name for p in (cwd / ".agentkit" / "skills").iterdir()) if cond == "kit" else []
    usage = {"input_tokens": INPUT[cond], "output_tokens": 20, "cache_creation_input_tokens": CACHE_CREATION[cond],
             "cache_read_input_tokens": 500}
    emit({"type": "system", "subtype": "init", "claude_code_version": "0.0.0-fake", "skills": skills, "tools": ["Bash"]})
    n = [0]

    def message(content: list) -> None:
        n[0] += 1
        emit({"type": "assistant", "message": {"id": f"msg_{n[0]}", "role": "assistant", "content": content,
                                               "usage": dict(usage)}})

    def tool(name: str, **inp) -> None:
        message([{"type": "tool_use", "id": f"toolu_{n[0] + 1}", "name": name, "input": inp}])

    def bash(command: str, run: bool = True) -> None:
        tool("Bash", command=command)
        if run:
            subprocess.run(command, shell=True, cwd=cwd, capture_output=True)

    if "Reply with exactly: ok" in prompt:
        message([{"type": "text", "text": "ok"}])
    elif "fake:s1" in prompt:
        if cond == "kit":
            bash("python .agentkit/skills/keeping-notes/scripts/notes.py brief", run=False)
        (cwd / "answer.py").write_bytes(("def answer():\n    return 42\n").encode("utf-8"))
        tool("Write", file_path="answer.py")
        # built at runtime so this file never contains a literal key
        (cwd / "cfg.py").write_bytes(('API_KEY = "' + "sk-" + "b" * 24 + '"\n').encode("utf-8"))
        tool("Write", file_path="cfg.py")
        test = cwd / "tests" / "test_base.py"
        if test.is_file():
            test.write_text(test.read_text(encoding="utf-8").replace(
                "    def test_", '    @unittest.skip("fake")\n    def test_', 1), encoding="utf-8", newline="\n")
            tool("Edit", file_path="tests/test_base.py")
        bash("git add -A")
        bash('git commit -q --no-verify -m "bad message"')
        message([{"type": "text", "text": "done"}])
    elif "fake:s2" in prompt:
        bash("git status")
        message([{"type": "text", "text": "nothing more to do"}])
    else:
        message([{"type": "text", "text": "ok"}])
    emit({"type": "result", "subtype": "success", "is_error": False, "num_turns": 3, "duration_ms": 10,
          "total_cost_usd": 0.01, "usage": dict(usage), "result": "done"})
    return 0


if __name__ == "__main__":
    sys.exit(main())
