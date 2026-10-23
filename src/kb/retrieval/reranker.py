from pathlib import Path

from kb.schema import Hit


class Reranker:
    def __init__(self, config: dict):
        from sentence_transformers import CrossEncoder

        path = Path(config["model"])
        if not path.exists() or not any(path.iterdir()):
            raise FileNotFoundError(f"本地 reranker 模型不存在: {path}")
        self.config = config
        self.model = CrossEncoder(str(path), device=config["device"], local_files_only=True)

    def rank(self, query: str, hits: list[Hit], limit: int) -> list[Hit]:
        if not hits:
            return []
        scores = self.model.predict(
            [(query, h.chunk.text) for h in hits], batch_size=self.config["batch_size"]
        )
        ranked = [
            Hit(chunk=h.chunk, score=float(s))
            for h, s in zip(hits, scores, strict=False)
            if float(s) >= self.config["min_score"]
        ]
        return sorted(ranked, key=lambda h: -h.score)[:limit]
