"""Hidden acceptance tests for T4 (fixture: mini-faq-rag, seed: search without accent folding). Copied in after the
agent finishes. Checked through the CLI, the user-facing contract, so internal refactors are allowed."""
import re
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ID_RE = re.compile(r"^\d+\. \[([^\]]+)\]", re.M)


def ids(query, k=3):
    r = subprocess.run([sys.executable, "-m", "faq", "search", query, "-k", str(k)], cwd=ROOT, capture_output=True,
                       encoding="utf-8", errors="replace")
    assert r.returncode == 0, r.stderr[-400:]
    return ID_RE.findall(r.stdout)


class AccentInsensitive(unittest.TestCase):
    def test_unaccented_query_finds_accented_entry_first(self):
        self.assertEqual(ids("quen mat khau")[:1], ["faq-002"])

    def test_d_stroke_folds_to_d(self):
        self.assertIn("faq-001", ids("dang ky tai khoan"))

    def test_accented_mixed_and_upper_case_queries(self):
        for q in ("quên mật khẩu", "quên mat khau", "QUEN MAT KHAU"):
            with self.subTest(query=q):
                self.assertEqual(ids(q)[:1], ["faq-002"])

    def test_more_unaccented_queries_hit_top_3(self):
        for q, want in (("huy don hang nhu the nao", "faq-017"), ("ship hang mat bao nhieu tien", "faq-012"),
                        ("tra gop", "faq-010"), ("chinh sach doi tra", "faq-018")):
            with self.subTest(query=q):
                self.assertIn(want, ids(q))

    def test_repository_tests_pass(self):
        r = subprocess.run([sys.executable, "-m", "unittest", "discover", "-s", "tests", "-t", "."], cwd=ROOT,
                           capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr[-600:])


if __name__ == "__main__":
    unittest.main()
