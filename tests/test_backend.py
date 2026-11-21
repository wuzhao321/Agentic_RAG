import csv
import io
import json
import os
import socket
import subprocess
import sys
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
import yaml
from docx import Document as WordDocument

if not os.environ.get("KB_TEST_DATABASE_URL") and os.environ.get("KB_TEST_REAL_BGE") != "1":
    pytest.importorskip("sqlalchemy")
from sqlalchemy import create_engine, make_url
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import CreateSchema, DropSchema

from kb.backend.models import Base
from kb.backend.schemas import DocumentMetadata, JobRequest
from kb.backend.service import (
    ServiceError,
    create_job,
    get_job,
    import_registry,
    list_documents,
    retry_job,
    upload_document,
)
from kb.backend.settings import Settings
from kb.backend.worker import Worker
from kb.config import load_config
from kb.ingest.indexer import read_chunks
from kb.ingest.metadata import scan
from kb.locking import LockBusyError, file_lock
from kb.retrieval.bm25 import BM25Index


class TinyEmbedding:
    def encode(self, texts):
        return [[1.0, len(text) / 100, 0.5] for text in texts]


def document_bytes(text="CreateOrder API"):
    document = WordDocument()
    document.add_paragraph(text)
    output = io.BytesIO()
    document.save(output)
    output.seek(0)
    return output


@pytest.fixture
def backend(tmp_path):
    config = load_config("ingest")
    (tmp_path / "configs").mkdir()
    (tmp_path / "configs/ingest.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    for name in ["agent", "retrieval", "demo", "backend"]:
        (tmp_path / f"configs/{name}.yaml").write_text(
            yaml.safe_dump(load_config(name)), encoding="utf-8"
        )
    url = os.environ.get("KB_TEST_DATABASE_URL")
    settings = Settings(
        root=tmp_path,
        database_url=url or "postgresql+psycopg://unused/logic_tests_only",
    )
    admin, schema = None, None
    if url:
        from kb.backend.db import migrate

        admin = create_engine(url)
        schema = "kb_test_" + uuid.uuid4().hex
        with admin.begin() as connection:
            connection.execute(CreateSchema(schema))
        scoped_url = make_url(url).update_query_dict({"options": f"-csearch_path={schema}"})
        settings = settings.model_copy(
            update={"database_url": scoped_url.render_as_string(hide_password=False)}
        )
        engine = create_engine(scoped_url)
    else:
        engine = create_engine(
            f"sqlite:///{tmp_path / 'logic.sqlite'}", connect_args={"check_same_thread": False}
        )
    try:
        if url:
            migrate(engine)
            migrate(engine)
        else:
            Base.metadata.create_all(engine)
        yield settings, sessionmaker(engine, expire_on_commit=False), engine
    finally:
        engine.dispose()
        if admin:
            with admin.begin() as connection:
                connection.execute(DropSchema(schema, cascade=True))
            admin.dispose()


def add_document(settings, sessions, text="CreateOrder API"):
    return upload_document(
        sessions,
        settings,
        document_bytes(text),
        "../../interface.docx",
        DocumentMetadata(doc_type="接口说明书", project_name="后端演示"),
    )


def seed_registry(settings, text="Existing API"):
    config = settings.ingest_config()
    raw = Path(config["raw_dir"])
    raw.mkdir(parents=True)
    (raw / "existing.docx").write_bytes(document_bytes(text).getvalue())
    registry = Path(config["registry"])
    registry.parent.mkdir(parents=True)
    row = {
        "doc_id": "existing",
        "file_name": "existing.docx",
        "doc_type": "接口说明书",
        "project_name": "后端演示",
        "project_id": "",
    }
    with registry.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=row)
        writer.writeheader()
        writer.writerow(row)
    return raw, registry


def test_catalog_concurrent_idempotency_and_full_publish(backend, monkeypatch):
    from kb import runtime

    settings, sessions, _ = backend
    raw, registry = seed_registry(settings)
    assert import_registry(sessions, settings) == 1
    assert import_registry(sessions, settings) == 0
    first = add_document(settings, sessions)
    second = add_document(settings, sessions, "DeleteOrder API")

    def submit(_):
        return create_job(sessions, settings, "same-request", JobRequest()).id

    with ThreadPoolExecutor(max_workers=10) as pool:
        identifiers = list(pool.map(submit, range(10)))
    assert len(set(identifiers)) == 1
    identifier = identifiers[0]
    with pytest.raises(ServiceError, match="不同请求") as conflict:
        create_job(sessions, settings, "same-request", JobRequest(description="changed"))
    assert conflict.value.status == 409

    class ObservedEmbedding(TinyEmbedding):
        def encode(self, texts):
            assert get_job(sessions, identifier).status == "running"
            assert list_documents(sessions, 0, 20)["total"] == 3
            return super().encode(texts)

    assert Worker(settings, sessions, lambda cfg: ObservedEmbedding()).run(once=True)
    job = get_job(sessions, identifier)
    assert job.status == "succeeded" and job.attempt_count == 1
    assert job.result["total_chunks"] == 3
    manifest = json.loads((settings.index_dir / "active.json").read_text())
    assert manifest["embedding"] == load_config("ingest", settings.root)["embedding"]
    chunks = read_chunks(settings.index_dir)
    assert {c.metadata["doc_id"] for c in chunks} == {"existing", first.id, second.id}
    assert len(scan(raw, registry, settings.ingest_config())) == 1

    loaded = {}

    def embedding(config):
        loaded["embedding"] = config["model"]
        return TinyEmbedding()

    def reranker(config):
        loaded["reranker"] = config["model"]
        return None

    monkeypatch.setattr(runtime, "LocalEmbedding", embedding)
    monkeypatch.setattr(runtime, "Reranker", reranker)
    runtime.make_retrieval(settings.root)
    for key, config_name in [("embedding", "ingest"), ("reranker", "retrieval")]:
        model = load_config(config_name, settings.root)[key]["model"]
        assert Path(loaded[key]) == (settings.root / model).resolve()


def test_worker_failure_interrupt_retry_and_published_recovery(backend, monkeypatch):
    settings, sessions, _ = backend
    add_document(settings, sessions)
    create_job(sessions, settings, "initial", JobRequest())
    Worker(settings, sessions, lambda cfg: TinyEmbedding()).run(once=True)
    active = settings.index_dir / "active.json"
    before = active.read_bytes()
    add_document(settings, sessions, "New API")
    job = create_job(sessions, settings, "second", JobRequest())

    class BrokenEmbedding:
        def encode(self, texts):
            raise RuntimeError("simulated model failure")

    Worker(settings, sessions, lambda cfg: BrokenEmbedding()).run(once=True)
    assert get_job(sessions, job.id).status == "failed"
    assert active.read_bytes() == before
    hits = BM25Index(read_chunks(settings.index_dir)).search("CreateOrder", 1)
    assert hits and "CreateOrder" in hits[0].chunk.text
    retry_job(sessions, settings, job.id)
    retry_job(sessions, settings, job.id)

    class InterruptedEmbedding:
        def encode(self, texts):
            raise SystemExit("simulate process exit")

    with pytest.raises(SystemExit):
        Worker(settings, sessions, lambda cfg: InterruptedEmbedding()).run(once=True)
    assert get_job(sessions, job.id).status == "running"
    assert active.read_bytes() == before
    recovered = Worker(settings, sessions, lambda cfg: TinyEmbedding())
    assert not recovered.run(once=True)
    assert get_job(sessions, job.id).status == "interrupted"
    retry_job(sessions, settings, job.id)

    def stop_before_database_update(*args, **kwargs):
        raise SystemExit("simulate crash after publication")

    with monkeypatch.context() as patch:
        patch.setattr(recovered, "finish", stop_before_database_update)
        with pytest.raises(SystemExit):
            recovered.run(once=True)
    assert active.read_bytes() != before
    assert get_job(sessions, job.id).status == "running"
    assert not recovered.run(once=True)
    restored = get_job(sessions, job.id)
    assert restored.status == "succeeded" and restored.attempt_count == 3
    assert restored.result["total_chunks"] == 2


def test_stale_snapshot_and_writer_exclusion(backend):
    settings, sessions, _ = backend
    add_document(settings, sessions)
    old = create_job(sessions, settings, "old", JobRequest())
    add_document(settings, sessions, "New API")
    worker = Worker(settings, sessions, lambda cfg: TinyEmbedding())
    worker.run(once=True)
    assert get_job(sessions, old.id).status == "failed"
    with pytest.raises(ServiceError, match="已变化"):
        retry_job(sessions, settings, old.id)
    latest = create_job(sessions, settings, "latest", JobRequest())
    for name in [".write.lock", ".worker.lock"]:
        with file_lock(settings.index_dir / name), pytest.raises(LockBusyError):
            worker.run(once=True)
    assert get_job(sessions, latest.id).attempt_count == 0
    worker.run(once=True)
    assert get_job(sessions, latest.id).status == "succeeded"


@pytest.mark.parametrize("same_content", [True, False])
def test_late_upload_preserves_published_index(backend, monkeypatch, same_content):
    from kb.ingest import indexer

    settings, sessions, _ = backend
    add_document(settings, sessions)
    create_job(sessions, settings, "first", JobRequest())
    worker = Worker(settings, sessions, lambda config: TinyEmbedding())
    worker.run(once=True)
    active = settings.index_dir / "active.json"
    before = active.read_bytes()
    if not same_content:
        add_document(settings, sessions, "Another document")
    job = create_job(sessions, settings, "next", JobRequest())
    publish = indexer._publish

    def upload_then_publish(*args):
        add_document(settings, sessions, "Arrived after embedding")
        return publish(*args)

    monkeypatch.setattr(indexer, "_publish", upload_then_publish)
    worker.run(once=True)
    failed = get_job(sessions, job.id)
    assert failed.status == "failed" and "已变化" in failed.error
    assert active.read_bytes() == before


@contextmanager
def live_api(settings):
    # Pick a loopback port for the CLI in this isolated project directory.
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    config = {**load_config("backend", settings.root), "port": port}
    (settings.root / "configs/backend.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")
    command = [sys.executable, "-m", "kb.cli"]
    subprocess.run([*command, "backend-init"], cwd=settings.root, check=True, timeout=60)
    process = subprocess.Popen([*command, "api"], cwd=settings.root)
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{port}", timeout=120, trust_env=False) as api:
            deadline = time.monotonic() + 60
            while process.poll() is None and time.monotonic() < deadline:
                try:
                    if api.get("/health", timeout=2).is_success:
                        break
                except httpx.ConnectError:
                    pass
                time.sleep(0.1)
            else:
                pytest.fail(f"API did not become healthy (exit={process.poll()})")
            yield api
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


def observe_live_worker(api, settings, identifier):
    command = [sys.executable, "-m", "kb.cli", "worker", "--once"]
    process = subprocess.Popen(command, cwd=settings.root)
    observed = set()
    try:
        deadline = time.monotonic() + 300
        while process.poll() is None:
            status = api.get(f"/ingest-jobs/{identifier}").raise_for_status().json()
            observed.add(status["status"])
            if time.monotonic() > deadline:
                pytest.fail("Worker did not finish the three-document job within five minutes")
            time.sleep(0.1)
        assert process.returncode == 0
        assert "running" in observed
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def test_five_http_endpoints_and_upload_validation(backend, monkeypatch):
    real_bge = os.environ.get("KB_TEST_REAL_BGE") == "1"
    if real_bge and not os.environ.get("KB_TEST_DATABASE_URL"):
        pytest.fail("真实 HTTP/BGE 联调需要 KB_TEST_DATABASE_URL 指向 PostgreSQL")
    if not os.environ.get("KB_TEST_DATABASE_URL"):
        pytest.importorskip("fastapi", reason="API integration requires requirements-backend.txt")
        pytest.importorskip("alembic")
        pytest.importorskip("python_multipart")
    from fastapi.testclient import TestClient

    from kb.backend.api import create_app

    settings, sessions, engine = backend
    if real_bge:
        (settings.root / "models").symlink_to(
            Path(__file__).resolve().parents[1] / "models", target_is_directory=True
        )
        monkeypatch.setenv("KB_DATABASE_URL", settings.database_url)
        monkeypatch.setenv("KB_HOST", "127.0.0.1")
        monkeypatch.setenv("KB_ACCESS_TOKEN", "")
        monkeypatch.setenv("KB_ALLOWED_HOSTS", "127.0.0.1,localhost")
        monkeypatch.chdir(settings.root.parent)
    seed_registry(settings, "采购登记表记载的钢材批号为 GC-183。")
    if not real_bge:
        assert import_registry(sessions, settings) == 1
    samples = [
        ("夜班温室操作.docx", "蓝桉温室的夜间目标温度为十八摄氏度。"),
        ("陶瓷出库规程.docx", "白瓷茶具出库时必须使用双层缓冲纸包装。"),
    ]
    clients = live_api(settings) if real_bge else TestClient(create_app(settings, engine))
    with clients as client:
        metadata = {"doc_type": "接口说明书", "project_name": "后端演示"}
        assert client.get("/docs").status_code == 200
        for name, text in samples:
            response = client.post(
                "/documents", data=metadata, files={"file": (name, document_bytes(text).getvalue())}
            )
            assert response.status_code == 201
        assert client.get("/documents").json()["total"] == 3
        assert client.get("/api/status").json()["document_count"] == 3
        assert (
            client.post(
                "/api/query", json={"mode": "search", "question": "API", "session": "test"}
            ).status_code
            == 409
        )
        assert client.get("/documents?offset=-1").status_code == 422
        assert (
            client.post(
                "/documents", data=metadata, files={"file": ("bad.docx", b"not a docx")}
            ).status_code
            == 422
        )
        if not real_bge:  # A TCP client enforces Content-Length before the ASGI request arrives.
            assert (
                client.post(
                    "/documents", content=b"x", headers={"Content-Length": str(100 * 1024 * 1024)}
                ).status_code
                == 413
            )
        assert (
            client.get("/documents", headers={"Origin": "https://outside.example"}).status_code
            == 403
        )
        headers = {"Idempotency-Key": "http-1"}

        def submit(_):
            response = client.post("/ingest-jobs", json={}, headers=headers)
            assert response.status_code == 202
            return response.json()["id"]

        with ThreadPoolExecutor(max_workers=10) as pool:
            identifiers = set(pool.map(submit, range(10)))
        assert len(identifiers) == 1
        identifier = identifiers.pop()
        conflict = client.post("/ingest-jobs", json={"description": "changed"}, headers=headers)
        assert conflict.status_code == 409
        assert client.get(f"/ingest-jobs/{identifier}").json()["status"] == "pending"
        assert client.post(f"/ingest-jobs/{identifier}/retry").status_code == 202
        if real_bge:
            observe_live_worker(client, settings, identifier)
        else:

            class ObserveEmbedding(TinyEmbedding):
                def encode(self, texts):
                    status = client.get(f"/ingest-jobs/{identifier}").raise_for_status().json()
                    assert (status["status"], status["stage"]) == ("running", "embedding")
                    return super().encode(texts)

            Worker(settings, sessions, lambda cfg: ObserveEmbedding()).run(once=True)
        job = client.get(f"/ingest-jobs/{identifier}").json()
        assert job["status"] == "succeeded", job["error"]
        assert len(job["result"]["documents"]) == 3
        if real_bge:
            for question, name, expected in [
                ("采购登记表中的钢材批号是什么？", "existing.docx", "GC-183"),
                ("蓝桉温室的夜间目标温度是多少？", samples[0][0], "十八摄氏度"),
                ("白瓷茶具出库时用什么包装？", samples[1][0], "双层缓冲纸"),
            ]:
                response = client.post(
                    "/api/query", json={"mode": "search", "question": question, "session": "demo"}
                )
                assert response.status_code == 200
                assert any(
                    source["source"].split(" > ")[0] == name and expected in source["text"]
                    for source in response.json()["sources"]
                )
        assert client.post(f"/ingest-jobs/{identifier}/retry").status_code == 409
        assert client.get("/ingest-jobs/absent").status_code == 404
        assert "/api/query" in client.get("/openapi.json").json()["paths"]
        assert client.get("/ingest-jobs").json()[0]["id"] == identifier

    remote = settings.model_copy(update={"host": "0.0.0.0", "access_token": "a" * 32})
    with TestClient(create_app(remote, engine)) as client:
        assert client.get("/documents").status_code == 401
        assert (
            client.get("/api/status", headers={"Authorization": "Bearer " + "a" * 32}).status_code
            == 200
        )


def test_hosted_search_reloads_index_without_calling_llm(backend, monkeypatch):
    from types import SimpleNamespace

    import kb.backend.query as module

    settings, _, _ = backend
    service = module.HostedQueryService(settings)
    calls = []

    def retrieval(*args):
        calls.append("load")
        chunk = SimpleNamespace(
            id="chunk",
            text="retrieved evidence",
            metadata={"doc_name": "guide.pdf", "section_path": "API", "page": 1},
        )
        hit = SimpleNamespace(chunk=chunk, score=0.9)
        return SimpleNamespace(search=lambda question: [hit]), [chunk]

    def no_llm(config):
        pytest.fail("Search-only requests must not call a generation model")

    monkeypatch.setattr(module, "make_retrieval", retrieval)
    monkeypatch.setattr(module, "make_llm", no_llm)
    settings.index_dir.mkdir(parents=True)
    active = settings.index_dir / "active.json"
    active.write_text(json.dumps({"collection": "first"}), encoding="utf-8")
    assert service.query("search", "API", "session")["sources"][0]["text"] == "retrieved evidence"
    service.query("search", "API", "session")
    assert len(calls) == 1
    active.write_text(json.dumps({"collection": "second"}), encoding="utf-8")
    service.query("search", "API", "session")
    assert len(calls) == 2
    with service.lock, pytest.raises(ServiceError) as busy:
        service.query("search", "API", "session")
    assert busy.value.status == 429
    service.close()


def test_network_binding_requires_service_credential():
    with pytest.raises(ValueError, match="KB_ACCESS_TOKEN"):
        Settings(host="0.0.0.0", database_url="postgresql+psycopg://unused/db")
