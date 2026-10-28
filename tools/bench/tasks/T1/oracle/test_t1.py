"""Hidden acceptance tests for T1 (fixture: mini-faq-rag). Copied into the work copy after the agent finishes."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def faq(*args):
    return subprocess.run([sys.executable, "-m", "faq", *args], cwd=ROOT, capture_output=True, encoding="utf-8")


class JsonOutput(unittest.TestCase):
    def test_json_array_with_the_three_keys(self):
        r = faq("search", "quên mật khẩu", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        items = json.loads(r.stdout)
        self.assertIsInstance(items, list)
        self.assertTrue(1 <= len(items) <= 3, items)
        for it in items:
            self.assertEqual(set(it), {"question", "answer", "score"})
            self.assertIsInstance(it["score"], (int, float))
        self.assertIn("mật khẩu", items[0]["question"].lower())

    def test_sorted_best_first(self):
        scores = [it["score"] for it in json.loads(faq("search", "mật khẩu", "--json").stdout)]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_top_k_is_respected(self):
        items = json.loads(faq("search", "mật khẩu", "-k", "1", "--json").stdout)
        self.assertEqual(len(items), 1)

    def test_no_match_prints_empty_array(self):
        r = faq("search", "xyzxyz", "--json")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout), [])

    def test_plain_output_is_unchanged(self):
        out = faq("search", "quên mật khẩu").stdout
        self.assertTrue(out.startswith("1. ["), out[:80])
        self.assertNotIn("{", out.split("\n")[0])

    def test_invalid_top_k_still_exits_2(self):
        self.assertEqual(faq("search", "mật khẩu", "-k", "0", "--json").returncode, 2)


if __name__ == "__main__":
    unittest.main()
