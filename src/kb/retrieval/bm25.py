import re
from pathlib import Path

import jieba
from rank_bm25 import BM25Okapi

from kb.schema import Chunk, Hit


class BM25Index:
    def __init__(self, chunks: list[Chunk], dictionary: Path | None = None):
        self.chunks = chunks
        self.tokenizer = jieba.Tokenizer()
        if dictionary and dictionary.exists():
            self.tokenizer.load_userdict(str(dictionary))

    def tokens(self, text: str) -> list[str]:
        # ASCII identifiers remain whole, including underscores and hyphens.
        parts = re.findall(r"[A-Za-z0-9_./-]+|[\u4e00-\u9fff]+", text.lower())
        return [
            word
            for part in parts
            for word in ([part] if part.isascii() else self.tokenizer.lcut(part))
            if word.strip()
        ]

    def search(self, query: str, limit: int, filters: dict | None = None) -> list[Hit]:
        chunks = [
            c
            for c in self.chunks
            if all(c.metadata.get(k) == v for k, v in (filters or {}).items())
        ]
        tokens = self.tokens(query)
        corpus = [self.tokens(c.text) or ["__empty__"] for c in chunks]
        if not chunks or not tokens:
            return []
        scores = BM25Okapi(corpus).get_scores(tokens)
        # BM25 scores may be zero/negative for small corpora: require token overlap,
        # not a positive score, and keep the BM25 ordering.
        hits = [
            Hit(chunk=c, score=float(s))
            for c, words, s in zip(chunks, corpus, scores, strict=False)
            if set(tokens).intersection(words)
        ]
        return sorted(hits, key=lambda h: (-h.score, h.chunk.id))[:limit]
