"""Hidden acceptance tests for T2 (fixture: mini-faq-rag, seed: data/eval.jsonl). Copied in after the agent finishes."""
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EVAL = ROOT / "data" / "eval.jsonl"
METRICS = ROOT / "out" / "metrics.json"
# Ranks with the fixture's search: 1, 2, 4, no result -> recall@1 = 1/4, recall@3 = 2/4.
OTHER_SET = [("hotline", "faq-026"), ("mã giảm giá", "faq-024"), ("đăng ký", "faq-001"), ("bitcoin", "faq-007")]


def git(*args):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, encoding="utf-8", errors="replace").stdout


class Metrics(unittest.TestCase):
    def assert_metrics(self, recall1, recall3):
        if METRICS.exists():
            METRICS.unlink()
        r = subprocess.run([sys.executable, "scripts/eval.py"], cwd=ROOT, capture_output=True, encoding="utf-8",
                           errors="replace")
        self.assertEqual(r.returncode, 0, r.stderr[-500:])
        m = json.loads(METRICS.read_text(encoding="utf-8"))
        self.assertAlmostEqual(m["recall@1"], recall1, places=6, msg=m)
        self.assertAlmostEqual(m["recall@3"], recall3, places=6, msg=m)

    def test_seeded_eval_set(self):
        # 8 of 13 queries rank the expected entry first, 11 of 13 within the top 3
        self.assert_metrics(8 / 13, 11 / 13)

    def test_reads_the_eval_file(self):
        saved = EVAL.read_bytes()
        try:
            EVAL.write_text("".join(json.dumps({"query": q, "expected_id": i}, ensure_ascii=False) + "\n"
                                    for q, i in OTHER_SET), encoding="utf-8")
            self.assert_metrics(0.25, 0.5)
        finally:
            EVAL.write_bytes(saved)

    def test_script_committed_and_out_not(self):
        self.assertTrue(git("ls-files", "scripts/eval.py").strip(), "scripts/eval.py is not committed")
        self.assertEqual(git("ls-files", "out").strip(), "", "out/ must not be committed")


if __name__ == "__main__":
    unittest.main()
