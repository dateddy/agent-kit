"""Tests for scripts/guard.py (the logic behind guard-git.sh). Stdlib unittest, Python 3.9+.

Run: python -m unittest scripts/tests/test_guard_py.py -v
"""
import os
import sys
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


if __name__ == "__main__":
    unittest.main()
