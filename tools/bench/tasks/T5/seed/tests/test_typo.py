import unittest

from faq.data import load_faq
from faq.search import FaqSearcher


class TypoToleranceTest(unittest.TestCase):
    """A word with one wrong, missing or extra letter should still find its entry."""

    @classmethod
    def setUpClass(cls):
        cls.searcher = FaqSearcher(load_faq())

    def top_id(self, query):
        results = self.searcher.search(query, 3)
        self.assertTrue(results, f"no result for {query!r}")
        return results[0][0]["id"]

    def test_one_wrong_letter(self):
        self.assertEqual(self.top_id("zalopey"), "faq-007")

    def test_one_missing_letter(self):
        self.assertEqual(self.top_id("secur"), "faq-008")

    def test_one_extra_letter(self):
        self.assertEqual(self.top_id("trieuu"), "faq-010")

    def test_exact_matches_still_rank_first(self):
        self.assertEqual(self.top_id("mật khẩu"), "faq-003")
        self.assertEqual(self.top_id("đổi trả"), "faq-018")

    def test_two_wrong_letters_do_not_match(self):
        self.assertEqual(self.searcher.search("zaloxey"), [])


if __name__ == "__main__":
    unittest.main()
