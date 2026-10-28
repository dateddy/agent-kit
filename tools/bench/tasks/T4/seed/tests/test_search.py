import unittest

from faq.search import FaqSearcher, normalize

ENTRIES = [
    {"id": "pw", "question": "Tôi quên mật khẩu thì làm sao?", "answer": "Chọn Quên mật khẩu ở trang đăng nhập."},
    {"id": "ship", "question": "Phí giao hàng là bao nhiêu?", "answer": "Phí vận chuyển nội thành 20.000đ."},
    {"id": "pay", "question": "Có những phương thức thanh toán nào?", "answer": "COD, chuyển khoản, thẻ và ví điện tử."},
    {"id": "return", "question": "Chính sách đổi trả hàng?", "answer": "Đổi trả trong 7 ngày kể từ khi nhận hàng."},
]


class NormalizeTest(unittest.TestCase):
    def test_normalize_ignores_case(self):
        self.assertEqual(normalize("Quên Mật Khẩu"), normalize("quên mật khẩu"))


class FaqSearcherTest(unittest.TestCase):
    def setUp(self):
        self.searcher = FaqSearcher(ENTRIES)

    def test_ranks_matching_doc_first(self):
        results = self.searcher.search("quên mật khẩu")
        self.assertEqual(results[0][0]["id"], "pw")
        scores = [score for _, score in results]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_empty_query_returns_empty(self):
        self.assertEqual(self.searcher.search(""), [])
        self.assertEqual(self.searcher.search("?!..."), [])

    def test_unknown_word_returns_empty(self):
        self.assertEqual(self.searcher.search("xyzxyz"), [])

    def test_top_k_limits_results(self):
        self.assertEqual(len(self.searcher.search("hàng", top_k=1)), 1)
        self.assertGreater(len(self.searcher.search("hàng", top_k=3)), 1)


if __name__ == "__main__":
    unittest.main()
