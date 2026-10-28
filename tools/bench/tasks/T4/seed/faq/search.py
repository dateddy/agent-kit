"""BM25 keyword search over FAQ entries."""

import math
import re
from collections import Counter
from typing import Dict, List, Tuple

TOKEN_RE = re.compile(r"\w+")


def normalize(text: str) -> str:
    """Lowercase so "Quên Mật Khẩu" == "quên mật khẩu"."""
    return text.lower()


def tokenize(text: str) -> List[str]:
    return TOKEN_RE.findall(normalize(text))


class BM25Index:
    def __init__(self, docs: List[List[str]], k1: float = 1.5, b: float = 0.75):
        self.k1 = k1
        self.b = b
        self.doc_freqs = [Counter(doc) for doc in docs]
        self.doc_lens = [len(doc) for doc in docs]
        self.avg_len = sum(self.doc_lens) / len(docs) if docs else 0.0

        n = len(docs)
        df = Counter(term for doc in docs for term in set(doc))
        self.idf = {term: math.log(1 + (n - f + 0.5) / (f + 0.5)) for term, f in df.items()}

    def search(self, query_tokens: List[str], top_k: int) -> List[Tuple[int, float]]:
        """Return (doc index, score) pairs, best first, excluding non-matching docs."""
        scores = []
        for i, freqs in enumerate(self.doc_freqs):
            norm = self.k1 * (1 - self.b + self.b * self.doc_lens[i] / self.avg_len)
            score = 0.0
            for term in query_tokens:
                tf = freqs.get(term, 0)
                if tf:
                    score += self.idf[term] * tf * (self.k1 + 1) / (tf + norm)
            if score > 0:
                scores.append((i, score))
        scores.sort(key=lambda pair: pair[1], reverse=True)
        return scores[:top_k]


class FaqSearcher:
    def __init__(self, entries: List[Dict[str, str]]):
        self.entries = entries
        # The question is repeated so it weighs twice as much as the answer.
        docs = [tokenize(e["question"]) * 2 + tokenize(e["answer"]) for e in entries]
        self.index = BM25Index(docs)

    def search(self, query: str, top_k: int = 3) -> List[Tuple[Dict[str, str], float]]:
        tokens = tokenize(query)
        if not tokens:
            return []
        return [(self.entries[i], score) for i, score in self.index.search(tokens, top_k)]
