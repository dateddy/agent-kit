"""Tests for scripts/verify.py (the verify engine) and scripts/akcore.py. Stdlib unittest.

Run: python -m unittest scripts/tests/test_verify_py.py -v
The tools are bash stubs on PATH (ruff, pytest, diff-cover): they log their arguments to $STUB_LOG and take their
exit codes from STUB_RUFF_RC / STUB_PYTEST_RC / STUB_DC_RC. The old bash tests (test_verify.sh) stay the behaviour
reference; these cover what is new: the python modes, the unified config and the deprecated keys.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))
import akcore  # noqa: E402

VERIFY = SCRIPTS / "verify.py"
STUBS = {
    "ruff": 'echo "ruff $*" >>"$STUB_LOG"\n[ "${STUB_RUFF_RC:-0}" -ne 0 ] && echo "ruff: E501 line too long (stub)"\nexit "${STUB_RUFF_RC:-0}"\n',
    "pytest": 'echo "pytest $*" >>"$STUB_LOG"\nfor a in "$@"; do case $a in --cov-report=xml:*) mkdir -p "$(dirname "${a#--cov-report=xml:}")"; '
              'echo "<coverage/>" >"${a#--cov-report=xml:}" ;; esac; done\n[ "${STUB_PYTEST_RC:-0}" -ne 0 ] && echo "1 failed (stub)"\nexit "${STUB_PYTEST_RC:-0}"\n',
    "diff-cover": 'echo "diff-cover $*" >>"$STUB_LOG"\n[ "${STUB_DC_RC:-0}" -ne 0 ] && echo "Failure. Coverage is 50% (stub)"\nexit "${STUB_DC_RC:-0}"\n',
}
HAVE_BASH = akcore.find_bash() is not None or not akcore.IS_NT


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout


@unittest.skipUnless(HAVE_BASH, "needs bash for the tool stubs")
class VerifyCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp(prefix="ak-verify-py-"))
        cls.bin = cls.tmp / "bin"
        cls.bin.mkdir()
        for name, body in STUBS.items():
            p = cls.bin / name
            p.write_text("#!/usr/bin/env bash\n" + body, encoding="utf-8", newline="\n")
            p.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        def onerr(fn, path, _exc):
            os.chmod(path, 0o700)
            fn(path)

        shutil.rmtree(cls.tmp, onerror=onerr)

    def setUp(self):
        self.repo = Path(tempfile.mkdtemp(prefix="repo-", dir=self.tmp))
        git(self.repo, "init", "-q", "-b", "main")
        for k, v in (("user.name", "tester"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            git(self.repo, "config", k, v)
        self.commit("README.md", "hello")
        self.log = self.tmp / f"{self.repo.name}.log"
        self.log.write_text("", encoding="utf-8")

    def commit(self, path, text="x\n"):
        p = self.repo / path
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8", newline="\n")
        git(self.repo, "add", "--", path)
        git(self.repo, "commit", "-q", "-m", f"feat: {path}")

    def branch(self, name="feat/y"):
        git(self.repo, "switch", "-q", "-c", name)

    def cfg(self, data):
        (self.repo / ".agentkit.json").write_text(json.dumps(data), encoding="utf-8")

    def run_v(self, *args, stdin="", env=None, cwd=None):
        e = {**os.environ, "PATH": str(self.bin) + os.pathsep + os.environ["PATH"], "STUB_LOG": str(self.log),
             "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, **(env or {})}
        p = subprocess.run([sys.executable, str(VERIFY), *args], cwd=cwd or self.repo, input=stdin, capture_output=True,
                           text=True, env=e, encoding="utf-8")
        return p.returncode, p.stdout, p.stderr

    def calls(self):
        return self.log.read_text(encoding="utf-8")


class ModeTests(VerifyCase):
    def failing_lint(self):
        self.branch()
        self.commit("src/a.py")
        return {"STUB_RUFF_RC": "1"}

    def test_modes_cli_hook_push_exit_codes(self):
        env = self.failing_lint()
        rc, out, err = self.run_v(env=env)
        self.assertEqual((rc, out), (1, ""), err)
        self.assertIn("VERDICT: FAIL (exit 1)", err)
        self.assertIn("lint        FAIL", err)
        rc, out, err = self.run_v("--hook", stdin="{}", env=env)
        self.assertEqual((rc, out), (2, ""), err)
        self.assertIn("VERDICT: FAIL (exit 2)", err)
        rc, out, err = self.run_v("--push", env=env)
        self.assertEqual((rc, out), (1, ""), err)

    def test_all_pass_is_exit_0_in_every_mode(self):
        self.branch()
        self.commit("src/a.py")
        for args in ((), ("--push",), ("--hook",)):
            rc, out, err = self.run_v(*args, stdin="{}")
            self.assertEqual((rc, out), (0, ""), f"{args}: {err}")
            self.assertIn("VERDICT: PASS (exit 0)", err)

    def test_stop_hook_active_never_blocks(self):
        env = self.failing_lint()
        rc, _, err = self.run_v("--hook", stdin='{"stop_hook_active": false}', env=env)
        self.assertEqual(rc, 2, err)
        rc, out, err = self.run_v("--hook", stdin='{"stop_hook_active" : true}', env=env)
        self.assertEqual((rc, out), (0, ""), err)
        self.assertIn("stop_hook_active", err)
        self.assertIn("lint        FAIL", err)

    def test_hook_lints_only_the_changed_python_files(self):
        self.branch()
        self.commit("src/a.py")
        self.commit("docs/readme.md")
        self.run_v("--hook", stdin="{}")
        self.assertIn("ruff check --quiet --output-format concise --force-exclude", self.calls())
        self.assertIn("src/a.py", self.calls())
        self.assertNotIn("docs/readme.md", self.calls())

    def test_hook_with_nothing_changed_is_silent_and_runs_nothing(self):
        rc, out, err = self.run_v("--hook", stdin="{}")
        self.assertEqual((rc, out, err), (0, "", ""))
        self.assertEqual(self.calls(), "")

    def test_usage_error_exits_1_never_2(self):
        rc, _, err = self.run_v("--bogus")
        self.assertEqual(rc, 1, err)

    def test_stages_alias_only_and_test_name(self):
        self.branch()
        self.commit("src/a.py")
        rc, _, err = self.run_v("--only", "lint,test")
        self.assertEqual(rc, 0, err)
        self.assertIn("pytest --cov", self.calls())
        self.assertIn("diff-cover  SKIP  not selected (--stages)", err)

    def test_not_a_repo(self):
        plain = Path(tempfile.mkdtemp(dir=self.tmp))
        rc, _, err = self.run_v(cwd=plain)
        self.assertEqual(rc, 1)
        self.assertIn("not inside a git repository", err)
        self.assertEqual(self.run_v("--hook", stdin="{}", cwd=plain)[0], 0)


class EditModeTests(VerifyCase):
    def edit(self, path):
        return self.run_v("--edit", stdin=json.dumps({"tool_input": {"file_path": str(path)}}))

    def test_edit_mode_in_repo_only(self):
        (self.repo / "m.py").write_text("x=1\n", encoding="utf-8")
        rc, out, err = self.edit(self.repo / "m.py")
        self.assertEqual((rc, out), (0, ""), err)
        lines = self.calls().strip().splitlines()
        self.assertEqual(len(lines), 2, lines)
        self.assertTrue(lines[0].startswith("ruff check --fix --quiet --force-exclude") and lines[0].endswith("m.py"))
        self.assertTrue(lines[1].startswith("ruff format --quiet --force-exclude") and lines[1].endswith("m.py"))
        self.log.write_text("", encoding="utf-8")
        other = Path(tempfile.mkdtemp(dir=self.tmp))
        git(other, "init", "-q")
        (other / "theirs.py").write_text("x=1\n", encoding="utf-8")
        loose = self.tmp / "loose.py"
        loose.write_text("x=1\n", encoding="utf-8")
        for f in (other / "theirs.py", loose, self.repo / "missing.py", self.repo / "README.md"):
            self.assertEqual(self.edit(f)[0], 0)
        self.assertEqual(self.calls(), "")

    def test_edit_reports_leftovers_and_missing_tool(self):
        (self.repo / "m.py").write_text("x=1\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(self.repo / "m.py")}})
        rc, _, err = self.run_v("--edit", stdin=payload, env={"STUB_RUFF_RC": "1"})
        self.assertEqual(rc, 2)
        self.assertIn("ruff left problems in", err)
        self.cfg({"guards": {"ruff_cmd": "no-such-ruff"}})
        rc, _, err = self.run_v("--edit", stdin=payload)
        self.assertEqual(rc, 2)
        self.assertIn("no-such-ruff not found", err)
        self.cfg({"verify": {"lint": False, "format": False}})
        self.assertEqual(self.run_v("--edit", stdin=payload)[0], 0)

    def test_edit_without_file_is_a_noop(self):
        self.assertEqual(self.run_v("--edit", stdin='{"tool_input":{}}')[0], 0)
        self.assertEqual(self.run_v("--edit", stdin="not json")[0], 0)


class AutoToolTests(VerifyCase):
    """"auto" (the default) runs a stage when its tool exists and SKIPs it when not; true or an explicit tool key means
    required (FAIL when missing); false / null switch it off. Applies to every stage, diff-cover included."""

    def path_with(self, *tools):
        """A PATH holding only python, git, bash and the named stubs: no other ruff / pytest / diff-cover can be found."""
        d = Path(tempfile.mkdtemp(dir=self.tmp))
        for t in tools:
            shutil.copy(self.bin / t, d / t)
        dirs = [d, Path(sys.executable).parent, Path(shutil.which("git")).parent]
        if akcore.find_bash():
            dirs.append(Path(akcore.find_bash()).parent)
        return os.pathsep.join(str(x) for x in dict.fromkeys(dirs))

    def changed(self):
        self.branch()
        self.commit("src/a.py")

    def test_repo_without_diff_cover_passes_with_a_skip(self):
        self.changed()
        rc, out, err = self.run_v(env={"PATH": self.path_with("ruff", "pytest")})
        self.assertEqual((rc, out), (0, ""), err)
        self.assertRegex(err, r"diff-cover  SKIP  diff-cover not found \(auto")
        self.assertIn("VERDICT: PASS (exit 0)", err)
        self.assertIn("pytest --cov", self.calls())

    def test_true_makes_a_missing_tool_a_failure(self):
        self.changed()
        self.cfg({"verify": {"diff_cover": True}})
        rc, _, err = self.run_v(env={"PATH": self.path_with("ruff", "pytest")})
        self.assertEqual(rc, 1, err)
        self.assertIn("diff-cover  FAIL", err)
        self.assertIn("diff-cover not found: install it, or set verify.diff_cover to false", err)

    def test_every_missing_tool_is_a_skip_under_auto(self):
        self.changed()
        rc, _, err = self.run_v(env={"PATH": self.path_with()})
        self.assertEqual(rc, 0, err)
        for stage in ("lint", "tests", "diff-cover"):
            self.assertRegex(err, stage + r"\s+SKIP\s+\S+ not found \(auto")

    def test_an_explicit_tool_key_is_required(self):
        self.changed()
        for cfg, stage in (({"verify": {"ruff_cmd": "no-such-ruff"}}, "lint"), ({"verify": {"pytest_cmd": "no-such-pytest"}}, "tests"),
                           ({"guards": {"diff_cover_cmd": "no-such-dc"}}, "diff-cover")):
            self.cfg(cfg)
            rc, _, err = self.run_v(env={"PATH": self.path_with("ruff", "pytest")})
            self.assertEqual(rc, 1, err)
            self.assertRegex(err, stage + r"\s+FAIL")

    def test_missing_coverage_report_skips_under_auto_and_fails_when_required(self):
        self.changed()
        self.cfg({"verify": {"tests": False}})
        rc, _, err = self.run_v()
        self.assertEqual(rc, 0, err)
        self.assertRegex(err, r"diff-cover  SKIP  no coverage report")
        self.cfg({"verify": {"tests": False, "diff_cover": True}})
        rc, _, err = self.run_v()
        self.assertEqual(rc, 1, err)
        self.assertIn("is missing: diff-cover needs the coverage report", err)

    def test_false_and_null_switch_a_stage_off(self):
        self.changed()
        self.cfg({"verify": {"diff_cover": False, "lint": None}})
        rc, _, err = self.run_v()
        self.assertEqual(rc, 0, err)
        self.assertIn("diff-cover  SKIP  disabled: verify.diff_cover=false", err)
        self.assertIn("lint        SKIP  disabled: verify.lint=null", err)

    def test_edit_without_ruff_is_silent_under_auto_and_loud_when_required(self):
        (self.repo / "m.py").write_text("x=1\n", encoding="utf-8")
        payload = json.dumps({"tool_input": {"file_path": str(self.repo / "m.py")}})
        env = {"PATH": self.path_with()}
        self.assertEqual(self.run_v("--edit", stdin=payload, env=env)[0], 0)
        self.cfg({"verify": {"lint": True}})
        rc, _, err = self.run_v("--edit", stdin=payload, env=env)
        self.assertEqual(rc, 2)
        self.assertIn("ruff not found", err)


class ConfigTests(VerifyCase):
    def test_legacy_keys_warn_and_apply(self):
        self.branch()
        self.commit("src/a.py")
        self.cfg({"guards": {"ruff": False, "pytest_cmd": "pytest -p no:cacheprovider", "fail_under": 70}})
        rc, _, err = self.run_v()
        self.assertEqual(rc, 0, err)
        self.assertIn("WARN deprecated: guards.ruff -> verify.lint / verify.format", err)
        self.assertIn("WARN deprecated: guards.pytest_cmd -> verify.pytest_cmd", err)
        self.assertIn("lint        SKIP  disabled: guards.ruff=false", err)
        self.assertIn("pytest -p no:cacheprovider --cov", self.calls())
        self.assertIn("--fail-under=70", self.calls())
        self.assertNotIn("ruff", self.calls())

    def test_new_keys_win_over_legacy(self):
        self.branch()
        self.commit("src/a.py")
        self.cfg({"verify": {"lint": False, "fail_under": 55}, "guards": {"ruff": True, "fail_under": 70}})
        rc, _, err = self.run_v()
        self.assertEqual(rc, 0, err)
        self.assertIn("lint        SKIP  disabled: verify.lint=false", err)
        self.assertIn("--fail-under=55", self.calls())

    def test_old_verify_test_key_maps_to_tests(self):
        self.branch()
        self.commit("src/a.py")
        self.cfg({"verify": {"test": "false", "diff_cover": False}})
        rc, _, err = self.run_v()
        self.assertEqual(rc, 0, err)
        self.assertIn("WARN deprecated: verify.test -> verify.tests", err)
        self.assertIn("tests       SKIP  disabled: verify.tests=false", err)

    def test_custom_command_runs_verbatim(self):
        self.branch()
        self.commit("src/a.py")
        self.cfg({"verify": {"lint": f'"{sys.executable}" -c "import sys; print(\'bad\'); sys.exit(1)"'}})
        rc, _, err = self.run_v("--stages", "lint")
        self.assertEqual(rc, 1)
        self.assertIn("lint        FAIL", err)
        self.assertIn("bad", err)

    def test_null_gate_is_disabled_as_in_the_0_5_0_skill(self):
        self.branch()
        self.commit("src/a.py")
        self.cfg({"verify": {"format": None, "typecheck": None, "test": None}})
        rc, out, err = self.run_v("--detect")
        rows = {line.split()[0]: line for line in out.splitlines()}
        self.assertIn("disabled: verify.format=null", rows["format"])
        self.assertIn("disabled: verify.tests=null", rows["tests"])
        rc, _, err = self.run_v("--stages", "lint,format,tests,scope")
        self.assertEqual(rc, 0, err)
        self.assertIn("ruff check", self.calls())
        self.assertNotIn("ruff format", self.calls(), "a null format also keeps `ruff format --check` out of lint")
        self.assertNotIn("pytest", self.calls())

    def test_detect_lists_commands(self):
        (self.repo / "package.json").write_text(json.dumps({"scripts": {"lint": "eslint .", "test": "jest"}}), encoding="utf-8")
        rc, out, _ = self.run_v("--detect")
        self.assertEqual(rc, 0)
        rows = {line.split()[0]: line for line in out.splitlines()}
        self.assertIn("npm run lint", rows["lint"])
        self.assertIn("npm run test", rows["tests"])
        self.assertIn("package.json script 'lint'", rows["lint"])
        self.assertIn("diff-cover", rows["diff-cover"])
        self.assertEqual(set(rows), set(__import__("verify").ALL_STAGES))

    def test_unknown_stage_in_config_blocks_a_hook(self):
        self.branch()
        self.commit("src/a.py")
        self.cfg({"verify": {"hook_stages": "lint, typo"}})
        rc, _, err = self.run_v("--hook", stdin="{}")
        self.assertEqual(rc, 2)
        self.assertIn("unknown stage 'typo' in verify.hook_stages", err)


PLAN = ("# Plan: demo\nConstraints: none\nDone when: x\nOut of scope: y\n\n### Step 1: Parse\n- Mode: solo\n"
        "- Files: src/a.py\n- Contract: c\n- Tests: t\n- Out of scope: o\n")


class ScopeTests(VerifyCase):
    def plan(self, crlf):
        self.branch("feat/x")
        notes = self.repo / "notes" / "tasks"
        notes.mkdir(parents=True)
        (notes / "feat--x.plan.md").write_text(PLAN, encoding="utf-8", newline="\n")
        note = "# Task: demo\n- branch: feat/x\n\n## Plan\n- [ ] 1. Parse\n\n## Next\nx\n"
        (notes / "feat--x.md").write_bytes((note.replace("\n", "\r\n") if crlf else note).encode("utf-8"))

    def test_scope_reads_a_crlf_task_note(self):
        """nt writes CRLF notes on Windows; plan.py used to find no checklist in them and the scope stage failed."""
        self.plan(crlf=True)
        self.commit("src/a.py")
        rc, _, err = self.run_v("--stages", "scope")
        self.assertEqual(rc, 0, err)
        self.assertIn("scope       PASS", err)
        self.commit("src/other.py")
        rc, _, err = self.run_v("--stages", "scope")
        self.assertEqual(rc, 1)
        self.assertIn("Out-of-plan file: src/other.py. Log the reason in the decision log or revert.", err)


class IntegrityTests(VerifyCase):
    def test_integrity_cli_exit_2_on_a_deleted_test(self):
        self.branch()
        self.commit("tests/test_a.py", "def test_a():\n    assert 1\n")
        (self.repo / "tests" / "test_a.py").unlink()
        rc, out, err = self.run_v("--stages", "integrity")
        self.assertEqual((rc, out), (2, ""), err)
        self.assertIn("test file DELETED: tests/test_a.py", err)
        self.assertIn("VERDICT: PASS WITH TEST CHANGES (exit 2)", err)

    def test_integrity_in_hook_blocks_and_in_push_only_reports(self):
        self.branch()
        self.commit("tests/test_a.py", "def test_a():\n    assert 1\n")
        (self.repo / "tests" / "test_a.py").unlink()
        self.assertEqual(self.run_v("--hook", "--stages", "integrity", stdin="{}")[0], 2)
        rc, _, err = self.run_v("--push", "--stages", "integrity")
        self.assertEqual(rc, 0, err)
        self.assertIn("test file DELETED", err)


class AkcoreTests(unittest.TestCase):
    def test_slug_matches_notes_py(self):
        self.assertEqual(akcore.slug("feat/x"), "feat--x")
        self.assertEqual(akcore.slug("-a b/c!-"), "a-b--c")

    def test_as_list_accepts_arrays_and_separated_strings(self):
        self.assertEqual(akcore.as_list(["a", "b"]), ["a", "b"])
        self.assertEqual(akcore.as_list("lint, scope\ntests"), ["lint", "scope", "tests"])
        self.assertEqual(akcore.as_list(None), [])

    def test_is_off(self):
        self.assertTrue(all(akcore.is_off(v) for v in (False, "false", "0", 0, "No", "off")))
        self.assertFalse(any(akcore.is_off(v) for v in (True, "auto", "x")))


if __name__ == "__main__":
    unittest.main()
