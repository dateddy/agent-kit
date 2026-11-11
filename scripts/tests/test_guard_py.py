"""Tests for scripts/guard.py (the logic behind guard-git.sh). Stdlib unittest, Python 3.9+.

Run: python -m unittest scripts/tests/test_guard_py.py -v
"""
import json
import os
import shutil
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

    def test_lex_ops_kinds(self):
        table = [
            ("a && b", [("word", "a"), ("op", "&&"), ("word", "b")]),
            ("a || b", [("word", "a"), ("op", "||"), ("word", "b")]),
            ("a | b", [("word", "a"), ("op", "|"), ("word", "b")]),
            ("a |& b", [("word", "a"), ("op", "|&"), ("word", "b")]),
            ("a & b", [("word", "a"), ("op", "&"), ("word", "b")]),
            ("a;b", [("word", "a"), ("op", ";"), ("word", "b")]),
            ("a\nb", [("word", "a"), ("op", "\n"), ("word", "b")]),
            ("(a)", [("op", "("), ("word", "a"), ("op", ")")]),
            ("`a`", [("op", "`"), ("word", "a"), ("op", "`")]),
            ('echo "a | b"', [("word", "echo"), ("word", "a | b")]),
        ]
        for text, want in table:
            with self.subTest(text=text):
                self.assertEqual(guard.lex(text), want)

    def test_lex_redirects_table(self):
        table = [
            ("echo x>/dev/null", [("word", "echo"), ("word", "x"), ("redir", ">"), ("word", "/dev/null")]),
            ("a 2>&1 | b", [("word", "a"), ("dup", "2>&1"), ("op", "|"), ("word", "b")]),
            ("a &>log", [("word", "a"), ("redir", "&>"), ("word", "log")]),
            ("a &>>log", [("word", "a"), ("redir", "&>>"), ("word", "log")]),
            ('a >>"my file"', [("word", "a"), ("redir", ">>"), ("word", "my file")]),
            ("a 1>&2", [("word", "a"), ("dup", "1>&2")]),
            ("a >&2", [("word", "a"), ("dup", ">&2")]),
            ("a >&log", [("word", "a"), ("redir", ">&"), ("word", "log")]),
            ("a 2>err", [("word", "a"), ("redir", "2>"), ("word", "err")]),
            ("a 2>>err", [("word", "a"), ("redir", "2>>"), ("word", "err")]),
            ("a >| f", [("word", "a"), ("redir", ">|"), ("word", "f")]),
            ("a2>f", [("word", "a2"), ("redir", ">"), ("word", "f")]),         # "a2" is a word, not fd 2
            ('a ">" f', [("word", "a"), ("word", ">"), ("word", "f")]),        # quoted > is a plain word
            ("cat < f", [("word", "cat"), ("redir_in", "<"), ("word", "f")]),
            ("cat <<EOF", [("word", "cat"), ("redir_in", "<<"), ("word", "EOF")]),
        ]
        for text, want in table:
            with self.subTest(text=text):
                self.assertEqual(guard.lex(text), want)

    def test_seg_ops(self):
        table = [
            ("vf | tail", [(["vf"], [], "|"), (["tail"], [], "")]),
            ("vf > log", [(["vf"], [(">", "log")], "")]),
            ("a 2>&1 | b", [(["a"], [], "|"), (["b"], [], "")]),
            ("a >x >>y; b", [(["a"], [(">", "x"), (">>", "y")], ";"), (["b"], [], "")]),
            ("a &&  && b", [(["a"], [], "&&"), (["b"], [], "")]),
            ("a >", [(["a"], [(">", "")], "")]),
            ("cat < f", [(["cat", "f"], [], "")]),         # input redirect: its target stays a word (deny_read checks it)
        ]
        for text, want in table:
            with self.subTest(text=text):
                self.assertEqual(guard.seg_ops(text), want)


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


def judge(cmd, branch="main", data=None):
    j = guard.Judge(guard.Config(data), branch)
    j.scan(cmd, 0)
    return j.decision()


HINT_MAIN = "Inspect the convention first: git log --first-parent --merges -5 main --oneline"


class RuleTests(unittest.TestCase):
    def test_merge_rules(self):
        def plain(why, b="main"):
            return f"BLOCK: {why} on protected branch '{b}'. Use: git merge --no-ff <branch>\n" + HINT_MAIN.replace(" main ", f" {b} ")

        table = [
            ("git merge x", plain("a plain 'git merge' can fast-forward")),
            ("git merge --ff-only x", plain("--ff-only is not allowed")),
            ("git merge --squash x", plain("--squash is not allowed")),
            ("git merge --no-ff --squash x", plain("--squash is not allowed")),
            ("git merge --ff-only --squash x", plain("--squash is not allowed")),   # squash wins whatever the order
            ("git merge --squash --ff-only x", plain("--squash is not allowed")),
            ("git merge --no-ff --ff-only x", plain("--ff-only is not allowed")),   # --no-ff does not rescue --ff-only
            ("git merge --no-ff x", None),
            ("git merge --no-ff -m 'feat: m' x", None),
            ("git merge --abort", None),
            ("git merge --continue", None),
            ("git merge --quit", None),
            ("git merge --ff-only --abort", None),                                    # control flags pass whatever else is there
            ("git merge-base main x", None),
            ("git merge", plain("a plain 'git merge' can fast-forward")),
        ]
        for cmd, want in table:
            with self.subTest(cmd=cmd):
                d = judge(cmd)
                if want is None:
                    self.assertEqual(d.kind, "allow")
                else:
                    self.assertEqual((d.kind, d.message), ("block", want))
        self.assertEqual(judge("git merge --ff-only x", "feat").kind, "allow")      # not protected
        self.assertEqual(judge("git merge --ff-only x", "").kind, "allow")          # detached HEAD
        d = judge("git merge x", "trunk", {"git": {"protected_branches": ["trunk"]}, "guards": {"merge_convention": "merge commit named X"}})
        self.assertEqual(d.message, "BLOCK: a plain 'git merge' can fast-forward on protected branch 'trunk'. Use: git merge --no-ff <branch>\n"
                         "Convention: merge commit named X\n"
                         "Inspect the convention first: git log --first-parent --merges -5 trunk --oneline")
        d = judge("git merge x", "main", {"guards": {"merge_convention": "line1\n\nline2"}})
        self.assertIn("Convention: line1\nline2\nInspect", d.message)               # empty lines are dropped, like sed '/^$/d'

    def test_commit_policy(self):
        ask = "Commit on protected branch 'main'. Approve only if the user asked for it; otherwise create a task branch first."
        block = "BLOCK: commit on protected branch 'main' (git.protected_commit=block). Create a task branch: git switch -c <type>/<name>"
        table = [
            (None, ("ask", ask)),
            ({}, ("ask", ask)),
            ({"git": {"protected_commit": "ask"}}, ("ask", ask)),
            ({"git": {"protected_commit": "weird"}}, ("ask", ask)),                 # anything unknown means ask
            ({"git": {"protected_commit": "allow"}}, ("allow", "")),
            ({"git": {"protected_commit": "block"}}, ("block", block)),
        ]
        for data, want in table:
            with self.subTest(data=data):
                d = judge("git commit -m x", "main", data)
                self.assertEqual((d.kind, d.message), want)
        self.assertEqual(judge("git commit -m x", "feat").kind, "allow")
        self.assertEqual(judge("git commit -m x", "").kind, "allow")

    def test_push_specs(self):
        ASK = "Push to protected branch '{}'. Approve only if the user asked for it; prefer pushing a task branch."
        ALL = "every branch (--all/--mirror)"
        table = [
            # push arguments, current branch, protected branch that was hit (None = allowed)
            ("", "main", "main"),
            ("", "feat", None),
            ("", "", None),
            ("origin HEAD:main", "feat", "main"),
            ("origin feat/x:main", "feat", "main"),
            ("origin --delete main", "feat", "main"),
            ("origin refs/heads/main", "feat", "main"),
            ("origin HEAD", "feat", None),
            ("origin HEAD", "main", "main"),                                          # HEAD means the current branch
            ("origin HEAD", "", None),
            ("origin v1.0.0", "feat", None),
            ("-u origin feat/x", "feat", None),
            ("origin main:feat", "feat", None),                                       # only the destination counts
            ("origin feat:release/1", "feat", "release/1"),
            ("origin HEAD:", "feat", None),
            ("origin main feat", "feat", "main"),
            ("origin feat main", "feat", "main"),
            ("--all origin", "feat", ALL),
            ("--mirror", "feat", ALL),
            ("--all origin feat", "feat", ALL),                                       # no protected refspec: the --all hit stays
            ("--all", "main", "main"),                                                # a bare push from a protected branch overrides it
            ("-o main origin feat", "feat", None),                                    # -o takes a value, so main is not a refspec
            ("--push-option x --repo y origin feat", "feat", None),
            ("origin", "main", "main"),
            ("origin", "feat", None),
        ]
        for args, br, hit in table:
            with self.subTest(args=args, branch=br):
                d = judge(("git push " + args).strip(), br)
                if hit is None:
                    self.assertEqual(d.kind, "allow")
                else:
                    self.assertEqual((d.kind, d.message), ("ask", ASK.format(hit)))
        d = judge("git push origin main", "feat", {"git": {"protected_commit": "block"}})
        self.assertEqual((d.kind, d.message), ("block", "BLOCK: push to protected branch 'main' (git.protected_commit=block). Push a task branch and merge with --no-ff."))
        self.assertEqual(judge("git push origin main", "feat", {"git": {"protected_commit": "allow"}}).kind, "allow")

    def test_first_block_beats_ask(self):
        d = judge("git commit -m x && git merge x")                                    # ask first, block second
        self.assertEqual(d.kind, "block")
        self.assertIn("a plain 'git merge' can fast-forward", d.message)
        d = judge("git merge x && git merge --squash y")                              # two blocks: the first one
        self.assertIn("a plain 'git merge' can fast-forward", d.message)
        d = judge("git commit -m x; git push")                                        # two asks: the first one
        self.assertEqual(d.kind, "ask")
        self.assertTrue(d.message.startswith("Commit on protected branch"))
        d = judge("git push; git commit -m x")
        self.assertTrue(d.message.startswith("Push to protected branch"))
        d = judge("git commit -m x && git merge x", "main", {"git": {"protected_commit": "block"}})
        self.assertTrue(d.message.startswith("BLOCK: commit on protected branch"))   # first of two blocks
        self.assertEqual(judge("git status").kind, "allow")


class Rules1bTests(unittest.TestCase):
    """The 1b git rules. Everything is judged on the unprotected branch "feat" unless a test says otherwise."""

    def kinds(self, cmds, want, branch="feat", data=None):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(judge(cmd, branch, data).kind, want)

    def test_no_verify_table(self):
        self.kinds(["git commit --no-verify -m x", "git commit -nm x", "git commit -a -n -m x", "git push --no-verify",
                    "git merge --no-verify x", "git commit -m x --no-verify"], "block")
        self.kinds(["git push -n", "git push --dry-run", 'git commit -m "--no-verify in msg"', "git commit -m -n", "git commit -mn",
                    "git log -n 5", "git commit -m x"], "allow")
        d = judge("git commit --no-verify -m x")
        self.assertTrue(d.message.startswith("BLOCK: 'git commit' skips the git hooks"), d.message)

    def test_hooks_path_table(self):
        self.kinds(["git -c core.hooksPath=/x status", "git -c CORE.HOOKSPATH=/x commit -m x", "git -c core.hookspath= status",
                    "git config core.hooksPath x", "git config --global core.hooksPath x", "git config CORE.hookspath x"], "block")
        self.kinds(["git config --get core.hooksPath", "git config --list", "git config --unset core.hooksPath",
                    "git -c user.name=x status", "git config user.name x"], "allow")

    def test_force_push_table(self):
        self.kinds(["git push -f origin main", "git push --force origin main", "git push --force-with-lease origin main",
                    "git push --force-with-lease=main origin main", "git push --force-if-includes origin main",
                    "git push origin +main", "git push origin +feat:main", "git push -f --all origin"], "block")
        self.assertEqual(judge("git push -f", "main").kind, "block")                   # bare force push from a protected branch
        self.assertEqual(judge("git push -fu origin main", "feat").kind, "block")      # -f inside a bundle
        d = judge("git push --force origin main")
        self.assertEqual(d.message, "BLOCK: force push to protected branch 'main'. Push a task branch and merge with --no-ff.")
        self.kinds(["git push -f origin feat", "git push origin +feat", "git push --force-with-lease origin feat"], "ask")
        self.kinds(["git push origin feat", "git push -u origin feat"], "allow")
        self.kinds(["git push -f origin feat"], "block", data={"guards": {"destructive": "block"}})
        self.kinds(["git push -f origin feat"], "allow", data={"guards": {"destructive": "allow"}})
        self.kinds(["git push -f origin main"], "block", data={"guards": {"destructive": "allow"}})   # protected: always

    def test_destructive_table(self):
        self.kinds(["git reset --hard", "git reset --hard HEAD~1", "git reset --merge", "git reset --keep",
                    "git branch -D x", "git branch -d -f x", "git branch --delete --force x", "git branch -Dr x",
                    "git clean -fd", "git clean -f", "git clean --force", "git stash drop", "git stash clear",
                    "git checkout -- f.txt", "git checkout .", "git checkout HEAD -- f", "git checkout -- .",
                    "git restore f", "git restore --worktree f", "git restore --staged --worktree f", "git restore -S -W f",
                    "git push --delete origin x", "git push origin :x", "git push -d origin x",
                    "git filter-branch x", "git filter-repo", "git update-ref -d refs/x", "git reflog expire --all",
                    "git gc --prune=now"], "ask")
        self.kinds(["git checkout -b x", "git checkout main", "git checkout --", "git restore --staged f", "git restore -S f",
                    "git clean -n", "git clean -nf", "git clean --dry-run -f", "git branch -d x", "git branch -f x HEAD", "git branch",
                    "git stash pop", "git stash", "git stash list", "git reset --soft HEAD~1", "git reset HEAD f", "git reset",
                    "git gc", "git push origin feat", "git update-ref refs/x HEAD", "git reflog", "git switch -c x",
                    "git status", "git log --oneline"], "allow")
        d = judge("git reset --hard")
        self.assertEqual((d.kind, d.message), ("ask", "Destructive command 'git reset': discards changes in the working tree. "
                                                      "Approve only if the user asked for it."))

    def test_destructive_policy_block_allow(self):
        for value, want in (("block", "block"), ("allow", "allow"), ("ask", "ask"), ("weird", "ask")):
            with self.subTest(value=value):
                d = judge("git reset --hard", "feat", {"guards": {"destructive": value}})
                self.assertEqual(d.kind, want)
        d = judge("git reset --hard", "feat", {"guards": {"destructive": "block"}})
        self.assertIn("destructive", d.message)
        self.assertIn("guards.destructive=block", d.message)

    def test_old_rules_keep_their_messages(self):
        d = judge("git push origin --delete main")                                     # protected + delete: the push rule speaks first
        self.assertEqual((d.kind, d.message), ("ask", "Push to protected branch 'main'. Approve only if the user asked for it; "
                                                      "prefer pushing a task branch."))

    def test_verify_pipe_table(self):
        self.kinds(["vf | tail", "vf |& tee l", "python skills/verifying-changes/scripts/verify.py | tail -5", "bash scripts/verify.sh | head",
                    "py -3 verify.py | cat", "time vf | tail", ".agentkit/scripts/verify.sh --hook | cat", "vf --step 2 2>&1 | tail",
                    "python3 'C:\\x\\verify.py' | cat", 'bash -c "vf | tail"', "VF=1 vf | tail"], "block")
        self.kinds(["vf > log", "vf 2>&1 > log", "vf || echo F", "vf; echo x | tail", "vf && echo ok", "echo x | vf",
                    "python -m pytest | tail", "vf >log; tail log", "vf", "git log | tail"], "allow")
        d = judge("vf | tail")
        self.assertIn("hides its exit code", d.message)
        self.assertTrue(d.message.startswith("BLOCK: piping verify"), d.message)

    def test_headless_ask_becomes_block(self):
        ask = judge("git reset --hard")
        self.assertEqual(ask.kind, "ask")
        old = os.environ.get("AGENTKIT_HEADLESS")
        try:
            os.environ["AGENTKIT_HEADLESS"] = "1"
            d = guard.finalize(ask)
            self.assertEqual(d.kind, "block")
            self.assertTrue(d.message.startswith("BLOCK (AGENTKIT_HEADLESS=1: no user to ask): Destructive command"), d.message)
            self.assertEqual(guard.finalize(guard.ALLOW).kind, "allow")
            self.assertEqual(guard.finalize(judge("git merge x", "main")).kind, "block")          # a block stays a block
            os.environ["AGENTKIT_HEADLESS"] = "0"                                                  # only the exact value 1 counts
            self.assertEqual(guard.finalize(ask).kind, "ask")
            del os.environ["AGENTKIT_HEADLESS"]
            self.assertEqual(guard.finalize(ask).kind, "ask")
        finally:
            if old is None:
                os.environ.pop("AGENTKIT_HEADLESS", None)
            else:
                os.environ["AGENTKIT_HEADLESS"] = old

    def test_headless_through_the_command_line(self):
        def run(headless):
            env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
            env.pop("AGENTKIT_HEADLESS", None)
            if headless:
                env["AGENTKIT_HEADLESS"] = "1"
            with tempfile.TemporaryDirectory() as d:
                p = subprocess.run([sys.executable, GUARD, "bash", "git reset --hard"], cwd=d, env=env, capture_output=True)
            return p.returncode, p.stdout, p.stderr
        rc, out, err = run(False)
        self.assertEqual(rc, 0)
        self.assertIn(b'"permissionDecision":"ask"', out)
        rc, out, err = run(True)
        self.assertEqual((rc, out), (1, b""))
        self.assertTrue(err.startswith(b"BLOCK (AGENTKIT_HEADLESS=1: no user to ask): "), err)


GUARD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guard.py")
ASK_COMMIT = "Commit on protected branch 'main'. Approve only if the user asked for it; otherwise create a task branch first."
ASK_JSON = ('{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"'
            + ASK_COMMIT + '"}}\n').encode()
NOT_JSON = b"guard-git: stdin is not Claude Code hook JSON. Pass the command as arguments instead.\n"


class CliTests(unittest.TestCase):
    """guard.py as a process: argument mode and Claude Code hook mode, in a throwaway repo on main."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.repo = cls.tmp.name
        cls.env = dict(os.environ, GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_SYSTEM=os.devnull, PYTHONUTF8="1")
        subprocess.run(["git", "init", "-q", "-b", "main", cls.repo], check=True, env=cls.env)
        subprocess.run(["git", "-C", cls.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "i"],
                       check=True, env=cls.env)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def run_guard(self, args, stdin=b"", cwd=None):
        p = subprocess.run([sys.executable, GUARD, *args], input=stdin, capture_output=True, cwd=cwd or self.repo, env=self.env)
        return p.returncode, p.stdout, p.stderr

    def hook(self, command, cwd=None):
        return self.run_guard(["bash"], json.dumps({"tool_input": {"command": command}}).encode(), cwd)

    def test_ask_json_exact_bytes(self):
        for rc, out, err in (self.run_guard(["bash", "git", "commit", "-m", "x"]), self.hook("git commit -m x")):
            self.assertEqual((rc, out, err), (0, ASK_JSON, b""))      # stdout is exactly the JSON, nothing on stderr
        self.assertEqual(json.loads(ASK_JSON)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_arg_mode_block_rc1(self):
        rc, out, err = self.run_guard(["bash", "git", "merge", "--ff-only", "x"])
        self.assertEqual((rc, out), (1, b""))
        self.assertTrue(err.decode().startswith("BLOCK: --ff-only is not allowed on protected branch 'main'."))
        self.assertTrue(err.endswith(b"git log --first-parent --merges -5 main --oneline\n"))
        rc, out, err2 = self.hook("git merge --ff-only x")             # hook mode: only exit 2 blocks, same message
        self.assertEqual((rc, out, err2), (2, b"", err))
        self.assertEqual(self.run_guard(["bash", "git", "merge", "--no-ff", "x"]), (0, b"", b""))
        self.assertEqual(self.run_guard(["bash", "git", "log"]), (0, b"", b""))

    def test_hook_stdin_crlf_json(self):
        lf = '{\n "tool_input": {\n  "command": "git commit -m x"\n }\n}\n'
        crlf = lf.replace("\n", "\r\n")
        self.assertEqual(self.run_guard(["bash"], crlf.encode()), (0, ASK_JSON, b""))
        self.assertEqual(self.run_guard(["bash"], lf.encode()), (0, ASK_JSON, b""))
        # CR and LF at the end of the command (JSON escapes) are not part of the command
        self.assertEqual(self.hook("git commit -m x\r\n"), (0, ASK_JSON, b""))
        self.assertEqual(self.hook("git commit -m x\r"), (0, ASK_JSON, b""))
        # CR inside the command is dropped, as `tr -d '\r'` did
        self.assertEqual(self.hook("git comm\rit -m x"), (0, ASK_JSON, b""))
        rc, out, err = self.hook("git merge --ff-only x\r\n")
        self.assertEqual((rc, out), (2, b""))
        self.assertNotIn(b"\r", err)

    def test_hook_empty_command(self):
        for text in ('{"tool_input":{}}', '{"tool_input":{"command":null}}', '{"tool_input":{"command":false}}', '{"tool_input":{"command":""}}',
                     '{"tool_input":null}', '{"tool_input":"x"}', '{"tool_input":[1]}', "{}", "[]", "3", '"git commit"', "true"):
            with self.subTest(stdin=text):
                self.assertEqual(self.run_guard(["bash"], text.encode()), (0, b"", b""))
        # a command that is not a string is judged as its JSON text: no "git" in it, so it passes
        self.assertEqual(self.run_guard(["bash"], b'{"tool_input":{"command":3}}'), (0, b"", b""))

    def test_hook_invalid_json_exit2(self):
        for text in (b"not json", b"", b"   \n", b"null", b"false", b"{", b"\xff\xfe"):
            with self.subTest(stdin=text):
                self.assertEqual(self.run_guard(["bash"], text), (2, b"", NOT_JSON))

    def test_non_git_command_is_skipped_before_any_git_call(self):
        with tempfile.TemporaryDirectory() as d:                         # not a repo, with a config that is not valid JSON
            with open(os.path.join(d, ".agentkit.json"), "w", encoding="utf-8") as f:
                f.write("{ nope")
            rc, out, err = self.hook("ls -la", cwd=d)
            self.assertEqual((rc, out), (0, b""))
            self.assertTrue(err.startswith(b"WARN: ") and err.endswith(b".agentkit.json is not valid JSON; using defaults\n"), err)
            rc, out, err = self.run_guard(["bash"], b"not json", cwd=d)  # the WARN comes first, then the stdin error
            self.assertEqual(rc, 2)
            self.assertTrue(err.startswith(b"WARN: ") and err.endswith(NOT_JSON))

    def test_config_is_read_from_the_repo(self):
        with tempfile.TemporaryDirectory() as d:
            subprocess.run(["git", "init", "-q", "-b", "main", d], check=True, env=self.env)
            with open(os.path.join(d, ".agentkit.json"), "w", encoding="utf-8") as f:
                f.write('{"git": {"protected_commit": "block"}}')
            os.makedirs(os.path.join(d, "sub"))
            rc, out, err = self.run_guard(["bash", "git", "commit"], cwd=os.path.join(d, "sub"))
            self.assertEqual((rc, out), (1, b""))
            self.assertTrue(err.startswith(b"BLOCK: commit on protected branch 'main' (git.protected_commit=block)."))

    def test_subcommands(self):
        for args in ([], ["git", "commit"], ["frobnicate"], ["--help"]):
            with self.subTest(args=args):
                rc, out, err = self.run_guard(args)
                self.assertEqual((rc, out), (1, b""))
                self.assertTrue(err.startswith(b"usage: guard.py bash [COMMAND...]"))


def norm(p):
    return p.replace("\\", "/")


class PathTests(unittest.TestCase):
    """resolve_path, RepoInfo and `guard.py edit` on throwaway repos (symlink cases skip where symlinks are not allowed)."""

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.tmp = os.path.realpath(cls.tmpdir.name)
        cls.home = os.path.join(cls.tmp, "home")
        cls.repo = os.path.join(cls.tmp, "repo")
        cls.outside = os.path.join(cls.tmp, "outside")
        cls.other = os.path.join(cls.tmp, "other")
        cls.wt = os.path.join(cls.tmp, "wt")
        for d in (cls.home, cls.repo, cls.outside, cls.other):
            os.makedirs(d)
        cls.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull, "HOME": cls.home, "USERPROFILE": cls.home}
        cls.env.pop("AGENTKIT_HEADLESS", None)
        cls.git(cls.repo, "init", "-q")
        cls.git(cls.repo, "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init")
        cls.git(cls.repo, "worktree", "add", "-q", cls.wt, "-b", "wt")
        cls.git(cls.other, "init", "-q")
        os.makedirs(os.path.join(cls.repo, "sub"))
        cls.can_link = True
        try:
            os.symlink(cls.outside, os.path.join(cls.repo, "link"), target_is_directory=True)
        except (OSError, NotImplementedError):
            # Windows without developer mode: a directory junction needs no privilege and realpath follows it too
            r = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(cls.repo, "link"), cls.outside], capture_output=True) if os.name == "nt" else None
            cls.can_link = bool(r and r.returncode == 0)
        cls.write_cfg({"fs": {"allow_write": []}})                      # nothing outside the repo is allowed unless a test says so
        with open(os.path.join(cls.outside, ".agentkit.json"), "w", encoding="utf-8") as f:
            json.dump({"fs": {"allow_write": []}}, f)

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    @classmethod
    def git(cls, cwd, *args):
        subprocess.run(["git", *args], cwd=cwd, env=cls.env, check=True, capture_output=True)

    @classmethod
    def write_cfg(cls, data):
        with open(os.path.join(cls.repo, ".agentkit.json"), "w", encoding="utf-8") as f:
            json.dump(data, f)

    def run_edit(self, args, payload=None, cwd=None, env=None):
        stdin = json.dumps(payload).encode() if payload is not None else b""
        p = subprocess.run([sys.executable, GUARD, "edit", *args], input=stdin, capture_output=True, cwd=cwd or self.repo, env=env or self.env)
        return p.returncode, p.stdout, p.stderr

    def hook(self, tool_input, cwd=None, env=None):
        return self.run_edit([], {"tool_name": "Edit", "tool_input": tool_input, "cwd": cwd or self.repo}, cwd=cwd, env=env)

    def test_resolve_table(self):
        old = {k: os.environ.get(k) for k in ("HOME", "USERPROFILE")}
        os.environ["HOME"] = os.environ["USERPROFILE"] = self.home
        try:
            r, o = self.repo, self.outside
            table = [
                ("a/b.txt", r, norm(os.path.join(r, "a", "b.txt"))),                 # a new file in a directory that does not exist
                ("../outside/x", r, norm(os.path.join(o, "x"))),                      # .. leaves the repo
                ("sub/../x", r, norm(os.path.join(r, "x"))),
                ("x", o, norm(os.path.join(o, "x"))),                                 # the same relative path under two cwds
                ("x", r, norm(os.path.join(r, "x"))),
                (os.path.join(o, "y"), r, norm(os.path.join(o, "y"))),                # absolute stays absolute
                ("~", r, norm(self.home)),
                ("~/x", r, norm(self.home) + "/x"),
                ("$HOME/x", r, norm(self.home) + "/x"),
                ("${HOME}/x/../y", r, norm(self.home) + "/y"),
                (".", r, norm(r)),
            ]
            for p, cwd, want in table:
                with self.subTest(p=p, cwd=cwd):
                    self.assertEqual(guard.resolve_path(p, cwd), want)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_resolve_symlink_out_of_repo(self):
        if not self.can_link:
            self.skipTest("symlinks are not available")
        want = norm(os.path.join(self.outside, "new", "f.txt"))
        self.assertEqual(guard.resolve_path("link/new/f.txt", self.repo), want)       # nothing of it exists, the link is followed
        self.assertFalse(guard.RepoInfo(self.repo).in_repo(guard.resolve_path("link/new/f.txt", self.repo)))

    def test_resolve_windows_forms(self):
        if os.name == "nt":
            table = [
                ("C:\\x\\y", "C:/x/y"),
                ("c:/x/../y", "c:/y"),
                ("/c/x/y", "C:/x/y"),                                                 # MSYS form
                ("/C/x", "C:/x"),
                ("/c", "C:/"),
            ]
            for p, want in table:
                with self.subTest(p=p):
                    self.assertEqual(guard.resolve_path(p, self.repo).lower(), want.lower())
            self.assertTrue(guard.same_or_under("c:/Users/X/y", "C:/users/x"))        # drive letter and case do not matter
        else:
            self.assertEqual(guard.resolve_path("/c/x/y", self.repo), "/c/x/y")       # elsewhere /c/... is an ordinary path
            self.assertFalse(guard.same_or_under("/Users/X/y", "/users/x"))           # and case matters

    def test_same_or_under(self):
        table = [
            ("/a/b", "/a", True), ("/a", "/a", True), ("/a/", "/a", True), ("/a/b", "/a/", True),
            ("/ab", "/a", False), ("/a", "/a/b", False), ("/b/a", "/a", False),
        ]
        for p, root, want in table:
            with self.subTest(p=p, root=root):
                self.assertEqual(guard.same_or_under(p, root), want)

    def test_in_repo_worktree(self):
        info = guard.RepoInfo(self.repo)
        self.assertEqual(info.top, norm(self.repo))
        table = [
            (os.path.join(self.repo, "sub", "f"), True),
            (os.path.join(self.repo, "new", "deep", "f"), True),
            (os.path.join(self.wt, "f"), True),                                       # a worktree of the same repo
            (os.path.join(self.wt, "not", "yet", "f"), True),
            (os.path.join(self.outside, "f"), False),
            (os.path.join(self.other, "f"), False),                                   # another repository
            (os.path.join(self.tmp, "f"), False),
        ]
        for p, want in table:
            with self.subTest(p=p):
                self.assertEqual(info.in_repo(guard.resolve_path(p, self.repo)), want)
        wt_info = guard.RepoInfo(self.wt)                                             # and from inside the worktree
        self.assertTrue(wt_info.in_repo(guard.resolve_path(os.path.join(self.repo, "f"), self.wt)))

    def test_repo_info_outside_any_repo(self):
        info = guard.RepoInfo(self.outside)                                           # no repo: the cwd is the project root
        self.assertEqual(info.top, norm(self.outside))
        self.assertTrue(info.in_repo(guard.resolve_path("x", self.outside)))
        self.assertFalse(info.in_repo(guard.resolve_path("../x", self.outside)))

    def test_cfg_list(self):
        c = guard.Config({"fs": {"allow_write": [], "deny_read": ["a", "b"], "x": "str"}})
        self.assertEqual(c.cfg_list("fs.allow_write", ["d"]), [])                     # an explicit empty list is kept
        self.assertEqual(c.cfg_list("fs.deny_read", ["d"]), ["a", "b"])
        self.assertEqual(c.cfg_list("fs.missing", ["d"]), ["d"])
        self.assertEqual(c.cfg_list("fs.x", ["d"]), ["d"])                            # not a list: the default
        self.assertEqual(guard.Config().cfg_list("fs.allow_write", ["d"]), ["d"])

    def test_write_decision_defaults_and_allow_write(self):
        info = guard.RepoInfo(self.repo)
        cfg = guard.Config()
        scratch = guard.resolve_path(os.path.join(tempfile.gettempdir(), "agentkit-zz", "f"), self.repo)
        self.assertEqual(guard.write_decision(scratch, info, cfg).kind, "allow")      # the OS scratch dir is allowed by default
        home_cache = guard.resolve_path("~/.cache/x", self.repo)
        self.assertEqual(guard.write_decision(home_cache, info, cfg).kind, "allow")
        d = guard.write_decision(guard.resolve_path("~/.bashrc", self.repo), info, cfg)
        self.assertEqual(d.kind, "ask")
        self.assertIn("(fs.outside_repo_write=ask)", d.message)
        self.assertEqual(guard.write_decision(guard.resolve_path("sub/f", self.repo), info, cfg).kind, "allow")
        blocked = guard.Config({"fs": {"outside_repo_write": "block", "allow_write": []}})
        d = guard.write_decision(guard.resolve_path("~/.bashrc", self.repo), info, blocked)
        self.assertEqual(d.kind, "block")
        self.assertTrue(d.message.startswith("BLOCK: write outside the repo: "), d.message)
        mine = guard.Config({"fs": {"allow_write": [norm(self.outside) + "/**"]}})
        self.assertEqual(guard.write_decision(guard.resolve_path(os.path.join(self.outside, "d", "f"), self.repo), info, mine).kind, "allow")
        self.assertEqual(guard.write_decision(guard.resolve_path(os.path.join(self.other, "f"), self.repo), info, mine).kind, "ask")

    def test_edit_hook_json(self):
        rc, out, err = self.hook({"file_path": os.path.join(self.repo, "sub", "f.py")})
        self.assertEqual((rc, out, err), (0, b"", b""))                               # inside the repo
        rc, out, err = self.hook({"file_path": os.path.join(self.wt, "f.py")})
        self.assertEqual((rc, out, err), (0, b"", b""))                               # a worktree of the repo
        rc, out, err = self.hook({"file_path": os.path.join(self.outside, "f.py")})
        self.assertEqual((rc, err), (0, b""))
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        rc, out, err = self.hook({"notebook_path": os.path.join(self.outside, "n.ipynb")})       # NotebookEdit
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        rc, out, err = self.hook({})                                                  # no path: nothing to judge
        self.assertEqual((rc, out, err), (0, b"", b""))

    def test_edit_message_has_absolute_path(self):
        rc, out, err = self.hook({"file_path": "../outside/new/x.txt"})               # relative to the cwd of the hook JSON
        reason = json.loads(out)["hookSpecificOutput"]["permissionDecisionReason"]
        self.assertIn(norm(os.path.join(self.outside, "new", "x.txt")), reason)
        self.assertNotIn("..", reason)

    def test_edit_cwd_changes_the_answer(self):
        rc, out, err = self.hook({"file_path": "x.txt"}, cwd=self.repo)
        self.assertEqual((rc, out), (0, b""))
        rc, out, err = self.hook({"file_path": "x.txt"}, cwd=self.outside)            # same relative path, other cwd: inside its own root
        self.assertEqual((rc, out), (0, b""))
        rc, out, err = self.hook({"file_path": "../repo/x.txt"}, cwd=self.outside)    # outside's root is not the repo
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_edit_allow_write_and_block_policy(self):
        self.write_cfg({"fs": {"allow_write": [norm(self.outside) + "/**"]}})
        try:
            rc, out, err = self.hook({"file_path": os.path.join(self.outside, "ok.txt")})
            self.assertEqual((rc, out, err), (0, b"", b""))
            rc, out, err = self.hook({"file_path": os.path.join(self.other, "no.txt")})
            self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
            self.write_cfg({"fs": {"outside_repo_write": "block", "allow_write": []}})
            rc, out, err = self.hook({"file_path": os.path.join(self.outside, "no.txt")})
            self.assertEqual((rc, out), (2, b""))                                     # hook mode: 2 blocks
            self.assertTrue(err.startswith(b"BLOCK: write outside the repo: "), err)
            rc, out, err = self.run_edit([os.path.join(self.outside, "no.txt")])      # argument mode: 1 blocks
            self.assertEqual((rc, out), (1, b""))
        finally:
            self.write_cfg({"fs": {"allow_write": []}})

    def test_edit_arg_mode(self):
        rc, out, err = self.run_edit([os.path.join(self.repo, "sub", "f")])
        self.assertEqual((rc, out, err), (0, b"", b""))
        rc, out, err = self.run_edit(["../outside/f"])                                # relative to the process cwd
        self.assertEqual(rc, 0)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")

    def test_edit_invalid_input(self):
        p = subprocess.run([sys.executable, GUARD, "edit"], input=b"not json", capture_output=True, cwd=self.repo, env=self.env)
        self.assertEqual((p.returncode, p.stdout), (2, b""))
        self.assertTrue(p.stderr.startswith(b"guard-fs: stdin is not Claude Code hook JSON"), p.stderr)

    def test_edit_headless(self):
        env = {**self.env, "AGENTKIT_HEADLESS": "1"}
        rc, out, err = self.hook({"file_path": os.path.join(self.outside, "f.py")}, env=env)
        self.assertEqual((rc, out), (2, b""))
        self.assertTrue(err.startswith(b"BLOCK (AGENTKIT_HEADLESS=1: no user to ask): Write outside the repo: "), err)
        rc, out, err = self.hook({"file_path": os.path.join(self.repo, "f.py")}, env=env)
        self.assertEqual((rc, out, err), (0, b"", b""))                               # inside the repo stays allowed


class BashFsTests(unittest.TestCase):
    """Writes and deletes outside the repo, redirects, cd tracking and fs.deny_read in `guard.py bash`."""

    NO_ALLOW = {"fs": {"allow_write": []}}      # nothing outside the repo is allowed, whatever the OS scratch directory is

    @classmethod
    def setUpClass(cls):
        cls.tmpdir = tempfile.TemporaryDirectory()
        cls.tmp = os.path.realpath(cls.tmpdir.name)
        cls.repo = os.path.join(cls.tmp, "repo")
        os.makedirs(os.path.join(cls.repo, "sub"))
        cls.env = {**os.environ, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        cls.env.pop("AGENTKIT_HEADLESS", None)
        subprocess.run(["git", "init", "-q"], cwd=cls.repo, env=cls.env, check=True, capture_output=True)

    @classmethod
    def tearDownClass(cls):
        cls.tmpdir.cleanup()

    def judge(self, cmd, data=None, cwd=None):
        j = guard.Judge(guard.Config(self.NO_ALLOW if data is None else data), "feat", cwd or self.repo)
        j.scan(cmd, 0)
        return j.decision()

    def kinds(self, cmds, want, data=None, cwd=None):
        for cmd in cmds:
            with self.subTest(cmd=cmd):
                self.assertEqual(self.judge(cmd, data, cwd).kind, want)

    def test_write_targets_table(self):
        table = [
            ("rm", ["-rf", "a", "b"], [("a", True), ("b", True)]),
            ("rm", ["--", "-f"], [("-f", True)]),
            ("rmdir", ["d"], [("d", True)]),
            ("touch", ["-d", "yesterday", "f"], [("f", False)]),
            ("mkdir", ["-p", "-m", "755", "d"], [("d", False)]),
            ("chmod", ["+x", "f", "g"], [("f", False), ("g", False)]),
            ("chmod", ["-R", "755", "d"], [("d", False)]),
            ("chown", ["me", "f"], [("f", False)]),
            ("truncate", ["-s", "0", "f"], [("f", True)]),
            ("tee", ["-a", "f", "g"], [("f", False), ("g", False)]),
            ("mv", ["a", "b"], [("a", True), ("b", True)]),
            ("mv", ["-t", "d", "a"], [("a", True), ("d", True)]),
            ("cp", ["a", "b"], [("b", False)]),
            ("cp", ["-r", "a", "b", "dir/"], [("dir/", False)]),
            ("cp", ["-t", "d", "a", "b"], [("d", False)]),
            ("cp", ["-rt", "d", "a"], [("d", False)]),
            ("cp", ["a"], []),
            ("ln", ["-s", "target", "link"], [("link", False)]),
            ("install", ["-m", "755", "a", "dst"], [("dst", False)]),
            ("dd", ["if=a", "of=b", "bs=1"], [("b", True)]),
            ("sed", ["-i", "s/a/b/", "f", "g"], [("f", True), ("g", True)]),
            ("sed", ["-i.bak", "-e", "s/a/b/", "f"], [("f", True)]),
            ("sed", ["-ni", "p", "f"], [("f", True)]),
            ("sed", ["--in-place=.b", "s/a/b/", "f"], [("f", True)]),
            ("sed", ["-n", "p", "f"], []),
            ("sed", ["s/a/b/", "f"], []),
            ("sed", ["-ne", "s/i/b/p", "f"], []),                                     # i inside the script is not -i
            ("perl", ["-pi", "-e", "s/a/b/", "f"], [("f", True)]),
            ("perl", ["-i.bak", "-pe", "s/a/b/", "f"], [("f", True)]),
            ("perl", ["-Mstrict", "-e", "1", "f"], []),
            ("perl", ["-ne", "print", "f"], []),
            ("ls", ["-la", "x"], []),
            ("cat", ["x"], []),
        ]
        for prog, args, want in table:
            with self.subTest(prog=prog, args=args):
                self.assertEqual(guard.write_targets(prog, args), want)

    def test_bash_write_table(self):
        self.kinds(["rm -rf build", "rm -rf ./dist ./build sub/x", "rm -rf sub/../x", "touch new/f", "mkdir -p a/b/c", "cp a b",
                    "cp /etc/passwd ./x", "mv a sub/b", "chmod +x sub/run.sh", "tee out.log", "sed -i s/a/b/ f.txt", "dd if=/dev/zero of=./f",
                    "ln -s a l", "install -m 755 a bin", "ls ../x", "cat ../x", "sed s/a/b/ ../o.txt", "sed -n p ../x", "echo hi"], "allow")
        self.kinds(["rm -rf /etc/x", "rm -rf ../x", "rm -rf ~/x", "rm -rf $HOME/x", "mv a ../../x", "mv /outside/a b", "cp a ../../x",
                    "dd if=a of=/x", "sed -i s/a/b/ ../o.txt", "perl -pi -e s/a/b/ ../o.txt", "tee -a ~/x", "touch ../x", "mkdir -p ../x",
                    "chmod +x ../x", "ln -s a ../l", "install -m 755 a ../bin/", "install -t ../d a", "truncate -s 0 ../f", "rmdir ../d",
                    "sudo rm -rf /etc/x", "FOO=1 rm -rf /etc/x", "cd sub && rm -rf ../../x", "echo a && rm -rf /etc/x",
                    'bash -c "rm -rf /etc/x"'], "ask")
        d = self.judge("rm -rf ../x")
        self.assertTrue(d.message.startswith("Write outside the repo: "), d.message)
        self.assertIn(guard.resolve_path("../x", self.repo), d.message)               # the absolute path is shown
        self.kinds(["cp a /tmp/b", "rm -rf /tmp/x", "touch ~/.cache/x/y", "mkdir -p " + guard.tempfile.gettempdir().replace("\\", "/") + "/zz"],
                   "allow", data={})                                                   # fs.allow_write defaults

    def test_redirect_table(self):
        self.kinds(["echo x > /dev/null", "echo x 2> /dev/null", "echo x &> /dev/null", "echo x >> /dev/stderr", "echo x > /dev/stdout",
                    "echo x >out.txt", "echo x > sub/f", "echo x 2>&1", "echo x 1>&2", "echo x >&2", "cat < ../x", "echo x >> $LOG"], "allow")
        self.kinds(["echo x > ~/x", "echo x >> ~/x", "echo x 2> ../e", "echo x &> ../../o", "echo x >| ../f", "> ../empty", "echo x > $F",
                    "echo x > ${OUT}/f"], "ask")
        self.kinds(["echo x 2> /tmp/e", "echo x > /tmp/x"], "allow", data={})
        self.assertEqual(self.judge("echo x > ../out").message.split(":")[0], "Write outside the repo")

    def test_unresolved_variables(self):
        self.kinds(["rm -rf $BUILD_DIR", "rm -rf ${BUILD}/x", "rm -rf $(pwd)/x", "rm -rf `pwd`/x", "mv a $DEST", "truncate -s 0 $F",
                    "dd of=$DEV if=a", "sed -i s/a/b/ $F"], "ask")
        self.kinds(["touch $F", "mkdir -p $OUT", "cp a $DEST/", "chmod +x $F", "tee $LOG", "ls $X"], "allow")   # only destructive commands ask
        d = self.judge("rm -rf $BUILD_DIR")
        self.assertEqual(d.message, "Destructive command 'rm': its target has a variable the guard cannot resolve: $BUILD_DIR. "
                                    "Approve only if the user asked for it.")
        self.kinds(["rm -rf $BUILD_DIR"], "block", data={"guards": {"destructive": "block"}})
        self.kinds(["rm -rf $BUILD_DIR"], "allow", data={"guards": {"destructive": "allow"}})

    def test_cd_tracking(self):
        self.kinds(["cd sub && rm -rf x", "cd sub && cd .. && rm -rf x", "cd /dev && echo x > /dev/null"], "allow")
        self.kinds(["cd .. && rm -rf x", "cd sub && cd ../.. && rm -rf x", "cd .. ; touch x", "cd ../outside && rm -rf /etc/x"], "ask")
        self.kinds(["cd $X && rm -rf y", "cd - && rm -rf y", "cd && rm -rf y", "cd `pwd` && rm -rf y", "pushd .. && rm -rf y"], "ask")   # cwd lost
        self.kinds(["cd $X && touch y", "cd $X && mkdir -p y"], "allow")                                 # not destructive: nothing to ask about
        self.kinds(["cd $X && cd /tmp/z && rm -rf y"], "allow", data={})                                  # an absolute cd finds the cwd again
        self.kinds(["cd $X && cd " + guard.tempfile.gettempdir().replace("\\", "/") + " && rm -rf y"], "allow", data={})
        self.kinds(["cd -P sub && rm -rf x"], "allow")
        self.kinds(["cd /tmp/z && rm -rf y"], "ask")                                                      # no allow_write: /tmp/z is outside
        self.kinds(["rm -rf x"], "allow", cwd=os.path.join(self.repo, "sub"))                            # the start cwd comes from the caller
        self.kinds(["rm -rf ../x"], "allow", cwd=os.path.join(self.repo, "sub"))                         # sub/../x is still in the repo
        self.kinds(["rm -rf ../../x"], "ask", cwd=os.path.join(self.repo, "sub"))

    def test_deny_read_table(self):
        self.kinds(["cat ~/.ssh/id_rsa", "cat ~/.ssh/config", "cat ~/.aws/credentials", "cat ~/.config/gh/hosts.yml", "cat ~/.gnupg/x",
                    "cat .env", "cat sub/.env", "cat .env.local", "cat server.pem", "cat ../k/id_rsa.pub", "git add .env",
                    'bash -c "cat ~/.aws/credentials"', "cat $HOME/.ssh/id_rsa", "head -n1 --file=~/.ssh/id_rsa", "cat < ~/.ssh/id_rsa",
                    "echo x > .env", "cd sub && cat .env"], "block", data={})
        self.kinds(["cat .env.example", "cat sub/.env.sample", "cat .env.template", "echo .envrc", "ls ~/.ssh", "cat README.md",
                    "cat ~/.sshconfig", "git commit -m 'drop .env handling'", "cat envfile", "ls ~/.cache"], "allow", data={})
        d = self.judge("cat ~/.ssh/id_rsa", data={})
        self.assertTrue(d.message.startswith("BLOCK: reads a protected file: "), d.message)
        self.assertIn(guard.resolve_path("~/.ssh/id_rsa", self.repo), d.message)
        mine = {"fs": {"deny_read": ["**/secret.txt", "~/vault/**"], "allow_read": ["**/public/secret.txt"]}}
        self.kinds(["cat secret.txt", "cat sub/secret.txt", "cat ~/vault/a"], "block", data=mine)
        self.kinds(["cat public/secret.txt", "cat .env", "cat ~/.ssh/id_rsa"], "allow", data=mine)           # a custom list replaces the default
        self.kinds(["cat .env"], "allow", data={"fs": {"deny_read": []}})

    def test_fs_rules_follow_the_policy_and_headless(self):
        block = {"fs": {"outside_repo_write": "block", "allow_write": []}}
        d = self.judge("rm -rf /etc/x", data=block)
        self.assertEqual(d.kind, "block")
        self.assertTrue(d.message.startswith("BLOCK: write outside the repo: "), d.message)
        self.kinds(["echo x > ~/x", "cp a ../b"], "block", data=block)
        self.kinds(["rm -rf build", "echo x > out"], "allow", data=block)
        old = os.environ.get("AGENTKIT_HEADLESS")
        os.environ["AGENTKIT_HEADLESS"] = "1"
        try:
            d = guard.finalize(self.judge("rm -rf /etc/x"))
            self.assertEqual(d.kind, "block")
            self.assertIn("AGENTKIT_HEADLESS=1", d.message)
        finally:
            if old is None:
                os.environ.pop("AGENTKIT_HEADLESS", None)
            else:
                os.environ["AGENTKIT_HEADLESS"] = old

    def run_hook(self, command, cwd=None, process_cwd=None):
        payload = {"tool_input": {"command": command}}
        if cwd:
            payload["cwd"] = cwd
        p = subprocess.run([sys.executable, GUARD, "bash"], input=json.dumps(payload).encode(), capture_output=True,
                           cwd=process_cwd or self.repo, env=self.env)
        return p.returncode, p.stdout, p.stderr

    def test_hook_json_cwd_is_used(self):
        with open(os.path.join(self.repo, ".agentkit.json"), "w", encoding="utf-8") as f:
            json.dump(self.NO_ALLOW, f)
        rc, out, err = self.run_hook("echo x > ../out.txt")
        self.assertEqual(rc, 0)
        self.assertIn(b'"permissionDecision":"ask"', out)
        rc, out, err = self.run_hook("echo x > ../out.txt", cwd=os.path.join(self.repo, "sub"))          # relative to the hook cwd: sub/../out.txt
        self.assertEqual((rc, out, err), (0, b"", b""))
        rc, out, err = self.run_hook("cat ~/.ssh/id_rsa")
        self.assertEqual((rc, out), (2, b""))
        self.assertTrue(err.startswith(b"BLOCK: reads a protected file: "), err)
        rc, out, err = self.run_hook("ls -la")                                                           # nothing relevant: allowed at once
        self.assertEqual((rc, out, err), (0, b"", b""))


class WrapperTests(unittest.TestCase):
    """guard-git.sh picks the interpreter: python3, python, py -3; the Microsoft Store stub (WindowsApps) is skipped."""

    WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guard-git.sh")

    def setUp(self):
        bash = shutil.which("bash")
        if bash is None or any(p in bash.lower() for p in ("system32", "windowsapps")):  # none, or the WSL launcher
            self.skipTest("bash not found")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def fake(self, rel, name, body):
        d = os.path.join(self.tmp.name, rel)
        os.makedirs(d, exist_ok=True)
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write("#!/bin/sh" + chr(10) + body + chr(10))
        os.chmod(path, 0o755)
        return d

    def minimal_bin(self):
        """A directory holding only bash and dirname (symlinks), so no real python can be found next to them."""
        d = os.path.join(self.tmp.name, "min")
        os.makedirs(d, exist_ok=True)
        for tool in ("bash", "dirname"):
            link = os.path.join(d, tool)
            if not os.path.exists(link):
                os.symlink(shutil.which(tool), link)
        return d

    def run_wrapper(self, dirs, *args, stdin=b"", minimal=False):
        tail = self.minimal_bin() if minimal else os.path.dirname(shutil.which("bash"))
        env = dict(os.environ, PATH=os.pathsep.join(dirs + [tail]))
        p = subprocess.run([shutil.which("bash"), self.WRAPPER, *args], input=stdin, capture_output=True, env=env)
        return p.returncode, p.stdout.decode(), p.stderr.decode()

    def test_store_stub_is_skipped_and_args_and_stdin_reach_guard_py(self):
        stub = self.fake("Apps/WindowsApps", "python3", "echo STUB; exit 9")
        real = self.fake("bin", "python", 'echo "FAKE $@"; cat')
        rc, out, err = self.run_wrapper([stub, real], "git", "commit", stdin=b"payload")
        self.assertEqual(rc, 0, err)
        self.assertNotIn("STUB", out)
        self.assertTrue(out.startswith("FAKE ") and "payload" in out, out)
        self.assertIn("guard.py bash git commit", out)
        self.assertIn("payload", out)                                           # stdin passes straight through exec

    def test_python3_comes_before_python(self):
        a = self.fake("a", "python3", 'echo "P3 $@"')
        b = self.fake("b", "python", 'echo "P2 $@"')
        rc, out, err = self.run_wrapper([a, b], "git", "status")
        self.assertEqual((rc, out.split()[0]), (0, "P3"), (out, err))

    def test_no_python_fails_loudly(self):
        if os.name == "nt":
            self.skipTest("needs symlinks to build a PATH without python (checked by hand on Windows)")
        stub = self.fake("Apps/WindowsApps", "python3", "echo STUB; exit 9")
        for dirs in ([], [stub]):
            rc, out, err = self.run_wrapper(dirs, "git", "commit", minimal=True)
            self.assertEqual((rc, out), (1, ""), err)                          # argument mode: exit 1
            self.assertIn("python not found: install Python 3.9+", err)
            rc, out, err = self.run_wrapper(dirs, stdin=b"{}", minimal=True)
            self.assertEqual((rc, out), (2, ""), err)                          # hook mode: only exit 2 blocks


    FS_WRAPPER = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "guard-fs.sh")

    def run_script(self, script, dirs, *args, stdin=b"", extra_env=None, cwd=None):
        env = dict(os.environ, PATH=os.pathsep.join(dirs + [os.path.dirname(shutil.which("bash"))]), **(extra_env or {}))
        p = subprocess.run([shutil.which("bash"), script, *args], input=stdin, capture_output=True, env=env, cwd=cwd)
        return p.returncode, p.stdout.decode(), p.stderr.decode()

    def test_wrappers_share_py_sh(self):
        stub = self.fake("Apps/WindowsApps", "python3", "echo STUB; exit 9")
        real = self.fake("bin", "python", 'echo "FAKE $@"')
        for script, sub in ((self.WRAPPER, "bash"), (self.FS_WRAPPER, "edit")):
            with self.subTest(script=os.path.basename(script)):
                rc, out, err = self.run_script(script, [stub, real], "x")
                self.assertEqual(rc, 0, err)
                self.assertNotIn("STUB", out)                                    # the Store stub is skipped by both
                self.assertIn(f"guard.py {sub} x", out)
        self.fake("Apps/WindowsApps", "python", "echo STUB; exit 9")             # both names are the Store stub: py -3 is next
        launcher = self.fake("launcher", "py", 'echo "PYLAUNCHER $@"')
        rc, out, err = self.run_script(self.FS_WRAPPER, [stub, launcher], "x")
        self.assertEqual(rc, 0, err)
        self.assertTrue(out.startswith("PYLAUNCHER -3 "), out)

    def test_guard_fs_wrapper_hook_json_and_exit_codes(self):
        py = os.path.dirname(sys.executable)
        home = os.path.join(self.tmp.name, "home")
        proj = os.path.join(self.tmp.name, "proj")                               # the project root: the home directory is outside it
        os.makedirs(home)
        os.makedirs(proj)
        with open(os.path.join(proj, ".agentkit.json"), "w", encoding="utf-8") as f:
            json.dump({"fs": {"allow_write": []}}, f)                           # the scratch directory (where this test lives) is not allowed
        env = {"HOME": home, "USERPROFILE": home, "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_SYSTEM": os.devnull}
        payload = json.dumps({"tool_input": {"file_path": "~/agentkit-guard-fs-test.txt"}, "cwd": proj}).encode()
        rc, out, err = self.run_script(self.FS_WRAPPER, [py], stdin=payload, extra_env=env, cwd=proj)
        self.assertEqual(rc, 0, err)
        self.assertEqual(json.loads(out)["hookSpecificOutput"]["permissionDecision"], "ask")
        self.assertIn("agentkit-guard-fs-test.txt", out)
        rc, out, err = self.run_script(self.FS_WRAPPER, [py], stdin=payload, extra_env=dict(env, AGENTKIT_HEADLESS="1"), cwd=proj)
        self.assertEqual((rc, out), (2, ""))                                      # hook mode: only exit 2 blocks
        self.assertIn("AGENTKIT_HEADLESS=1", err)
        rc, out, err = self.run_script(self.FS_WRAPPER, [py], "~/agentkit-guard-fs-test.txt", extra_env=dict(env, AGENTKIT_HEADLESS="1"), cwd=proj)
        self.assertEqual((rc, out), (1, ""))                                      # argument mode: exit 1
        rc, out, err = self.run_script(self.FS_WRAPPER, [py], "inside.txt", extra_env=env, cwd=proj)
        self.assertEqual((rc, out, err), (0, "", ""))                             # a file in the working directory is fine

    def test_hook_block_uses_the_py_sh_function(self):
        import importlib.util
        spec = importlib.util.spec_from_file_location("agentkit_install_t", os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "tools", "install.py"))
        install = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(install)
        body = install.find_py_function()
        self.assertTrue(any("WindowsApps" in line for line in body), body)
        for name in ("pre-commit", "commit-msg"):
            block = install.hook_block(name).split("\n")
            start = block.index(body[0])
            self.assertEqual(block[start:start + len(body)], body, name)       # the same lines as scripts/py.sh, no second copy of the logic
            self.assertEqual(sum("command -v python3" in line or 'command -v "$ak_c"' in line for line in block), 1, name)
            self.assertFalse(any("elif command -v python" in line for line in block), name)
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "py.sh"), encoding="utf-8") as f:
            self.assertIn("\n".join(body), f.read())


if __name__ == "__main__":
    unittest.main()
