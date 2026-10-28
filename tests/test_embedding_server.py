import base64
import struct
from types import SimpleNamespace

import pytest

from kb.embedding_server import MODEL, embedding_response, handler_class


class FakeEmbedding:
    model = SimpleNamespace(tokenizer=lambda texts: {"input_ids": [[1, 2] for _ in texts]})

    def encode(self, texts):
        return [[0.25, -0.5] for _ in texts]


def test_openai_float_and_base64_preserve_order_and_values():
    payload = {"model": MODEL, "input": ["文档", "问题"]}
    result = embedding_response(payload, FakeEmbedding())
    assert [r["index"] for r in result["data"]] == [0, 1]
    assert result["usage"]["total_tokens"] == 4
    encoded = embedding_response({**payload, "encoding_format": "base64"}, FakeEmbedding())
    assert struct.unpack("<2f", base64.b64decode(encoded["data"][0]["embedding"])) == (0.25, -0.5)


@pytest.mark.parametrize(
    "extra",
    [
        {"model": "other"},
        {"input": []},
        {"input": [1]},
        {"input": [""]},
        {"input": ["x"] * 129},
        {"input": "x" * 32769},
        {"encoding_format": "other"},
        {"dimensions": 768},
    ],
)
def test_reject_invalid_requests(extra):
    with pytest.raises(ValueError):
        embedding_response({"model": MODEL, "input": "中文", **extra}, FakeEmbedding())


def test_auth_requires_token_and_rejects_browser_origins():
    handler = handler_class(FakeEmbedding(), "test-token")
    request = SimpleNamespace(headers={"Authorization": "Bearer test-token"})
    assert handler.authorized(request)
    request.headers["Origin"] = "https://untrusted.example"
    assert not handler.authorized(request)
    assert not handler.authorized(SimpleNamespace(headers={}))
