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


class Recorder(guard.Judge):
    """A Judge whose rules only record how they were called: (rule, branch, args)."""

    def __init__(self, cfg, branch):
        super().__init__(cfg, branch)
        self.calls = []

    def rule_merge(self, b, args):
        self.calls.append(("merge", b, list(args)))

    def rule_commit(self, b):
        self.calls.append(("commit", b))

    def rule_push(self, b, args):
        self.calls.append(("push", b, list(args)))


PROTECT_ALL = guard.Config({"git": {"protected_branches": ["*"]}})  # every named branch is protected, so merge/commit reach the rules


def walk(cmd, branch="main", cfg=None):
    j = Recorder(cfg or PROTECT_ALL, branch)
    j.scan(cmd, 0)
    return j


class ScanTests(unittest.TestCase):
    def test_and_and_pipe_mixed(self):
        table = [
            ("git commit -m x", [("commit", "main")]),
            ("echo hi && git commit -m x", [("commit", "main")]),
            ("git fetch | git merge x || git push", [("merge", "main", ["x"]), ("push", "main", [])]),
            ("git commit -m x; git push origin main", [("commit", "main"), ("push", "main", ["origin", "main"])]),
            ("echo 'git commit' && ls", []),
            ('echo "git merge x; git commit"', []),
            ("git status & git merge --no-ff x", [("merge", "main", ["--no-ff", "x"])]),
            ("(git commit -m x)", [("commit", "main")]),
            ("echo `git commit -m x`", [("commit", "main")]),
            ("git\ncommit", []),                                      # newline separates commands: "git" alone, then "commit"
            ("git commit\ngit push", [("commit", "main"), ("push", "main", [])]),
            ("", []),
            ("   ", []),
            ('""', []),
            ("&& ||", []),
            ("git", []),                                              # no subcommand
            ("git -C", []),                                           # option without a value, no subcommand
            ("git log", []),
        ]
        for cmd, want in table:
            with self.subTest(cmd=cmd):
                self.assertEqual(walk(cmd).calls, want)

    def test_prefixes_and_wrappers(self):
        for cmd in ("FOO=1 git commit", "A=1 B=2 git commit", "env git commit", "env FOO=1 git commit", "command git commit",
                    "exec git commit", "nohup git commit", "time git commit", "sudo git commit", "sudo env A=b git commit",
                    "/usr/bin/git commit", "git.exe commit", "C:\\\\Program/git.exe commit"):
            with self.subTest(cmd=cmd):
                self.assertEqual(walk(cmd).calls, [("commit", "main")])
        for cmd in ("=1 git commit", "mygit commit", "gitx commit", "echo git commit", "FOO=1", "env", "xargs git commit"):
            with self.subTest(cmd=cmd):
                self.assertEqual(walk(cmd).calls, [])

    def test_scan_bash_c_three_levels(self):
        def nest(inner, levels):
            cmd = inner
            for _ in range(levels):
                cmd = 'bash -c "' + cmd.replace("\\", "\\\\").replace('"', '\\"') + '"'
            return cmd

        for levels, seen in ((1, True), (2, True), (3, True), (4, False)):
            with self.subTest(levels=levels):
                got = walk(nest("git commit -m x", levels)).calls
                self.assertEqual(got, [("commit", "main")] if seen else [])
        # the same depth limit holds for eval and for mixed wrappers
        self.assertEqual(walk("eval git commit").calls, [("commit", "main")])
        self.assertEqual(walk("eval eval eval git commit").calls, [("commit", "main")])
        self.assertEqual(walk("eval eval eval eval git commit").calls, [])
        self.assertEqual(walk("eval 'bash -c \"git commit\"'").calls, [("commit", "main")])
        for sh in ("bash", "sh", "zsh", "dash", "/bin/bash"):
            self.assertEqual(walk(sh + " -c 'git commit'").calls, [("commit", "main")], sh)
        for flags in ("-lc", "-ec", "-xc"):
            self.assertEqual(walk("bash " + flags + " 'git commit'").calls, [("commit", "main")], flags)
        self.assertEqual(walk("bash script.sh").calls, [])
        self.assertEqual(walk("bash -c").calls, [])                   # -c without a command
        self.assertEqual(walk("bash -n 'git commit'").calls, [])      # not a -c flag

    def test_git_dash_C_uses_dir_branch(self):
        with tempfile.TemporaryDirectory() as d0:
            d = d0.replace("\\", "/")  # a backslash inside double quotes is an escape, as in bash
            env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            try:
                subprocess.run(["git", "init", "-q", "-b", "dev", d], check=True)
                subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
                cfg = guard.Config({"git": {"protected_branches": ["dev"]}})
                # the current branch ("feat") is not protected, the -C repo's branch ("dev") is
                self.assertEqual(walk(f'git -C "{d}" commit', "feat", cfg).calls, [("commit", "dev")])
                self.assertEqual(walk(f'git -C "{d}" merge x', "feat", cfg).calls, [("merge", "dev", ["x"])])
                self.assertEqual(walk("git commit", "feat", cfg).calls, [])
                self.assertEqual(walk(f'git -C "{d}" -c core.x=1 commit', "feat", cfg).calls, [("commit", "dev")])
                # a -C that is not a repo has no branch: nothing is protected
                self.assertEqual(walk(f'git -C "{os.path.join(d, "nope")}" commit', "dev", cfg).calls, [])
                self.assertEqual(walk("git -C '' commit", "dev", cfg).calls, [("commit", "dev")])   # empty dir = no -C
                # push always reaches the rule, with the -C branch
                self.assertEqual(walk(f'git -C "{d}" push', "feat", cfg).calls, [("push", "dev", [])])
            finally:
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_git_global_options_skipped(self):
        for cmd in ("git -c user.name=x commit", "git --git-dir .git commit", "git --work-tree . commit", "git --namespace n commit",
                    "git --super-prefix p commit", "git --no-pager commit", "git -p -P commit", "git -c a=b -c c=d --bare commit"):
            with self.subTest(cmd=cmd):
                self.assertEqual(walk(cmd).calls, [("commit", "main")])

    def test_switch_tracking(self):
        table = [
            # command line, initial branch, calls
            ("git switch topic && git commit", "main", [("commit", "topic")]),
            ("git switch -c topic && git merge x", "main", [("merge", "topic", ["x"])]),
            ("git switch -C topic; git commit", "main", [("commit", "topic")]),
            ("git switch --create topic && git commit", "main", [("commit", "topic")]),
            ("git switch --force-create topic && git commit", "main", [("commit", "topic")]),
            ("git switch --orphan topic && git commit", "main", [("commit", "topic")]),
            ("git checkout -b topic && git commit", "main", [("commit", "topic")]),
            ("git checkout -B topic main && git commit", "main", [("commit", "topic")]),   # start point is not the branch
            ("git switch --detach && git commit", "main", []),
            ("git switch -d HEAD~1 && git commit", "main", []),
            ("git switch main && git commit", "feat", [("commit", "main")]),
            ("git switch feat && git switch main && git commit", "dev", [("commit", "main")]),
            ("git switch -- topic && git commit", "main", [("commit", "main")]),           # -- stops option parsing, target stays unset
            ("git switch -q topic && git commit", "main", [("commit", "topic")]),
            ("git switch && git commit", "main", [("commit", "main")]),                    # no target: unchanged
            ("git switch -c && git commit", "main", [("commit", "main")]),                 # -c without a name: unchanged
            ("git commit && git switch topic", "main", [("commit", "main")]),              # switching later does not matter
            ("bash -c 'git switch topic' && git commit", "main", [("commit", "topic")]),   # nested scan moves the same branch
        ]
        for cmd, br, want in table:
            with self.subTest(cmd=cmd):
                self.assertEqual(walk(cmd, br).calls, want)

    def test_checkout_moves_only_to_existing_branches(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
            old = {k: os.environ.get(k) for k in env}
            os.environ.update(env)
            cwd = os.getcwd()
            try:
                subprocess.run(["git", "init", "-q", "-b", "main", d], check=True)
                subprocess.run(["git", "-C", d, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"], check=True)
                subprocess.run(["git", "-C", d, "branch", "topic"], check=True)
                os.chdir(d)
                self.assertEqual(walk("git checkout topic && git commit", "main").calls, [("commit", "topic")])
                self.assertEqual(walk("git checkout README.md && git commit", "main").calls, [("commit", "main")])   # a path, not a branch
                self.assertEqual(walk("git switch README.md && git commit", "main").calls, [("commit", "README.md")])  # switch trusts its argument
                # with -C the branch is not tracked
                self.assertEqual(walk("git -C . checkout topic && git commit", "main").calls, [("commit", "main")])
            finally:
                os.chdir(cwd)
                for k, v in old.items():
                    if v is None:
                        os.environ.pop(k, None)
                    else:
                        os.environ[k] = v

    def test_rule_args_are_passed_through(self):
        self.assertEqual(walk("git merge --no-ff -m 'a b' feat/x").calls, [("merge", "main", ["--no-ff", "-m", "a b", "feat/x"])])
        self.assertEqual(walk("git push -u origin HEAD:main").calls, [("push", "main", ["-u", "origin", "HEAD:main"])])
        # on a branch that is not protected, merge and commit are not judged at all
        self.assertEqual(walk("git merge x && git commit", "feat", guard.Config()).calls, [])
        self.assertEqual(walk("git merge x && git commit", "").calls, [])           # detached HEAD
        self.assertEqual(walk("git push", "feat", guard.Config()).calls, [("push", "feat", [])])


if __name__ == "__main__":
    unittest.main()
