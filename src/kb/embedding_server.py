"""Loopback-only OpenAI embeddings adapter for the local Dify comparison."""

import argparse
import base64
import hmac
import json
import secrets
import struct
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock

from kb.config import load_config

MODEL = "bge-large-zh-v1.5"
MAX_BODY = 1_048_576


def embedding_response(payload, embedding):
    if not isinstance(payload, dict) or payload.get("model") != MODEL:
        raise ValueError("unsupported model")
    texts = payload.get("input")
    if isinstance(texts, str):
        texts = [texts]
    if (
        not isinstance(texts, list)
        or not 1 <= len(texts) <= 128
        or any(not isinstance(t, str) or not t or len(t) > 32768 for t in texts)
    ):
        raise ValueError("input must contain 1..128 nonempty strings of at most 32768 characters")
    encoding = payload.get("encoding_format", "float")
    if encoding not in {"float", "base64"}:
        raise ValueError("unsupported encoding_format")
    if payload.get("dimensions", 1024) != 1024:
        raise ValueError("model dimensions must be 1024")
    vectors = embedding.encode(texts)
    tokens = sum(len(ids) for ids in embedding.model.tokenizer(texts)["input_ids"])
    return {
        "object": "list",
        "model": MODEL,
        "data": [
            {
                "object": "embedding",
                "index": i,
                "embedding": vector
                if encoding == "float"
                else base64.b64encode(struct.pack(f"<{len(vector)}f", *vector)).decode("ascii"),
            }
            for i, vector in enumerate(vectors)
        ],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


def handler_class(embedding, token):
    inference_lock = Lock()

    class Handler(BaseHTTPRequestHandler):
        def setup(self):
            super().setup()
            self.connection.settimeout(30)

        def log_message(self, *args):
            pass  # Never log input text or credentials.

        def reply(self, status, body):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def authorized(self):
            supplied = self.headers.get("Authorization", "").encode()
            return not self.headers.get("Origin") and hmac.compare_digest(
                supplied, ("Bearer " + token).encode()
            )

        def do_GET(self):
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            if self.path != "/v1/models":
                return self.reply(404, {"error": "not found"})
            self.reply(200, {"object": "list", "data": [{"id": MODEL, "object": "model"}]})

        def do_POST(self):
            if not self.authorized():
                return self.reply(401, {"error": "unauthorized"})
            if self.path != "/v1/embeddings":
                return self.reply(404, {"error": "not found"})
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < size <= MAX_BODY:
                    return self.reply(413, {"error": "invalid body size"})
                payload = json.loads(self.rfile.read(size))
                with inference_lock:
                    result = embedding_response(payload, embedding)
            except (ValueError, TypeError):
                return self.reply(400, {"error": "invalid embedding request"})
            except Exception:
                return self.reply(500, {"error": "embedding inference failed"})
            self.reply(200, result)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--token-file", type=Path, default=Path(".local/dify-embedding.token"))
    args = parser.parse_args()
    args.token_file.parent.mkdir(parents=True, exist_ok=True)
    if not args.token_file.exists():
        with args.token_file.open("x") as stream:
            args.token_file.chmod(0o600)
            stream.write(secrets.token_urlsafe(32))
    args.token_file.chmod(0o600)
    token = args.token_file.read_text().strip()
    if len(token) < 32:
        raise ValueError("embedding token must contain at least 32 characters")
    from kb.retrieval.vector import LocalEmbedding

    embedding = LocalEmbedding(load_config("ingest")["embedding"])

    class Server(ThreadingHTTPServer):
        request_queue_size = 32

    with Server(("127.0.0.1", args.port), handler_class(embedding, token)) as server:
        print(f"Embeddings ready: http://127.0.0.1:{args.port}/v1 ({MODEL})", flush=True)
        server.serve_forever()


if __name__ == "__main__":
    main()
