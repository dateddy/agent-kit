"""Hidden acceptance tests for T5 (fixture: mini-faq-rag @ 6783eb2, seed: tests/test_typo.py). Copied in after the
agent finishes. The seeded test must pass without being edited, so the search itself has to tolerate one-letter typos."""
import hashlib
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# sha256 of each test file with CRLF folded to LF (a Windows checkout may convert line endings)
TEST_FILES = {
    "tests/__init__.py": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
    "tests/eval_queries.json": "9ffaf66efbd9e7f999e93e2c26b99f76d2bec6b0006bca788de230a004b466a8",
    "tests/test_cli.py": "2ce3d1cc6313191eedcf9a5cb1f86dcd45eff86a0fea6a22b1b29762cf5df415",
    "tests/test_data.py": "9c95f7f89b38cdb037590fed4b9d11d93c6ec8b2703bc96c6ee4069d4742b21b",
    "tests/test_eval.py": "ee1270fc159af1adf6ffbf876e005dd27c8465cad1a44b5001d37b8a5340eead",
    "tests/test_search.py": "2c43c51ff276d300e9516f5420abe7f77c1cece17dbd6023d80763890bb1fb45",
    "tests/test_typo.py": "8e5e0b87edba00080a768b09e7fa981339ac5f1da064c184af5b27b99db67c7b",
}


def digest(data):
    return hashlib.sha256(data.replace(b"\r\n", b"\n")).hexdigest()


class TestsUnchanged(unittest.TestCase):
    def test_working_tree_copies(self):
        for rel, want in TEST_FILES.items():
            with self.subTest(file=rel):
                p = ROOT / rel
                self.assertTrue(p.is_file(), f"{rel} was deleted")
                self.assertEqual(digest(p.read_bytes()), want, f"{rel} was modified")

    def test_committed_copies(self):
        for rel, want in TEST_FILES.items():
            with self.subTest(file=rel):
                r = subprocess.run(["git", "show", f"HEAD:{rel}"], cwd=ROOT, capture_output=True)
                self.assertEqual(r.returncode, 0, f"{rel} is not in HEAD")
                self.assertEqual(digest(r.stdout), want, f"{rel} was modified in HEAD")


class SuiteAndTypos(unittest.TestCase):
    def test_repository_tests_pass(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=ROOT,
                           capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])

    def test_other_one_letter_typos(self):
        # words the seeded test does not use, so special-casing its queries does not pass
        sys.path.insert(0, str(ROOT))
        from faq.data import load_faq
        from faq.search import FaqSearcher
        searcher = FaqSearcher(load_faq())
        for query, want in (("mastercad", "faq-007"), ("vnpau", "faq-007"), ("phieuu", "faq-021")):
            with self.subTest(query=query):
                results = searcher.search(query, 3)
                self.assertTrue(results, f"no result for {query!r}")
                self.assertEqual(results[0][0]["id"], want)


if __name__ == "__main__":
    unittest.main()
