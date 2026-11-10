"""Tests for scripts/guard.py (the logic behind guard-git.sh). Stdlib unittest, Python 3.9+.

Run: python -m unittest scripts/tests/test_guard_py.py -v
"""
import os
import subprocess
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import guard  # noqa: E402

S = guard.SEP


class TokenizeTests(unittest.TestCase):
    def test_tokenize_table(self):
        table = [
            ("", []),
            ("   \t ", []),
            ("git status", ["git", "status"]),
            ("git\tstatus", ["git", "status"]),
            ('""', [""]),
            ("''", [""]),
            ('a "" b', ["a", "", "b"]),
            ('echo "a b"', ["echo", "a b"]),
            ("echo 'a b'", ["echo", "a b"]),
            ("a\"b c\"d", ["ab cd"]),
            ('\'"a"\'', ['"a"']),                      # double quotes inside single quotes
            ('"it\'s"', ["it's"]),                     # single quote inside double quotes
            ('"a\\"b"', ['a"b']),                      # escaped quote inside double quotes
            ('"a\\\\b"', ['a\\b']),                    # escaped backslash inside double quotes
            ("'a\\b'", ["a\\b"]),                      # backslash is literal inside single quotes
            ("a\\ b", ["a b"]),                        # escaped space outside quotes
            ("a\\;b", ["a;b"]),                        # escaped separator outside quotes
            ("a\\", ["a"]),                            # trailing lone backslash is dropped
            ("\\", []),
            ('"abc', ["abc"]),                         # unterminated quote still yields the word
            ("a && b", ["a", S, "b"]),
            ("a || b", ["a", S, "b"]),
            ("a | b", ["a", S, "b"]),
            ("a & b", ["a", S, "b"]),
            ("a&&b||c|d&e", ["a", S, "b", S, "c", S, "d", S, "e"]),
            ("a &&& b", ["a", S, S, "b"]),             # only an identical next char is swallowed
            ("a |& b", ["a", S, S, "b"]),
            ("a;b", ["a", S, "b"]),
            ("a\nb", ["a", S, "b"]),
            ("(a)", [S, "a", S]),
            ("`a`", [S, "a", S]),
            ("$(a)", ["$", S, "a", S]),
            ('echo "a && b"', ["echo", "a && b"]),
            ("git commit -m 'x; y'", ["git", "commit", "-m", "x; y"]),
            ('bash -c "bash -c \\"git merge x\\""', ["bash", "-c", 'bash -c "git merge x"']),
            ("a" + S + "b", ["a" + S + "b"]),          # a SEP char inside a word is not a separator
        ]
        for text, want in table:
            with self.subTest(text=text):
                self.assertEqual(guard.tokenize(text), want)

    def test_segments_drop_empty(self):
        table = [
            ([], []),
            ([S], []),
            ([S, S], []),
            (["a"], [["a"]]),
            (["a", S, "b", "c"], [["a"], ["b", "c"]]),
            ([S, "a", S, S, "b", S], [["a"], ["b"]]),
            ([""], [[""]]),                            # an empty word is a word, not a separator
        ]
        for toks, want in table:
            with self.subTest(toks=toks):
                self.assertEqual(guard.segments(toks), want)


class ConfigTests(unittest.TestCase):
    def test_cfg_defaults_and_types(self):
        data = {
            "git": {"protected_branches": ["trunk", "release/*"], "protected_commit": "block", "n": 3, "f": guard._Num("1.0"), "off": False},
            "guards": {"merge_convention": "merge commit named X\r\n", "empty": "", "nul": None, "obj": {"a": [1, "b"]}},
            "scalar": "x",
        }
        c = guard.Config(data)
        table = [
            ("git.protected_branches", "d", "trunk\nrelease/*"),     # array: one element per line
            ("git.protected_commit", "d", "block"),
            ("git.n", "d", "3"),
            ("git.f", "d", "1.0"),                                    # the literal is kept, like jq 1.7
            ("git.off", "d", "false"),                                # false is a real value, not "absent"
            ("guards.merge_convention", "d", "merge commit named X"), # CR and trailing newline removed
            ("guards.empty", "d", "d"),                               # empty string -> default
            ("guards.nul", "d", "d"),                                 # null -> default
            ("guards.obj", "d", '{"a":[1,"b"]}'),                     # object: compact JSON
            ("guards.missing", "d", "d"),
            ("missing.deep.path", "d", "d"),
            ("scalar.deeper", "d", "d"),                              # walking into a string -> default
            ("git.protected_branches.x", "d", "d"),                   # walking into an array -> default
            ("git.nope", "", ""),
        ]
        for path, default, want in table:
            with self.subTest(path=path):
                self.assertEqual(c.cfg(path, default), want)

    def test_cfg_from_file(self):
        with tempfile.TemporaryDirectory() as d:
            f = os.path.join(d, ".agentkit.json")
            self.assertEqual(guard.Config.from_file(f).warning, "")          # missing file: defaults, no warning
            self.assertEqual(guard.Config.from_file(f).cfg("a.b", "dflt"), "dflt")
            cases = [("not json", True), ("", True), ("null", True), ("false", True),
                     ("{}", False), ('{"a":{"b":"v"}}', False), ("﻿{}", False), ("0", False), ("[]", False)]
            for text, warns in cases:
                with self.subTest(text=text):
                    with open(f, "w", encoding="utf-8", newline="") as fh:
                        fh.write(text)
                    c = guard.Config.from_file(f)
                    self.assertEqual(bool(c.warning), warns)
                    if warns:
                        self.assertEqual(c.warning, f"WARN: {f} is not valid JSON; using defaults")
                        self.assertEqual(c.cfg("a.b", "dflt"), "dflt")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write('{"a":{"b":"v"}}')
            self.assertEqual(guard.Config.from_file(f).cfg("a.b", "dflt"), "v")
            with open(f, "w", encoding="utf-8") as fh:
                fh.write('{"x":1.0,"y":1E+3,"z":7}')
            c = guard.Config.from_file(f)
            self.assertEqual([c.cfg("x"), c.cfg("y"), c.cfg("z")], ["1.0", "1E+3", "7"])  # literals kept, like jq 1.7

    def test_is_protected_globs(self):
        default = guard.Config()
        for b in ("main", "master", "develop", "release/1.0", "release/a/b"):
            self.assertTrue(default.is_protected(b), b)
        for b in ("feat/x", "release", "mainline", "Main", ""):
            self.assertFalse(default.is_protected(b), b)
        table = [
            ("[!x]", "y", True), ("[!x]", "x", False),
            ("[^x]", "y", True), ("[^x]", "x", False),                     # bash treats ^ like !, fnmatch does not
            ("[a-c]1", "b1", True), ("[a-c]1", "d1", False),
            ("ma?n", "main", True), ("ma?n", "man", False),
            ("*", "a/b", True), ("a*", "a/b/c", True),                      # * crosses "/"
            ("\\*", "*", True), ("\\*", "a", False),
            ("[[:digit:]]x", "5x", True), ("[[:digit:]]x", "ax", False),
            ("[", "[", True), ("a[b", "a[b", True),                         # no closing bracket: literal
            ("[]]", "]", True),
            ("v1.0", "v1x0", False),                                        # "." is literal
        ]
        for pat, text, want in table:
            with self.subTest(pat=pat, text=text):
                self.assertEqual(guard.glob_match(pat, text), want)
        c = guard.Config({"git": {"protected_branches": ["trunk", "", "rel-*"]}})
        self.assertTrue(c.is_protected("trunk"))
        self.assertTrue(c.is_protected("rel-2"))
        self.assertFalse(c.is_protected("main"))                            # configured list replaces the default


class GitTests(unittest.TestCase):
    def test_branch_helpers(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            cwd = os.getcwd()
            try:
                subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
                subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
                self.assertEqual(guard.current_branch(d), "main")
                os.chdir(d)
                self.assertEqual(guard.current_branch(), "main")
                self.assertTrue(guard.branch_exists("main"))
                self.assertFalse(guard.branch_exists("nope"))
                subprocess.run(["git", "checkout", "-q", "--detach"], check=True)
                self.assertEqual(guard.current_branch(), "")
                with open(".agentkit.json", "w", encoding="utf-8") as fh:
                    fh.write('{"git":{"protected_commit":"allow"}}')
                os.makedirs("sub")
                self.assertEqual(guard.load_config("sub").cfg("git.protected_commit", "ask"), "allow")  # found from a subdirectory
            finally:
                os.chdir(cwd)
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v
            self.assertEqual(guard.current_branch(tempfile.gettempdir()), "")  # not a repo


if __name__ == "__main__":
    unittest.main()
