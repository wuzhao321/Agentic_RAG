import csv
import hashlib
import json
import os
import uuid
from collections.abc import Callable
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from kb.ingest.chunker import chunk_document
from kb.ingest.metadata import parse
from kb.locking import file_lock
from kb.retrieval.vector import open_collection
from kb.schema import Chunk


def read_chunks(index_dir: Path, manifest: dict | None = None) -> list[Chunk]:
    if manifest is None:
        manifest = json.loads((index_dir / "active.json").read_text(encoding="utf-8"))
    path = index_dir / manifest["chunks_file"]
    return [
        Chunk.model_validate_json(line) for line in path.read_text(encoding="utf-8").splitlines()
    ]


def ingest(
    config: dict,
    embedding,
    *,
    rows: list[dict] | None = None,
    progress: Callable | None = None,
    job: dict | None = None,
    guard: Callable | None = None,
    publish_guard: Callable[[], AbstractContextManager] | None = None,
) -> dict:
    """Serialize index writers and enter publish_guard for the atomic manifest switch."""
    with file_lock(Path(config["index_dir"]) / ".write.lock"):
        return _ingest(config, embedding, rows, progress, job, guard, publish_guard)


def _publish(active, manifest, publish_guard=None):
    temporary = active.parent / f"active_{uuid.uuid4().hex}.tmp"
    try:
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        with publish_guard() if publish_guard else nullcontext():
            temporary.replace(active)
    finally:
        temporary.unlink(missing_ok=True)


def _ingest(config, embedding, rows, progress, job, guard, publish_guard):
    def notify(stage, completed, total):
        if progress:
            progress(stage, completed, total)

    if guard:
        guard()
    raw = Path(config["raw_dir"]).resolve()
    if rows is None:
        with Path(config["registry"]).open(encoding="utf-8-sig", newline="") as f:
            registry = list(csv.DictReader(f))
    else:
        registry = rows
    if not registry:
        raise ValueError("登记表为空，请先放入 PDF/DOCX 并运行 kb scan")
    if len({r["doc_id"] for r in registry}) != len(registry):
        raise ValueError("登记表 doc_id 重复")
    all_chunks, report = [], []
    notify("parsing", 0, len(registry))
    for number, row in enumerate(registry, 1):
        if row["doc_type"] not in {"技术规范", "交付报告", "接口说明书", "其他"}:
            raise ValueError(f"请补全 doc_type: {row['file_name']}")
        if not row["project_name"]:
            raise ValueError(f"请补全 project_name: {row['file_name']}")
        path = (raw / row.get("storage_path", row["file_name"])).resolve()
        if not path.is_relative_to(raw):
            raise ValueError("登记表文件路径超出 data/raw")
        if row.get("sha256"):
            with path.open("rb") as handle:
                if hashlib.file_digest(handle, "sha256").hexdigest() != row["sha256"]:
                    raise ValueError(f"文档内容与任务快照不一致：{row['file_name']}")
        parsed = parse(path, config)
        metadata = {key: row[key] for key in ("doc_id", "doc_type", "project_id", "project_name")}
        metadata.update(doc_name=row["file_name"], source_format=path.suffix[1:].lower())
        chunks = chunk_document(parsed, metadata, config)
        all_chunks.extend(chunks)
        warnings = list(parsed.warnings)
        if any(len(c.text) > config["chunk_size"] for c in chunks):
            warnings.append("存在超长表格行，保留完整行导致 chunk 超过目标长度")
        report.append(
            {
                "document": row["file_name"],
                "chunks": len(chunks),
                "tables": sum(b.kind == "table" for b in parsed.blocks),
                "warnings": warnings,
            }
        )
        notify("parsing", number, len(registry))
    if not all_chunks:
        raise ValueError("没有可索引文本；检查解析告警及扫描件")
    index_dir = Path(config["index_dir"])
    index_dir.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(c.model_dump_json() for c in all_chunks) + "\n"
    fingerprint = hashlib.sha256(
        (payload + json.dumps(config, sort_keys=True)).encode()
    ).hexdigest()
    active = index_dir / "active.json"
    if active.exists():
        old = json.loads(active.read_text(encoding="utf-8"))
        if old["fingerprint"] == fingerprint:
            result = {
                "documents": report,
                "total_chunks": len(all_chunks),
                "unchanged": True,
                "collection": old["collection"],
                "fingerprint": fingerprint,
            }
            if job:
                notify("publishing", 0, 1)
                if guard:
                    guard()
                _publish(active, {**old, "job": job, "result": result}, publish_guard)
            return result
    # Build a fresh generation. Atomic manifest swap publishes both vector and BM25 corpus.
    # Old generations remain available if a rebuild is interrupted.
    generation = uuid.uuid4().hex
    collection_name = f"kb_{generation}"
    collection = open_collection(index_dir / "chroma", collection_name, config["chroma"])
    size = config["embedding"]["batch_size"]
    notify("embedding", 0, len(all_chunks))
    for offset in range(0, len(all_chunks), size):
        batch = all_chunks[offset : offset + size]
        collection.upsert(
            ids=[c.id for c in batch],
            documents=[c.text for c in batch],
            metadatas=[c.metadata for c in batch],
            embeddings=embedding.encode([c.text for c in batch]),
        )
        notify("embedding", min(offset + size, len(all_chunks)), len(all_chunks))
    chunks_file = f"chunks_{generation}.jsonl"
    (index_dir / chunks_file).write_text(payload, encoding="utf-8")
    manifest = {
        "collection": collection_name,
        "chunks_file": chunks_file,
        "fingerprint": fingerprint,
        "embedding": config["embedding"],
    }
    result = {
        "documents": report,
        "total_chunks": len(all_chunks),
        "unchanged": False,
        "collection": collection_name,
        "fingerprint": fingerprint,
    }
    if job:
        manifest.update(job=job, result=result)
    notify("publishing", 0, 1)
    if guard:
        guard()
    _publish(active, manifest, publish_guard)
    return result
